"""A window line names the file it shows (Document: <absolute path>), so an
agent can pass it to textedit_save; parsers stop values at it (2026-10-10)."""
import pathlib
import unittest

from nlc.driver import _document_path, _line_attr
from nlc.flow import Selector

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINE = "0 Window  Title: 노트.txt, Identifier: _NS:31, Document: /tmp/a/노트.txt [focused]"


class DocumentOnWindowLineTests(unittest.TestCase):
    def test_parsers_separate_document_from_other_values(self):
        for parse in (Selector._attr, _line_attr):
            self.assertEqual("_NS:31", parse(LINE, "Identifier"))
            self.assertEqual("/tmp/a/노트.txt", parse(LINE, "Document"))
            self.assertEqual("노트.txt", parse(LINE, "Title"))

    def test_only_local_file_urls_become_paths(self):
        import unicodedata
        decomposed = "file:///tmp/" + unicodedata.normalize("NFD", "노트") + ".txt"
        self.assertEqual("/tmp/노트.txt", _document_path(decomposed))
        self.assertEqual("/tmp/a b.txt", _document_path("file:///tmp/a%20b.txt"))
        for bad in ("https://x/a.txt", "file://host/a.txt", "file:///a.txt?x=1",
                    "file:///a%0A.txt", "relative.txt"):
            self.assertIsNone(_document_path(bad), bad)

    def test_helper_renders_it_for_windows_only(self):
        swift = (ROOT / "helper/NLCHelper.swift").read_text()
        self.assertIn('if role == kAXWindowRole as String, let doc = str(el, "AXDocument")', swift)
        self.assertIn('url.isFileURL, url.query == nil, url.fragment == nil', swift)
        self.assertIn('precomposedStringWithCanonicalMapping', swift)


if __name__ == "__main__":
    unittest.main()
