"""MAMBook.getAudibleBooks with a canned, offline Audible: pinned ASINs, hinted candidates, duration ranking."""
import ast
import contextlib
import io
import tempfile
import unittest
from unittest.mock import patch

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


class ParsedNameSearchTest(unittest.TestCase):
    """Release-name parsing feeds the Audible search when the id3 tags are junk (roadmap item 3)."""

    def test_junk_id3_searches_with_parsed_title_and_author_separately(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0RELAXED1", "Relaxed", ["Megan Fate Marshman"], 244)])
            mb = mambook("Megan Fate Marshman - Relaxed.m4b", id3_book("", [], 244 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0RELAXED1")
        self.assertIn("Parsed release name 'Megan Fate Marshman - Relaxed.m4b': title:'Relaxed' authors:['Megan Fate Marshman']", out)
        self.assertEqual(out.count("Using parsed release name"), 1)
        self.assertIn("\ttitle:Relaxed\n", out)
        self.assertIn('\tauthors:"Megan Fate Marshman"', out)
        self.assertIn("\tkeywords:relaxed megan fate marshman", out)

    def test_good_id3_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0GOOD0001", "The Coworker", ["Freida McFadden"], 492)])
            mb = mambook("Some Odd Folder Name", id3_book("The Coworker", ["Freida McFadden"], 492 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0GOOD0001")
        self.assertNotIn("Using parsed release name", out)
        self.assertNotIn("Parsed release name", out)          # good-tag books keep upstream's output verbatim
        self.assertIn("\ttitle:The Coworker\n", out)

    def test_legacy_flag_restores_upstream_search_keys(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/flags/parse_names": 0})
            client = FakeAudible(search=[])
            mb = mambook("Megan Fate Marshman - Relaxed.m4b", id3_book("", [], 244 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        self.assertNotIn("Parsed release name", out)
        self.assertIn("Found alternative title", out)           # upstream's getAltTitle path

    def test_asin_in_brackets_is_used_for_the_lookup(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B09HY7C3BH": product("B09HY7C3BH", "Becoming Your Own Banker", ["R. Nelson Nash"], 236)})
            mb = mambook("Becoming Your Own Banker [B09HY7C3BH]", id3_book("", [], 236 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B09HY7C3BH")
        self.assertEqual(client.calls[0][0].rsplit("/", 1)[1], "B09HY7C3BH")

    def test_ambiguous_name_retries_swapped(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)

            class SwapAware(FakeAudible):
                def get(self, url, params=None):
                    self.calls.append((url, dict(params or {})))
                    if params and params.get("author") == '"Sally Hepworth"':
                        return type("R", (), {"raise_for_status": lambda s: None,
                                              "json": lambda s: {"products": [product("B0MABEL000", "Mad Mabel", ["Sally Hepworth"], 600)]}})()
                    return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"products": [], "total_results": 0}})()
            client = SwapAware()
            mb = mambook("Mad Mabel - Sally Hepworth.m4b", id3_book("", [], 600 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0MABEL000")
        self.assertIn("retrying with the release name read the other way round", out)
        self.assertLessEqual(len(client.calls), 3)

    def test_title_only_fallback_when_author_constraint_finds_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)

            class TitleOnly(FakeAudible):
                def get(self, url, params=None):
                    self.calls.append((url, dict(params or {})))
                    if params and not params.get("author"):
                        return type("R", (), {"raise_for_status": lambda s: None,
                                              "json": lambda s: {"products": [product("B0PENNAME1", "The Forgotten Soldier", ["Guy Sajer"], 900)]}})()
                    return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"products": [], "total_results": 0}})()
            client = TitleOnly()
            mb = mambook("Guy Sajer - The Forgotten Soldier.m4b", id3_book("", [], 900 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0PENNAME1")
        self.assertIn("retrying the Audible search with the title only", out)
        self.assertLessEqual(len(client.calls), 3)

    def test_hint_title_takes_precedence_over_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0HINTED01", "Hinted Title", ["Hinted Author"], 100)])
            mb = mambook("Wrong Author - Wrong Title.m4b", id3_book("", [], 100 * 60), hint={"title": "Hinted Title", "authors": ["Hinted Author"]})
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0HINTED01")
        self.assertNotIn("Using parsed release name", out)


class ParsedNameSafetyTest(unittest.TestCase):
    """A parse can add matches but must never lose one upstream found, nor accept a same-author wrong title."""

    def test_wrong_parse_falls_back_to_upstream_search_and_still_matches(self):
        # "Always Looking Up" looks like a name; the subtitle becomes the title; upstream matched on the whole string
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B002V0KM3W", "Always Looking Up", ["Michael J Fox"], 272)])
            mb = mambook("Always Looking Up - The Adventures of an Incurable Optimist.mp3", id3_book("", [], 272 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B002V0KM3W")
        self.assertIn("No match; retrying with the file's own tags as before", out)
        self.assertIn("Found alternative title", out)

    def test_parsed_title_rejects_a_different_book_by_the_same_author(self):
        # the disc-folder case: release "Brad Thor - Takedown", Audible offers another Brad Thor title
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0WRONG001", "The First Commandment", ["Brad Thor"], 330)])
            mb = mambook("Brad Thor - Takedown Unabridged - Complete", id3_book("AudioTrack 02", ["unknown artist"], 60 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        self.assertIn("This book doesn't have a matching title or author", out)

    def test_good_tags_take_exactly_one_upstream_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[])
            mb = mambook("Odd Folder", id3_book("The Coworker", ["Freida McFadden"], 492 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("No match; retrying", out)

    def test_filename_asin_does_not_override_usable_id3_title(self):
        # folder has another book by the same author; usable tags must search by title, not pin that ASIN.
        # requireTitle is only set when the parsed title replaced a junk tag, so an ASIN-only apply
        # would accept the wrong product through the author gate and file the book under the wrong title.
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(
                by_asin={"B0WRONG001": product("B0WRONG001", "The First Commandment", ["Brad Thor"], 330)},
                search=[product("B0RIGHT000", "Takedown", ["Brad Thor"], 400)],
            )
            mb = mambook("Brad Thor - Takedown [B0WRONG001]", id3_book("Takedown", ["Brad Thor"], 400 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0RIGHT000")
        self.assertEqual(best.title, "Takedown")
        self.assertTrue(all(u.endswith("/catalog/products") for u, _ in client.calls), client.calls)
        self.assertNotIn("Using parsed release name", out)
        self.assertEqual(len(client.calls), 1)

    def test_filename_asin_is_ignored_when_id3_title_is_usable_even_if_artist_is_junk(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(
                by_asin={"B0WRONG001": product("B0WRONG001", "The First Commandment", ["Brad Thor"], 330)},
                search=[product("B0RIGHT000", "Takedown", ["Brad Thor"], 400)],
            )
            mb = mambook("Brad Thor - Takedown [B0WRONG001]", id3_book("Takedown", ["unknown artist"], 400 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0RIGHT000")
        self.assertEqual(best.title, "Takedown")
        self.assertTrue(all(u.endswith("/catalog/products") for u, _ in client.calls), client.calls)

    def test_parsed_authors_with_usable_title_reject_a_different_book_by_that_author(self):
        # usable id3 title, junk artist, folder names the wrong author. requireTitle used to be
        # False when only authors were replaced, so the author-only gate filed Patterson's
        # Along Came a Spider as The Guest (token_sort 71 >= matchrate 60).
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0WRONG001", "Along Came a Spider", ["James Patterson"], 400)])
            mb = mambook("James Patterson - The Guest", id3_book("The Guest", ["unknown artist"], 400 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        self.assertIn("This book doesn't have a matching title or author", out)
        self.assertIn("Using parsed release name for authors", out)

    def test_parsed_authors_with_usable_title_still_accept_the_matching_title(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0RIGHT000", "Takedown", ["Brad Thor"], 400)])
            mb = mambook("Brad Thor - Takedown", id3_book("Takedown", ["unknown artist"], 400 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0RIGHT000")
        self.assertEqual(best.title, "Takedown")
        self.assertIn("Using parsed release name for authors", out)


def _mam_book(title, authors, snatched=True):
    b = myx_classes.Book(title=title)
    b.authors = [myx_classes.Contributor(a) for a in authors]
    b.snatched = snatched
    return b


class ParsedAuthorsMamRankingTest(unittest.TestCase):
    """MAM ranking must use the id3 title (not file.m4b) when only authors were parsed."""

    def _run(self, name, id3, hits):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/flags/verbose": 1})
            mb = mambook(name, id3)
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch("myx_mam.getMAMBook", return_value=hits):
                best = mb.getMAMBooks(cfg, mb.files[0])
        return best, out.getvalue()

    def test_correct_mam_hit_is_accepted_against_the_id3_title_not_the_basename(self):
        # mambook files are named file.m4b; ranking against that basename would reject The Guest
        best, out = self._run("James Patterson - The Guest",
                              id3_book("The Guest", ["unknown artist"], 400 * 60),
                              [_mam_book("The Guest", ["James Patterson"])])
        self.assertIsNotNone(best)
        self.assertEqual(best.title, "The Guest")
        self.assertIn("Using parsed release name for authors", out)

    def test_different_mam_title_by_the_parsed_author_is_rejected(self):
        best, out = self._run("James Patterson - The Guest",
                              id3_book("The Guest", ["unknown artist"], 400 * 60),
                              [_mam_book("Along Came a Spider", ["James Patterson"])])
        self.assertIsNone(best)
        self.assertIn("This book doesn't have a matching title or author", out)


class NarratorInArtistTagTest(unittest.TestCase):
    def secrets(self):
        return [product("B0BYRNE000", "The Secret", ["Rhonda Byrne"], 264),
                product("B0REACHER0", "The Secret", ["Lee Child", "Andrew Child"], 554)]

    def test_release_authors_are_tried_when_the_tag_names_someone_else(self):
        # the artist tag holds the narrator; the release name has the real authors
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)

            class ByAuthor(FakeAudible):
                def get(self, url, params=None):
                    self.calls.append((url, dict(params or {})))
                    hits = [p for p in self.search if params.get("author", "") and any(a["name"].split()[-1] in params["author"] for a in p["authors"])]
                    return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"products": hits, "total_results": len(hits)}})()
            client = ByAuthor(search=self.secrets())
            mb = mambook("Lee Child, Andrew Child - Jack Reacher 28 - The Secret, Scott Brick narrator m4b",
                         id3_book("", ["Scott Brick"], 555 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0REACHER0")
        self.assertIn("retrying with the authors from the release name: ['Lee Child', 'Andrew Child']", out)

    def test_title_only_attempt_accepts_the_runtime_match_over_a_higher_score(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)

            class TitleOnlyReturns(FakeAudible):
                def get(self, url, params=None):
                    self.calls.append((url, dict(params or {})))
                    hits = self.search if not params.get("author") else []
                    return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"products": hits, "total_results": len(hits)}})()
            client = TitleOnlyReturns(search=self.secrets())
            mb = mambook("Unknown Person - The Secret", id3_book("", ["Scott Brick"], 555 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0REACHER0")
        self.assertTrue("Duration match preferred" in out or "Title-verified result accepted on duration" in out)
        self.assertNotIn("Found alternative title", out)      # upstream's alt-title step only runs for the upstream attempt


class ReviewRoundTwoTest(unittest.TestCase):
    def test_unabridged_folder_with_good_tags_is_one_upstream_attempt_without_a_fake_asin(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0STORMS00", "Season of Storms", ["Andrzej Sapkowski"], 705)])
            mb = mambook("Andrzej Sapkowski - Season of Storms (Unabridged)", id3_book("Season of Storms", ["Andrzej Sapkowski"], 705 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0STORMS00")
        self.assertEqual(len(client.calls), 1)
        self.assertTrue(client.calls[0][0].endswith("/catalog/products"))
        self.assertNotIn("UNABRIDGED", out)
        self.assertNotIn("Parsed release name", out)

    def test_tag_title_equal_to_file_stem_with_good_authors_keeps_upstream_gate(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0RELAXED1", "Relaxed", ["Megan Fate Marshman"], 244)])
            mb = mambook("Relaxed.m4b", id3_book("Relaxed", ["Megan Fate Marshman"], 244 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0RELAXED1")
        self.assertNotIn("Using parsed release name", out)
        self.assertEqual(len(client.calls), 1)

    def test_interactive_mode_runs_only_the_upstream_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/flags/interactive": 1})
            client = FakeAudible(search=[product("B0BYRNE000", "The Secret", ["Rhonda Byrne"], 264)])
            mb = mambook("Lee Child - The Secret", id3_book("", [], 555 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(len(client.calls), 1)
        self.assertNotIn("Using parsed release name", out)
        self.assertIn("Found alternative title", out)

    def test_hint_candidates_with_empty_tag_title_still_derive_the_alt_title(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(by_asin={"B0AAAAAAA1": product("B0AAAAAAA1", "Takedown", ["Brad Thor"], 330)})
            mb = mambook("Brad Thor - Takedown", id3_book("", [], 330 * 60), hint={"candidates": ["B0AAAAAAA1"]})
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0AAAAAAA1")
        self.assertIn("Found alternative title", out)

    def test_audible_match_count_reflects_the_attempt_that_matched(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)

            class TitleOnly(FakeAudible):
                def get(self, url, params=None):
                    self.calls.append((url, dict(params or {})))
                    hits = self.search if not params.get("author") else []
                    return type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"products": hits, "total_results": len(hits)}})()
            client = TitleOnly(search=[product("B0PENNAME1", "The Forgotten Soldier", ["Guy Sajer"], 900),
                                       product("B0OTHER000", "The Forgotten Soldiers", ["Someone Else"], 100)])
            mb = mambook("Guy Sajer - The Forgotten Soldier.m4b", id3_book("", [], 900 * 60))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0PENNAME1")
        self.assertEqual(len(mb.audibleMatches), 2)


class MalformedAsinTagTest(unittest.TestCase):
    def test_malformed_asin_tag_is_ignored_not_sent_to_audible(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0GOOD0001", "The Coworker", ["Freida McFadden"], 492)])
            mb = mambook("x", id3_book("The Coworker", ["Freida McFadden"], 492 * 60, asin="../../etc/passwd"))
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, "B0GOOD0001")
        self.assertIn("Ignoring malformed ASIN tag", out)
        self.assertTrue(all(u.endswith("/catalog/products") for u, _ in client.calls))


class DuplicateAttemptTest(unittest.TestCase):
    def test_identical_queries_are_not_repeated(self):
        # good tag title, empty artist tag, no ASIN: title-only and upstream would be the same query
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[])
            mb = mambook("The Coworker - Freida McFadden.m4b", id3_book("The Coworker", [], 492 * 60))
            best, out = run(mb, client, cfg)
        self.assertIsNone(best)
        queries = [(c[1].get("title"), c[1].get("author")) for c in client.calls]
        self.assertEqual(len(queries), len(set(queries)), queries)
