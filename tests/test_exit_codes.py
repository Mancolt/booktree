"""booktree's exit status is part of its interface for hooks and timers: 0 processed, 2 configuration/input problem,
1 unhandled error. Runs booktree.py as a subprocess against temporary directories; no network is involved because the
configurations either fail validation or find no files."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

import booktree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_booktree(*args, env_extra=None):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, os.path.join(ROOT, "booktree.py"), *args], capture_output=True, text=True,
                          cwd=ROOT, env=env, timeout=120)


class ExitCodeTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.td.name, "src")
        self.media = os.path.join(self.td.name, "media")
        self.logs = os.path.join(self.td.name, "logs")
        for d in (self.src, self.media, self.logs):
            os.makedirs(d)

    def tearDown(self):
        self.td.cleanup()

    def config(self, name="config.json", **over):
        cfg = {"Config": {
            "metadata": "audible", "matchrate": 60, "fuzzy_match": "token_sort",
            "log_path": self.logs, "cache_path": self.td.name, "session": "",
            "paths": [{"files": ["**/*.m4b"], "source_path": self.src, "media_path": self.media}],
            "flags": {"dry_run": 0, "verbose": 0, "multibook": 0, "ebooks": 0, "no_opf": 0, "no_cache": 0,
                      "fixid3": 0, "add_narrators": 0, "interactive": 0, "hardlink": 1},
            "target_path": {"multi_author": "{first_author}", "in_series": "{author}/{series}/{series} #{part} - {title}",
                            "no_series": "{author}/{title}", "disc_folder": "{title} {disc}"},
            "tokens": {"skip_series": 0, "kw_ignore": [], "kw_ignore_words": [], "title_patterns": []},
        }}
        for path, value in over.items():
            node = cfg
            parts = path.split("/")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        p = os.path.join(self.td.name, name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        return p

    def test_constants(self):
        self.assertEqual((booktree.EXIT_OK, booktree.EXIT_ERROR, booktree.EXIT_USAGE, booktree.EXIT_INTERRUPTED), (0, 1, 2, 130))

    def test_clean_run_over_an_empty_source_exits_0(self):
        r = run_booktree(self.config())
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Completed processing 0 books", r.stdout)
        self.assertEqual(r.stderr, "")

    def test_missing_or_unreadable_config_exits_2_without_a_traceback(self):
        r = run_booktree(os.path.join(self.td.name, "nope.json"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("Your config path is invalid", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        bad = os.path.join(self.td.name, "bad.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        r = run_booktree(bad)
        self.assertEqual(r.returncode, 2)
        self.assertIn("There was a problem reading your config file", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_usage_error_exits_2(self):
        r = run_booktree()
        self.assertEqual(r.returncode, 2)

    def test_nonexistent_source_or_media_path_exits_2_after_processing_the_others(self):
        good = {"files": ["**/*.m4b"], "source_path": self.src, "media_path": self.media}
        bad = {"files": ["**/*.m4b"], "source_path": os.path.join(self.td.name, "missing"), "media_path": self.media}
        r = run_booktree(self.config(**{"Config/paths": [good, bad]}))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("Completed processing 0 books", r.stdout)          # the good path still ran
        self.assertIn("Your source and media paths are invalid", r.stdout)

    def test_log_mode_with_a_missing_input_file_exits_2(self):
        r = run_booktree(self.config(**{"Config/metadata": "log", "Config/paths": [
            {"files": os.path.join(self.td.name, "fix.csv"), "source_path": self.src, "media_path": self.media}]}))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("Your input file", r.stdout)

    def test_invalid_hints_file_exits_2(self):
        hints = os.path.join(self.td.name, "hints.json")
        with open(hints, "w") as fh:
            fh.write('{"x": {"asin": "not-an-asin"}}')
        r = run_booktree(self.config(), "--hints", hints)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        # not JSON at all, and a path that is not a file: usage errors too, not tracebacks
        with open(hints, "w") as fh:
            fh.write("{not json")
        r = run_booktree(self.config(), "--hints", hints)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("Could not use the hints file", r.stdout)
        self.assertNotIn("Traceback", r.stderr)
        r = run_booktree(self.config(), "--hints", self.td.name)
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_no_mam_session_exits_2(self):
        r = run_booktree(self.config(**{"Config/metadata": "mam-audible"}), env_extra={"MAM_SESSION": "", "MAM_SESSION_FILE": ""})
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        self.assertIn("No MAM session found", r.stdout)
        self.assertIn("Your MAM cookie is not valid", r.stdout)
        self.assertNotIn("Traceback", r.stderr)

    def test_unhandled_error_exits_1_with_traceback_and_a_json_failure_record(self):
        # a "files" pattern list that is a number raises TypeError inside main(): an unhandled error, not a validated input
        jsonl = os.path.join(self.logs, "run.jsonl")
        r = run_booktree(self.config(**{"Config/paths": [{"files": 123, "source_path": self.src, "media_path": self.media}]}), f"--json-log={jsonl}")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("Traceback", r.stderr)
        with open(jsonl, encoding="utf-8") as fh:
            records = [json.loads(line) for line in fh]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["type"], "run")
        self.assertEqual(records[0]["exit_code"], 1)
        self.assertTrue(records[0]["error"].startswith("TypeError"))

    def test_malformed_paths_entry_exits_2_without_a_traceback(self):
        for paths in ({"files": ["**/*.m4b"], "source_path": self.src, "media_path": self.media},   # object, not list
                      [{"source_path": self.src, "media_path": self.media}],                        # no "files"
                      ["not an object"], [], None):                                                 # nothing to process
            r = run_booktree(self.config(**{"Config/paths": paths}))
            self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
            self.assertIn("Config/paths must be a non-empty list", r.stdout)
            self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
