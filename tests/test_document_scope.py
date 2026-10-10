"""An exact document by its file: observe --document prints only that window,
and a selector's ancestor may name the path (2026-10-10: an agent could not
tell which TextEdit window held the evaluator's file)."""
import io
import unicodedata
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from nlc.cli import document_window_view, main
from nlc.flow import Selector

PATH = '/Users/x/저장 검증.txt'
TREE = '\n'.join([
    '0 Window  Title: 저장 검증.txt, Document: ' + unicodedata.normalize('NFD', PATH) + ' [focused]',
    '  1 ScrollArea',
    '    2 TextArea  Value: owned, Identifier: First Text View',
    '3 Window  Title: 저장 검증.txt, Document: /Users/x/other/저장 검증.txt',
    '  4 ScrollArea',
    '    5 TextArea  Value: personal, Identifier: First Text View',
])


class DocumentScopeTests(unittest.TestCase):
    def test_view_is_the_one_window_showing_that_file(self):
        view = document_window_view(TREE, PATH)
        self.assertIn('2 TextArea', view)
        self.assertNotIn('personal', view)
        self.assertIsNone(document_window_view(TREE, '/Users/x/none.txt'))
        twice = TREE + '\n6 Window  Title: again, Document: ' + PATH
        self.assertIsNone(document_window_view(twice, PATH))

    def test_selector_ancestor_names_the_file_composed(self):
        # TextEdit's text area is 'First Text View' in every document window.
        field = dict(role='TextArea', identifier='First Text View')
        self.assertEqual(Selector(**field, ancestor=PATH).resolve(TREE)[0], 2)
        same_title = Selector(**field, ancestor='저장 검증.txt')
        with self.assertRaises(Exception):
            same_title.resolve(TREE)          # two windows share the title

    def test_cli_prints_only_that_window_and_refuses_bad_arguments(self):
        class D:
            def observe(self, *a, **k):
                return {'type': 'observation', 'tree': TREE}
        with patch('nlc.cli._make_driver', return_value=D()):
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(['observe', 'com.apple.TextEdit', '--document', PATH])
            self.assertEqual(code, 0)
            self.assertNotIn('personal', out.getvalue())
            for argv in (['observe', 'com.apple.TextEdit', '--document'],
                         ['observe', 'com.apple.TextEdit', '--document', 'relative.txt'],
                         ['observe', 'com.apple.TextEdit', '--document', PATH, '--all-windows']):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(argv), 2, argv)
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['observe', 'com.apple.TextEdit', '--document', '/nope.txt']), 1)


if __name__ == '__main__':
    unittest.main()
