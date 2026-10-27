"""Pinned-ASIN vetoes: runtime limit in minutes and/or percent, narrator disagreement, and the --hints warning for
entries that matched no release. Every option is off by default."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import myx_args
import myx_classes
import myx_hints
import myx_names
from tests.support import FakeAudible, FakeConfig, product, write_json
from tests.test_matching import id3_book, mambook, run

PIN = "B0CC3NZ34S"


def narrated(book, *names):
    book.narrators = [myx_classes.Contributor(n) for n in names]
    return book


def pinned_product(runtime_min, *narrators):
    return product(PIN, "The Coworker", ["Freida McFadden"], runtime_min, narrators=[{"name": n} for n in narrators])


class RuntimeRefusedTest(unittest.TestCase):
    def test_both_limits_off_never_refuses(self):
        self.assertFalse(myx_hints.runtimeRefused(500, 600))

    def test_minutes_only_is_the_old_behaviour(self):
        self.assertTrue(myx_hints.runtimeRefused(31, 600, max_delta_min=30))
        self.assertFalse(myx_hints.runtimeRefused(30, 600, max_delta_min=30))

    def test_percent_only(self):
        self.assertTrue(myx_hints.runtimeRefused(17.5, 212, max_pct=8))       # 8.3 %
        self.assertFalse(myx_hints.runtimeRefused(16, 212, max_pct=8))        # 7.5 %

    def test_both_limits_must_be_exceeded(self):
        # short book: 18 min is 8.5 % of 212 but under 20 min -> kept
        self.assertFalse(myx_hints.runtimeRefused(18, 212, max_delta_min=20, max_pct=8))
        # long book: 60 min is over 20 min but only 2 % of 2882 -> kept
        self.assertFalse(myx_hints.runtimeRefused(60, 2882, max_delta_min=20, max_pct=8))
        # abridged edition: 230 min off a 600 min file -> refused
        self.assertTrue(myx_hints.runtimeRefused(230, 600, max_delta_min=20, max_pct=8))

    def test_unknown_expected_runtime_never_refuses_on_percent(self):
        self.assertFalse(myx_hints.runtimeRefused(50, 0, max_pct=8))
        self.assertFalse(myx_hints.runtimeRefused(None, 600, max_delta_min=1, max_pct=1))


class ValidatePinLimitsTest(unittest.TestCase):
    def test_defaults_and_numbers_are_fine(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(myx_hints.validatePinLimits(FakeConfig(td)))
            self.assertIsNone(myx_hints.validatePinLimits(FakeConfig(td, **{
                "Config/pin_max_runtime_delta_min": 20, "Config/pin_max_runtime_delta_pct": 8.5, "Config/pin_narrator_veto": 1})))

    def test_typos_stop_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            for key, bad in (("pin_max_runtime_delta_pct", "8%"), ("pin_max_runtime_delta_pct", -1),
                             ("pin_max_runtime_delta_min", True), ("pin_max_runtime_delta_min", float("inf")),
                             ("pin_narrator_veto", "yes"), ("pin_narrator_veto", 2)):
                problem = myx_hints.validatePinLimits(FakeConfig(td, **{f"Config/{key}": bad}))
                self.assertIsNotNone(problem, (key, bad))
                self.assertIn(key, problem)


class PercentLimitTest(unittest.TestCase):
    def test_percent_limit_refuses_and_falls_back(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_max_runtime_delta_min": 20, "Config/pin_max_runtime_delta_pct": 8})
            client = FakeAudible(by_asin={PIN: pinned_product(492)}, search=[product("B0RIGHT000", "Short Book", ["Someone"], 300)])
            mb = mambook("x", id3_book("Short Book", ["Someone"], 300 * 60))
            mb.pinnedAsin = PIN
            best, out = run(mb, client, cfg)
        self.assertIn(f"Refusing pinned ASIN {PIN}: runtime 492min differs from expected 300min by 192min (64%) (limits 20min and 8%)", out)
        self.assertIn(f"Pinned ASIN {PIN} refused on runtime, falling back to search", out)
        self.assertEqual(best.asin, "B0RIGHT000")

    def test_small_difference_on_a_short_book_is_only_a_warning(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_max_runtime_delta_min": 20, "Config/pin_max_runtime_delta_pct": 8})
            client = FakeAudible(by_asin={PIN: pinned_product(230)})
            mb = mambook("x", id3_book("", [], 212 * 60))
            mb.pinnedAsin = PIN
            best, out = run(mb, client, cfg)
        self.assertEqual(best.asin, PIN)
        self.assertIn("Warning: pinned ASIN runtime 230min differs from expected 212min by 18min", out)

    def test_minutes_only_message_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_max_runtime_delta_min": 30})
            client = FakeAudible(by_asin={PIN: pinned_product(492)})
            mb = mambook("x", id3_book("", [], 300 * 60))
            mb.pinnedAsin = PIN
            _, out = run(mb, client, cfg)
        self.assertIn(f"Refusing pinned ASIN {PIN}: runtime 492min differs from expected 300min by 192min (limit 30min)", out)


class NarratorNamesTest(unittest.TestCase):
    def test_junk_and_labels_are_no_narrator(self):
        self.assertEqual(myx_names.narratorNames(["Unknown", "Penguin Audio", "HarperAudio", "Blackstone Publishing",
                                                  "Full Cast", "", "  ", "Macmillan Audiobooks"]), [])

    def test_credit_prefix_is_stripped_and_real_names_kept(self):
        self.assertEqual(myx_names.narratorNames(["Read By: Danielle Ferland", "narrated by Scott Brick", "Claudio Santos"]),
                         ["Danielle Ferland", "Scott Brick", "Claudio Santos"])

    def test_contributor_objects_are_accepted(self):
        self.assertEqual(myx_names.narratorNames([myx_classes.Contributor("Ray Porter")]), ["Ray Porter"])


class NarratorsDisagreeTest(unittest.TestCase):
    def test_different_recordings(self):
        self.assertTrue(myx_names.narratorsDisagree(["George Guidall"], ["Simon Vance"]))
        self.assertTrue(myx_names.narratorsDisagree(["Read By: Danielle Ferland"], ["Armand Schultz"]))

    def test_same_narrator_in_any_spelling(self):
        self.assertFalse(myx_names.narratorsDisagree(["Ganser", "L.J."], ["L. J. Ganser"]))
        self.assertFalse(myx_names.narratorsDisagree(["Megan Marshman"], ["Megan Fate Marshman"]))
        self.assertFalse(myx_names.narratorsDisagree(["José Pérez Jr."], ["Jose Perez"]))
        self.assertFalse(myx_names.narratorsDisagree(["Armand Schultz", "Brad Thorafterword"], ["Armand Schultz"]))
        self.assertFalse(myx_names.narratorsDisagree(["Brad Thorafterword"], ["Brad Thor"]))

    def test_any_product_narrator_is_enough(self):
        self.assertFalse(myx_names.narratorsDisagree(["Will Damron"], ["Will Damron", "Megan Tusing"]))

    def test_missing_or_junk_on_either_side_is_no_evidence(self):
        self.assertFalse(myx_names.narratorsDisagree([], ["Simon Vance"]))
        self.assertFalse(myx_names.narratorsDisagree(["Unknown"], ["Molly Millwood"]))
        self.assertFalse(myx_names.narratorsDisagree(["Penguin Audio"], ["Deborah McBride"]))
        self.assertFalse(myx_names.narratorsDisagree(["George Guidall"], []))
        self.assertFalse(myx_names.narratorsDisagree(["George Guidall"], ["Full Cast"]))

    def test_uncomparable_product_names_are_no_evidence(self):
        # initials only, or a script that leaves no Latin letters: nothing to compare, so never a refusal
        self.assertFalse(myx_names.narratorsDisagree(["George Guidall"], ["J."]))
        self.assertFalse(myx_names.narratorsDisagree(["George Guidall"], ["村上春樹"]))
        self.assertFalse(myx_names.narratorsDisagree(["George Guidall"], ["J.", "村上春樹"]))
        # one comparable name is enough to decide
        self.assertTrue(myx_names.narratorsDisagree(["George Guidall"], ["J.", "Simon Vance"]))

    def test_author_in_the_narrator_tag_is_no_evidence(self):
        self.assertFalse(myx_names.narratorsDisagree(["Freida McFadden"], ["Alyson Krawchuk"], authors=["Freida McFadden"]))
        self.assertFalse(myx_names.narratorsDisagree(["McFadden, Freida"], ["Alyson Krawchuk"],
                                                     authors=[myx_classes.Contributor("Freida McFadden")]))
        # an author who narrates their own book still agrees
        self.assertFalse(myx_names.narratorsDisagree(["Brian Page"], ["Brian Page"], authors=["Brian Page"]))
        # the author plus a different narrator in the tag: the narrator still decides
        self.assertTrue(myx_names.narratorsDisagree(["Freida McFadden", "George Guidall"], ["Alyson Krawchuk"],
                                                    authors=["Freida McFadden"]))


class NarratorVetoTest(unittest.TestCase):
    def _run(self, tagged, product_narrators, veto=1, book=None, search=None):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_narrator_veto": veto})
            client = FakeAudible(by_asin={PIN: pinned_product(492, *product_narrators)}, search=search or [])
            mb = mambook("x", narrated(id3_book("The Coworker", ["Freida McFadden"], 492 * 60), *tagged))
            mb.pinnedAsin = PIN
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                best = mb.getAudibleBooks(client, mb.ffprobeBook if book is None else book, cfg)
        return best, out.getvalue()

    def test_off_by_default(self):
        best, out = self._run(["George Guidall"], ["Simon Vance"], veto=0)
        self.assertEqual(best.asin, PIN)
        self.assertNotIn("Refusing", out)

    def test_disagreement_refuses_and_falls_back(self):
        other = product("B0OTHER000", "The Coworker", ["Freida McFadden"], 492, narrators=[{"name": "George Guidall"}])
        best, out = self._run(["George Guidall"], ["Simon Vance"], search=[other])
        self.assertIn(f"Refusing pinned ASIN {PIN}: tagged narrator(s) George Guidall not among the product's Simon Vance", out)
        self.assertIn(f"Pinned ASIN {PIN} refused on narrator, falling back to search", out)
        self.assertEqual(best.asin, "B0OTHER000")

    def test_agreement_and_junk_tags_are_accepted(self):
        for tagged in (["Alyson Krawchuk"], ["Krawchuk, Alyson"], ["Unknown"], ["Penguin Audio"], []):
            best, out = self._run(tagged, ["Alyson Krawchuk"])
            self.assertEqual(best.asin, PIN, tagged)
            self.assertIn(f"Pinned ASIN {PIN} accepted", out)

    def test_author_tagged_as_narrator_is_accepted(self):
        # pinned_product's author is Freida McFadden; the file credits her in the narrator (composer) tag
        best, out = self._run(["Freida McFadden"], ["Alyson Krawchuk"])
        self.assertEqual(best.asin, PIN)
        self.assertNotIn("Refusing", out)

    def test_uses_the_files_tags_not_the_metadata_book(self):
        # mam-audible passes the MAM row (or None) as `book`; its narrators are not the file's
        mam_row = narrated(id3_book("The Coworker", ["Freida McFadden"], 0), "Someone Else")
        best, _ = self._run(["Alyson Krawchuk"], ["Alyson Krawchuk"], book=mam_row)
        self.assertEqual(best.asin, PIN)
        best, _ = self._run(["Alyson Krawchuk"], ["Alyson Krawchuk"], book=None)
        self.assertEqual(best.asin, PIN)

    def test_runtime_and_narrator_reasons_are_both_reported(self):
        # two products for one ASIN (e.g. two marketplaces): one refused on runtime, the other on narrator
        prods = [pinned_product(300, "George Guidall"), pinned_product(492, "Simon Vance")]
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/pin_narrator_veto": 1, "Config/pin_max_runtime_delta_min": 30})
            mb = mambook("x", narrated(id3_book("", [], 492 * 60), "George Guidall"))
            mb.pinnedAsin = PIN
            out = io.StringIO()
            with patch("myx_audible.getAudibleBook", return_value=prods), contextlib.redirect_stdout(out):
                best = mb.acceptPinnedAsin(FakeAudible(), cfg, None, "english")
        self.assertIsNone(best)
        self.assertEqual(mb.audibleMatches, [])
        self.assertIn(f"Pinned ASIN {PIN} refused on runtime and narrator, falling back to search", out.getvalue())


class UnusedHintsWarningTest(unittest.TestCase):
    def setUp(self):
        myx_hints._cache.clear()
        myx_hints._appliedRefresh.clear()
        myx_hints._usedHints.clear()

    def _warn(self, cfg):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            myx_hints.warnUnusedRefresh(cfg)
        return out.getvalue()

    def test_cli_hints_entry_that_matched_nothing_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {"Found Release": {"asin": PIN}, "/src/Gone": {"asin": "B0GONE0000"},
                                                 "Title Only": {"title": "Something"}})
            cfg = FakeConfig(td, **{"Config/hints_file": path, "Config/hints_from_cli": True})
            with contextlib.redirect_stdout(io.StringIO()):
                hints = myx_hints.getHints(cfg)
            self.assertEqual(myx_hints.findHint(hints, "Found Release")["asin"], PIN)
            out = self._warn(cfg)
        self.assertIn("Warning: --hints entry '/src/Gone' (ASIN B0GONE0000) matched no release in this run", out)
        self.assertNotIn("Found Release", out)
        self.assertNotIn("Title Only", out)              # only pins are checked

    def test_hint_found_through_a_file_path_counts_as_used(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {"/src/Some Folder": {"asin": PIN}})
            cfg = FakeConfig(td, **{"Config/hints_file": path, "Config/hints_from_cli": True})
            with contextlib.redirect_stdout(io.StringIO()):
                hints = myx_hints.getHints(cfg)
            self.assertIsNotNone(myx_hints.findHint(hints, "Some Folder", ["/src/Some Folder/cd1/01.mp3"], root="/src"))
            self.assertEqual(self._warn(cfg), "")

    def test_configured_hints_file_is_not_checked(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {"/src/Gone": {"asin": "B0GONE0000"}})
            cfg = FakeConfig(td, **{"Config/hints_file": path})
            with contextlib.redirect_stdout(io.StringIO()):
                myx_hints.getHints(cfg)
            self.assertEqual(self._warn(cfg), "")

    def test_cli_pin_is_not_reported_twice(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {"Gone": {"asin": PIN}})
            cfg = FakeConfig(td, **{"Config/hints_file": path, "Config/hints_from_cli": True, "Config/pins": [f"Gone={PIN}"]})
            with contextlib.redirect_stdout(io.StringIO()):
                myx_hints.getHints(cfg)
            out = self._warn(cfg)
        self.assertEqual(out.count("'Gone'"), 1)
        self.assertIn("--pin 'Gone' matched no release", out)

    def test_hints_option_marks_the_file_as_given_on_the_command_line(self):
        with tempfile.TemporaryDirectory() as td:
            conf = os.path.join(td, "config.json")
            with open(conf, "w", encoding="utf-8") as fh:
                json.dump({"Config": {"metadata": "audible", "flags": {}}}, fh)
            params = SimpleNamespace(config_file=conf, dry_run=None, verbose=None, no_cache=None, no_opf=None,
                                     multibook=None, ebooks=None, fixid3=None, add_narrators=None, hints=td + "/h.json",
                                     legacy_names=None, refresh=None, json_log=None)
            cfg = myx_args.Config(params)
            self.assertEqual(cfg.get("Config/hints_file"), td + "/h.json")
            self.assertTrue(cfg.get("Config/hints_from_cli"))
            params.hints = None
            self.assertFalse(myx_args.Config(params).get("Config/hints_from_cli"))


if __name__ == "__main__":
    unittest.main()
