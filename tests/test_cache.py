import contextlib
import io
import json
import os
import tempfile
import time
import unittest

import myx_cache
import myx_utilities
from tests.support import FakeConfig

H = 3600


def write_entry(cfg, category, key, content, age_hours):
    path = os.path.join(myx_utilities.getCachePath(cfg), "__cache__", category, key)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(content, fh)
    t = time.time() - age_hours * H
    os.utime(path, (t, t))
    return path


class EmptyClassificationTest(unittest.TestCase):
    def test_audible(self):
        self.assertTrue(myx_cache.isEmptyResult("audible", {"products": [], "total_results": 0}))
        self.assertTrue(myx_cache.isEmptyResult("audible", {"product": {"asin": "B0X", "asset_details": [], "is_vvab": False}}))
        self.assertFalse(myx_cache.isEmptyResult("audible", {"product": {"asin": "B0X", "title": "T"}}))
        self.assertFalse(myx_cache.isEmptyResult("audible", {"products": [{"asin": "B0X", "title": "T"}]}))
        self.assertTrue(myx_cache.isEmptyResult("audible", "garbage"))

    def test_mam(self):
        self.assertTrue(myx_cache.isEmptyResult("mam", {"data": []}))
        self.assertTrue(myx_cache.isEmptyResult("mam", {"data": [{"id": 1, "my_snatched": 0}]}))
        self.assertFalse(myx_cache.isEmptyResult("mam", {"data": [{"id": 1, "my_snatched": 1}]}))


class FreshnessTest(unittest.TestCase):
    def test_ttls_by_category_and_emptiness(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            cases = [  # (category, content, age_h, expected_state)
                ("audible", {"products": [], "total_results": 0}, 1, "fresh"),
                ("audible", {"products": [], "total_results": 0}, 7, "expired"),
                ("audible", {"products": [{"title": "T"}]}, 24 * 29, "fresh"),
                ("audible", {"products": [{"title": "T"}]}, 24 * 31, "expired"),
                ("mam", {"data": []}, 23, "fresh"),
                ("mam", {"data": []}, 25, "expired"),
                ("mam", {"data": [{"my_snatched": 1}]}, 24 * 6, "fresh"),
                ("mam", {"data": [{"my_snatched": 1}]}, 24 * 8, "expired"),
            ]
            for i, (cat, content, age, expected) in enumerate(cases):
                path = write_entry(cfg, cat, f"k{i}", content, age)
                state, _, _, _ = myx_cache.entryState(path, cat, cfg)
                self.assertEqual(state, expected, (cat, content, age))
            # the processed-book marker never expires
            path = write_entry(cfg, "book", "b1", "MAMBook(...)", 24 * 400)
            self.assertEqual(myx_cache.entryState(path, "book", cfg)[0], "fresh")
            # corrupt entry older than the shortest TTL counts as expired (will be refetched)
            path = os.path.join(myx_utilities.getCachePath(cfg), "__cache__", "audible", "bad")
            open(path, "w").write("{not json")
            t = time.time() - 10 * H
            os.utime(path, (t, t))
            self.assertEqual(myx_cache.entryState(path, "audible", cfg)[0], "expired")

    def test_config_overrides(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/cache/audible_empty_hours": 48})
            path = write_entry(cfg, "audible", "k", {"products": []}, 30)
            self.assertEqual(myx_cache.entryState(path, "audible", cfg)[0], "fresh")

    def test_isCached_reports_expiry_and_honours_refresh(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            write_entry(cfg, "audible", "old", {"products": []}, 10)
            write_entry(cfg, "audible", "new", {"products": [{"title": "T"}]}, 1)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertFalse(myx_utilities.isCached("old", "audible", cfg))
                self.assertTrue(myx_utilities.isCached("new", "audible", cfg))
                self.assertFalse(myx_utilities.isCached("new", "audible", cfg, refresh=True))
                self.assertFalse(myx_utilities.isCached("missing", "audible", cfg))
            text = out.getvalue()
            self.assertIn("Checking cache: audible/old...", text)        # upstream phrase kept
            self.assertIn("Cache entry expired: audible/old (10h old, empty result, ttl 6h)", text)

    def test_cacheMe_is_atomic_and_keeps_format(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            with contextlib.redirect_stdout(io.StringIO()):
                myx_utilities.cacheMe("k", "audible", {"products": [], "total_results": 0}, cfg)
            d = os.path.join(td, "__cache__", "audible")
            self.assertEqual(os.listdir(d), ["k"])
            self.assertEqual(open(os.path.join(d, "k")).read(), '{"products": [], "total_results": 0}')


if __name__ == "__main__":
    unittest.main()


class HardeningTest(unittest.TestCase):
    def test_future_mtime_wrong_shape_and_oversize_entries_are_expired(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            p = write_entry(cfg, "mam", "future", {"data": [{"my_snatched": 1}]}, 1)
            t = time.time() + 3600 * 24 * 365 * 50
            os.utime(p, (t, t))
            self.assertEqual(myx_cache.entryState(p, "mam", cfg)[0], "expired")
            p = write_entry(cfg, "audible", "list", [1, 2, 3], 10)
            self.assertEqual(myx_cache.entryState(p, "audible", cfg)[0], "expired")
            p = os.path.join(myx_utilities.getCachePath(cfg), "__cache__", "audible", "huge")
            with open(p, "wb") as fh:
                fh.truncate(myx_cache.MAX_ENTRY_BYTES + 1)
            self.assertEqual(myx_cache.entryState(p, "audible", cfg)[0], "expired")

    def test_entries_within_the_shortest_ttl_are_fresh_without_parsing(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            p = os.path.join(myx_utilities.getCachePath(cfg), "__cache__", "audible", "young")
            open(p, "w").write("{not json")           # unreadable, but 1 h old: not parsed, reported fresh
            self.assertEqual(myx_cache.entryState(p, "audible", cfg)[0], "fresh")

    def test_malformed_fresh_audible_entry_is_refetched_not_fatal(self):
        import myx_audible
        from tests.support import FakeAudible, product
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            client = FakeAudible(search=[product("B0GOOD0001", "T", ["A"], 100)])
            with contextlib.redirect_stdout(io.StringIO()):
                myx_audible.getAudibleBook(client, cfg, title="T")
            d = os.path.join(td, "__cache__", "audible")
            key = os.listdir(d)[0]
            open(os.path.join(d, key), "w").write("[]")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                books = myx_audible.getAudibleBook(client, cfg, title="T")
        self.assertEqual([b["asin"] for b in books], ["B0GOOD0001"])
        self.assertIn("Ignoring malformed Audible cache entry", out.getvalue())
        self.assertEqual(len(client.calls), 2)

    def test_loadFromCache_returns_none_for_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            self.assertIsNone(myx_utilities.loadFromCache("missing", "audible", cfg))
