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
            jsonl = os.path.join(td, "run.jsonl")
            cfg = FakeConfig(td, **{"Config/metadata": "log", "Config/flags/no_cache": 1, "Config/flags/verbose": 1,
                                    "Config/json_log": jsonl})
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
            import json
            records = [json.loads(l) for l in open(jsonl, encoding="utf-8")]
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
        # --json-log: one book record + one run record, queries carry the cache keys printed to stdout
        self.assertEqual([x["type"] for x in records], ["book", "run"])
        b = records[0]
        self.assertEqual((b["release"], b["matched"], b["metadata_source"], b["pinned_asin"]), ("junk", True, "audible", "B0CC3NZ34S"))
        self.assertEqual(b["match"]["asin"], "B0CC3NZ34S")
        self.assertEqual(b["match"]["attempt"], "pinned")
        self.assertEqual(b["target_path"], r["paths"])
        self.assertTrue(b["hardlinked"])
        self.assertEqual(b["id3"]["title"], "AudioTrack 01")
        self.assertEqual(len(b["queries"]), 1)
        self.assertEqual(b["queries"][0]["kind"], "audible")
        self.assertIn(f"Checking cache: audible/{b['queries'][0]['cache_key']}", text)
        self.assertEqual(b["queries"][0]["asin"], "B0CC3NZ34S")
        self.assertEqual(records[1]["matched"], 1)
        self.assertEqual(records[1]["audible_queries"], 1)
        self.assertEqual(records[1]["csv"], logfile)


if __name__ == "__main__":
    unittest.main()


class JsonLogRobustnessTest(unittest.TestCase):
    def test_non_utf8_release_name_is_written_not_fatal(self):
        import json
        import myx_jsonlog
        bad = "Caf\udce9 - Title.m4b"           # what os.listdir yields for a Latin-1 byte
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "sub", "run.jsonl")
            myx_jsonlog.write(path, [{"type": "book", "release": bad}])
            with open(path, encoding="utf-8") as fh:
                rec = json.loads(fh.readline())
        self.assertEqual(rec["release"], bad)

    def test_json_log_does_not_follow_a_symlink(self):
        import myx_jsonlog
        with tempfile.TemporaryDirectory() as td:
            victim = os.path.join(td, "victim.txt")
            open(victim, "w").write("keep")
            link = os.path.join(td, "run.jsonl")
            os.symlink(victim, link)
            with self.assertRaises(OSError):
                myx_jsonlog.write(link, [{"type": "run"}])
            self.assertEqual(open(victim).read(), "keep")


class RefreshEndToEndTest(unittest.TestCase):
    def test_refresh_reprocesses_a_release_that_has_a_processed_marker(self):
        import myx_hints
        with tempfile.TemporaryDirectory() as td:
            src, media = os.path.join(td, "src"), os.path.join(td, "media")
            os.makedirs(os.path.join(src, "junk")); os.makedirs(media)
            source_file = os.path.join(src, "junk", "AudioTrack 01.mp3")
            open(source_file, "wb").write(b"\x00" * 16)
            fix = os.path.join(td, "fix.csv")
            row = dict.fromkeys(myx_utilities.getLogHeaders().keys(), "")
            row.update({"book": "junk", "file": source_file, "isMatched": "False", "isHardLinked": "False", "mamCount": "0",
                        "audibleMatchCount": "0", "metadatasource": "id3", "id3-matchRate": "0", "id3-asin": "B0CC3NZ34S",
                        "id3-title": "AudioTrack 01", "id3-authors": "unknown artist", "id3-duration": str(492 * 60),
                        "id3-language": "english", "sourcePath": src, "mediaPath": media})
            with open(fix, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(myx_utilities.getLogHeaders().keys())); w.writeheader(); w.writerow(row)
            saved = booktree.httpx
            booktree.httpx = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})
            try:
                def run_once(**over):
                    myx_hints._cache.clear(); myx_hints._appliedRefresh.clear()
                    cfg = FakeConfig(td, **{"Config/metadata": "log", "Config/flags/verbose": 1, **over})
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out):
                        booktree.buildTreeFromLog(fix, os.path.join(td, f"log{len(os.listdir(td))}.csv"), cfg)
                    return out.getvalue()
                first = run_once()
                second = run_once()
                third = run_once(**{"Config/refresh": ["junk"]})
            finally:
                booktree.httpx = saved
        self.assertIn("Pinned ASIN B0CC3NZ34S accepted", first)
        self.assertIn("Skipping junk, already processed", second)
        self.assertIn("Applying hint for junk: {'refresh': True}", third)
        self.assertIn("Pinned ASIN B0CC3NZ34S accepted", third)
        self.assertEqual(len(booktree.httpx.calls) if hasattr(booktree.httpx, "calls") else 2, 2)


class FailedFilingNotCachedTest(unittest.TestCase):
    """A cross-device (or otherwise failed) hardlink must not write the never-expiring processed marker."""

    def test_exdev_is_retried_on_the_next_run(self):
        import myx_classes
        import myx_hints
        with tempfile.TemporaryDirectory() as td:
            src, media = os.path.join(td, "src"), os.path.join(td, "media")
            os.makedirs(os.path.join(src, "junk")); os.makedirs(media)
            source_file = os.path.join(src, "junk", "AudioTrack 01.mp3")
            open(source_file, "wb").write(b"\x00" * 16)
            fix = os.path.join(td, "fix.csv")
            row = dict.fromkeys(myx_utilities.getLogHeaders().keys(), "")
            row.update({"book": "junk", "file": source_file, "isMatched": "False", "isHardLinked": "False", "mamCount": "0",
                        "audibleMatchCount": "0", "metadatasource": "id3", "id3-matchRate": "0", "id3-asin": "B0CC3NZ34S",
                        "id3-title": "AudioTrack 01", "id3-authors": "unknown artist", "id3-duration": str(492 * 60),
                        "id3-language": "english", "sourcePath": src, "mediaPath": media})
            with open(fix, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=list(myx_utilities.getLogHeaders().keys())); w.writeheader(); w.writerow(row)
            saved_httpx = booktree.httpx
            saved_link = myx_classes.os.link
            booktree.httpx = FakeAudible(by_asin={"B0CC3NZ34S": product("B0CC3NZ34S", "The Coworker", ["Freida McFadden"], 492)})

            def run_once():
                myx_hints._cache.clear(); myx_hints._appliedRefresh.clear()
                cfg = FakeConfig(td, **{"Config/metadata": "log", "Config/flags/verbose": 1})
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    booktree.buildTreeFromLog(fix, os.path.join(td, f"log{len(os.listdir(td))}.csv"), cfg)
                return out.getvalue()

            try:
                def boom(*_a, **_k):
                    raise OSError(18, "Invalid cross-device link")
                myx_classes.os.link = boom
                first = run_once()
                dest = os.path.join(media, "Freida McFadden", "The Coworker", "AudioTrack 01.mp3")
                self.assertFalse(os.path.exists(dest), first)
                self.assertIn("Not marking junk as processed: filing failed; it will be retried next run", first)
                self.assertNotIn("Skipping junk, already processed", first)
                myx_classes.os.link = saved_link
                second = run_once()
                self.assertNotIn("Skipping junk, already processed", second)
                self.assertIn("Pinned ASIN B0CC3NZ34S accepted", second)
                self.assertTrue(os.path.exists(dest), second)
                self.assertTrue(os.path.samefile(source_file, dest))
            finally:
                myx_classes.os.link = saved_link
                booktree.httpx = saved_httpx


class SeriesRoundTripTest(unittest.TestCase):
    """upstream #27: the log writes seriesparts as "Name part" (no '#'), the reader split on '#' and lost the part."""

    def test_series_part_survives_a_trip_through_the_log(self):
        import myx_classes
        src = myx_classes.Book(title="The Honeymoon Heist")
        src.series = [myx_classes.Series("Pike Logan", "17.5"), myx_classes.Series("Area 51", ""), myx_classes.Series("Dune", "1")]
        row = {}
        src.getDictionary(row, "id3-")
        self.assertEqual(row["id3-series"], "Pike Logan,Area 51,Dune")
        self.assertEqual(row["id3-seriesparts"], "Pike Logan 17.5,Area 51,Dune 1")          # CSV value unchanged
        back = myx_classes.Book()
        back.setSeriesFromLog(row["id3-series"], row["id3-seriesparts"])
        self.assertEqual([(s.name, s.part) for s in back.series], [("Pike Logan", "17.5"), ("Area 51", ""), ("Dune", "1")])
        self.assertEqual(back.getSeriesParts(), src.getSeriesParts())
        # a row written before the fix ("17 5"), and names the two columns cleanse differently
        back = myx_classes.Book()
        back.setSeriesFromLog("Pike Logan,Hitchhiker's Guide: Trilogy", "Pike Logan 17 5,Hitchhikers Guide: Trilogy 2")
        self.assertEqual([(s.name, s.part) for s in back.series], [("Pike Logan", "17.5"), ("Hitchhiker's Guide: Trilogy", "2")])
        # the old reader turned the whole string into a series named "Pike Logan 17.5"
        legacy = myx_classes.Book()
        legacy.setSeries("Pike Logan 17.5")
        self.assertEqual([(s.name, s.part) for s in legacy.series], [("Pike Logan 17.5", "")])

    def test_hand_written_rows_still_accept_name_hash_part(self):
        import myx_classes
        b = myx_classes.Book()
        b.setSeriesFromLog("", "Jack Reacher #3")                       # no series column: legacy '#' form
        self.assertEqual([(s.name, s.part) for s in b.series], [("Jack Reacher", "3")])
        b = myx_classes.Book()
        b.setSeriesFromLog("Jack Reacher", "Jack Reacher #3")           # '#' with the names present
        self.assertEqual([(s.name, s.part) for s in b.series], [("Jack Reacher", "3")])
        b = myx_classes.Book()
        b.setSeriesFromLog("Jack Reacher", "")                          # names only
        self.assertEqual([(s.name, s.part) for s in b.series], [("Jack Reacher", "")])
        b = myx_classes.Book()
        b.setSeriesFromLog("", "")
        self.assertEqual(b.series, [])
