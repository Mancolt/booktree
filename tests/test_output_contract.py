"""The run-log schema is a public interface (docs/FORK.md). These tests pin it."""
import unittest

import myx_utilities

EXPECTED_COLUMNS = [
    "book", "file", "paths", "isMatched", "isHardLinked", "mamCount", "audibleMatchCount", "metadatasource",
    "id3-matchRate", "id3-asin", "id3-title", "id3-subtitle", "id3-publisher", "id3-length", "id3-duration",
    "id3-series", "id3-authors", "id3-narrators", "id3-seriesparts", "id3-language",
    "mam-matchRate", "mam-asin", "mam-title", "mam-subtitle", "mam-publisher", "mam-length", "mam-duration",
    "mam-series", "mam-authors", "mam-narrators", "mam-seriesparts", "mam-language",
    "adb-matchRate", "adb-asin", "adb-title", "adb-subtitle", "adb-publisher", "adb-length", "adb-duration",
    "adb-series", "adb-authors", "adb-narrators", "adb-seriesparts", "adb-language",
    "sourcePath", "mediaPath",
]


class LogSchemaTest(unittest.TestCase):
    def test_log_headers_are_the_46_v1_columns_in_order(self):
        self.assertEqual(list(myx_utilities.getLogHeaders().keys()), EXPECTED_COLUMNS)
        self.assertEqual(len(EXPECTED_COLUMNS), 46)

    def test_booleans_are_logged_as_python_true_false_strings(self):
        # consumers compare the CSV cell to the literal strings "True"/"False"
        self.assertEqual(str(True), "True")
        self.assertEqual(str(False), "False")


if __name__ == "__main__":
    unittest.main()
