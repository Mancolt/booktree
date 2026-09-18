import os
import unittest

import myx_names as N
from tests.support import FakeConfig


def parse(name, known=(), file_name=None):
    return N.parseReleaseName(name, known_authors=known, file_name=file_name)


class ParseReleaseNameTest(unittest.TestCase):
    def check(self, name, title, authors=None, series=None, part=None, asin=None, known=()):
        p = parse(name, known)
        self.assertEqual(p["title"], title, name)
        if authors is not None:
            self.assertEqual(p["authors"], authors, name)
        if series is not None:
            self.assertEqual(p["series"], series, name)
        if part is not None:
            self.assertEqual(p["part"], part, name)
        if asin is not None:
            self.assertEqual(p["asin"], asin, name)
        return p

    def test_author_dash_title(self):
        self.check("Megan Fate Marshman - Relaxed.m4b", "Relaxed", ["Megan Fate Marshman"])
        self.check("Guy Sajer - The Forgotten Soldier.m4b", "The Forgotten Soldier", ["Guy Sajer"])
        self.check("Benjamin Hardy, Blake Erickson - The Science of Scaling.m4b", "The Science of Scaling", ["Benjamin Hardy", "Blake Erickson"])
        self.check("Code Red - Vince Flynn & Kyle Mills 2023 m4b", "Code Red", ["Vince Flynn", "Kyle Mills"])

    def test_title_dash_author(self):
        self.check("The Coworker - Freida McFadden.m4b", "The Coworker", ["Freida McFadden"])
        self.check("Just and Unjust Wars - Michael Walzer (M4B)", "Just and Unjust Wars", ["Michael Walzer"])
        self.check("The Athens Solution - Brad Thor (MP3)", "The Athens Solution", ["Brad Thor"])

    def test_title_by_author(self):
        self.check("Hunt, Gather, Parent by Michaeleen Doucleff.m4b", "Hunt, Gather, Parent", ["Michaeleen Doucleff"])
        self.check("Chip War by Chris Miller.m4b", "Chip War", ["Chris Miller"])

    def test_no_space_dash_between_author_and_title(self):
        self.check("Beth Brower-The Unselected Journals of Emma M. Lion Vol 4", "The Unselected Journals of Emma M. Lion", ["Beth Brower"], part="4")
        p = parse("Monica Wood - The One-in-a-Million Boy")
        self.assertEqual((p["title"], p["authors"]), ("The One-in-a-Million Boy", ["Monica Wood"]))

    def test_series_segments(self):
        self.check("Scot Harvath 02 - Path of the Assassin - Brad Thor", "Path of the Assassin", ["Brad Thor"], "Scot Harvath", "02")
        self.check("Brad Thor - Scot Harvath 21 - Rising Tiger", "Rising Tiger", ["Brad Thor"], "Scot Harvath", "21")
        self.check("Pike Logan 03 - Enemy of Mine - Brad Taylor", "Enemy of Mine", ["Brad Taylor"], "Pike Logan", "03")
        self.check("Tier One 06 - Collateral", "Collateral", [], "Tier One", "06")
        self.check("No Plan B  [Jack Reacher 27]", "No Plan B", [], "Jack Reacher", "27")
        self.check("Brad Thor - SH11.5  - Free Fall 64K", "Free Fall", ["Brad Thor"], part="11.5")
        self.check("Jeffrey Wilson, Brian Andrews - Insurgent, Tier One Book 10", "Insurgent", ["Jeffrey Wilson", "Brian Andrews"], "Tier One", "10")
        self.check("Book 1 - The Sandbox.m4b", "The Sandbox", [], part="1")
        self.check("Lee Child, Andrew Child - Jack Reacher 28 - The Secret, Scott Brick narrator m4b", "The Secret", ["Lee Child", "Andrew Child"], "Jack Reacher", "28")

    def test_ten_letter_words_are_not_asins(self):
        for word in ("UNABRIDGED", "DRAMATIZED", "COLLECTION", "AUDIOBOOKS", "Abcdefghij"):
            self.assertFalse(N.ASIN_RE.match(word), word)
        for ok in ("B09HY7C3BH", "b0fxhq85fy", "0593408357", "159463X"[:0] or "154914657X"):
            self.assertTrue(N.ASIN_RE.match(ok), ok)

    def test_asin_and_isbn_brackets(self):
        self.check("Becoming Your Own Banker [B09HY7C3BH]", "Becoming Your Own Banker", [], asin="B09HY7C3BH")
        self.check("Dave Portnoy - Cancel Me If You Can [B0FXHQ85FY]", "Cancel Me If You Can", ["Dave Portnoy"], asin="B0FXHQ85FY")
        self.check("Catastrophe 1914 - Max Hastings [0593408357]", "Catastrophe 1914", ["Max Hastings"], asin="0593408357")

    def test_noise_removed(self):
        self.check("Andrzej Sapkowski - Season of Storms (Unabridged)", "Season of Storms", ["Andrzej Sapkowski"], asin="")
        self.check("Alice Feeney - Sometimes I Lie [Unabridged]", "Sometimes I Lie", ["Alice Feeney"], asin="")
        self.check("Author Name - Some Title (Dramatized)", "Some Title", ["Author Name"], asin="")
        self.check("Author Name - Some Title (Collection)", "Some Title", ["Author Name"], asin="")
        self.check("Brad Thor - Takedown Unabridged - Complete", "Takedown", ["Brad Thor"])
        self.check("[M4B] Andy Weir - Project Hail Mary", "Project Hail Mary", ["Andy Weir"])
        self.check("John Milton - Paradise Lost [M4B]", "Paradise Lost", ["John Milton"])
        self.check("Freida McFadden - Dead Med 2024 m4b", "Dead Med", ["Freida McFadden"])
        self.check("JP Delaney - Playing Nice (2020)", "Playing Nice", ["JP Delaney"])
        self.check("Brad Thor - The Athena Project A Thriller", "The Athena Project", ["Brad Thor"])
        self.check("Past Tense -A Jack Reacher Novel", "Past Tense")
        self.check("A Wanted Man -A Jack Reacher Novel- (Audiobook)- By Lee Child-read by Dick Hill", "A Wanted Man", ["Lee Child"])

    def test_last_first_author_order(self):
        self.check("Thor, Brad - SH08 - The Apostle", "The Apostle", ["Brad Thor"], part="08")

    def test_leading_track_or_volume_numbers(self):
        self.check("01 Meditations.m4b", "Meditations", [], part="1")
        self.check("3 - Harry Potter and the Prisoner of Azkaban", "Harry Potter and the Prisoner of Azkaban", [], part="3")
        self.check("22.5 The Christmas Scorpion", "The Christmas Scorpion", [], part="22.5")
        self.check("12 Rules for Life - Jordan B. Peterson", "12 Rules for Life", ["Jordan B. Peterson"])   # a bare number is title

    def test_year_is_kept_when_it_is_the_whole_title(self):
        self.check("George Orwell - 1984", "1984", ["George Orwell"])
        self.check("Takedown (Book 3) - Brad Thor", "Takedown", ["Brad Thor"], series="", part="3")
        p = parse("Monica Wood – The One-in-a-Million Boy")          # en dash without surrounding spaces on one side
        self.assertEqual(p["authors"], ["Monica Wood"])

    def test_ambiguous_names_offer_the_swapped_reading(self):
        p = parse("Mad Mabel - Sally Hepworth.m4b")
        self.assertIn("alternative", p)
        readings = {(p["title"], tuple(p["authors"])), (p["alternative"]["title"], tuple(p["alternative"]["authors"]))}
        self.assertIn(("Mad Mabel", ("Sally Hepworth",)), readings)
        # with a known author from id3 there is no ambiguity
        p = parse("Mad Mabel - Sally Hepworth.m4b", known=["Sally Hepworth"])
        self.assertEqual((p["title"], p["authors"]), ("Mad Mabel", ["Sally Hepworth"]))
        self.assertNotIn("alternative", p)

    def test_disc_folders_use_the_file_name_and_release_folder(self):
        p = parse("cd1", file_name="Brad Thor - Takedown - Unabridged.mp3")
        self.assertEqual((p["title"], p["authors"]), ("Takedown", ["Brad Thor"]))

        class F:
            def __init__(self, fullPath):
                self.fullPath = fullPath
        self.assertEqual(N.releaseNameForBook([F("/src/Some Release/cd1/01.mp3")], "/src", "cd1"), "Some Release")
        self.assertEqual(N.releaseNameForBook([F("/src/Loose.m4b")], "/src", "Loose.m4b"), "Loose.m4b")
        self.assertEqual(N.releaseNameForBook([F("/elsewhere/x.m4b")], "/src", "fallback"), "fallback")

    def test_grouping_name_walks_past_disc_folders_and_keeps_releases_apart(self):
        src = "/data/downloads"
        self.assertEqual(N.groupingName(f"{src}/The Guest/cd1/d1.m4b", src, "cd1"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/The Guest/cd2/d2.m4b", src, "cd2"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/Along Came a Spider/cd1/d1.m4b", src, "cd1"), "Along Came a Spider")
        self.assertEqual(N.groupingName(f"{src}/The Guest/Disc 01/track.mp3", src, "Disc 01"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/The Guest/part 2/track.mp3", src, "part 2"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/The Guest/cd1/MP3/d1.mp3", src, "MP3"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/Along Came a Spider/cd1/MP3/d1.mp3", src, "MP3"), "Along Came a Spider")
        # a title folder under cd1/ is the release, not the ancestor above the disc
        self.assertEqual(
            N.groupingName(f"{src}/Patterson/cd1/Along Came a Spider/a.m4b", src, "Along Came a Spider"),
            "Along Came a Spider")
        # a normal Book/file.m4b or Author/Title/file.m4b still keys on the immediate parent
        self.assertEqual(N.groupingName(f"{src}/The Guest/book.m4b", src, "The Guest"), "The Guest")
        self.assertEqual(N.groupingName(f"{src}/Patterson/The Guest/book.m4b", src, "The Guest"), "The Guest")
        # a disc folder sitting at the source root stays itself; a loose file uses the fallback
        self.assertEqual(N.groupingName(f"{src}/cd1/track.m4b", src, "cd1"), "cd1")
        self.assertEqual(N.groupingName(f"{src}/loose.m4b", src, "loose.m4b"), "loose.m4b")
        self.assertEqual(N.groupingName("/elsewhere/x.m4b", src, "fallback"), "fallback")

    def test_unparseable_names_degrade_to_a_title(self):
        for name in ("BT-TFS", "Dempsey.m4b", "Ember", "The Rise and Fall of the Great Powers"):
            p = parse(name)
            self.assertTrue(p["title"], name)
            self.assertEqual(p["authors"], [], name)
        self.assertEqual(parse("")["title"], "")
        self.assertEqual(parse("(Unabridged)")["title"], "")

    def test_junk_detection(self):
        for t in ("", "AudioTrack 01", "Track 12", "Chapter 3", "cd1", "Unknown", "01", None):
            self.assertTrue(N.isJunkTitle(t), t)
        self.assertTrue(N.isJunkTitle("The Coworker - Freida McFadden", "The Coworker - Freida McFadden.m4b"))
        self.assertFalse(N.isJunkTitle("The Coworker", "The Coworker - Freida McFadden.m4b"))
        self.assertTrue(N.isJunkAuthors(["unknown artist"]))
        self.assertTrue(N.isJunkAuthors(["01"]))
        self.assertTrue(N.isJunkAuthors([]))
        self.assertFalse(N.isJunkAuthors(["unknown artist", "Brad Thor"]))

    def test_name_likeness(self):
        self.assertGreaterEqual(N.nameLikeness("Freida McFadden"), 0.9)
        self.assertGreaterEqual(N.nameLikeness("J. R. R. Tolkien"), 0.6)
        self.assertGreaterEqual(N.nameLikeness("Ursula K. Le Guin"), 0.6)
        self.assertEqual(N.nameLikeness("The Coworker"), 0.0)
        self.assertEqual(N.nameLikeness("SCARS John Dempsey"), 0.0)
        self.assertEqual(N.nameLikeness("Tier One 06"), 0.0)
        self.assertLess(N.nameLikeness("Killshot"), 0.6)


if __name__ == "__main__":
    unittest.main()


class HostileInputTest(unittest.TestCase):
    def test_junk_detection_is_linear_on_huge_tags(self):
        import time
        for value in (" " * 20000 + "x", "AudioTrack " + "0" * 20000, "a" * 20000):
            t0 = time.time()
            N.isJunkTitle(value)
            N.isJunkAuthors([value])
            self.assertLess(time.time() - t0, 0.5, "regex backtracking on a long tag")
        self.assertTrue(N.isJunkTitle("   AudioTrack 01   "))
        self.assertFalse(N.isJunkTitle("Real Title"))

    def test_parser_is_bounded_on_huge_names(self):
        import time
        for value in ("a" * 20000, "-" * 20000, " - " * 5000, "[" + "a" * 19998 + "]", "Author Name - " * 1000):
            t0 = time.time()
            p = N.parseReleaseName(value)
            self.assertLess(time.time() - t0, 0.5)
            self.assertIsInstance(p, dict)

    def test_odd_inputs_never_raise(self):
        for value in ("", "   ", "[]", "()", "[[[", "]]]", "[)(]", "-", " - ", "|", ";", "by", "by X", "Book 1", "\x00\x01",
                      "Jos\udce9 Author - Title.m4b", "Author - Title\n"):
            self.assertIsInstance(N.parseReleaseName(value), dict, repr(value))


class SecondPassTest(unittest.TestCase):
    def test_single_word_titles_lose_release_years_but_year_titles_survive(self):
        p = parse("Andy Weir - Artemis 2017")
        self.assertEqual((p["title"], p["authors"]), ("Artemis", ["Andy Weir"]))
        p = parse("Freida McFadden - Relaxed 2024 m4b")
        self.assertEqual(p["title"], "Relaxed")
        self.assertEqual(parse("George Orwell - 1984")["title"], "1984")
        self.assertEqual(parse("Frederick Kempe - Berlin 1961")["title"], "Berlin 1961")
        self.assertEqual(parse("Max Hastings - Catastrophe 1914")["title"], "Catastrophe 1914")

    def test_all_digit_titles_are_not_junk_unless_track_like(self):
        for real in ("1984", "1776", "2666", "11.22.63"):
            self.assertFalse(N.isJunkTitle(real), real)
        for junk in ("01", "007", "7", "42", "Track 12"):
            self.assertTrue(N.isJunkTitle(junk), junk)

    def test_large_numbers_are_titles_not_series_parts(self):
        p = parse("Fahrenheit 451 - Ray Bradbury")
        self.assertEqual((p["title"], p["authors"], p["series"]), ("Fahrenheit 451", ["Ray Bradbury"], ""))
        p = parse("Apollo 13 - Jim Lovell")                   # 13 could be a part; both readings are searched
        self.assertIn("Apollo", p["title"] + p["series"])

    def test_one_word_tag_does_not_count_as_overlap(self):
        self.assertFalse(N.authorsOverlap(["Child"], ["Lee Child"]))
        self.assertTrue(N.authorsOverlap(["Lee Child"], ["Child, Lee"]))
        self.assertTrue(N.authorsOverlap(["Andrew Child"], ["Lee Child", "Andrew Child"]))
        self.assertFalse(N.authorsOverlap(["Stephen Fry"], ["Stephen King"]))


class BookGroupingKeyTest(unittest.TestCase):
    """Hybrid-mode grouping: discs of one release are one book; two releases' cd1/ folders are not."""

    def _bf(self, rel, src="/data/downloads"):
        import myx_classes
        return myx_classes.BookFile(rel, f"{src}/{rel}", src, "/data/media")

    def test_two_releases_with_cd1_are_not_the_same_book(self):
        import booktree
        guest = self._bf("The Guest/cd1/d1.m4b")
        spider = self._bf("Along Came a Spider/cd1/d1.m4b")
        self.assertEqual(booktree.bookGroupingKey(guest), "The Guest")
        self.assertEqual(booktree.bookGroupingKey(spider), "Along Came a Spider")
        self.assertNotEqual(booktree.bookGroupingKey(guest), booktree.bookGroupingKey(spider))
        self.assertEqual(booktree.bookGroupingKey(self._bf("The Guest/cd2/d2.m4b")), "The Guest")

    def test_normal_and_author_title_layouts_and_multibook_are_unchanged(self):
        import booktree
        self.assertEqual(booktree.bookGroupingKey(self._bf("The Guest/book.m4b")), "The Guest")
        self.assertEqual(booktree.bookGroupingKey(self._bf("Patterson/The Guest/book.m4b")), "The Guest")
        loose = self._bf("loose.m4b")
        self.assertEqual(booktree.bookGroupingKey(loose), "loose.m4b")
        nested = self._bf("The Guest/cd1/d1.m4b")
        self.assertEqual(booktree.bookGroupingKey(nested, multibook=True), "d1.m4b")

    def test_pin_by_release_folder_sees_files_inside_cd1(self):
        import booktree
        import myx_hints
        myx_hints._appliedRefresh.clear()
        src = "/data/downloads"
        hints = {"The Guest": {"asin": "B0CC3NZ34S", "refresh": True}}
        full = f"{src}/The Guest/cd1/d1.m4b"
        self.assertTrue(booktree.refreshRequested(hints, full, src))
        self.assertFalse(booktree.refreshRequested({"Other Book": {"refresh": True}}, full, src))
        self.assertFalse(booktree.refreshRequested({}, full, src))

    def test_last_scan_of_one_new_disc_pulls_in_the_older_disc(self):
        import booktree
        import tempfile
        import time
        src = tempfile.mkdtemp()
        try:
            for rel in ("The Guest/cd1/d1.m4b", "The Guest/cd2/d2.m4b", "Other Book/file.m4b"):
                os.makedirs(os.path.join(src, os.path.dirname(rel)), exist_ok=True)
                with open(os.path.join(src, rel), "wb") as fh:
                    fh.write(b"x")
            old = time.time() - 3600
            new = time.time() + 10
            os.utime(os.path.join(src, "The Guest/cd1/d1.m4b"), (old, old))
            os.utime(os.path.join(src, "Other Book/file.m4b"), (old, old))
            os.utime(os.path.join(src, "The Guest/cd2/d2.m4b"), (new, new))
            last_run = time.time()
            files = ["The Guest/cd1/d1.m4b", "The Guest/cd2/d2.m4b", "Other Book/file.m4b"]
            hot = booktree.hotGroupingKeys(files, src, "/media", last_run, {}, multibook=False)
            self.assertEqual(hot, {"The Guest"})
            guest = [booktree.bookGroupingKey(self._bf(rel, src)) for rel in files if rel.startswith("The Guest")]
            self.assertEqual(set(guest), {"The Guest"})
            # only a mix of old + new files should bypass the processed marker
            mixed = [os.path.getmtime(os.path.join(src, rel)) <= last_run
                     for rel in ("The Guest/cd1/d1.m4b", "The Guest/cd2/d2.m4b")]
            self.assertTrue(any(mixed) and not all(mixed))
            all_new_cutoff = old - 10
            self.assertFalse(any(os.path.getmtime(os.path.join(src, rel)) <= all_new_cutoff
                                 for rel in ("The Guest/cd1/d1.m4b", "The Guest/cd2/d2.m4b")))
        finally:
            import shutil
            shutil.rmtree(src)


class DiscFolderTest(unittest.TestCase):
    def test_every_folder_grouped_as_a_disc_is_filed_into_a_disc_subfolder(self):
        import myx_utilities
        for folder in ("cd1", "CD 2", "Disc 1", "disc01", "disk 1", "Disk2", "Part 2", "part 10", "Title Disc 2"):
            self.assertTrue(N.DISC_FOLDER.match(folder) is None or myx_utilities.isMultiCD(folder), folder)
        for folder in ("cd1", "Disk 1", "Part 2", "Title Disc 2"):
            self.assertTrue(myx_utilities.isMultiCD(folder), folder)
        for folder in ("Counterpart 2", "Rampart 5", "Some Book", "Chapter 3", "1984"):
            self.assertFalse(myx_utilities.isMultiCD(folder), folder)

    def test_grouped_discs_get_distinct_target_folders(self):
        # Disk 1/01.mp3 and Disk 2/01.mp3 are one book since #15; they must not both target Author/Title/01.mp3
        import myx_classes
        book = myx_classes.Book(asin="B000000001", title="Title")
        book.authors = [myx_classes.Contributor("Author")]
        cfg = FakeConfig("/tmp", **{"Config/target_path/no_series": "{author}/{title}",
                                    "Config/target_path/disc_folder": "{title} {disc}"})
        targets = set()
        for disc in ("Disk 1", "Disk 2", "Part 1", "Part 2"):
            bf = myx_classes.BookFile(f"Title/{disc}/01.mp3", f"/dl/Title/{disc}/01.mp3", "/dl", "/lib")
            targets.add(bf.getConfigTargetPath(cfg, book))
        self.assertEqual(len(targets), 4)
        self.assertIn("/lib/Author/Title/Title Disk 1", targets)

