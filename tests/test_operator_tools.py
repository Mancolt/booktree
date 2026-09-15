"""Operator tools: --pin RELEASE=ASIN, Config/notify (ntfy + heartbeat), Config/abs (scan trigger) and
Config/dedupe_roots. No network: the requests module inside myx_notify / myx_library is replaced by a fake."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import requests

import myx_hints
import myx_library
import myx_notify
from tests.support import FakeConfig

ASIN = "B0C5Q9XJ1K"


class FakeHttp:
    """Stands in for `requests` inside the modules under test; records every call."""
    RequestException = requests.RequestException

    def __init__(self, status=200, fail=False):
        self.status, self.fail, self.calls = status, fail, []

    def _call(self, method, url, **kw):
        self.calls.append((method, url, kw))
        if self.fail:
            raise requests.ConnectionError("boom")
        return SimpleNamespace(status_code=self.status, text="")

    def post(self, url, **kw):
        return self._call("post", url, **kw)

    def get(self, url, **kw):
        return self._call("get", url, **kw)


class PinTest(unittest.TestCase):
    def setUp(self):
        myx_hints._cache.clear()
        myx_hints._appliedRefresh.clear()
        self.td = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.td.cleanup()

    def test_pin_becomes_an_authoritative_refreshing_hint(self):
        cfg = FakeConfig(self.td.name, **{"Config/pins": ["Some Author - Some Book=" + ASIN.lower(), "/data/x/=B000000001"]})
        with contextlib.redirect_stdout(io.StringIO()):
            hints = myx_hints.getHints(cfg)
        self.assertEqual(hints["Some Author - Some Book"], {"asin": ASIN, "refresh": True})
        self.assertEqual(hints["/data/x"], {"asin": "B000000001", "refresh": True})       # path key normalised
        # a release name may itself contain "=": the ASIN is what follows the last one
        cfg = FakeConfig(self.td.name, **{"Config/pins": ["Title = Subtitle=" + ASIN]})
        self.assertEqual(myx_hints.getHints(cfg)["Title = Subtitle"]["asin"], ASIN)

    def test_pin_overrides_the_hints_file_and_keeps_its_other_fields(self):
        path = os.path.join(self.td.name, "hints.json")
        with open(path, "w") as fh:
            json.dump({"Rel": {"asin": "B000000009", "duration_min": 100}}, fh)
        cfg = FakeConfig(self.td.name, **{"Config/hints_file": path, "Config/pins": ["Rel=" + ASIN]})
        with contextlib.redirect_stdout(io.StringIO()):
            hint = myx_hints.getHints(cfg)["Rel"]
        self.assertEqual(hint, {"asin": ASIN, "duration_min": 100.0, "refresh": True})

    def test_malformed_pins_are_usage_errors(self):
        for bad in (["NoEquals"], ["=B000000001"], ["Rel=notanasin"], ["Rel=" + ASIN, "Rel=B000000002"]):
            with self.assertRaises(myx_hints.HintsError, msg=bad):
                myx_hints.getHints(FakeConfig(self.td.name, **{"Config/pins": bad}))

    def test_unused_pin_is_reported_after_the_run(self):
        cfg = FakeConfig(self.td.name, **{"Config/pins": ["Missing Release=" + ASIN, "Found=" + ASIN]})
        hints = myx_hints.getHints(cfg)
        self.assertEqual(myx_hints.findHint(hints, "Found")["asin"], ASIN)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            myx_hints.warnUnusedRefresh(cfg)
        self.assertIn("--pin 'Missing Release' matched no release", out.getvalue())
        self.assertNotIn("'Found'", out.getvalue())


class NotifyTest(unittest.TestCase):
    URL = "https://ntfy.example/secret-topic"

    def setUp(self):
        self.saved = myx_notify.requests
        self.http = FakeHttp()
        myx_notify.requests = self.http
        self.td = tempfile.TemporaryDirectory()
        os.environ.pop("NTFY_TOKEN", None)

    def tearDown(self):
        myx_notify.requests = self.saved
        self.td.cleanup()
        os.environ.pop("NTFY_TOKEN", None)

    def cfg(self, **over):
        return FakeConfig(self.td.name, **{"Config/notify/ntfy_url": self.URL, **over})

    def summary(self, books=3, matched=2, hardlinked=4, names=("Some Book",), **extra):
        return {"books": books, "matched": matched, "unmatched": books - matched, "hardlinked_files": hardlinked,
                "unmatched_names": list(names), "csv": "/logs/booktree_log_1.csv", "dry_run": False, "error": None, **extra}

    def test_mode_matrix(self):
        self.assertTrue(myx_notify.shouldNotify("always", 0, 0))
        self.assertFalse(myx_notify.shouldNotify("unmatched", 0, 0))
        self.assertTrue(myx_notify.shouldNotify("unmatched", 1, 0))
        self.assertTrue(myx_notify.shouldNotify("unmatched", 0, 2))
        self.assertFalse(myx_notify.shouldNotify("failure", 5, 0))
        self.assertTrue(myx_notify.shouldNotify("failure", 0, 1))

    def test_message_names_the_unmatched_books_and_the_log(self):
        title, priority, body = myx_notify.compose(self.summary(), 0)
        self.assertEqual((title, priority), ("booktree: books need review", "default"))
        self.assertIn("2/3 matched, 1 unmatched", body)
        self.assertIn("  Some Book", body)
        self.assertIn("4 file(s) hardlinked", body)
        self.assertIn("log: /logs/booktree_log_1.csv", body)
        title, priority, body = myx_notify.compose(self.summary(matched=3, names=()), 0)
        self.assertEqual((title, priority), ("booktree run complete", "low"))
        self.assertIn("all 3 books matched", body)
        title, priority, body = myx_notify.compose(self.summary(books=0, matched=0, hardlinked=0, names=(), error="KeyError: x"), 1)
        self.assertEqual((title, priority), ("booktree run failed (exit 1)", "high"))
        self.assertIn("KeyError: x", body)
        many = self.summary(books=30, matched=0, names=[f"Book {i}" for i in range(30)])
        self.assertIn("... and 20 more", myx_notify.compose(many, 0)[2])
        self.assertIn("(dry run)", myx_notify.compose(self.summary(dry_run=True), 0)[2])

    def test_body_is_bounded_and_control_characters_are_stripped(self):
        evil = "Evil\r\nTitle: pwned\x00" + "x" * 500
        _, _, body = myx_notify.compose(self.summary(names=[evil]), 0)
        self.assertNotIn("\r", body.split("unmatched:")[1])
        self.assertNotIn("\x00", body)
        self.assertIn("Evil  Title: pwned", body)
        self.assertLess(max(len(l) for l in body.splitlines()), myx_notify.MAX_NAME + 4)
        huge = self.summary(books=10, matched=0, names=["n" * 119 for _ in range(10)], error="e" * 1000)
        _, _, body = myx_notify.compose(huge, 1)
        self.assertLessEqual(len(body.encode()), myx_notify.MAX_BODY)
        self.assertIn("...", body)
        self.assertTrue(body.endswith("log: /logs/booktree_log_1.csv"))

    def test_send_posts_with_token_and_hits_heartbeat_only_on_a_clean_run(self):
        os.environ["NTFY_TOKEN"] = "tk_secret"
        cfg = self.cfg(**{"Config/notify/on": "always", "Config/notify/heartbeat_url": "https://kuma.example/push/abc?status=up"})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(myx_notify.send(cfg, self.summary(), 0), (True, True))
        methods = [(m, u) for m, u, _ in self.http.calls]
        self.assertEqual(methods, [("post", self.URL), ("get", "https://kuma.example/push/abc?status=up")])
        headers = self.http.calls[0][2]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer tk_secret")
        self.assertEqual(headers["Title"], "booktree: books need review")
        self.assertIn(b"1 unmatched", self.http.calls[0][2]["data"])
        self.assertTrue(all(kw["allow_redirects"] is False for _, _, kw in self.http.calls))
        # neither the topic URL nor the token nor the push URL reaches stdout
        for secret in ("secret-topic", "tk_secret", "kuma.example"):
            self.assertNotIn(secret, out.getvalue())
        self.http.calls.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(myx_notify.send(cfg, self.summary(), 2), (True, False))
        self.assertEqual([m for m, _, _ in self.http.calls], ["post"])                # no heartbeat after a failure

    def test_default_mode_is_unmatched_and_nothing_is_sent_when_all_matched(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(myx_notify.send(self.cfg(), self.summary(matched=3, names=()), 0), (False, False))
        self.assertEqual(self.http.calls, [])
        self.assertEqual(myx_notify.send(FakeConfig(self.td.name), self.summary(), 2), (False, False))  # not configured

    def test_endpoint_failures_never_raise_and_never_print_the_url(self):
        self.http.fail = True
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(myx_notify.send(self.cfg(**{"Config/notify/heartbeat_url": "https://kuma.example/push/abc"}), self.summary(), 0), (False, False))
        self.assertIn("Notification could not be sent: ConnectionError", out.getvalue())
        self.assertNotIn("secret-topic", out.getvalue())
        self.http.fail, self.http.status = False, 403
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(myx_notify.send(self.cfg(), self.summary(), 0), (False, False))
        self.assertIn("refused by the ntfy server (status 403)", out.getvalue())

    def test_validate_catches_typos_before_the_run(self):
        self.assertIsNone(myx_notify.validate(self.cfg()))
        self.assertIsNone(myx_notify.validate(FakeConfig(self.td.name)))
        self.assertIn("Config/notify/on", myx_notify.validate(self.cfg(**{"Config/notify/on": "sometimes"})))
        self.assertIn("http(s) URL", myx_notify.validate(self.cfg(**{"Config/notify/ntfy_url": "ntfy.sh/topic"})))
        self.assertIn("heartbeat_url", myx_notify.validate(self.cfg(**{"Config/notify/heartbeat_url": "kuma/push"})))


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.saved = myx_library.requests
        self.http = FakeHttp()
        myx_library.requests = self.http
        myx_library.reset()
        self.td = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.td.name, "downloads")
        self.lib = os.path.join(self.td.name, "library")
        os.makedirs(os.path.join(self.src, "Some Book"))
        os.makedirs(os.path.join(self.lib, "Author", "Some Book"))
        self.source_file = os.path.join(self.src, "Some Book", "book.m4b")
        with open(self.source_file, "wb") as fh:
            fh.write(b"\x00" * 16)
        for k in ("ABS_API_TOKEN", "ABS_API_TOKEN_FILE"):
            os.environ.pop(k, None)

    def tearDown(self):
        myx_library.requests = self.saved
        myx_library.reset()
        self.td.cleanup()
        for k in ("ABS_API_TOKEN", "ABS_API_TOKEN_FILE"):
            os.environ.pop(k, None)

    def cfg(self, **over):
        return FakeConfig(self.td.name, **{"Config/paths": [{"files": ["**/*.m4b"], "source_path": self.src, "media_path": self.lib}], **over})

    def test_hardlinked_book_is_found_by_inode_and_index_is_built_once(self):
        cfg = self.cfg(**{"Config/dedupe_roots": [self.lib]})
        self.assertIsNone(myx_library.validateDedupeRoots(cfg))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(myx_library.alreadyFiled(cfg, [self.source_file]))      # not filed yet
        self.assertIn("Indexed 0 media files under 1 dedupe root(s)", out.getvalue())
        filed = os.path.join(self.lib, "Author", "Some Book", "Some Book.m4b")
        os.link(self.source_file, filed)
        myx_library.reset()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(myx_library.alreadyFiled(cfg, [self.source_file]), os.path.dirname(filed))
            self.assertEqual(myx_library.alreadyFiled(cfg, [self.source_file]), os.path.dirname(filed))
        self.assertEqual(out.getvalue().count("Indexed"), 1)
        # a copy (different inode) is not the same book; a missing file is skipped, not fatal
        other = os.path.join(self.src, "Some Book", "other.m4b")
        with open(other, "wb") as fh:
            fh.write(b"\x00" * 16)
        self.assertIsNone(myx_library.alreadyFiled(cfg, [other, os.path.join(self.src, "nope.m4b")]))
        self.assertIsNone(myx_library.alreadyFiled(FakeConfig(self.td.name), [self.source_file]))   # feature off

    def test_every_file_must_be_filed_and_symlinks_do_not_count(self):
        cfg = self.cfg(**{"Config/dedupe_roots": [self.lib]})
        second = os.path.join(self.src, "Some Book", "cd2.m4b")
        with open(second, "wb") as fh:
            fh.write(b"\x00" * 16)
        target = os.path.join(self.lib, "Author", "Some Book")
        os.link(self.source_file, os.path.join(target, "cd1.m4b"))                 # only the first disc was filed
        os.symlink(second, os.path.join(target, "cd2.m4b"))                        # a symlink is not a filed copy
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(myx_library.alreadyFiled(cfg, [self.source_file, second]))   # so the filing is completed
        os.remove(os.path.join(target, "cd2.m4b"))
        os.link(second, os.path.join(target, "cd2.m4b"))
        myx_library.reset()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(myx_library.alreadyFiled(cfg, [self.source_file, second]), target)

    def test_pinned_or_refreshed_book_is_filed_even_when_a_copy_exists(self):
        # the copy in the library is the wrong match being corrected: dedupe must not block the pin
        import booktree
        import myx_classes
        wrong = os.path.join(self.lib, "Wrong Author", "Wrong Title")
        os.makedirs(wrong)
        os.link(self.source_file, os.path.join(wrong, "book.m4b"))
        cfg = self.cfg(**{"Config/dedupe_roots": [self.lib], "Config/metadata": "audible"})
        for refresh in (False, True):
            myx_library.reset()
            mb = myx_classes.MAMBook("Some Book")
            bf = myx_classes.BookFile("Some Book/book.m4b", self.source_file, self.src, self.lib)
            match = myx_classes.Book(asin=ASIN, title="Right Title")
            match.authors = [myx_classes.Contributor("Right Author")]
            bf.ffprobeBook = match
            mb.files.append(bf)
            mb.ffprobeBook = mb.bestAudibleMatch = match
            mb.metadata = "audible"
            mb.isMatched = True
            mb.refresh = refresh
            with contextlib.redirect_stdout(io.StringIO()) as out:
                linked = booktree.hardlinkUnlessFiled(mb, cfg)
            right = os.path.join(self.lib, "Right Author", "Right Title", "book.m4b")
            self.assertEqual(linked, refresh, out.getvalue())
            self.assertEqual(os.path.exists(right), refresh)
            if not refresh:
                self.assertIn("Already in the library at " + wrong, out.getvalue())
                self.assertEqual(mb.alreadyFiled, wrong)
                # the CSV row still carries the target path of a matched book
                self.assertEqual(mb.getLogRecord(bf, cfg)["paths"], os.path.dirname(right))

    def test_root_containing_or_inside_the_source_path_is_refused(self):
        self.assertIn("contains the source path", myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": [self.td.name]})))
        self.assertIn("contains the source path", myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": [self.src]})))
        self.assertIn("lies inside the source path", myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": [os.path.join(self.src, "Some Book")]})))
        self.assertIn("is not a directory", myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": [os.path.join(self.td.name, "missing")]})))
        self.assertIn("must be a list", myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": 5})))
        self.assertIsNone(myx_library.validateDedupeRoots(self.cfg(**{"Config/dedupe_roots": []})))

    def test_abs_scan_is_posted_with_the_token_after_hardlinks_only(self):
        os.environ["ABS_API_TOKEN"] = "abs_secret"
        cfg = self.cfg(**{"Config/abs/url": "http://abs.example:13378/", "Config/abs/library_id": "lib-1"})
        self.assertIsNone(myx_library.validateAbs(cfg))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(myx_library.triggerAbsScan(cfg, 0))                 # nothing new: no scan
            self.assertFalse(myx_library.triggerAbsScan(cfg, 3, dry_run=True))   # dry run: announced, not sent
            self.assertTrue(myx_library.triggerAbsScan(cfg, 3))
        self.assertEqual([(m, u) for m, u, _ in self.http.calls], [("post", "http://abs.example:13378/api/libraries/lib-1/scan")])
        self.assertEqual(self.http.calls[0][2]["headers"]["Authorization"], "Bearer abs_secret")
        self.assertIs(self.http.calls[0][2]["allow_redirects"], False)
        self.assertNotIn("would trigger", out.getvalue())                          # dry run with nothing matched: silent
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(myx_library.triggerAbsScan(cfg, 0, dry_run=True, matched=2))
        self.assertIn("would trigger an Audiobookshelf library scan after hardlinking 2 matched book(s)", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(myx_library.triggerAbsScan(cfg, 3))
        self.assertIn("Triggered an Audiobookshelf scan of library lib-1", out.getvalue())
        self.assertNotIn("abs_secret", out.getvalue())
        self.http.fail = True
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(myx_library.triggerAbsScan(cfg, 3))
        self.assertIn("scan request failed: ConnectionError", out.getvalue())

    def test_abs_validation_and_token_file(self):
        cfg = self.cfg(**{"Config/abs/url": "http://abs.example", "Config/abs/library_id": "lib-1"})
        self.assertIn("no API token", myx_library.validateAbs(cfg))
        token_file = os.path.join(self.td.name, "abs.txt")
        with open(token_file, "w") as fh:
            fh.write("\nfrom-file\n")
        os.environ["ABS_API_TOKEN_FILE"] = token_file
        self.assertIsNone(myx_library.validateAbs(cfg))
        self.assertEqual(myx_library.absToken(), "from-file")
        self.assertIn("library_id is required", myx_library.validateAbs(self.cfg(**{"Config/abs/url": "http://abs.example"})))
        self.assertIn("http(s) URL", myx_library.validateAbs(self.cfg(**{"Config/abs/url": "abs.example", "Config/abs/library_id": "x"})))
        os.environ["ABS_API_TOKEN"] = "t"
        self.assertIn("letters, digits", myx_library.validateAbs(self.cfg(**{"Config/abs/url": "http://abs.example", "Config/abs/library_id": "../../login"})))
        self.assertIsNone(myx_library.validateAbs(self.cfg(**{"Config/abs/url": "http://abs.example", "Config/abs/library_id": "lib_c1u58o0jxtgb3nkj2y"})))
        self.assertIsNone(myx_library.validateAbs(self.cfg(**{"Config/abs/url": "http://abs.example", "Config/abs/scan": "0"})))   # string flag
        self.assertIsNone(myx_library.validateAbs(self.cfg()))                     # not configured
        self.assertIsNone(myx_library.validateAbs(self.cfg(**{"Config/abs/url": "http://abs.example", "Config/abs/scan": 0})))


if __name__ == "__main__":
    unittest.main()
