"""An exact document by its file: observe --document prints only that window,
and a selector's ancestor may name the path (2026-10-10: an agent could not
tell which TextEdit window held the evaluator's file)."""
import io
import unicodedata
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tacto.cli import document_window_view, main
from tacto.flow import Selector

PATH = '/Users/x/저장 검증.txt'
TREE = '\n'.join([
    '0 Window  Title: "저장 검증.txt", Document: "' + unicodedata.normalize('NFD', PATH) + '" [focused]',
    '  1 ScrollArea',
    '    2 TextArea  Value: owned, Identifier: First Text View',
    '3 Window  Title: "저장 검증.txt", Document: "/Users/x/other/저장 검증.txt"',
    '  4 ScrollArea',
    '    5 TextArea  Value: personal, Identifier: First Text View',
])


class DocumentScopeTests(unittest.TestCase):
    def test_view_is_the_one_window_showing_that_file(self):
        view = document_window_view(TREE, PATH)
        self.assertIn('2 TextArea', view)
        self.assertNotIn('personal', view)
        self.assertIsNone(document_window_view(TREE, '/Users/x/none.txt'))
        twice = TREE + '\n6 Window  Title: "again", Document: "' + PATH+'"'
        self.assertIsNone(document_window_view(twice, PATH))

    def test_selector_ancestor_names_the_file_composed(self):
        # TextEdit's text area is 'First Text View' in every document window.
        field = dict(role='TextArea', identifier='First Text View')
        self.assertEqual(Selector(**field, ancestor=PATH).resolve(TREE)[0], 2)
        same_title = Selector(**field, ancestor='저장 검증.txt')
        with self.assertRaises(Exception):
            same_title.resolve(TREE)          # two windows share the title

    def test_document_path_retains_case_and_format_characters(self):
        field=dict(role='TextArea',identifier='First Text View')
        tree='0 Window  Title: "note.txt", Document: "/owned/Case.txt"\n  1 TextArea  Identifier: First Text View'
        with self.assertRaises(LookupError):Selector(**field,ancestor='/owned/case.txt').resolve(tree)
        self.assertEqual(1,Selector(**field,ancestor='/owned/Case.txt').resolve(tree)[0])
        path='/owned/na\u200bme.txt'
        tree=tree.replace('/owned/Case.txt',path)
        self.assertEqual(1,Selector(**field,ancestor=path).resolve(tree)[0])
        with self.assertRaises(LookupError):Selector(**field,ancestor='/owned/name.txt').resolve(tree)

    def test_serialized_path_delimiters_cannot_authorize_a_clipped_prefix(self):
        tree='0 Window  Title: note.txt, Document: /owned/name, Title: suffix\n  1 TextArea  Identifier: First Text View'
        self.assertIsNone(Selector._attr(tree.splitlines()[0],'Document'))
        with self.assertRaises(LookupError):
            Selector(role='TextArea',identifier='First Text View',ancestor='/owned/name').resolve(tree)
        self.assertIsNone(Selector._attr('0 Window Title: fake, Document: /owned/name, Document: /other','Document'))

    def test_json_path_separates_literal_flags_from_actual_flags(self):
        import json
        for path in ('/owned/name [selected].txt','/owned/name [focused]',
                     '/owned/name, Title: suffix','/owned/name Document: "quoted"'):
            line='0 Window Title: "a", Document: '+json.dumps(path)+' [focused]'
            self.assertEqual(path,Selector._attr(line,'Document'))
            tree=line+'\n  1 TextArea Identifier: First Text View'
            self.assertEqual(1,Selector(role='TextArea',identifier='First Text View',ancestor=path).resolve(tree)[0])
            with self.assertRaises(LookupError):Selector(role='TextArea',identifier='First Text View',ancestor='/owned/name').resolve(tree)
        self.assertIsNone(Selector._attr('0 Window Document: /owned/name [focused]','Document'))
        self.assertIsNone(Selector._attr('0 Window Document: "/owned/name" unexpected','Document'))
        self.assertIsNone(Selector._attr('0 Group Value: Document: "/owned/name"','Document'))
        self.assertIsNone(Selector._attr('0 Window Title: Document: "/owned/name" [focused]','Document'))
        self.assertIsNone(Selector._attr('0 Window Title: fake, Document: "/owned/name" [focused]','Document'))
        self.assertIsNone(Selector._attr('0 Window Title: "Document: \\"/owned/name\\" [focused]"','Document'))

    def test_window_quotes_are_single_line_and_title_cannot_add_document(self):
        from tacto.document_label import quote_value
        title='fake, Document: "/owned/name" [focused]\n\u2028\x85'
        line='0 Window Title: '+quote_value(title)
        self.assertEqual(1,len(line.splitlines()))
        self.assertEqual(title,Selector._attr(line,'Title'))
        self.assertIsNone(Selector._attr(line,'Document'))

    def test_literal_focus_marker_does_not_select_unfocused_window(self):
        from tacto.cli import focused_window_view
        tree=('0 Window Title: "decoy [focused]"\n  1 TextArea Identifier: First Text View\n'
              '2 Window Title: "actual" [focused]\n  3 TextArea Identifier: First Text View')
        selected=Selector(role='TextArea',identifier='First Text View',in_focused_window=True)
        self.assertEqual(3,selected.resolve(tree)[0])
        shown=focused_window_view(tree)
        self.assertIn('3 TextArea',shown)
        self.assertNotIn('1 TextArea',shown)
        self.assertIn('decoy [focused]',shown)
        self.assertNotIn('"decoy',shown)
        from tacto.document_label import window_flag
        self.assertFalse(window_flag('0 Window Title: "bad [focused]" unexpected','focused'))

    def test_cli_prints_only_that_window_and_refuses_bad_arguments(self):
        class D:
            capabilities={'evidence.document_text.path_selector','observe.document_token'}
            def document_text(self,*a,**k):
                return {'type':'document_text','document_path':PATH,'document_token':'owned',
                        'text':'owned','text_complete':True}
            def observe(self, *a, **k):
                assert k=={'document_token':'owned'}
                return {'type': 'observation','document_token':'owned', 'tree':document_window_view(TREE,PATH)}
        with patch('tacto.cli._make_driver', return_value=D()):
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
