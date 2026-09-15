"""End-to-end: log mode (fix.csv) with an explicit id3-asin whose id3 title/author disagree with Audible."""
import contextlib
import csv
import io
import os
import tempfile
import unittest

import booktree
import myx_hints
import myx_utilities
from tests.support import FakeAudible, FakeConfig, product


class LogModePinTest(unittest.TestCase):
    def test_fix_csv_asin_is_authoritative_and_the_book_is_hardlinked(self):
        with tempfile.TemporaryDirectory() as td:
            src, media = os.path.join(td, "src"), os.path.join(td, "media")
            os.makedirs(os.path.join(src, "junk"))
            os.makedirs(media)
            source_file = os.path.join(src, "junk", "AudioTrack 01.mp3")
            with open(source_file, "wb") as fh:
                fh.write(b"\x00" * 16)
            fix = os.path.join(td, "fix.csv")
            row = dict.fromkeys(myx_utilities.getLogHeaders().keys(), "")
            row.update({"book": "junk", "file": source_file, "isMatched": "False", "isHardLinked": "False", "mamCount": "0",
                        "audibleMatchCount": "0", "metadatasource": "id3", "id3-matchRate": "0", "id3-asin": "B0CC3NZ34S",
                        "id3-title": "AudioTrack 01", "id3-authors": "unknown artist", "id3-duration": str(492 * 60),
                        "id3-language": "english", "sourcePath": src, "mediaPath": media})
            with open(fix, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(myx_utilities.getLogHeaders().keys()))
                w.writeheader(); w.writerow(row)
            cfg = FakeConfig(td, **{"Config/metadata": "log", "Config/flags/no_cache": 1, "Config/flags/verbose": 1})
            myx_hints._cache.clear()
            saved = booktree.httpx
            booktree.httpx = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            out = io.StringIO()
            try:
                with contextlib.redirect_stdout(out):
                    logfile = os.path.join(td, "booktree_log_test.csv")
                    booktree.buildTreeFromLog(fix, logfile, cfg)
            finally:
                booktree.httpx = saved
            with open(logfile, newline="", encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            text = out.getvalue()
            self.assertEqual(len(rows), 1)
            r = rows[0]
            #existence checks must happen before the temporary directory is removed
            linked = os.path.exists(os.path.join(r["paths"], "AudioTrack 01.mp3"))
            opf = os.path.exists(os.path.join(r["paths"], "metadata.opf"))
            same_inode = linked and os.path.samefile(source_file, os.path.join(r["paths"], "AudioTrack 01.mp3"))
        self.assertEqual(r["isMatched"], "True")
        self.assertEqual(r["isHardLinked"], "True")
        self.assertEqual(r["metadatasource"], "audible")
        self.assertEqual(r["adb-asin"], "B0CC3NZ34S")
        self.assertEqual(r["adb-title"], "The Coworker")
        self.assertEqual(r["id3-title"], "AudioTrack 01")          # the file's own tags are logged unchanged
        self.assertEqual(r["paths"], os.path.join(media, "Freida McFadden", "The Coworker"))
        self.assertTrue(linked)
        self.assertTrue(same_inode)          # a hardlink, not a copy
        self.assertTrue(opf)
        self.assertIn("Pinned ASIN B0CC3NZ34S accepted: The Coworker by Freida McFadden", text)
        self.assertIn("Creating Hardlinks for 1 matched books", text)
        self.assertIn("Hardlinking files for The Coworker", text)
        self.assertEqual(len(list(myx_utilities.getLogHeaders().keys())), len(r))


if __name__ == "__main__":
    unittest.main()
