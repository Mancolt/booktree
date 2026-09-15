"""metadata.opf must be well-formed XML for any metadata (docs/FORK.md roadmap item 1).

Audiobookshelf discards the whole OPF, ASIN included, when one text node contains a bare '&'.
"""
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET

import myx_classes
import myx_utilities

NS = {"dc": "http://purl.org/dc/elements/1.1/", "opf": "http://www.idpf.org/2007/opf"}


def make_book(**overrides):
    book = myx_classes.Book(asin="B0TEST0001", title="Plain Title", subtitle="", publisher="Plain House",
                            language="english", description="A plain description.")
    book.publishYear = "2024-05-01T00:00:00Z"
    book.authors = [myx_classes.Contributor("Plain Author")]
    book.narrators = [myx_classes.Contributor("Plain Narrator")]
    book.series = [myx_classes.Series("Plain Series", "1")]
    book.genres = ["Plain Genre"]
    book.tags = ["Plain Tag"]
    for k, v in overrides.items():
        setattr(book, k, v)
    return book


def parse(text):
    return ET.fromstring(text.encode("utf-8"))


def texts(root, xpath):
    return [e.text or "" for e in root.findall(xpath, NS)]


class OpfEscapingTest(unittest.TestCase):
    def test_ampersand_in_publisher_the_tipping_point_case(self):
        # through the public entry point, as createHardLinks calls it: this is the file Audiobookshelf rejected
        with tempfile.TemporaryDirectory() as td:
            myx_utilities.createOPF(make_book(publisher="Little, Brown & Company"), td)
            with open(os.path.join(td, "metadata.opf"), encoding="utf-8") as fh:
                root = parse(fh.read())
        self.assertEqual(texts(root, ".//dc:publisher"), ["Little, Brown & Company"])
        self.assertEqual(texts(root, ".//dc:identifier"), ["B0TEST0001"])

    def test_metadata_containing_a_template_token_is_not_resubstituted(self):
        # second-order injection: an author literally named "__DESCRIPTION__" must not be replaced by the
        # description (CDATA-encoded, not entity-escaped) and so smuggle elements such as a second ASIN
        book = make_book(authors=[myx_classes.Contributor("__DESCRIPTION__")],
                         description='</dc:creator><dc:identifier opf:scheme="ASIN">B0EVIL0000</dc:identifier><dc:creator opf:role="aut">Evil',
                         title="__SERIES__", subtitle="__GENRES__ and __TAGS__ and __ASIN__")
        root = parse(myx_utilities.renderOPF(book))
        self.assertEqual(texts(root, ".//dc:identifier"), ["B0TEST0001"])
        self.assertEqual([e.text for e in root.findall(".//dc:creator", NS) if e.get("{%s}role" % NS["opf"]) == "aut"],
                         ["__DESCRIPTION__"])
        self.assertEqual(texts(root, ".//dc:title"), ["__SERIES__"])
        self.assertEqual(texts(root, ".//dc:subtitle"), ["__GENRES__ and __TAGS__ and __ASIN__"])
        self.assertEqual(len(root.findall(".//dc:subject", NS)), 1)

    def test_boundary_and_type_edge_cases_stay_well_formed(self):
        cases = [
            dict(description="]]>"), dict(description="]]>x"), dict(description="x]]>"), dict(description="]]>]]>"),
            dict(description="a]]]]>b"), dict(description="]"), dict(description="]]"), dict(genres=["]]"], tags=["]"]),
            dict(title=None, subtitle=None, publisher=None, asin=None, language=None, description=None, publishYear=None),
            dict(series=[myx_classes.Series("S", 3)]), dict(series=[myx_classes.Series("S", 14.5)]),
            dict(title="a\udcffb"),                       # lone surrogate from a bad decode must not break utf-8 output
            dict(publishYear="19"), dict(publishYear=""),
        ]
        for overrides in cases:
            with self.subTest(**{k: repr(v)[:30] for k, v in overrides.items()}):
                text = myx_utilities.renderOPF(make_book(**overrides))
                text.encode("utf-8")
                root = parse(text)
                if overrides.get("description"):
                    self.assertIn(overrides["description"], texts(root, ".//dc:description")[0])
        self.assertEqual(texts(parse(myx_utilities.renderOPF(make_book(publishYear=None))), ".//dc:date"), [""])
        self.assertEqual(texts(parse(myx_utilities.renderOPF(make_book(title="a\udcffb"))), ".//dc:title"), ["ab"])

    def test_angle_brackets_and_quotes_in_title_author_narrator_subtitle(self):
        book = make_book(title='Tipping <Point> & "Quotes"', subtitle="It's <not> 'simple'",
                         authors=[myx_classes.Contributor("O'Brien & Sons <Ltd>"), myx_classes.Contributor('Jane "JD" Doe')],
                         narrators=[myx_classes.Contributor("R&B <Voice>")])
        root = parse(myx_utilities.renderOPF(book))
        self.assertEqual(texts(root, ".//dc:title"), ['Tipping <Point> & "Quotes"'])
        self.assertEqual(texts(root, ".//dc:subtitle"), ["It's <not> 'simple'"])
        creators = {e.get("{%s}role" % NS["opf"]): [] for e in root.findall(".//dc:creator", NS)}
        for e in root.findall(".//dc:creator", NS):
            creators[e.get("{%s}role" % NS["opf"])].append(e.text)
        self.assertEqual(creators["aut"], ["O'Brien & Sons <Ltd>", 'Jane "JD" Doe'])
        self.assertEqual(creators["nrt"], ["R&B <Voice>"])

    def test_quotes_in_series_attributes(self):
        book = make_book(series=[myx_classes.Series("Tom's \"Great\" <Series> & More", "2.5")])
        root = parse(myx_utilities.renderOPF(book))
        metas = {m.get("name"): m.get("content") for m in root.iter("{http://www.idpf.org/2007/opf}meta")}
        self.assertEqual(metas["calibre:series"], "Tom's \"Great\" <Series> & More")
        self.assertEqual(metas["calibre:series_index"], "2.5")

    def test_cdata_terminator_in_description_genre_and_tag(self):
        book = make_book(description="Ends a CDATA ]]> early & continues", genres=["Sci]]>Fi"], tags=["a]]>b"])
        root = parse(myx_utilities.renderOPF(book))
        self.assertIn("Ends a CDATA ]]> early & continues", texts(root, ".//dc:description")[0])
        self.assertEqual(texts(root, ".//dc:subject"), ["Sci]]>Fi"])
        self.assertEqual(texts(root, ".//dc:tag"), ["a]]>b"])

    def test_backslash_sequences_no_longer_break_rendering(self):
        # upstream used re.sub with the metadata as the replacement string: '\1' raised "invalid group reference"
        book = make_book(description="Chapter \\1 and a path C:\\new\\folder", title="Back\\slash")
        root = parse(myx_utilities.renderOPF(book))
        self.assertEqual(texts(root, ".//dc:title"), ["Back\\slash"])
        self.assertIn("C:\\new\\folder", texts(root, ".//dc:description")[0])

    def test_invalid_xml_control_characters_are_stripped(self):
        book = make_book(title="Bell\x07 and NUL\x00 kept\ttab", description="form\x0cfeed")
        text = myx_utilities.renderOPF(book)
        root = parse(text)
        self.assertEqual(texts(root, ".//dc:title"), ["Bell and NUL kept\ttab"])
        self.assertIn("formfeed", texts(root, ".//dc:description")[0])

    def test_unicode_round_trips(self):
        book = make_book(title="Café Ærø – 日本語", authors=[myx_classes.Contributor("Zoë Brontë")])
        root = parse(myx_utilities.renderOPF(book))
        self.assertEqual(texts(root, ".//dc:title"), ["Café Ærø – 日本語"])
        self.assertEqual(texts(root, ".//dc:creator"), ["Zoë Brontë", "Plain Narrator"])

    def test_plain_metadata_renders_exactly_as_upstream_did(self):
        # no special characters: byte-identical to the upstream template expansion, so existing libraries do not churn
        text = myx_utilities.renderOPF(make_book())
        self.assertIn("    <dc:title>Plain Title</dc:title>\n", text)
        self.assertIn("    <dc:description><![CDATA[ A plain description. ]]></dc:description>\n", text)
        self.assertIn("\t<dc:creator opf:role='aut'>Plain Author</dc:creator>\n", text)
        self.assertIn("\t<ns0:meta name='calibre:series' content='Plain Series' />\n", text)
        self.assertIn("    <dc:date>2024</dc:date>", text)
        self.assertIn("\t<dc:subject><![CDATA[Plain Genre]]></dc:subject>\n", text)

    def test_createOPF_writes_file_independent_of_cwd(self):
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as td:
            try:
                os.chdir(td)          # upstream resolved the template relative to the cwd and failed elsewhere
                myx_utilities.createOPF(make_book(publisher="A & B"), td)
            finally:
                os.chdir(cwd)
            with open(os.path.join(td, "metadata.opf"), encoding="utf-8") as fh:
                root = parse(fh.read())
        self.assertEqual(texts(root, ".//dc:publisher"), ["A & B"])


if __name__ == "__main__":
    unittest.main()
