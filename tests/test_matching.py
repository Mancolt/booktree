"""MAMBook.getAudibleBooks with a canned, offline Audible: pinned ASINs, hinted candidates, duration ranking."""
import ast
import contextlib
import io
import tempfile
import unittest

import myx_classes
import myx_hints
from tests.support import FakeAudible, FakeConfig, product, write_json


def id3_book(title, authors, duration_s, asin=""):
    b = myx_classes.Book(asin=asin, title=title, duration=duration_s)
    b.authors = [myx_classes.Contributor(a) for a in authors]
    return b


def mambook(name, id3, path="/data/downloads/complete/audio", hint=None):
    mb = myx_classes.MAMBook(name)
    bf = myx_classes.BookFile(f"{name}/file.m4b", f"{path}/{name}/file.m4b", path, "/data/Audiobooks")
    bf.ffprobeBook = id3
    mb.files.append(bf)
    mb.ffprobeBook = id3
    mb.hint = hint
    if hint and hint.get("asin"):
        mb.pinnedAsin = hint["asin"]
    return mb


def run(mb, client, cfg):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        result = mb.getAudibleBooks(client, mb.ffprobeBook, cfg)
    return result, out.getvalue()


class PinnedAsinTest(unittest.TestCase):
    def test_pinned_asin_wins_even_when_id3_title_and_author_disagree(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            mb = mambook("junk", id3_book("AudioTrack 01", ["unknown artist"], 492 * 60))
            mb.pinnedAsin = "B0CC3NZ34S"                 # as buildTreeFromLog sets it from fix.csv's id3-asin
            best, out = run(mb, client, cfg)
        self.assertIsNotNone(best)
        self.assertEqual(best.asin, "B0CC3NZ34S")
        self.assertEqual(best.title, "The Coworker")
        self.assertIn("Pinned ASIN B0CC3NZ34S accepted: The Coworker by Freida McFadden", out)
        self.assertEqual(len(client.calls), 1)          # one lookup, no search
        self.assertEqual(len(mb.audibleMatches), 1)

    def test_pinned_asin_with_wrong_runtime_is_still_accepted_with_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            mb = mambook("x", id3_book("", [], 300 * 60))
            mb.pinnedAsin = "B0CC3NZ34S"
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0CC3NZ34S")
        self.assertIn("Warning: pinned ASIN runtime 492min differs from expected 300min by 192min", out)

    def test_skeleton_answer_for_pinned_asin_falls_back_to_normal_search(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={}, search=[product("B0GVLGC2X8", "The Dinner Party", ["Freida McFadden"], 400)])
            mb = mambook("The Dinner Party - Freida McFadden.m4b", id3_book("The Dinner Party", ["Freida McFadden"], 400 * 60))
            mb.pinnedAsin = "B0GVLGC2X8"
            best, out = run(mb, client, cfg)
        self.assertIn("Ignoring Audible result B0GVLGC2X8 without a title (incomplete catalog entry)", out)
        self.assertIn("returned no usable Audible product, falling back to search", out)
        self.assertEqual(best.asin, "B0GVLGC2X8")     # found by the ordinary search instead


class SkeletonResultTest(unittest.TestCase):
    def test_skeleton_search_result_is_skipped_not_compared_against_empty_strings(self):
        # upstream issue #25: "Checking if  or  matches my book ..." then a rejection
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[{"asin": "B0GVLGC2X8", "asset_details": [], "is_vvab": False, "language": "english",
                                          "content_type": "Product"}])
            mb = mambook("The Dinner Party", id3_book("The Dinner Party", ["Freida McFadden"], 400 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        self.assertIn("Ignoring Audible result B0GVLGC2X8 without a title", out)
        self.assertNotIn("Checking if  or  matches", out)


class DurationRankingTest(unittest.TestCase):
    def editions(self):
        return [product("B0LONG0000", "Unbroken", ["Laura Hillenbrand"], 852),      # unabridged, 14h12m
                product("B0SHORT000", "Unbroken", ["Laura Hillenbrand"], 305)]      # abridged

    def test_runtime_picks_the_edition_when_titles_tie(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            mb = mambook("Unbroken", id3_book("Unbroken", ["Laura Hillenbrand"], 305 * 60 + 40))
            best, out = run(mb, FakeAudible(search=self.editions()), cfg)
        self.assertEqual(best.asin, "B0SHORT000")
        self.assertIn("Duration: 305min vs expected 305min (difference 0min)", out)

    def test_without_runtime_evidence_the_highest_score_wins_as_upstream(self):
        # files report no duration: no "Duration:" evidence lines, no duration preference, plain best score
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            mb = mambook("Unbroken", id3_book("Unbroken", ["Laura Hillenbrand"], 0))
            best, out = run(mb, FakeAudible(search=self.editions()), cfg)
        self.assertIsNotNone(best)
        self.assertNotIn("Duration match preferred", out)
        self.assertNotIn("vs expected", out)
        rates = {}
        for chunk in out.split("\tMatch Rate: ")[1:]:
            rate = ast.literal_eval(chunk.split("\n", 1)[0])["token_sort"]
            asin = "B0LONG0000" if "Duration:852min" in chunk else "B0SHORT000"
            rates[asin] = rate
        self.assertEqual(best.asin, max(rates, key=rates.get))

    def test_runtime_does_not_rescue_a_result_below_the_match_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            mb = mambook("Something Else Entirely", id3_book("Something Else Entirely", ["Nobody"], 305 * 60))
            best, out = run(mb, FakeAudible(search=self.editions()), cfg)
        self.assertIsNone(best)

    def test_hint_duration_overrides_file_duration(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            mb = mambook("Unbroken", id3_book("Unbroken", ["Laura Hillenbrand"], 305 * 60), hint={"duration_min": 852})
            best, _ = run(mb, FakeAudible(search=self.editions()), cfg)
        self.assertEqual(best.asin, "B0LONG0000")


class HintedCandidatesTest(unittest.TestCase):
    def test_candidates_are_fetched_by_asin_and_ranked_by_runtime(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={
                "B0AAAAAAA1": product("B0AAAAAAA1", "Relaxed", ["Megan Fate Marshman"], 244),
                "B0BBBBBBB2": product("B0BBBBBBB2", "Relaxed Workbook", ["Megan Fate Marshman"], 95)})
            mb = mambook("Megan Fate Marshman - Relaxed.m4b", id3_book("", [], 244 * 60 + 30),
                         hint={"candidates": ["B0BBBBBBB2", "B0AAAAAAA1"]})
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0AAAAAAA1")
        self.assertEqual([u.rsplit("/", 1)[1] for u, _ in client.calls], ["B0BBBBBBB2", "B0AAAAAAA1"])
        self.assertIn("Fetching 2 hinted candidate ASIN(s)", out)

    def test_candidates_with_no_runtime_match_and_poor_scores_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B0AAAAAAA1": product("B0AAAAAAA1", "Unrelated Title", ["Someone"], 900)})
            mb = mambook("junk.m4b", id3_book("junk", [], 244 * 60), hint={"candidates": ["B0AAAAAAA1"]})
            best, _ = run(mb, client, cfg)
        self.assertIsNone(best)

    def test_hint_title_and_authors_drive_the_search_but_not_the_logged_id3(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0AAAAAAA1", "Relaxed", ["Megan Fate Marshman"], 244)])
            id3 = id3_book("AudioTrack 01", ["unknown artist"], 244 * 60)
            mb = mambook("junk", id3, hint={"title": "Relaxed", "authors": ["Megan Fate Marshman"]})
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0AAAAAAA1")
        self.assertIn("title:Relaxed", out)
        self.assertEqual(id3.title, "AudioTrack 01")           # what the CSV logs as id3-title is untouched
        self.assertEqual(id3.authors[0].name, "unknown artist")


class ApplyHintsTest(unittest.TestCase):
    def test_hints_file_is_applied_by_release_name(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {"Some Release": {"asin": "B0CC3NZ34S", "duration_min": 492}})
            cfg = FakeConfig(td, **{"Config/hints_file": path})
            myx_hints._cache.clear()
            mb = mambook("Some Release", id3_book("", [], 0))
            with contextlib.redirect_stdout(io.StringIO()):
                hint = mb.applyHints(cfg)
        self.assertEqual(hint["asin"], "B0CC3NZ34S")
        self.assertEqual(mb.pinnedAsin, "B0CC3NZ34S")
        self.assertEqual(mb.getExpectedDuration(), 492.0)

    def test_missing_hints_file_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/hints_file": td + "/nope.json"})
            myx_hints._cache.clear()
            with self.assertRaises(myx_hints.HintsError):
                myx_hints.getHints(cfg)


if __name__ == "__main__":
    unittest.main()


class ReviewFindingsTest(unittest.TestCase):
    """Regressions for the step-2 review findings."""

    def test_failed_pin_falls_back_to_a_title_search_not_the_same_asin(self):
        # log mode: the id3 asin IS the pin; the fallback must not hit the per-ASIN URL again
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={}, search=[product("B0GVLGC2X8", "The Dinner Party", ["Freida McFadden"], 400)])
            mb = mambook("The Dinner Party", id3_book("The Dinner Party", ["Freida McFadden"], 400 * 60, asin="B0GVLGC2X8"))
            mb.pinnedAsin = "B0GVLGC2X8"
            best, out = run(mb, client, cfg)
        self.assertEqual([u.rsplit("/", 1)[1] for u, _ in client.calls], ["B0GVLGC2X8", "products"])
        self.assertEqual(client.calls[1][1]["asin"], "")
        self.assertEqual(best.asin, "B0GVLGC2X8")

    def test_pin_is_used_even_when_no_metadata_book_is_passed(self):
        # mam-audible mode passes bestMAMMatch, which is None when MAM found nothing
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            mb = mambook("x", id3_book("", [], 492 * 60))
            mb.pinnedAsin = "B0CC3NZ34S"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                best = mb.getAudibleBooks(client, None, cfg)
        self.assertEqual(best.asin, "B0CC3NZ34S")
        self.assertIn("Found 1 Audible match(es)", out.getvalue())

    def test_pin_runtime_ceiling_refuses_and_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_max_runtime_delta_min": 30})
            client = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)},
                                 search=[product("B0RIGHT000", "Short Book", ["Someone"], 300)])
            mb = mambook("x", id3_book("Short Book", ["Someone"], 300 * 60))
            mb.pinnedAsin = "B0CC3NZ34S"
            best, out = run(mb, client, cfg)
        self.assertIn("Refusing pinned ASIN B0CC3NZ34S: runtime 492min differs from expected 300min by 192min (limit 30min)", out)
        self.assertIn("refused on runtime, falling back to search", out)
        self.assertEqual(best.asin, "B0RIGHT000")

    def test_refused_pin_without_a_fallback_logs_no_audible_matches(self):
        # mam-audible with no MAM hit passes book=None: nothing to search with, and the refused product must not
        # be reported as a match candidate (audibleMatchCount)
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_max_runtime_delta_min": 30})
            client = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            mb = mambook("x", id3_book("", [], 300 * 60))
            mb.pinnedAsin = "B0CC3NZ34S"
            with contextlib.redirect_stdout(io.StringIO()):
                best = mb.getAudibleBooks(client, None, cfg)
        self.assertIsNone(best)
        self.assertEqual(mb.audibleMatches, [])

    def test_regex_metacharacters_in_author_or_series_names_cannot_break_the_alt_title(self):
        import myx_utilities
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            for author, series in (("Brad [", "Series ("), ("(a+)+$", "x*"), ("a.c", "\\d+")):
                book = id3_book("", [author], 0)
                book.series = [myx_classes.Series(series, "")]
                with contextlib.redirect_stdout(io.StringIO()):
                    myx_utilities.getAltTitle("Some Folder - Title", book, cfg)   # must not raise or hang

    def test_hinted_title_survives_fixid3(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/flags/fixid3": 1})
            client = FakeAudible(search=[product("B0AAAAAAA1", "Relaxed", ["Megan Fate Marshman"], 244)])
            mb = mambook("junk", id3_book("AudioTrack 01", ["unknown artist"], 244 * 60),
                         hint={"title": "Relaxed", "authors": ["Megan Fate Marshman"]})
            best, out = run(mb, client, cfg)
        self.assertIn("title:Relaxed", out)
        self.assertNotIn("Found alternative title", out)
        self.assertEqual(best.asin, "B0AAAAAAA1")
