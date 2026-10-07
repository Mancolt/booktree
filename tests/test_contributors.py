"""Empty contributor names: a blank composer/artist tag or a stray comma must not become Contributor("") and must
never reach Audible as narrator/author `""` (seen with Before She Knew Him, whose empty composer tag made the
catalog search return nothing)."""
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch

import myx_audible
import myx_classes
import myx_mam
import myx_utilities
from tests.support import FakeAudible, FakeConfig, product


def probed(tags, duration=600.0):
    """BookFile.ffprobe over canned ffprobe output (no ffprobe binary, no file)."""
    bf = myx_classes.BookFile("Release/file.m4b", "/src/Release/file.m4b", "/src", "/media")
    with patch.object(myx_classes.BookFile, "__probe_file__", return_value={"format": {"duration": duration, "tags": tags}}):
        return bf.ffprobe("Release")


def names(people):
    return [p.name for p in people]


class ContributorsHelperTest(unittest.TestCase):
    def test_names_empty_once_cleansed_are_dropped(self):
        # cleanseAuthor turns "." into a space and drops "'", so these are empty names too
        self.assertEqual(myx_classes.contributors(["", " ", ".", " . ", "'", None]), [])

    def test_real_names_are_kept_as_given(self):
        self.assertEqual(names(myx_classes.contributors(["Peter Swanson", " Val McDermid"])), ["Peter Swanson", " Val McDermid"])

    def test_clean_function_runs_before_the_emptiness_check(self):
        self.assertEqual(myx_classes.contributors(["[GraphicAudio]"], myx_utilities.removeGA), [])


class SettersTest(unittest.TestCase):
    def test_set_narrators_skips_empty_tokens(self):
        b = myx_classes.Book()
        b.setNarrators(" , ,")
        self.assertEqual(b.narrators, [])
        b.setNarrators("Sophie Amoss, , Graham Halstead,")
        self.assertEqual(names(b.narrators), ["Sophie Amoss", " Graham Halstead"])

    def test_set_authors_skips_empty_tokens(self):
        b = myx_classes.Book()
        b.setAuthors("A Writer, , B Writer")
        self.assertEqual(len(b.authors), 2)
        self.assertEqual(b.getAuthors(), "A Writer,B Writer")


class GetListTest(unittest.TestCase):
    def test_no_enclosed_empty_token(self):
        people = [myx_classes.Contributor(""), myx_classes.Contributor("."), myx_classes.Contributor("A Writer"),
                  myx_classes.Contributor(" ")]
        self.assertEqual(myx_utilities.getList(people, "|", '"'), '"A Writer"')
        self.assertEqual(myx_utilities.getList([myx_classes.Contributor("")], "|", '"'), "")

    def test_empty_series_name_is_skipped(self):
        series = [myx_classes.Series("", "1"), myx_classes.Series("Saga", "2")]
        self.assertEqual(myx_utilities.getList(series, ",", ""), "Saga")

    def test_book_getters_never_return_an_enclosed_empty_name(self):
        b = myx_classes.Book()
        b.narrators = [myx_classes.Contributor("")]
        b.authors = [myx_classes.Contributor(" "), myx_classes.Contributor("Peter Swanson")]
        self.assertEqual(b.getNarrators(delimiter="|", encloser='"'), "")
        self.assertEqual(b.getAuthors(delimiter="|", encloser='"'), '"Peter Swanson"')


class FfprobeTest(unittest.TestCase):
    def test_empty_composer_gives_no_narrators(self):
        book = probed({"title": "Before She Knew Him", "artist": "Peter Swanson", "composer": ""})
        self.assertEqual(book.narrators, [])
        self.assertEqual(names(book.authors), ["Peter Swanson"])

    def test_blank_artist_and_stray_commas(self):
        book = probed({"title": "T", "artist": "  ", "composer": "Reader One, , Reader Two (Narrator)"})
        self.assertEqual(book.authors, [])
        self.assertEqual([n.strip() for n in names(book.narrators)], ["Reader One", "Reader Two"])

    def test_empty_composer_does_not_reach_audible_as_quoted_empty_narrator(self):
        """The reported case end to end: the catalog query must carry narrator '' (Audible then finds the book),
        not '\"\"' (Audible answers 0 results)."""
        book = probed({"title": "Before She Knew Him", "artist": "Peter Swanson", "composer": ""}, duration=615 * 60)
        mb = myx_classes.MAMBook("Peter Swanson - Before She Knew Him")
        bf = myx_classes.BookFile("Peter Swanson - Before She Knew Him/Peter Swanson - Before She Knew Him.m4b",
                                  "/src/x.m4b", "/src", "/media")
        bf.ffprobeBook = book
        mb.files.append(bf)
        mb.ffprobeBook = book
        client = FakeAudible(search=[product("0062838180", "Before She Knew Him", ["Peter Swanson"], 615)])
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as out:
            #narrators are only sent to Audible with add_narrators on (as in the config the report came from)
            mb.getAudibleBooks(client, book, FakeConfig(tmp, **{"Config/flags/add_narrators": 1}))
        searches = [p for (url, p) in client.calls if url.endswith("/catalog/products")]
        self.assertTrue(searches)
        self.assertEqual(searches[0]["narrator"], "")
        for params in searches:
            self.assertNotIn('""', params.get("narrator", ""))
            self.assertNotIn('""', params.get("author", ""))
        self.assertNotIn('narrators:""', out.getvalue())
        self.assertEqual(mb.bestAudibleMatch.asin, "0062838180")


class MAMBookFfprobeTest(unittest.TestCase):
    """MAMBook.ffprobe is not called by booktree.py (BookFile.ffprobe is the live parser); upstream appended a
    nameless Contributor() there, which raised TypeError for any file with an artist tag."""

    def run_ffprobe(self, tags):
        with patch.object(myx_utilities, "probe_file", return_value={"format": {"tags": tags}}):
            return myx_classes.MAMBook("Release").ffprobe("/src/Release/file.m4b")

    def test_authors_are_named(self):
        book = self.run_ffprobe({"title": "T", "artist": "Writer One, [GraphicAudio], Writer Two (Author)", "composer": ", Reader (Narrator)"})
        self.assertEqual(names(book.authors), ["Writer One", "Writer Two"])
        self.assertEqual([n.strip() for n in names(book.narrators)], ["Reader"])

    def test_empty_tags_give_empty_lists(self):
        book = self.run_ffprobe({"title": "T", "artist": "", "composer": " "})
        self.assertEqual((book.authors, book.narrators), ([], []))


class RemoteNamesTest(unittest.TestCase):
    """An empty name from Audible or MAM is dropped too: an empty id3 author and an empty result author would
    otherwise compare equal in isThisMyAuthorsBook."""

    def test_audible_product_with_empty_narrator(self):
        p = product("B000000001", "T", ["Writer", ""], 100, narrators=[{"name": ""}, {"name": "Reader"}])
        book = myx_audible.product2Book(p)
        self.assertEqual(names(book.authors), ["Writer"])
        self.assertEqual(names(book.narrators), ["Reader"])

    def test_mam_result_with_empty_author(self):
        row = {"id": 1, "title": "T", "author_info": json.dumps({"1": "", "2": "Writer"}),
               "narrator_info": json.dumps({"3": " "}), "my_snatched": 1}
        with patch.object(myx_mam, "searchMAM", return_value=[row]):
            books = myx_mam.getMAMBook(FakeConfig("/nonexistent"), titleFilename="T.m4b")
        self.assertEqual(names(books[0].authors), ["Writer"])
        self.assertEqual(books[0].narrators, [])


if __name__ == "__main__":
    unittest.main()
