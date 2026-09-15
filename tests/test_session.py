"""MAM session sources (mousehole state file, cookie store, config/env), the cookie store that replaced cookies.pkl,
and the start-up cookie check. No network: requests.Session is replaced by a fake that answers like MAM."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from types import SimpleNamespace

import email
import http.client

import requests
import urllib3
from requests.cookies import RequestsCookieJar, extract_cookies_to_jar, get_cookie_header

import myx_args
import myx_mam
import myx_session
from tests.support import FakeConfig

COOKIE_A = "fakemamid" + "a" * 24          # shaped like a mam_id, deliberately low-entropy (gitleaks)
COOKIE_B = "rotatedmamid" + "b" * 24
MAM_URL = "https://www.myanonamouse.net/json/checkCookie.php"


class _Resp:
    def __init__(self, status=200, text="{}", payload=None):
        self.status_code, self.text, self._payload = status, text, payload

    def json(self):
        return self._payload


def sentCookies(jar, url=MAM_URL):
    """The mam_id values the real requests machinery would put in the Cookie header of a request to `url`."""
    header = get_cookie_header(jar, requests.Request("GET", url).prepare()) or ""
    return [part.split("=", 1)[1] for part in header.split("; ") if part.startswith("mam_id=")]


def rotateViaSetCookie(jar, value, url=MAM_URL, domain=".myanonamouse.net"):
    """Feed the jar a real `Set-Cookie` response header, exactly as requests does after MAM answers. MAM sends
    `Domain=.myanonamouse.net`; `domain=None` mimics a host-only cookie."""
    attrs = f"mam_id={value}; path=/; secure; HttpOnly; SameSite=Lax" + (f"; Domain={domain}" if domain else "")
    raw = urllib3.response.HTTPResponse(body=io.BytesIO(b"{}"), headers={"Set-Cookie": attrs}, status=200,
                                        preload_content=False)
    raw._original_response = SimpleNamespace(
        msg=email.message_from_string(f"Set-Cookie: {attrs}\r\n\r\n", _class=http.client.HTTPMessage))
    extract_cookies_to_jar(jar, requests.Request("GET", url).prepare(), raw)


class MamLikeSession:
    """Accepts the cookies listed in `valid`; a GET with an accepted cookie may rotate it with a Set-Cookie, as MAM
    does. Raises like requests when MAM cannot be reached (`unreachable`)."""
    valid = set()
    rotate_to = None
    rotate_domain = ".myanonamouse.net"
    unreachable = False
    instances = []

    def __init__(self):
        self.headers = {}
        self.cookies = RequestsCookieJar()
        self.requests = []
        MamLikeSession.instances.append(self)

    def _sent(self):
        values = sentCookies(self.cookies)
        return values[0] if len(values) == 1 else values

    def get(self, url, timeout=None):
        self.requests.append(("get", url, self._sent()))
        if MamLikeSession.unreachable:
            raise requests.ConnectionError("Max retries exceeded with url: " + url)
        if self._sent() in MamLikeSession.valid:
            if MamLikeSession.rotate_to:
                rotateViaSetCookie(self.cookies, MamLikeSession.rotate_to, url, MamLikeSession.rotate_domain)
            return _Resp(200, '{"Success":true}', {"Success": True})
        return _Resp(403, "Forbidden", None)

    def post(self, url, json=None, timeout=None):
        self.requests.append(("post", url, self._sent()))
        return _Resp(200, "{}", {"data": [], "total": 0, "found": 0, "perpage": 50, "start": 0})


class SessionTestBase(unittest.TestCase):
    def setUp(self):
        self.saved = (myx_mam.requests, myx_mam._sleep, myx_mam._now)
        myx_mam.requests = SimpleNamespace(Session=MamLikeSession)
        myx_mam._sleep = lambda s: None
        myx_mam._now = lambda: 0.0
        myx_mam.resetRunCounters()
        MamLikeSession.instances = []
        MamLikeSession.valid = set()
        MamLikeSession.rotate_to = None
        MamLikeSession.rotate_domain = ".myanonamouse.net"
        MamLikeSession.unreachable = False
        self.td = tempfile.TemporaryDirectory()
        self.log = os.path.join(self.td.name, "logs")
        os.makedirs(self.log)

    def tearDown(self):
        myx_mam.requests, myx_mam._sleep, myx_mam._now = self.saved
        myx_mam.resetRunCounters()
        self.td.cleanup()

    def cfg(self, **over):
        return FakeConfig(self.td.name, **{"Config/log_path": self.log, **over})

    def write(self, name, content, binary=False):
        path = os.path.join(self.td.name, name)
        with open(path, "wb" if binary else "w") as fh:
            fh.write(content)
        return path


class MouseholeStateFileTest(SessionTestBase):
    def test_v2_cookie_key_is_read_verbatim(self):
        p = self.write("state.json", json.dumps({"version": 2, "cookie": " " + COOKIE_A + "\n", "lastMamContact": {"reached": True}}))
        self.assertEqual(myx_session.readMousehole(p), COOKIE_A)

    def test_legacy_currentCookie_is_read(self):
        p = self.write("state.json", json.dumps({"currentCookie": COOKIE_A}))
        self.assertEqual(myx_session.readMousehole(p), COOKIE_A)

    def test_v2_without_cookie_falls_back_to_legacy_key_then_none(self):
        p = self.write("state.json", json.dumps({"version": 2, "cookie": "", "currentCookie": COOKIE_A}))
        self.assertEqual(myx_session.readMousehole(p), COOKIE_A)
        p = self.write("state2.json", json.dumps({"version": 2, "lastMamContact": {"reached": False}}))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(myx_session.readMousehole(p))
        self.assertIn("holds no cookie yet", out.getvalue())

    def test_missing_malformed_non_object_and_oversized_files_are_reported_without_content(self):
        cases = {
            "missing.json": None,
            "bad.json": "{not json",
            "list.json": "[1, 2]",
            "big.json": "x" * (myx_session.MAX_STATE_BYTES + 1),
            "num.json": json.dumps({"version": 2, "cookie": 12345}),
        }
        for name, content in cases.items():
            p = os.path.join(self.td.name, name) if content is None else self.write(name, content)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertIsNone(myx_session.readMousehole(p), name)
            self.assertIn(name, out.getvalue())
            self.assertNotIn("not json", out.getvalue())

    def test_path_from_config_or_environment(self):
        os.environ.pop("MOUSEHOLE_STATE_FILE", None)
        self.assertEqual(myx_session.mouseholePath(self.cfg()), "")
        self.assertEqual(myx_session.mouseholePath(self.cfg(**{"Config/mousehole_state_file": "/m/state.json"})), "/m/state.json")
        os.environ["MOUSEHOLE_STATE_FILE"] = "/env/state.json"
        try:
            self.assertEqual(myx_session.mouseholePath(self.cfg()), "/env/state.json")
            self.assertEqual(myx_session.mouseholePath(self.cfg(**{"Config/mousehole_state_file": "/m/state.json"})), "/m/state.json")
        finally:
            del os.environ["MOUSEHOLE_STATE_FILE"]


class CookieStoreTest(SessionTestBase):
    def test_store_round_trip_is_owner_only_and_atomic(self):
        self.assertIsNone(myx_session.loadStore(self.log))
        self.assertTrue(myx_session.saveStore(self.log, COOKIE_A))
        path = myx_session.storePath(self.log)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        with open(path) as fh:
            self.assertEqual(json.load(fh), {"mam_id": COOKIE_A})
        self.assertEqual(myx_session.loadStore(self.log), COOKIE_A)
        self.assertEqual([f for f in os.listdir(self.log) if f.endswith(".tmp")], [])
        myx_session.clearStore(self.log)
        self.assertFalse(os.path.exists(path))
        myx_session.clearStore(self.log)          # idempotent

    def test_store_lives_under_the_same_default_log_path_as_the_csv_log(self):
        # Config/log_path empty: the CSV goes to ./logs (myx_utilities.getLogPath), so the store does too
        cwd = os.getcwd()
        os.chdir(self.td.name)
        try:
            self.assertEqual(myx_session.logPath(self.cfg(**{"Config/log_path": ""})), os.path.join(self.td.name, "logs"))
            self.assertEqual(myx_session.logPath(self.cfg(**{"Config/log_path": None})), os.path.join(self.td.name, "logs"))
            self.assertEqual(myx_session.logPath(self.cfg()), self.log)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(myx_session.accepted(self.cfg(**{"Config/log_path": ""}), MamLikeSession(), COOKIE_A, "config"))
            self.assertEqual(myx_session.loadStore(os.path.join(self.td.name, "logs")), COOKIE_A)
        finally:
            os.chdir(cwd)

    def test_unreadable_store_is_ignored_not_fatal(self):
        self.write("logs/cookies.json", "garbage")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(myx_session.loadStore(self.log))
        self.assertIn("Ignoring unreadable cookie store", out.getvalue())
        self.write("logs/cookies.json", json.dumps({"mam_id": ""}))
        self.assertIsNone(myx_session.loadStore(self.log))

    def test_planted_fifo_and_deeply_nested_store_do_not_hang_or_crash(self):
        path = myx_session.storePath(self.log)
        os.mkfifo(path)
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertIsNone(myx_session.loadStore(self.log))     # would block forever with a plain open()
            self.assertIn("not a regular file", out.getvalue())
        finally:
            os.remove(path)
        self.write("logs/cookies.json", "[" * 200000)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertIsNone(myx_session.loadStore(self.log))         # RecursionError inside json
        self.assertIn("RecursionError", out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(myx_session.candidates(self.cfg(**{"Config/session": COOKIE_A})), [(COOKIE_A, "config")])

    def test_values_that_cannot_be_a_cookie_are_rejected_before_any_request(self):
        # a newline would make http.client echo the whole header (the secret) in its ValueError; a `;` would smuggle
        # a second cookie into the header sent to MAM
        for bad in (COOKIE_A + "\nX-Injected: 1", "x; PHPSESSID=evil", 'a"b', "a b", "\u00e9"):
            myx_session.saveStore(self.log, bad)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                found = myx_session.candidates(self.cfg(**{"Config/session": bad}))
            self.assertEqual(found, [], repr(bad))
            self.assertIn("not a valid cookie value", out.getvalue())
            self.assertNotIn("evil", out.getvalue())
            self.assertNotIn(COOKIE_A, out.getvalue())
        self.assertTrue(myx_session.validValue(COOKIE_A))
        self.assertTrue(myx_session.validValue("a" * 338))

    def test_legacy_pickle_is_removed_and_never_loaded(self):
        # a pickle that would raise (and could run code) if anything unpickled it
        legacy = self.write("logs/cookies.pkl", b"\x80\x04\x95\x10\x00\x00\x00\x00\x00\x00\x00\x8c\x08builtins\x94\x8c\x04exit\x94\x93\x94)R\x94.", binary=True)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            found = myx_session.candidates(self.cfg(**{"Config/session": COOKIE_A}))
        self.assertFalse(os.path.exists(legacy))
        self.assertIn("Removed the obsolete cookies.pkl", out.getvalue())
        self.assertEqual(found, [(COOKIE_A, "config")])
        with contextlib.redirect_stdout(io.StringIO()) as out:
            myx_session.candidates(self.cfg(**{"Config/session": COOKIE_A}))
        self.assertEqual(out.getvalue(), "")      # said once


class CandidateOrderTest(SessionTestBase):
    def test_order_is_mousehole_then_store_then_config_and_duplicates_collapse(self):
        state = self.write("state.json", json.dumps({"version": 2, "cookie": "mh"}))
        myx_session.saveStore(self.log, "store")
        cfg = self.cfg(**{"Config/session": "cfg", "Config/mousehole_state_file": state})
        self.assertEqual(myx_session.candidates(cfg), [("mh", "mousehole"), ("store", "cookie store"), ("cfg", "config")])
        myx_session.saveStore(self.log, "cfg")
        self.assertEqual(myx_session.candidates(cfg), [("mh", "mousehole"), ("cfg", "cookie store")])
        myx_session.clearStore(self.log)
        self.assertEqual(myx_session.candidates(self.cfg(**{"Config/session": "  "})), [])
        self.assertEqual(myx_session.resolve(self.cfg()), ("", None))

    def test_resolve_prefers_the_value_mam_accepted_this_run(self):
        cfg = self.cfg(**{"Config/session": "cfg"})
        self.assertEqual(myx_session.resolve(cfg), ("cfg", "config"))
        myx_session.markValidated(COOKIE_B, "config")
        self.assertEqual(myx_session.resolve(cfg), (COOKIE_B, "config"))
        myx_session.reset()
        self.assertEqual(myx_session.resolve(cfg), ("cfg", "config"))


class AcceptedTest(SessionTestBase):
    def jar(self, value):
        s = MamLikeSession()
        myx_session.apply(s, value)
        return s

    def test_config_session_is_saved_to_the_store_once_accepted(self):
        cfg = self.cfg(**{"Config/session": COOKIE_A})
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(myx_session.accepted(cfg, self.jar(COOKIE_A), COOKIE_A, "config"))
        self.assertIn("Cookie updated...", out.getvalue())
        self.assertEqual(myx_session.loadStore(self.log), COOKIE_A)
        self.assertEqual(myx_session.resolve(cfg), (COOKIE_A, "config"))

    def test_rotation_from_mam_is_kept_and_used_for_the_rest_of_the_run(self):
        # MAM's Set-Cookie carries Domain=.myanonamouse.net (seen in upstream's pickled jar); a host-only one must
        # work too. Either way the rotation, not the value we sent, is what the rest of the run and the store use.
        for domain in (".myanonamouse.net", None):
            myx_session.reset()
            cfg = self.cfg()
            myx_session.saveStore(self.log, COOKIE_A)
            s = self.jar(COOKIE_A)
            self.assertEqual(sentCookies(s.cookies), [COOKIE_A])
            rotateViaSetCookie(s.cookies, COOKIE_B, domain=domain)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(myx_session.accepted(cfg, s, COOKIE_A, "cookie store"), domain)
            self.assertEqual(myx_session.loadStore(self.log), COOKIE_B, domain)
            self.assertEqual(myx_session.resolve(cfg), (COOKIE_B, "cookie store"), domain)
            # the next MAM request of the run carries the rotated value only
            s2 = self.jar(myx_session.resolve(cfg)[0])
            self.assertEqual(sentCookies(s2.cookies), [COOKIE_B], domain)

    def test_cookie_is_sent_to_mam_only(self):
        s = self.jar(COOKIE_A)
        self.assertEqual(sentCookies(s.cookies, "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"), [COOKIE_A])
        self.assertEqual(sentCookies(s.cookies, "https://api.audible.com/1.0/catalog/products"), [])
        self.assertEqual(sentCookies(s.cookies, "https://myanonamouse.net.evil.example/"), [])

    def test_unchanged_store_value_is_not_rewritten(self):
        cfg = self.cfg()
        myx_session.saveStore(self.log, COOKIE_A)
        before = os.stat(myx_session.storePath(self.log)).st_mtime_ns
        self.assertFalse(myx_session.accepted(cfg, self.jar(COOKIE_A), COOKIE_A, "cookie store"))
        self.assertEqual(os.stat(myx_session.storePath(self.log)).st_mtime_ns, before)

    def test_mousehole_cookie_is_never_copied_into_the_store(self):
        cfg = self.cfg()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(myx_session.accepted(cfg, self.jar(COOKIE_A), COOKIE_A, "mousehole"))
        self.assertFalse(os.path.exists(myx_session.storePath(self.log)))
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(myx_session.resolve(cfg), (COOKIE_A, "mousehole"))


class CheckMAMCookieTest(SessionTestBase):
    def check(self, cfg):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ok = myx_mam.checkMAMCookie(cfg)
        return ok, out.getvalue()

    def test_config_session_accepted_saved_and_reused_without_a_second_test(self):
        MamLikeSession.valid = {COOKIE_A}
        ok, out = self.check(self.cfg(**{"Config/session": COOKIE_A}))
        self.assertTrue(ok)
        self.assertIn("Checking MAM cookie from the config...", out)
        self.assertEqual(myx_session.loadStore(self.log), COOKIE_A)
        with contextlib.redirect_stdout(io.StringIO()):
            myx_mam.searchMAM(self.cfg(**{"Config/session": COOKIE_A}), "A.m4b", "", '"m4b"')
        methods = [(m, u.rsplit("/", 1)[1]) for s in MamLikeSession.instances for m, u, _ in s.requests]
        self.assertEqual(methods, [("get", "checkCookie.php"), ("post", "loadSearchJSONbasic.php")])
        self.assertEqual(MamLikeSession.instances[-1].requests[-1][2], COOKIE_A)

    def test_rejected_store_is_removed_and_config_is_tried_next(self):
        MamLikeSession.valid = {COOKIE_A}
        myx_session.saveStore(self.log, "stale")
        ok, out = self.check(self.cfg(**{"Config/session": COOKIE_A}))
        self.assertTrue(ok)
        self.assertIn("from the cookie store was rejected, trying the next source", out)
        self.assertIn("Checking MAM cookie from the config...", out)
        self.assertEqual(myx_session.loadStore(self.log), COOKIE_A)

    def test_mousehole_wins_and_a_rotation_is_not_written_back(self):
        MamLikeSession.valid = {COOKIE_A}
        MamLikeSession.rotate_to = COOKIE_B
        state = self.write("state.json", json.dumps({"version": 2, "cookie": COOKIE_A}))
        cfg = self.cfg(**{"Config/session": "other", "Config/mousehole_state_file": state})
        ok, out = self.check(cfg)
        self.assertTrue(ok)
        self.assertIn("Checking MAM cookie from the mousehole...", out)
        self.assertFalse(os.path.exists(myx_session.storePath(self.log)))
        with open(state) as fh:
            self.assertEqual(json.load(fh)["cookie"], COOKIE_A)
        self.assertEqual(myx_session.resolve(cfg), (COOKIE_B, "mousehole"))

    def test_unreachable_mam_keeps_the_store_and_stops_after_one_attempt(self):
        MamLikeSession.unreachable = True
        myx_session.saveStore(self.log, COOKIE_A)
        ok, out = self.check(self.cfg(**{"Config/session": "other"}))
        self.assertFalse(ok)
        self.assertIn("Could not reach MAM", out)
        self.assertNotIn("was rejected", out)
        self.assertEqual(myx_session.loadStore(self.log), COOKIE_A)          # a WAN blip must not discard the rotation
        self.assertEqual(len(MamLikeSession.instances), 1)                 # no second candidate is tried
        self.assertNotIn(COOKIE_A, out)

    def test_nothing_configured_and_everything_rejected(self):
        ok, out = self.check(self.cfg())
        self.assertFalse(ok)
        self.assertIn("No MAM session found", out)
        self.assertEqual(MamLikeSession.instances, [])
        ok, out = self.check(self.cfg(**{"Config/session": "bad"}))
        self.assertFalse(ok)
        self.assertIn("from the config was rejected", out)
        self.assertNotIn("trying the next source", out)

    def test_secret_never_reaches_stdout(self):
        MamLikeSession.valid = {COOKIE_A, COOKIE_B}
        MamLikeSession.rotate_to = COOKIE_B
        state = self.write("state.json", json.dumps({"version": 2, "cookie": COOKIE_A}))
        for cfg in (self.cfg(**{"Config/session": COOKIE_A}), self.cfg(**{"Config/mousehole_state_file": state}),
                    self.cfg(**{"Config/session": "bad"})):
            myx_mam.resetRunCounters()
            ok, out = self.check(cfg)
            with contextlib.redirect_stdout(io.StringIO()) as out2:
                myx_mam.searchMAM(cfg, "A.m4b", "", '"m4b"')
            for text in (out, out2.getvalue()):
                self.assertNotIn(COOKIE_A, text)
                self.assertNotIn(COOKIE_B, text)


class SessionFromEnvironmentTest(unittest.TestCase):
    def setUp(self):
        self.saved = {k: os.environ.pop(k, None) for k in ("MAM_SESSION", "MAM_SESSION_FILE")}

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    @staticmethod
    def _params(config_file):
        return SimpleNamespace(config_file=config_file, dry_run=None, verbose=None, no_cache=None, no_opf=None,
                               multibook=None, ebooks=None, fixid3=None, add_narrators=None, hints=None,
                               legacy_names=None, refresh=None, json_log=None)

    def test_secret_file_fills_an_empty_session_and_env_var_wins_over_it(self):
        with tempfile.TemporaryDirectory() as td:
            cfgfile = os.path.join(td, "config.json")
            with open(cfgfile, "w") as fh:
                json.dump({"Config": {"metadata": "mam", "session": "", "flags": {}}}, fh)
            secret = os.path.join(td, "mam_session")
            with open(secret, "w") as fh:
                fh.write("\n  " + COOKIE_A + "  \nsecond line\n")
            os.environ["MAM_SESSION_FILE"] = secret
            self.assertEqual(myx_args.Config(self._params(cfgfile)).get("Config/session"), COOKIE_A)
            os.environ["MAM_SESSION"] = "env-wins"
            self.assertEqual(myx_args.Config(self._params(cfgfile)).get("Config/session"), "env-wins")

    def test_missing_or_empty_secret_file_is_reported_by_path_only(self):
        with tempfile.TemporaryDirectory() as td:
            cfgfile = os.path.join(td, "config.json")
            with open(cfgfile, "w") as fh:
                json.dump({"Config": {"metadata": "mam", "flags": {}}}, fh)
            os.environ["MAM_SESSION_FILE"] = os.path.join(td, "nope")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertIsNone(myx_args.Config(self._params(cfgfile)).get("Config/session"))
            self.assertIn("Could not read MAM_SESSION_FILE", out.getvalue())
            empty = os.path.join(td, "empty")
            with open(empty, "w") as fh:
                fh.write("\n")
            os.environ["MAM_SESSION_FILE"] = empty
            with contextlib.redirect_stdout(io.StringIO()) as out:
                myx_args.Config(self._params(cfgfile))
            self.assertIn("is empty", out.getvalue())


if __name__ == "__main__":
    unittest.main()
