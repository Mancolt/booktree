"""Config.session resolution, including the MAM_SESSION environment-variable fallback."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import myx_args


def _params(config_file):
    """A params namespace with every override left unset (as importArgs would produce for defaults)."""
    return SimpleNamespace(config_file=config_file, dry_run=None, verbose=None, no_cache=None, no_opf=None,
                           multibook=None, ebooks=None, fixid3=None, add_narrators=None, hints=None,
                           legacy_names=None, refresh=None, json_log=None)


class ConfigSessionEnvFallbackTest(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.get("MAM_SESSION")
        os.environ.pop("MAM_SESSION", None)

    def tearDown(self):
        if self._saved_env is None:
            os.environ.pop("MAM_SESSION", None)
        else:
            os.environ["MAM_SESSION"] = self._saved_env

    def _write_config(self, tmpdir, session_value):
        cfg = {"Config": {"metadata": "mam", "flags": {}}}
        if session_value is not None:
            cfg["Config"]["session"] = session_value
        path = os.path.join(tmpdir, "config.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        return path

    def test_env_fills_in_an_empty_session(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = myx_args.Config(_params(self._write_config(td, "")))
            self.assertEqual(cfg.get("Config/session"), "")
            os.environ["MAM_SESSION"] = "env-cookie"
            cfg = myx_args.Config(_params(self._write_config(td, "")))
            self.assertEqual(cfg.get("Config/session"), "env-cookie")

    def test_env_fills_in_a_missing_session_key(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MAM_SESSION"] = "env-cookie"
            cfg = myx_args.Config(_params(self._write_config(td, None)))
            self.assertEqual(cfg.get("Config/session"), "env-cookie")

    def test_config_session_wins_over_env(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["MAM_SESSION"] = "env-cookie"
            cfg = myx_args.Config(_params(self._write_config(td, "config-cookie")))
            self.assertEqual(cfg.get("Config/session"), "config-cookie")

    def test_no_env_and_empty_config_stays_empty(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = myx_args.Config(_params(self._write_config(td, "")))
            self.assertEqual(cfg.get("Config/session"), "")


if __name__ == "__main__":
    unittest.main()
