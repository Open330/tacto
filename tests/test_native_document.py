"""Document identity contract without macOS imports or actual GUI access."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import Mock

from nlc.native_document import TextEditDocumentReader, DocumentSnapshotError


class Adapter:
    def __init__(self):
        self.process = (10, 'launch-1')
        self.window, self.body = object(), object()
        self.pairs = [(self.window, self.body)]
        self.text = '한글😀'
        self.path = (-25212, None)
        self.range_hook = None
        self.range_result = None

    def process_identity(self, target):
        return self.process

    def documents(self, target):
        return list(self.pairs)

    def equal(self, a, b):
        return a is b

    def read_attribute(self, element, name):
        if name == 'AXDocument':
            return self.path
        return (0, self.text if name == 'AXValue' else len(self.text.encode('utf-16-le')) // 2)

    def read_range(self, element, start, length):
        if self.range_hook:
            self.range_hook()
        return self.range_result if self.range_result is not None else (0, self.text)


class NativeDocumentTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.reader = TextEditDocumentReader(self.adapter)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name).resolve() / '한글 file.rtf'

    def test_unicode_unsaved_and_saved_path_with_edits_between_reads(self):
        first = self.reader.read()
        self.assertEqual(first['text'], '한글😀')
        self.assertIsNone(first['document_path'])
        self.adapter.text = ''
        self.adapter.path = (0, self.path.as_uri())
        second = self.reader.read(document_token=first['document_token'])
        self.assertEqual(second['text'], '')
        self.assertEqual(second['document_path'], str(self.path))
        self.assertEqual(second['type'], 'document_text')
        self.assertNotIn('source_format', second)

    def test_multiple_documents_need_existing_identity_token(self):
        token = self.reader.read()['document_token']
        self.adapter.pairs.append((object(), object()))
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read()
        self.assertEqual(self.reader.read(document_token=token)['document_token'], token)

    def test_replacement_window_body_and_process_restart_invalidate_token(self):
        for change in ('window', 'body', 'restart'):
            with self.subTest(change=change):
                adapter = Adapter()
                reader = TextEditDocumentReader(adapter)
                token = reader.read()['document_token']
                if change == 'restart':
                    adapter.process = (10, 'launch-2')
                else:
                    adapter.pairs = [(object(), adapter.body)] if change == 'window' else [(adapter.window, object())]
                with self.assertRaises(DocumentSnapshotError):
                    reader.read(document_token=token)
                adapter.pairs, adapter.process = [(adapter.window, adapter.body)], (10, 'launch-1')
                with self.assertRaises(DocumentSnapshotError):
                    reader.read(document_token=token)

    def test_session_and_bounded_token_expiration(self):
        reader = TextEditDocumentReader(self.adapter, max_tokens=1)
        old = reader.read()['document_token']
        new = reader.read()['document_token']
        with self.assertRaises(DocumentSnapshotError):
            reader.read(document_token=old)
        with self.assertRaises(DocumentSnapshotError):
            TextEditDocumentReader(self.adapter).read(document_token=new)

    def test_url_rejection_and_read_failure_are_not_unsaved(self):
        for value in ('', '/tmp/x', 'https://example.com/a', 'file://remote/tmp/x',
                      'file://user@localhost/tmp/x', 'file:///tmp/x?x', 'file:///tmp/x#f',
                      'file:///tmp/%00', 'file:///tmp/%2fx', 'file:///tmp/../x',
                      'file:///tmp/%zz', 'file:///tmp/%FF'):
            self.adapter.path = (0, value)
            with self.subTest(value=value), self.assertRaises(DocumentSnapshotError):
                self.reader.read()
        for result in ((-25204, ''), (0, None), (True, ''), None):
            self.adapter.path = result
            with self.subTest(result=result), self.assertRaises(DocumentSnapshotError):
                self.reader.read()

    def test_mid_read_identity_and_path_races_issue_no_token(self):
        for change in ('window', 'process', 'path'):
            adapter = Adapter()
            reader = TextEditDocumentReader(adapter)
            def mutate():
                if change == 'window':
                    adapter.pairs = [(object(), adapter.body)]
                elif change == 'process':
                    adapter.process = (11, 'launch-2')
                else:
                    adapter.path = (0, self.path.as_uri())
            adapter.range_hook = mutate
            with self.subTest(change=change), self.assertRaises(DocumentSnapshotError):
                reader.read()
            self.assertEqual(len(reader._tokens), 0)

    def test_partial_text_and_failed_enumeration_issue_no_token(self):
        self.adapter.range_result = (0, '한')
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read()
        self.assertEqual(len(self.reader._tokens), 0)
        self.adapter.range_result = None
        def unavailable(target):
            raise RuntimeError('enumeration incomplete')
        self.adapter.documents = unavailable
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read()

    def test_wrong_application_is_rejected(self):
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read('other.app')

    def test_invalid_budget_is_rejected_before_any_adapter_call(self):
        adapter = Mock()
        reader = TextEditDocumentReader(adapter)
        for budget in (1000001, 10**100, -1, True, False, 1.0, None, '10'):
            with self.subTest(budget=budget), self.assertRaises(DocumentSnapshotError):
                reader.read(max_utf16_units=budget)
        self.assertEqual(adapter.mock_calls, [])

    def test_zero_budget_accepts_only_empty_document(self):
        self.adapter.text = ''
        self.assertEqual(self.reader.read(max_utf16_units=0)['text'], '')
        self.adapter.text = 'x'
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read(max_utf16_units=0)


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class DriverDocumentTextTests(unittest.TestCase):
    def test_driver_returns_complete_text_without_advertising_save_snapshot(self):
        from nlc.driver import Driver
        driver = Driver.__new__(Driver)  # No initialization or live UI calls.
        driver._document_reader = TextEditDocumentReader(Adapter())
        result = driver.document_text('com.apple.TextEdit')
        self.assertEqual(result['type'], 'document_text')
        self.assertEqual(result['text'], '한글😀')
        self.assertTrue(result['text_complete'])
        self.assertNotIn('evidence.document_snapshot', driver.capabilities)

    def test_driver_wraps_adapter_failure_without_partial_complete_result(self):
        from nlc.driver import Driver
        driver = Driver.__new__(Driver)
        adapter = Adapter()
        def failed(target):
            raise RuntimeError('native enumeration unavailable')
        adapter.documents = failed
        driver._document_reader = TextEditDocumentReader(adapter)
        result = driver.document_text('com.apple.TextEdit')
        self.assertEqual(result['error'], 'document_snapshot_unavailable')
        self.assertEqual(result['effect'], 'not_started')
        self.assertNotIn('text_complete', result)
        self.assertNotIn('text', result)


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class NativeFileReferenceTests(unittest.TestCase):
    def test_reference_tracks_renamed_owned_file(self):
        from Foundation import NSURL
        from nlc.native_document_ax import resolve_document_url
        from nlc.native_document import _document_path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve() / '원본 😀.rtf'
            path.write_text('owned fixture')
            ref = NSURL.fileURLWithPath_(str(path)).fileReferenceURL()
            self.assertIsNotNone(ref)
            raw = str(ref.absoluteString())
            self.assertTrue(ref.isFileReferenceURL())
            self.assertEqual(_document_path((0, resolve_document_url(raw))), str(path))
            renamed = path.with_name('변경 😀.rtf')
            path.rename(renamed)
            self.assertEqual(_document_path((0, resolve_document_url(raw))), str(renamed))
            renamed.unlink()
            with self.assertRaises((RuntimeError, DocumentSnapshotError)):
                resolve_document_url(raw)

    def test_path_urls_and_malformed_inputs_keep_existing_contract(self):
        from nlc.native_document_ax import resolve_document_url
        with tempfile.TemporaryDirectory() as directory:
            missing = (Path(directory).resolve() / 'missing.rtf').as_uri()
            self.assertEqual(resolve_document_url(missing), missing)
        for raw in ('file:///.file/../etc/passwd', 'file:///.file/%2e%2e/etc/passwd',
                    'file://remote/.file/id=1.2', 'file:///.file/%zz',
                    'file:///.file/id=1.2?query', 'https://example.com/x'):
            with self.subTest(raw=raw), self.assertRaises(DocumentSnapshotError):
                resolve_document_url(raw)
