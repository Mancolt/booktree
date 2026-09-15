import contextlib
import io
import tempfile
import unittest

import myx_mam
from tests.support import FakeConfig


class _Resp:
    def __init__(self, status=200, text="", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload


class FakeSession:
    """Stands in for requests.Session: records requests, serves canned search answers."""
    instances = []

    def __init__(self):
        self.headers = {}
        self.cookies = None
        self.requests = []
        FakeSession.instances.append(self)

    def get(self, url, timeout=None):
        self.requests.append(("get", url))
        return _Resp(200, "{}", {})

    def post(self, url, json=None, timeout=None):
        self.requests.append(("post", url))
        return FakeSession.answer


class MamThrottleTest(unittest.TestCase):
    def setUp(self):
        self.saved = (myx_mam.requests, myx_mam._sleep, myx_mam._now, myx_mam.pickle)
        self.clock = [1000.0]
        self.sleeps = []
        myx_mam.requests = type("R", (), {"Session": FakeSession})
        myx_mam._sleep = lambda s: (self.sleeps.append(s), self.clock.__setitem__(0, self.clock[0] + s))
        myx_mam._now = lambda: self.clock[0]
        myx_mam.pickle = type("P", (), {"dump": staticmethod(lambda *a, **k: None), "load": staticmethod(lambda *a, **k: None)})
        myx_mam.resetRunCounters()
        FakeSession.instances = []
        FakeSession.answer = _Resp(200, '{"data": []}', {"data": [], "total": 0, "found": 0, "perpage": 50, "start": 0})

    def tearDown(self):
        myx_mam.requests, myx_mam._sleep, myx_mam._now, myx_mam.pickle = self.saved
        myx_mam.resetRunCounters()

    def search(self, cfg, title):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            r = myx_mam.searchMAM(cfg, title, "", '"m4b"')
        return r, out.getvalue()

    def test_requests_are_spaced_at_least_six_seconds_apart(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            self.search(cfg, "One.m4b")
            self.search(cfg, "Two.m4b")
        # cookie test GET once per run, then one POST per search: 3 MAM requests, 2 waits of >= 6 s between them
        reqs = [q for s in FakeSession.instances for q in s.requests]
        self.assertEqual([m for m, _ in reqs], ["get", "post", "post"])
        self.assertEqual(len(self.sleeps), 2)
        self.assertTrue(all(s >= 5.99 for s in self.sleeps), self.sleeps)

    def test_invalid_knobs_fall_back_to_defaults_with_one_warning(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/mam/min_interval_seconds": "abc", "Config/mam/max_queries_per_run": "60.0"})
            _, out1 = self.search(cfg, "A.m4b")
            _, out2 = self.search(cfg, "B.m4b")
        self.assertIn("Ignoring invalid Config/mam/min_interval_seconds='abc', using 6.0", out1)
        self.assertNotIn("Ignoring invalid", out2)          # warned once
        self.assertEqual(len(self.sleeps), 2)
        for bad in (-50, float("inf"), "1e12"):
            self.assertEqual(myx_mam._knob(FakeConfig(td, **{"Config/mam/min_interval_seconds": bad}), "min_interval_seconds", 6.0, 0.0, 3600.0, float), 6.0)
        self.assertEqual(myx_mam._knob(FakeConfig(td, **{"Config/mam/max_queries_per_run": "60.0"}), "max_queries_per_run", 60, 0, 100000, int), 60)

    def test_malformed_cache_entry_is_searched_again(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            self.search(cfg, "X.m4b")
            d = os.path.join(td, "__cache__", "mam")
            key = os.listdir(d)[0]
            open(os.path.join(d, key), "w").write("[1, 2, 3]")
            r, out = self.search(cfg, "X.m4b")
        self.assertIn("Ignoring malformed MAM cache entry", out)
        self.assertEqual(sum(len([q for q in s.requests if q[0] == "post"]) for s in FakeSession.instances), 2)

    def test_empty_answer_is_cached_and_not_requeried(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            FakeSession.answer = _Resp(200, '{"error":"Nothing returned, out of 0"}', None)
            r1, _ = self.search(cfg, "Nothing.m4b")
            posts_after_first = sum(len([q for q in s.requests if q[0] == "post"]) for s in FakeSession.instances)
            r2, _ = self.search(cfg, "Nothing.m4b")
            posts_after_second = sum(len([q for q in s.requests if q[0] == "post"]) for s in FakeSession.instances)
        self.assertIsNone(r1)
        self.assertEqual(r2, [])                          # served from the (empty) cache entry
        self.assertEqual(posts_after_first, posts_after_second, "second identical search must not hit MAM")

    def test_unsnatched_answer_is_cached_but_filtered(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            FakeSession.answer = _Resp(200, "x", {"data": [{"id": 1, "title": "T", "my_snatched": 0, "author_info": "{}"}], "total": 1})
            self.search(cfg, "T.m4b")
            with contextlib.redirect_stdout(io.StringIO()):
                books = myx_mam.getMAMBook(cfg, titleFilename="T.m4b", extension='"m4b"')
        self.assertEqual(books, [])
        self.assertEqual(sum(len([q for q in s.requests if q[0] == "post"]) for s in FakeSession.instances), 1)

    def test_errors_are_never_cached(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td)
            FakeSession.answer = _Resp(500, "boom", None)
            r, out = self.search(cfg, "Err.m4b")
            import os
            self.assertIsNone(r)
            self.assertIn("error searching MAM", out)
            self.assertEqual(os.listdir(os.path.join(td, "__cache__", "mam")), [])

    def test_query_budget_per_run(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/mam/max_queries_per_run": 2})
            self.search(cfg, "A.m4b"); self.search(cfg, "B.m4b")
            r, out = self.search(cfg, "C.m4b")
        self.assertIsNone(r)
        self.assertIn("MAM query budget for this run exhausted (2 searches)", out)
        self.assertEqual(sum(len([q for q in s.requests if q[0] == "post"]) for s in FakeSession.instances), 2)


if __name__ == "__main__":
    unittest.main()
