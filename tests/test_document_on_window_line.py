"""A window line names the file it shows (Document: <absolute path>), so an
agent can pass it to textedit_save; parsers stop values at it (2026-10-10)."""
import pathlib
import unittest

from tacto.driver import _document_path, _line_attr
from tacto.flow import Selector

ROOT = pathlib.Path(__file__).resolve().parents[1]
LINE = '0 Window  Title: "노트.txt", Identifier: "_NS:31", Document: "/tmp/a/노트.txt" [focused]'


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
                    "file:///a%0A.txt", "relative.txt", "file://[oops/a.txt", "file:///a%zz.txt",
                    "file:///a%FF.txt", "file:///.file/id=1"):
            self.assertIsNone(_document_path(bad), bad)

    def test_helper_renders_it_for_windows_only(self):
        swift = (ROOT / "helper/TactoHelper.swift").read_text()
        walker = swift[swift.index('func walk(_ el:'):swift.index('// MARK: - Session state')]
        self.assertIn('if role == kAXWindowRole as String, let doc = document,', walker)
        self.assertIn('let path = documentURLSyntaxPath(doc), !path.hasPrefix("/.file/")', walker)
        self.assertIn('precomposedStringWithCanonicalMapping', walker)



class SyntaxParityTests(unittest.TestCase):
    """The helper's and Python's syntax-only parsers agree, run for real."""
    CASES = ["file:///tmp/a.txt", "file://localhost/tmp/a.txt", "file://host/a.txt",
             "file://user@localhost/a.txt", "file://localhost:80/a.txt", "file:///a%zz.txt",
             "file:///a%.txt", "file:///a%FF.txt", "file://[oops/a.txt", "file:///a%2Fb.txt",
             "file:///a%00.txt", "file:///a%20b.txt", "file:///a.txt?x=1", "file:///a.txt#f",
             "https://x/a.txt", "file:////net/a.txt", "file:///%EB%85%B8%ED%8A%B8.txt", "",
             # Codex review of a33bcaa: case, escaped authority, leading space
             "FILE:///tmp/a.txt", "File:///tmp/a.txt", "file://%6cocalhost/tmp/a.txt",
             "file://local%68ost/tmp/a.txt", " file:///tmp/a.txt", "file://localhost",
             "file://LOCALHOST/tmp/a.txt"]

    def test_same_answers(self):
        import json, shutil, subprocess, tempfile
        if not shutil.which("swift"):
            self.skipTest("Swift required")
        from tacto.native_document import document_url_syntax_path
        swift = (ROOT / "helper/TactoHelper.swift").read_text()
        def fn(name):
            body = swift[swift.index(f"func {name}("):]
            return body[:body.index("\n}\n") + 3]
        code = "\n".join(fn(n) for n in ("documentEscapesAreWellFormed", "documentDecodePercent",
                                          "documentURLSyntaxPath"))
        harness = ("import Foundation\n" + code + "\nlet cases = " + json.dumps(self.CASES) +
                   "\nlet out = cases.map { documentURLSyntaxPath($0) ?? \"<nil>\" }\n"
                   "print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)\n")
        with tempfile.TemporaryDirectory(prefix="tacto-url-parity-") as d:
            src = pathlib.Path(d) / "main.swift"; src.write_text(harness, encoding="utf-8")
            run = subprocess.run(["swift", str(src)], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        helper = json.loads(run.stdout)
        python = [document_url_syntax_path(c) or "<nil>" for c in self.CASES]
        self.assertEqual(dict(zip(self.CASES, python)), dict(zip(self.CASES, helper)))
        self.assertEqual("/tmp/a.txt", python[0])
        self.assertEqual("/노트.txt", python[16])
        for bad in self.CASES[2:11] + self.CASES[12:16] + self.CASES[17:]:
            self.assertEqual("<nil>", dict(zip(self.CASES, python))[bad], bad)

if __name__ == "__main__":
    unittest.main()
