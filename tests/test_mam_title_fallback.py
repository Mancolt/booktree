"""Config/mam/title_fallback: a third MAM search by title and author, only when both file-name searches found no
snatched torrent. Seen 2026-10-07 with *Before She Knew Him* (Peter Swanson): both file-name searches returned the
release's own torrent, not yet marked my_snatched, so getMAMBook kept nothing."""
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
# the file-name search booktree has always sent for this release; its sha256 names the live cache entry
CLASSIC = '("Peter Swanson") ("Peter Swanson \\- Before She Knew Him.m4b") "m4b" @dummy mamDummy'
CLASSIC_PREFIX = "9b70af34efbbacc6"     # first 16 hex digits of the live entry's file name
WIDENED = ' ("Peter Swanson \\- Before She Knew Him.m4b") "m4b" @dummy mamDummy'
FALLBACK = '("Peter Swanson") before she knew him @dummy mamDummy'


def row(id_, title, authors=("Peter Swanson",), filetype="m4b", snatched=0):
    names = ", ".join(f'"{n}": "{a}"' for n, a in enumerate(authors))
    return {"id": id_, "title": title, "author_info": "{" + names + "}", "narrator_info": "", "series_info": "",
            "filetype": filetype, "lang_code": "ENG", "my_snatched": snatched}


class _Resp:
    def __init__(self, payload):
        self.status_code, self._payload = 200, payload
        self.text = '{"error":"Nothing returned, out of 0"}' if not payload["data"] else "x"

    def json(self):
        return self._payload


class FakeSession:
    """requests.Session stand-in: file-name searches (filenames in srchIn) and title searches get separate answers."""
    instances = []
    by_filename = []
    by_title = []

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
        rows = FakeSession.by_filename if "filenames" in json["tor"]["srchIn"] else FakeSession.by_title
        return _Resp({"data": list(rows), "total": len(rows), "found": len(rows), "perpage": 50, "start": 0})


class OfflineSession(FakeSession):
    def get(self, url, timeout=None):
        raise ConnectionError("offline")

    def post(self, url, json=None, timeout=None):
        raise ConnectionError("offline")


def posts():
    return [p for s in FakeSession.instances for p in s.posts]


def release(title="Before She Knew Him", authors=("Peter Swanson",), name=RELEASE, file=FILE):
    id3 = myx_classes.Book(title=title, duration=615 * 60)
    id3.authors = [myx_classes.Contributor(a) for a in authors]
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
        FakeSession.by_filename = [row(2, "Before She Knew Him")]                 # found, not snatched yet
        FakeSession.by_title = [row(1, "Before She Knew Him", filetype="mp3"),
                                row(2, "Before She Knew Him")]
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)

    def tearDown(self):
        myx_mam.requests, myx_mam._sleep, myx_mam._now = self.saved
        myx_mam.resetRunCounters()

    def cfg(self, on=True, **kw):
        if on is not None:
            kw["Config/mam/title_fallback"] = 1 if on is True else on
        return FakeConfig(self.td.name, **kw)

    def run_mam(self, mb, cfg):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            best = mb.getMAMBooks(cfg, mb.files[0])
        return best, out.getvalue()


class TriggerTest(Base):
    def test_not_called_when_a_file_name_search_found_a_snatched_torrent(self):
        hit = myx_classes.Book(title="Before She Knew Him")
        hit.authors = [myx_classes.Contributor("Peter Swanson")]
        hit.snatched = True
        with patch("myx_mam.getMAMBook", return_value=[hit]), patch("myx_mam.getMAMBookByTitle") as fb:
            best, out = self.run_mam(release(), self.cfg())
        fb.assert_not_called()
        self.assertIs(best, hit)
        self.assertNotIn("MAM title search", out)

    def test_not_called_when_the_widened_search_found_one(self):
        hit = myx_classes.Book(title="Before She Knew Him")
        hit.authors = [myx_classes.Contributor("Peter Swanson")]
        hit.snatched = True
        with patch("myx_mam.getMAMBook", side_effect=[[], [hit]]) as fn, patch("myx_mam.getMAMBookByTitle") as fb:
            best, _ = self.run_mam(release(), self.cfg())
        self.assertEqual(fn.call_count, 2)
        fb.assert_not_called()
        self.assertIs(best, hit)

    def test_called_once_after_both_file_name_searches_found_nothing(self):
        cand = myx_classes.Book(title="Before She Knew Him")
        cand.authors = [myx_classes.Contributor("Peter Swanson")]
        cfg = self.cfg()
        with patch("myx_mam.getMAMBook", return_value=[]) as fn, \
                patch("myx_mam.getMAMBookByTitle", return_value=[cand]) as fb:
            mb = release()
            best, out = self.run_mam(mb, cfg)
        self.assertEqual(fn.call_count, 2)
        fb.assert_called_once_with(cfg, "before she knew him", ["Peter Swanson"], "m4b", refresh=False)
        self.assertIs(best, cand)
        self.assertEqual(mb.matchAttempt, "mam-title")
        self.assertEqual(mb.mamMatches, [cand])
        self.assertIn("No snatched MAM match by file name, MAM title search: before she knew him | by: Peter Swanson", out)
        self.assertIn("Using the only MAM title match (not marked snatched yet): Before She Knew Him", out)
        self.assertEqual(mb.mamAttempt, "title")


class EndToEndTest(Base):
    def test_query_shape_cache_keys_throttle_and_budget(self):
        mb = release()
        best, out = self.run_mam(mb, self.cfg())
        sent = posts()
        self.assertEqual([p["tor"]["text"] for p in sent], [CLASSIC, WIDENED, FALLBACK])
        self.assertEqual([p["tor"]["srchIn"] for p in sent[:2]],
                         [{"title": "true", "author": "true", "fileTypes": "true", "filenames": "true"}] * 2)
        self.assertEqual(sent[2]["tor"]["srchIn"], {"title": "true", "author": "true"})
        self.assertEqual(sent[2]["tor"]["main_cat"], [13, 16])
        self.assertEqual(sent[2]["perpage"], 50)
        # the mp3 edition is dropped by the file-type gate, the m4b is the only candidate left
        self.assertEqual(best.title, "Before She Knew Him")
        self.assertFalse(best.snatched)
        # one cookie check + three searches, every one spaced >= 6 s, each search counted against the budget
        self.assertEqual(len(self.sleeps), 3)
        self.assertTrue(all(s >= 5.99 for s in self.sleeps), self.sleeps)
        self.assertEqual(myx_mam._mamQueriesThisRun, 3)
        # three distinct cache entries; the classic key is byte-identical to the one the live cache holds
        keys = sorted(os.listdir(os.path.join(self.td.name, "__cache__", "mam")))
        _, fkey = myx_mam.titleFallbackQuery("before she knew him", ["Peter Swanson"])
        self.assertTrue(myx_utilities.getHash(CLASSIC).startswith(CLASSIC_PREFIX))
        self.assertEqual(keys, sorted([myx_utilities.getHash(CLASSIC), myx_utilities.getHash(WIDENED), fkey]))
        self.assertNotEqual(fkey, myx_utilities.getHash(FALLBACK))   # the key also names the search fields
        self.assertIn(f"Checking cache: mam/{fkey}", out)
        # a second run is served from the cache: no MAM request at all
        FakeSession.instances = []
        myx_mam.resetRunCounters()
        best2, _ = self.run_mam(release(), self.cfg())
        self.assertEqual(posts(), [])
        self.assertEqual(best2.title, "Before She Knew Him")

    def test_flag_off_is_the_old_behaviour_exactly(self):
        outs = []
        for flag in (None, 0, "0"):
            FakeSession.instances = []
            myx_mam.resetRunCounters()
            with tempfile.TemporaryDirectory() as td:
                cfg = FakeConfig(td) if flag is None else FakeConfig(td, **{"Config/mam/title_fallback": flag})
                with patch("myx_mam.getMAMBookByTitle") as fb:
                    mb = release()
                    best, out = self.run_mam(mb, cfg)
                fb.assert_not_called()
                keys = sorted(os.listdir(os.path.join(td, "__cache__", "mam")))
            self.assertIsNone(best)
            self.assertEqual(mb.matchAttempt, "")
            self.assertEqual([p["tor"]["text"] for p in posts()], [CLASSIC, WIDENED])
            self.assertEqual(keys, sorted([myx_utilities.getHash(CLASSIC), myx_utilities.getHash(WIDENED)]))
            outs.append(out.replace(td, "<td>"))
        self.assertEqual(outs[0], outs[1])
        self.assertEqual(outs[0], outs[2])
        self.assertNotIn("MAM title search", outs[0])

    def test_invalid_flag_value_counts_as_off_with_a_warning(self):
        with patch("myx_mam.getMAMBookByTitle") as fb:
            _, out = self.run_mam(release(), self.cfg(on="yes"))
        fb.assert_not_called()
        self.assertIn("Ignoring invalid Config/mam/title_fallback='yes', using 0", out)

    def test_offline_or_unreachable_mam_does_not_crash_or_match(self):
        myx_mam.requests = type("R", (), {"Session": OfflineSession})
        mb = release()
        best, out = self.run_mam(mb, self.cfg())
        self.assertIsNone(best)
        self.assertEqual(mb.matchAttempt, "")
        self.assertIn("error searching MAM", out)
        self.assertEqual(os.listdir(os.path.join(self.td.name, "__cache__", "mam")), [])

    def test_an_error_inside_the_fallback_is_contained(self):
        # seen in the offline replay: a session without a cookie jar raised AttributeError before searchMAM's own
        # error handling, and the book lost its Audible step
        mb = release()
        with patch("myx_mam.getMAMBookByTitle", side_effect=AttributeError("'NoneType' object has no attribute 'set'")):
            best, out = self.run_mam(mb, self.cfg())
        self.assertIsNone(best)
        self.assertIn("error in the MAM title search AttributeError", out)

    def test_no_fallback_search_when_the_ranking_would_not_run(self):
        # getMAMBooks only ranks results with verbose on (upstream); a search whose result is dropped is not spent
        with patch("myx_mam.getMAMBookByTitle") as fb:
            best, _ = self.run_mam(release(), self.cfg(**{"Config/flags/verbose": 0}))
        fb.assert_not_called()
        self.assertIsNone(best)
        self.assertEqual(len(posts()), 2)

    def test_no_fallback_search_after_mam_refused_the_cookie(self):
        class Refused(FakeSession):
            def get(self, url, timeout=None):
                return type("R", (), {"status_code": 403, "text": "refused"})()
        myx_mam.requests = type("R", (), {"Session": Refused})
        with tempfile.TemporaryDirectory() as td:          # empty cache: the file-name searches need MAM
            with contextlib.redirect_stdout(io.StringIO()):
                best = release().getMAMBooks(FakeConfig(td, **{"Config/mam/title_fallback": 1}), release().files[0])
        self.assertIsNone(best)
        self.assertFalse(myx_mam.titleFallbackEnabled(self.cfg()))
        myx_mam.resetRunCounters()
        self.assertTrue(myx_mam.titleFallbackEnabled(self.cfg()))

    def test_long_author_names_are_cut_in_the_query(self):
        mb = release(authors=("Peter Swanson" + "x" * 5000,))
        with patch("myx_mam.getMAMBookByTitle", return_value=[]) as fb:
            self.run_mam(mb, self.cfg())
        self.assertEqual(len(fb.call_args[0][2][0]), myx_classes.MAM_FALLBACK_MAX_AUTHOR)

    def test_exhausted_budget_skips_the_fallback_search(self):
        mb = release()
        best, out = self.run_mam(mb, self.cfg(**{"Config/mam/max_queries_per_run": 2}))
        self.assertIsNone(best)
        self.assertEqual(len(posts()), 2)
        self.assertIn("MAM query budget for this run exhausted (2 searches)", out)


class GateTest(Base):
    def best(self, title_rows, mb=None, **cfg):
        FakeSession.by_title = title_rows
        mb = mb or release()
        best, out = self.run_mam(mb, self.cfg(**cfg))
        return best, out, mb

    def test_two_candidates_are_ambiguous(self):
        best, out, _ = self.best([row(1, "Before She Knew Him"), row(2, "Before She Knew Him")])
        self.assertIsNone(best)
        self.assertIn("2 MAM title matches: ambiguous, not using any", out)

    def test_a_snatched_candidate_does_not_break_the_tie(self):
        # the file-name searches found no snatched torrent of this release, so a snatched title match is usually an
        # earlier volume or another edition
        best, _, _ = self.best([row(1, "Before She Knew Him"), row(2, "Before She Knew Him", snatched=1)])
        self.assertIsNone(best)

    def test_the_only_candidate_is_used_snatched_or_not(self):
        best, out, mb = self.best([row(2, "Before She Knew Him", snatched=1)])
        self.assertTrue(best.snatched)
        self.assertEqual(mb.mamMatches, [best])
        self.assertIn("Using the only MAM title match: Before She Knew Him", out)

    def test_other_file_type_other_author_and_longer_title_are_rejected(self):
        best, out, _ = self.best([row(1, "Before She Knew Him", filetype="mp3"),
                                  row(2, "Before She Knew Him", authors=("Someone Else",)),
                                  row(3, "Before She Knew Him and After", snatched=1)])
        self.assertIsNone(best)
        self.assertIn("No MAM title match passed the title, author and file type check (2 result(s))", out)

    def test_partial_title_containment_is_not_enough(self):
        # "The Witch" vs "The Viscount and the Witch": partial_ratio is 100, the fallback gate wants the same title
        mb = release(title="The Witch", authors=("Freida McFadden",), name="The Witch (MP3)", file="01.mp3")
        best, _, _ = self.best([row(1, "The Viscount and the Witch", authors=("Freida McFadden",), filetype="mp3")], mb=mb)
        self.assertIsNone(best)

    def test_subtitle_and_unabridged_noise_do_not_matter(self):
        mb = release(title="Across the Fence: Expanded Edition: The Secret War in Vietnam (Unabridged)",
                     authors=("John Stryker Meyer",), name="John Stryker Meyer - Across the Fence Expanded Edition.m4b",
                     file="John Stryker Meyer - Across the Fence Expanded Edition.m4b")
        best, _, _ = self.best([row(7, "Across the Fence: Expanded Edition - The Secret War in Vietnam",
                                    authors=("John Stryker Meyer",))], mb=mb)
        self.assertEqual(best.title, "Across the Fence: Expanded Edition - The Secret War in Vietnam")
        self.assertEqual(posts()[2]["tor"]["text"], '("John Stryker Meyer") across the fence @dummy mamDummy')

    def test_parsed_title_and_authors_are_used_when_the_tags_are_junk(self):
        mb = release(title="AudioTrack 01", authors=("unknown artist",))
        best, _, _ = self.best([row(1, "Before She Knew Him")], mb=mb)
        self.assertEqual(posts()[2]["tor"]["text"], FALLBACK)
        self.assertEqual(best.title, "Before She Knew Him")

    def test_no_usable_title_or_author_means_no_extra_search(self):
        mb = release(title="AudioTrack 01", authors=("unknown artist",), name="cd1", file="01.m4b")
        best, out, _ = self.best([row(1, "AudioTrack 01")], mb=mb, **{"Config/flags/parse_names": 0})
        self.assertIsNone(best)
        self.assertEqual(len(posts()), 2)
        self.assertIn("No usable title and author for the MAM title search, skipping it", out)

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
        for ours, theirs in [("Shogun", "Sh\u014dgun"), ("Ender\u2019s Game", "Ender's Game"),
                             ("Across the Fence: Expanded Edition: The Secret War in Vietnam (Unabridged)",
                              "Across the Fence: Expanded Edition - The Secret War in Vietnam"),
                             ("Ender's Game", "Ender&#039;s Game"), ("Pride & Prejudice", "Pride &amp; Prejudice"),
                             ("The Sideways Life of Denny Voss", "The Sideways Life of Denny Voss: A Novel"),
                             ("The Memory Keeper of Kyiv (Unabridged)", "The Memory Keeper of Kyiv"),
                             ("Insurgent: Tier One, Book 10", "Insurgent: Tier One Book 10")]:
            self.assertTrue(same(ours, theirs), (ours, theirs))

    def test_a_sibling_volume_is_not_matched_end_to_end(self):
        # the release's own torrent lists the author as "W. Wight" (fails the author gate); the sibling is left alone
        mb = release(title="Cradle: Unsouled", authors=("Will Wight",), name="Will Wight - Unsouled", file="Unsouled.m4b")
        best, _, _ = self.best([row(1, "Cradle: Unsouled", authors=("W. Wight",)), row(2, "Cradle: Soulsmith", authors=("Will Wight",))], mb=mb)
        self.assertIsNone(best)
        self.assertEqual(posts()[2]["tor"]["text"], '("Will Wight") cradle @dummy mamDummy')

    def test_a_title_that_equals_the_release_folder_is_searched(self):
        mb = release(name="Before She Knew Him", file="Before She Knew Him.m4b")
        best, _, _ = self.best([row(2, "Before She Knew Him")], mb=mb)
        self.assertEqual(best.title, "Before She Knew Him")

    def test_huge_or_multi_line_tags_stay_bounded(self):
        import time
        t0 = time.monotonic()
        self.assertTrue(myx_classes.sameMamTitle("Real" + " " * 400000 + "x" * 0, "Real"))
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertEqual(myx_classes._oneLine("A\nB\x1b[31m C"), "A B [31m C")

    def test_search_operators_in_the_title_are_lower_cased(self):
        self.assertEqual(myx_classes.mamFallbackTitle("MAYBE NEAR SENTENCE"), "maybe near sentence")
        self.assertEqual(myx_classes.mamFallbackTitle("Ender\u2019s Game"), "ender's game")

    def test_author_names_are_escaped_in_the_query(self):
        search, _ = myx_mam.titleFallbackQuery("Title", ['Jean-Paul "JP" Doe', "A|B"])
        self.assertEqual(search, '("Jean\\-Paul \\"JP\\" Doe"|"A\\|B") Title @dummy mamDummy')


if __name__ == "__main__":
    unittest.main()
