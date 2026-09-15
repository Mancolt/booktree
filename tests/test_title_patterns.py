"""Config/tokens/title_patterns written as "\\bpart\\b" in JSON is the word between two backspaces (upstream's
template shipped it that way); booktree reads such a pattern as the intended word boundary."""
import contextlib
import io
import json
import tempfile
import unittest

import myx_classes
import myx_utilities
from tests.support import FakeConfig

BROKEN = json.loads('["-end", "\\bpart\\b", "\\btrack\\b", "\\bof\\b", "\\bbook\\b", "m4b"]')      # what the template produced
CORRECT = json.loads('["-end", "\\\\bpart\\\\b", "\\\\btrack\\\\b", "\\\\bof\\\\b", "\\\\bbook\\\\b", "m4b"]')


class TitlePatternsTest(unittest.TestCase):
    def setUp(self):
        myx_utilities._warnedTitlePatterns = False

    def test_template_string_really_contains_backspaces(self):
        self.assertIn("\x08", BROKEN[1])
        self.assertEqual(CORRECT[1], r"\bpart\b")

    def test_backspace_patterns_are_repaired_once_with_a_note(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/tokens/title_patterns": BROKEN})
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(myx_utilities.titlePatterns(cfg), CORRECT)
                myx_utilities.titlePatterns(cfg)
            self.assertEqual(out.getvalue().count("Note: Config/tokens/title_patterns"), 1)
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(myx_utilities.titlePatterns(FakeConfig(td, **{"Config/tokens/title_patterns": CORRECT})), CORRECT)
            self.assertEqual(out.getvalue(), "")
            self.assertEqual(myx_utilities.titlePatterns(FakeConfig(td, **{"Config/tokens/title_patterns": None})), [])

    def test_alt_title_drops_the_noise_words_with_either_spelling(self):
        for patterns in (BROKEN, CORRECT):
            with tempfile.TemporaryDirectory() as td:
                cfg = FakeConfig(td, **{"Config/tokens/title_patterns": patterns, "Config/flags/verbose": 0})
                book = myx_classes.Book(title="Book 3 Part 2 of the Track m4b")
                book.authors, book.series = [], []
                with contextlib.redirect_stdout(io.StringIO()):
                    myx_utilities.getAltTitle("x", book, cfg)
                self.assertEqual(book.title, "the", patterns)

    def test_alt_title_with_the_broken_patterns_used_to_keep_the_noise(self):
        # the defect: with backspaces in the pattern nothing matched, so the words stayed in the alternative title
        with tempfile.TemporaryDirectory() as td:
            cfg = FakeConfig(td, **{"Config/tokens/title_patterns": BROKEN, "Config/flags/verbose": 0})
            book = myx_classes.Book(title="Part 2 of the Track")
            book.authors, book.series = [], []
            saved = myx_utilities.titlePatterns
            myx_utilities.titlePatterns = lambda c: list(BROKEN)          # bypass the repair
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    myx_utilities.getAltTitle("x", book, cfg)
            finally:
                myx_utilities.titlePatterns = saved
            self.assertEqual(book.title, "part of the track")


if __name__ == "__main__":
    unittest.main()
