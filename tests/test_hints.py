import tempfile
import unittest

import myx_hints
from tests.support import write_json


class HintFileTest(unittest.TestCase):
    def test_valid_file_is_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "hints.json", {
                "Some Book.m4b": {"asin": "b0abc12345", "duration_min": "541", "title": " Relaxed ", "authors": "Megan Fate Marshman"},
                "/data/downloads/complete/audio/Other": {"candidates": ["B0ABC12345", "b0abc12345", "B0DEF67890"], "ignored": 1},
            })
            hints = myx_hints.loadHintsFile(path)
        self.assertEqual(hints["Some Book.m4b"], {"asin": "B0ABC12345", "duration_min": 541.0, "title": "Relaxed",
                                                  "authors": ["Megan Fate Marshman"]})
        self.assertEqual(hints["/data/downloads/complete/audio/Other"], {"candidates": ["B0ABC12345", "B0DEF67890"]})

    def test_invalid_files_fail_loudly(self):
        bad = [
            ["not", "an", "object"],
            {"k": {"asin": "TOO-SHORT"}},
            {"k": {"candidates": "B0ABC12345"}},
            {"k": {"candidates": ["B0ABC1234%d" % i for i in range(10)] + ["B0ZZZZZZZ1"]}},
            {"k": {"duration_min": -5}},
            {"k": {"duration_min": "soon"}},
            {"k": {"authors": [1, 2]}},
            {"k": "just a string"},
        ]
        for data in bad:
            with self.subTest(data=str(data)[:60]), tempfile.TemporaryDirectory() as td:
                path = write_json(td, "hints.json", data)
                with self.assertRaises(myx_hints.HintsError):
                    myx_hints.loadHintsFile(path)

    def test_lookup_by_name_then_release_folder_then_file_path(self):
        hints = {"By Name": {"asin": "B0000000N1"},
                 "/src/Release Folder": {"asin": "B0000000F1"},
                 "/src/Loose File.m4b": {"asin": "B0000000L1"}}
        self.assertEqual(myx_hints.findHint(hints, "By Name", ["/src/By Name/x.m4b"])["asin"], "B0000000N1")
        self.assertEqual(myx_hints.findHint(hints, "Release Folder", ["/src/Release Folder/cd1/01.mp3"], root="/src")["asin"], "B0000000F1")
        hints["/src"] = {"asin": "B0000000R1"}      # the source path itself is never a release: must not match
        self.assertIsNone(myx_hints.findHint(hints, "Other", ["/src/Other/cd1/01.mp3"], root="/src"))
        self.assertEqual(myx_hints.findHint(hints, "Loose File.m4b", ["/src/Loose File.m4b"])["asin"], "B0000000L1")
        self.assertIsNone(myx_hints.findHint(hints, "Unknown", ["/src/Unknown/a.m4b"], root="/src"))
        self.assertIsNone(myx_hints.findHint({}, "By Name", []))

    def test_isAsin(self):
        self.assertTrue(myx_hints.isAsin("B09HY7C3BH"))
        self.assertTrue(myx_hints.isAsin(" b09hy7c3bh "))
        self.assertTrue(myx_hints.isAsin("1549146572"))
        for bad in ("", None, "B09HY7C3B", "B09HY7C3BHX", "B09HY7-3BH"):
            self.assertFalse(myx_hints.isAsin(bad), bad)


class DurationTest(unittest.TestCase):
    def test_delta_and_tolerance(self):
        self.assertEqual(myx_hints.durationDelta(540, 541.5), 1.5)
        self.assertIsNone(myx_hints.durationDelta(0, 541))
        self.assertIsNone(myx_hints.durationDelta(540, None))
        self.assertIsNone(myx_hints.durationDelta("abc", 5))
        self.assertTrue(myx_hints.withinTolerance(2.0))
        self.assertFalse(myx_hints.withinTolerance(2.01))
        self.assertFalse(myx_hints.withinTolerance(None))

    def test_pickBest_reproduces_upstream_without_duration_evidence(self):
        scored = [("a", 70, None), ("b", 85, None), ("c", 85, None), ("d", 40, None)]
        self.assertEqual(myx_hints.pickBest(scored, 60)[0], "b")        # first highest score wins
        self.assertIsNone(myx_hints.pickBest([("d", 40, None)], 60))    # below threshold: no match

    def test_pickBest_prefers_runtime_within_tolerance_over_higher_score(self):
        scored = [("wrong edition", 90, 45.0), ("right edition", 72, 0.5)]
        self.assertEqual(myx_hints.pickBest(scored, 60)[0], "right edition")

    def test_pickBest_never_rescues_a_below_threshold_search_result(self):
        scored = [("close runtime, poor title", 30, 0.0), ("good title, far runtime", 75, 100.0)]
        self.assertEqual(myx_hints.pickBest(scored, 60)[0], "good title, far runtime")

    def test_pickBest_for_hinted_candidates_accepts_on_runtime_alone(self):
        scored = [("cand1", 30, 90.0), ("cand2", 25, 1.0)]
        self.assertEqual(myx_hints.pickBest(scored, 60, requireRate=False)[0], "cand2")
        self.assertIsNone(myx_hints.pickBest([("cand1", 30, 90.0)], 60, requireRate=False))

    def test_pickBest_ties_within_tolerance_break_on_score_then_delta(self):
        scored = [("x", 80, 1.9), ("y", 80, 0.2), ("z", 79, 0.0)]
        self.assertEqual(myx_hints.pickBest(scored, 60)[0], "y")


if __name__ == "__main__":
    unittest.main()


class HardeningTest(unittest.TestCase):
    def test_bounds_and_finiteness(self):
        for raw in ({"duration_min": float("inf")}, {"duration_min": True}, {"title": "x" * 301},
                    {"authors": ["a"] * 11}, {"authors": ["y" * 301]}):
            with self.subTest(raw=str(raw)[:40]), self.assertRaises(myx_hints.HintsError):
                myx_hints.normalizeHint(raw)
        self.assertEqual(myx_hints.normalizeHint({"duration_min": 1e6})["duration_min"], 1e6)

    def test_oversized_file_is_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            path = td + "/big.json"
            with open(path, "wb") as fh:
                fh.truncate(myx_hints.MAX_FILE_BYTES + 1)
            with self.assertRaises(myx_hints.HintsError):
                myx_hints.loadHintsFile(path)

    def test_path_keys_are_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "h.json", {"/src/Release/": {"asin": "B0000000F1"}})
            hints = myx_hints.loadHintsFile(path)
        self.assertEqual(myx_hints.findHint(hints, "Release", ["/src/Release/01.mp3"], root="/src")["asin"], "B0000000F1")

    def test_colliding_path_keys_are_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            path = write_json(td, "h.json", {"/src/Rel": {"asin": "B0000000F1"}, "/src/Rel/": {"asin": "B0000000F2"}})
            with self.assertRaises(myx_hints.HintsError):
                myx_hints.loadHintsFile(path)

    def test_ancestor_walk_needs_a_comparable_root(self):
        hints = {"/data": {"asin": "B0000000R1"}, "/data/dl/Rel": {"asin": "B0000000F1"}}
        self.assertIsNone(myx_hints.findHint(hints, "Rel", ["/data/dl/Rel/01.mp3"]))                 # no root: no walk
        self.assertIsNone(myx_hints.findHint(hints, "Rel", ["/data/dl/Rel/01.mp3"], root=""))
        self.assertIsNone(myx_hints.findHint(hints, "Rel", ["/data/dl/Rel/01.mp3"], root="dl"))      # relative vs absolute
        self.assertIsNone(myx_hints.findHint(hints, "Rel", ["/data/dl/Rel/01.mp3"], root="/other"))  # file not under root
        self.assertEqual(myx_hints.findHint(hints, "Rel", ["/data/dl/Rel/01.mp3"], root="/data/dl")["asin"], "B0000000F1")
        self.assertIsNone(myx_hints.findHint({"/data": {"asin": "B0000000R1"}}, "Rel", ["/data/dl/Rel/01.mp3"], root="/data/dl"))

    def test_hint_keyed_by_any_file_of_a_release(self):
        hints = {"/src/Rel/02.mp3": {"asin": "B0000000F2"}}
        self.assertEqual(myx_hints.findHint(hints, "Rel", ["/src/Rel/01.mp3", "/src/Rel/02.mp3"], root="/src")["asin"], "B0000000F2")
