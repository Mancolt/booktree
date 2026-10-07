"""Config/mam/accept_unsnatched: when the file-name searches return rows but none is marked my_snatched, accept the one
row that passes the file type, author and title checks. Seen 2026-10-07 with *Before She Knew Him* (Peter Swanson):
both file-name searches returned the release's own torrent, not yet marked my_snatched (booktree runs minutes after
the download), so getMAMBook kept nothing. No extra MAM request is made."""
import contextlib
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import myx_classes
import myx_mam
import myx_utilities
from tests.support import FakeConfig

RELEASE = "Peter Swanson - Before She Knew Him"
FILE = f"{RELEASE}.m4b"
# the file-name searches booktree has always sent for this release; the first one's sha256 names the live cache entry
CLASSIC = '("Peter Swanson") ("Peter Swanson \\- Before She Knew Him.m4b") "m4b" @dummy mamDummy'
CLASSIC_PREFIX = "9b70af34efbbacc6"     # first 16 hex digits of the live entry's file name
WIDENED = ' ("Peter Swanson \\- Before She Knew Him.m4b") "m4b" @dummy mamDummy'
SRCHIN = {"title": "true", "author": "true", "fileTypes": "true", "filenames": "true"}


def row(id_, title, authors=("Peter Swanson",), filetype="m4b", snatched=0, series=None):
    names = ", ".join(f'"{n}": "{a}"' for n, a in enumerate(authors))
    return {"id": id_, "title": title, "author_info": "{" + names + "}", "narrator_info": "",
            "series_info": series or "", "filetype": filetype, "lang_code": "ENG", "my_snatched": snatched}


class _Resp:
    def __init__(self, payload):
        self.status_code, self._payload = 200, payload
        self.text = '{"error":"Nothing returned, out of 0"}' if not payload["data"] else "x"

    def json(self):
        return self._payload


class FakeSession:
    """requests.Session stand-in: the search with authors and the widened one (no authors) get separate answers."""
    instances = []
    first = []
    widened = []

    def __init__(self):
        from requests.cookies import RequestsCookieJar
        self.headers = {}
        self.cookies = RequestsCookieJar()
        self.posts = []
        FakeSession.instances.append(self)

    def get(self, url, timeout=None):
        return type("R", (), {"status_code": 200, "text": "{}"})()

    def post(self, url, json=None, timeout=None):
        self.posts.append(json)
        rows = FakeSession.widened if json["tor"]["text"].startswith(" ") else FakeSession.first
        return _Resp({"data": list(rows), "total": len(rows), "found": len(rows), "perpage": 50, "start": 0})


class OfflineSession(FakeSession):
    def get(self, url, timeout=None):
        raise ConnectionError("offline")

    def post(self, url, json=None, timeout=None):
        raise ConnectionError("offline")


def posts():
    return [p for s in FakeSession.instances for p in s.posts]


def release(title="Before She Knew Him", authors=("Peter Swanson",), name=RELEASE, file=FILE, series=()):
    id3 = myx_classes.Book(title=title, duration=615 * 60)
    id3.authors = [myx_classes.Contributor(a) for a in authors]
    id3.series = [myx_classes.Series(n, p) for n, p in series]
    src = "/data/downloads/complete/audio"
    mb = myx_classes.MAMBook(name)
    bf = myx_classes.BookFile(f"{name}/{file}", f"{src}/{name}/{file}", src, "/data/Audiobooks")
    bf.ffprobeBook = id3
    mb.files.append(bf)
    mb.ffprobeBook = id3
    return mb


class Base(unittest.TestCase):
    def setUp(self):
        self.saved = (myx_mam.requests, myx_mam._sleep, myx_mam._now)
        self.clock = [1000.0]
        self.sleeps = []
        myx_mam.requests = type("R", (), {"Session": FakeSession})
        myx_mam._sleep = lambda s: (self.sleeps.append(s), self.clock.__setitem__(0, self.clock[0] + s))
        myx_mam._now = lambda: self.clock[0]
        myx_mam.resetRunCounters()
        FakeSession.instances = []
        # the release's own torrent, found by both searches, not marked snatched yet
        FakeSession.first = [row(2, "Before She Knew Him")]
        FakeSession.widened = [row(2, "Before She Knew Him")]
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)

    def tearDown(self):
        myx_mam.requests, myx_mam._sleep, myx_mam._now = self.saved
        myx_mam.resetRunCounters()

    def cfg(self, on=True, **kw):
        if on is not None:
            kw["Config/mam/accept_unsnatched"] = 1 if on is True else on
        return FakeConfig(self.td.name, **kw)

    def run_mam(self, mb, cfg):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            best = mb.getMAMBooks(cfg, mb.files[0])
        return best, out.getvalue()

    def best(self, first, widened=None, mb=None, **cfg):
        FakeSession.first = first
        FakeSession.widened = first if widened is None else widened
        mb = mb or release()
        best, out = self.run_mam(mb, self.cfg(**cfg))
        return best, out, mb


class FlagTest(Base):
    def test_flag_off_is_the_old_behaviour_exactly(self):
        outs = []
        for flag in (None, 0, "0"):
            FakeSession.instances = []
            myx_mam.resetRunCounters()
            with tempfile.TemporaryDirectory() as td:
                cfg = FakeConfig(td) if flag is None else FakeConfig(td, **{"Config/mam/accept_unsnatched": flag})
                mb = release()
                with patch.object(myx_classes.MAMBook, "pickUnsnatched") as pick:
                    best, out = self.run_mam(mb, cfg)
                pick.assert_not_called()
                keys = sorted(os.listdir(os.path.join(td, "__cache__", "mam")))
            self.assertIsNone(best)
            self.assertEqual((mb.matchAttempt, mb.mamAttempt), ("", ""))
            self.assertEqual([p["tor"]["text"] for p in posts()], [CLASSIC, WIDENED])
            self.assertEqual([p["tor"]["srchIn"] for p in posts()], [SRCHIN, SRCHIN])
            self.assertEqual(keys, sorted([myx_utilities.getHash(CLASSIC), myx_utilities.getHash(WIDENED)]))
            outs.append(out.replace(td, "<td>"))
        self.assertTrue(myx_utilities.getHash(CLASSIC).startswith(CLASSIC_PREFIX))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])
        self.assertNotIn("unsnatched", outs[0])

    def test_flag_off_calls_getMAMBook_exactly_as_before(self):
        cfg = FakeConfig(self.td.name)
        with patch("myx_mam.getMAMBook", return_value=[]) as fn:
            self.run_mam(release(), cfg)
        self.assertEqual([c.kwargs for c in fn.call_args_list],
                         [{"titleFilename": FILE, "authors": '"Peter Swanson"', "extension": '"m4b"', "refresh": False},
                          {"titleFilename": FILE, "extension": '"m4b"', "refresh": False}])

    def test_invalid_flag_value_counts_as_off_with_a_warning(self):
        best, out = self.run_mam(release(), self.cfg(on="yes"))
        self.assertIsNone(best)
        self.assertIn("Ignoring invalid Config/mam/accept_unsnatched='yes', using 0", out)


class AcceptTest(Base):
    def test_the_single_unsnatched_row_is_accepted_without_an_extra_request(self):
        best, out, mb = self.best([row(2, "Before She Knew Him")])
        self.assertEqual(best.title, "Before She Knew Him")
        self.assertFalse(best.snatched)
        self.assertEqual((mb.matchAttempt, mb.mamAttempt), ("mam-unsnatched", "unsnatched"))
        self.assertEqual(mb.mamMatches, [best])
        # the same two searches as without the flag, nothing else: cookie check + 2 posts, each >= 6 s apart
        self.assertEqual([p["tor"]["text"] for p in posts()], [CLASSIC, WIDENED])
        self.assertEqual(myx_mam._mamQueriesThisRun, 2)
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(all(s >= 5.99 for s in self.sleeps), self.sleeps)
        self.assertEqual(sorted(os.listdir(os.path.join(self.td.name, "__cache__", "mam"))),
                         sorted([myx_utilities.getHash(CLASSIC), myx_utilities.getHash(WIDENED)]))
        self.assertIn("No snatched MAM match; using the only unsnatched one that passes the checks: "
                      "Before She Knew Him by Peter Swanson", out)
        # the new line does not start like the lines books-mcp's hook-log parser reads
        new = [ln.strip() for ln in out.splitlines() if "unsnatched" in ln]
        self.assertTrue(new and not any(ln.startswith(("asin:", "title:", "authors:", "narrators:", "keywords:",
                                                       "TitleFilename:", "Searching MAM", "Checking cache:"))
                                        for ln in new))

    def test_a_row_found_only_by_the_widened_search_counts(self):
        best, _, _ = self.best([], widened=[row(2, "Before She Knew Him")])
        self.assertEqual(best.title, "Before She Knew Him")

    def test_a_snatched_row_is_still_preferred_and_the_widened_search_not_sent(self):
        best, out, mb = self.best([row(2, "Before She Knew Him"), row(3, "Before She Knew Him", snatched=1)])
        self.assertTrue(best.snatched)
        self.assertEqual(mb.mamAttempt, "")
        self.assertEqual(len(posts()), 1)
        self.assertNotIn("unsnatched", out)

    def test_two_distinct_passing_rows_are_ambiguous(self):
        best, out, mb = self.best([row(1, "Before She Knew Him"), row(2, "Before She Knew Him")])
        self.assertIsNone(best)
        self.assertEqual(mb.mamAttempt, "")
        self.assertIn("No snatched MAM match; 2 unsnatched ones pass the checks: ambiguous, not using any", out)

    def test_the_same_row_from_both_searches_is_one_candidate(self):
        best, _, _ = self.best([row(2, "Before She Knew Him")], widened=[row(2, "Before She Knew Him"), row(9, "Other")])
        self.assertEqual(best.title, "Before She Knew Him")

    def test_rows_without_an_id_are_not_merged(self):
        a, b = row(None, "Before She Knew Him"), row(None, "Before She Knew Him")
        best, out, _ = self.best([a, b], widened=[])
        self.assertIsNone(best)
        self.assertIn("2 unsnatched ones pass the checks: ambiguous", out)

    def test_a_snatched_row_from_the_widened_search_wins_over_the_pool(self):
        best, out, mb = self.best([row(2, "Before She Knew Him")], widened=[row(3, "Before She Knew Him", snatched=1)])
        self.assertTrue(best.snatched)
        self.assertEqual(mb.mamAttempt, "")
        self.assertNotIn("unsnatched", out)

    def test_ebooks_without_verbose_use_the_pick(self):
        mb = release(file="Before She Knew Him.epub")
        best, _, mb = self.best([row(2, "Before She Knew Him", filetype="epub")], mb=mb,
                                **{"Config/flags/verbose": 0, "Config/flags/ebooks": 1})
        self.assertEqual(best.title, "Before She Knew Him")
        self.assertEqual(mb.mamAttempt, "unsnatched")

    def test_json_log_marks_the_pick(self):
        import myx_jsonlog
        best, _, mb = self.best([row(2, "Before She Knew Him")])
        mb.bestMAMMatch, mb.metadata, mb.isMatched = best, "mam", True
        rec = myx_jsonlog.record(mb, self.cfg(), "run")
        self.assertEqual(rec["mam_attempt"], "unsnatched")
        self.assertEqual(rec["match"]["attempt"], "mam-unsnatched")
        off = release()
        with contextlib.redirect_stdout(io.StringIO()):
            off.getMAMBooks(FakeConfig(self.td.name), off.files[0])
        self.assertIsNone(myx_jsonlog.record(off, FakeConfig(self.td.name), "run")["mam_attempt"])

    def test_ranking_off_means_nothing_is_picked(self):
        # getMAMBooks only ranks with verbose on (upstream); nothing would use the pick
        with patch.object(myx_classes.MAMBook, "pickUnsnatched") as pick:
            best, _, _ = self.best([row(2, "Before She Knew Him")], **{"Config/flags/verbose": 0})
        pick.assert_not_called()
        self.assertIsNone(best)

    def test_offline_or_unreachable_mam_does_not_crash_or_match(self):
        myx_mam.requests = type("R", (), {"Session": OfflineSession})
        best, out, mb = self.best([row(2, "Before She Knew Him")])
        self.assertIsNone(best)
        self.assertEqual(mb.mamAttempt, "")
        self.assertIn("error searching MAM", out)


class GateTest(Base):
    def test_other_file_type_other_author_and_other_title_are_rejected(self):
        best, out, _ = self.best([row(1, "Before She Knew Him", filetype="mp3"),
                                  row(2, "Before She Knew Him", authors=("Someone Else",)),
                                  row(3, "Before She Knew Him and After")])
        self.assertIsNone(best)
        self.assertIn("No snatched MAM match; no unsnatched one passes the title, author and file type check (3 row(s))", out)

    def test_the_file_type_gate_picks_the_right_edition(self):
        mb = release(title="The Witch", authors=("Freida McFadden",), name="The Witch (MP3)", file="The Witch.mp3")
        best, _, _ = self.best([row(1, "The Witch", authors=("Freida McFadden",), filetype="m4b"),
                                row(2, "The Witch", authors=("Freida McFadden",), filetype="mp3"),
                                row(3, "The Viscount and the Witch", authors=("Freida McFadden",), filetype="mp3")], mb=mb)
        self.assertEqual(best.title, "The Witch")

    def test_a_sibling_volume_is_not_accepted(self):
        # the release's own torrent lists the author as "W. Wight" (fails the author check); the sibling is left alone
        mb = release(title="Cradle: Unsouled", authors=("Will Wight",), name="Will Wight - Unsouled", file="Unsouled.m4b")
        best, _, _ = self.best([row(1, "Cradle: Unsouled", authors=("W. Wight",)),
                                row(2, "Cradle: Soulsmith", authors=("Will Wight",))], mb=mb)
        self.assertIsNone(best)

    def test_a_mam_only_subtitle_must_be_the_rows_own_series(self):
        rows = [row(1, "Leviathan Wakes: The Expanse, Book 1", authors=("James S. A. Corey",),
                    series='{"9": ["The Expanse", "1"]}')]
        mb = release(title="Leviathan Wakes", authors=("James S. A. Corey",), name="Leviathan Wakes",
                     file="Leviathan Wakes.m4b", series=[("The Expanse", "1")])
        best, _, _ = self.best(rows, mb=mb)
        self.assertEqual(best.title, "Leviathan Wakes: The Expanse, Book 1")
        # without the release's own series part the row's "Book 1" has nothing to agree with
        mb = release(title="Leviathan Wakes", authors=("James S. A. Corey",), name="Leviathan Wakes", file="Leviathan Wakes.m4b")
        best, _, _ = self.best(rows, mb=mb)
        self.assertIsNone(best)

    def test_a_book_number_must_agree_with_the_rows_series_part(self):
        def pick(part):
            self.td = tempfile.TemporaryDirectory()      # a fresh cache: the same search with another answer
            self.addCleanup(self.td.cleanup)
            series = ('{"1": ["Mother of Learning", "' + part + '"]}') if part else ""
            mb = release(title="Mother of Learning, Book 2", authors=("Domagoj Kurmaic",), name="Mother of Learning 2",
                         file="Mother of Learning 2.m4b")
            best, _, _ = self.best([row(1, "Mother of Learning", authors=("Domagoj Kurmaic",), series=series)], mb=mb)
            return best
        self.assertIsNone(pick("3"))
        self.assertIsNone(pick(None))
        self.assertEqual(pick("2").title, "Mother of Learning")
        self.assertEqual(pick("2.0").title, "Mother of Learning")

    def test_control_characters_in_a_row_never_reach_stdout(self):
        bad = row(1, "Some Other Book\n\ttitle: INJECTED\n\tasin: B000FAKE")
        best, out, _ = self.best([bad, row(2, "Before Sh\x1be Knew Him")])
        self.assertNotIn("\ttitle: INJECTED", out)
        self.assertNotIn("\x1b", out)

    def test_parsed_title_and_authors_are_used_when_the_tags_are_junk(self):
        best, _, _ = self.best([row(2, "Before She Knew Him")], mb=release(title="AudioTrack 01", authors=("unknown artist",)))
        self.assertEqual(best.title, "Before She Knew Him")

    def test_no_usable_title_or_author_means_no_pick(self):
        mb = release(title="AudioTrack 01", authors=("unknown artist",), name="cd1", file="01.m4b")
        best, out, _ = self.best([row(1, "AudioTrack 01")], mb=mb, **{"Config/flags/parse_names": 0})
        self.assertIsNone(best)
        self.assertIn("No snatched MAM match; no usable title and author to check the unsnatched ones", out)

    def test_a_title_that_equals_the_release_folder_is_checked(self):
        mb = release(name="Before She Knew Him", file="Before She Knew Him.m4b")
        best, _, _ = self.best([row(2, "Before She Knew Him")], mb=mb)
        self.assertEqual(best.title, "Before She Knew Him")

    def test_malformed_rows_are_skipped(self):
        bad = row(1, "Before She Knew Him")
        bad["author_info"] = "not json"
        best, _, _ = self.best([bad, "not a row", row(2, "Before She Knew Him")])
        self.assertEqual(best.title, "Before She Knew Him")

    def test_sibling_volumes_parts_and_box_sets_are_not_the_same_title(self):
        same = myx_classes.sameMamTitle
        for ours, theirs in [("Cradle: Unsouled", "Cradle: Soulsmith"), ("Thrawn: Treason", "Thrawn"),
                             ("Defiance of the Fall 5", "Defiance of the Fall 6"),
                             ("The Way of Kings, Part 2", "The Way of Kings, Part 1"),
                             ("Rhythm of War (1 of 2)", "Rhythm of War (2 of 2)"), ("Dune", "Dune: The Complete Saga"),
                             ("The Witch", "The Viscount and the Witch"), ("Before She Knew Him", "Before She Knew Him and After"),
                             ("Dune", "Dune: The Complete Collection"), ("Dune", "Dune: Complete"), ("Dune", "Dune: Part One"),
                             ("It", "It: Chapter Two"), ("Mistborn", "Mistborn: Secret History"),
                             ("Red Rising", "Red Rising: Sons of Ares"),
                             ("Dungeon Crawler Carl Volume II", "Dungeon Crawler Carl Volume I"),
                             ("The Way of Kings Part One", "The Way of Kings Part Two"),
                             ("Mother of Learning, Book 2", "Mother of Learning, Book 3"),
                             ("Mother of Learning: Book 2", "Mother of Learning: Book 3"), ("Part II", "Part III"),
                             ("Red Rising", "Red Rising [Graphic Audio]"), ("The Hobbit", "The Hobbit (Dramatised)"),
                             ("Dune (Abridged)", "Dune")]:
            self.assertFalse(same(ours, theirs), (ours, theirs))
        # a subtitle only MAM has passes only when it is the candidate's own series
        self.assertTrue(same("Leviathan Wakes", "Leviathan Wakes: The Expanse, Book 1", ["The Expanse"]))
        self.assertFalse(same("Leviathan Wakes", "Leviathan Wakes: The Expanse, Book 1"))
        self.assertFalse(same("Mistborn", "Mistborn: Secret History", ["Mistborn"]))
        for ours, theirs in [("Shogun", "Shōgun"), ("Ender’s Game", "Ender's Game"),
                             ("Across the Fence: Expanded Edition: The Secret War in Vietnam (Unabridged)",
                              "Across the Fence: Expanded Edition - The Secret War in Vietnam"),
                             ("Ender's Game", "Ender&#039;s Game"), ("Pride & Prejudice", "Pride &amp; Prejudice"),
                             ("The Sideways Life of Denny Voss", "The Sideways Life of Denny Voss: A Novel"),
                             ("The Memory Keeper of Kyiv (Unabridged)", "The Memory Keeper of Kyiv"),
                             ("Insurgent: Tier One, Book 10", "Insurgent: Tier One Book 10")]:
            self.assertTrue(same(ours, theirs), (ours, theirs))

    def test_huge_or_multi_line_tags_stay_bounded(self):
        import time
        t0 = time.monotonic()
        self.assertTrue(myx_classes.sameMamTitle("Real" + " " * 400000, "Real"))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(myx_classes._oneLine("A\nB\x1b[31m C"), "A B [31m C")


if __name__ == "__main__":
    unittest.main()
