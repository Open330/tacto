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
    '0 Window  Title: 저장 검증.txt, Document: ' + unicodedata.normalize('NFD', PATH) + ' [focused]',
    '  1 ScrollArea',
    '    2 TextArea  Value: owned, Identifier: First Text View',
    '3 Window  Title: 저장 검증.txt, Document: /Users/x/other/저장 검증.txt',
    '  4 ScrollArea',
    '    5 TextArea  Value: personal, Identifier: First Text View',
])


class DocumentScopeTests(unittest.TestCase):
    def test_close_button_requires_its_exact_subrole_and_document(self):
        from tacto.flow import Flow
        tree='0 Window Title: one, Document: /owned/one.txt\n  1 Button Subrole: AXCloseButton\n  2 Button Subrole: AXMinimizeButton\n3 Window Title: two, Document: /owned/two.txt\n  4 Button Subrole: AXCloseButton'
        selector=Selector(role='Button', subrole='AXCloseButton', ancestor='/owned/one.txt')
        self.assertEqual(1,selector.resolve(tree)[0])
        with self.assertRaises(Exception): Selector(role='Button', subrole='AXCloseButton').resolve(tree)
        flow=Flow.from_json('{"schema":2,"name":"close-owned","target":"com.apple.TextEdit","steps":[{"action":"click","selector":{"role":"Button","subrole":"AXCloseButton","ancestor":"/owned/one.txt"}}]}')
        self.assertEqual('AXCloseButton', Flow.from_json(flow.to_json()).steps[0].selector.subrole)
        import json
        old=Flow.from_json('{"schema":2,"name":"old","target":"owned","steps":[{"action":"read","selector":{"role":"Window","title":"owned"}}]}')
        encoded=json.loads(old.to_json())
        self.assertNotIn('subrole',encoded['steps'][0]['selector'])
        from tacto.flow import _flow_json_value
        self.assertEqual({'subrole':None},_flow_json_value({'subrole':None}))

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

    def test_package_document_directory_marker_keeps_canonical_identity(self):
        import tempfile
        from pathlib import Path
        from tacto.native_document import _document_path, DocumentSnapshotError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); package=root/'owned.rtfd'; package.mkdir()
            self.assertEqual(str(package), _document_path((0, package.as_uri()+'/')))
            plain=root/'owned.txt'; plain.write_text('owned')
            for value in (plain.as_uri()+'/', package.as_uri()+'//', (root/'missing.rtfd').as_uri()+'/'):
                with self.assertRaises(DocumentSnapshotError): _document_path((0,value))

    def test_cli_prints_only_that_window_and_refuses_bad_arguments(self):
        class D:
            capabilities = {'evidence.document_text.path_selector', 'observe.document_token'}
            def document_text(self, target, *, document_path, max_utf16_units):
                if document_path != PATH or max_utf16_units != 1_000_000:
                    return {'type': 'error'}
                return {'type': 'document_text', 'document_path': PATH,
                        'document_token': 'owned', 'text': 'owned', 'text_complete': True}
            def observe(self, target, mode, *, document_token):
                if document_token != 'owned':
                    raise AssertionError('an unpinned app-wide read is forbidden')
                return {'type': 'observation', 'document_token': document_token,
                        'tree': document_window_view(TREE, PATH)}
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
