"""Document identity contract without macOS imports or actual GUI access."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

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

    def test_expired_caller_deadline_reads_no_document_and_issues_no_token(self):
        from nlc.model_budget import model_deadline
        self.adapter.process_identity = Mock(side_effect=AssertionError('must not read'))
        with patch('nlc.native_document.time.monotonic', return_value=10), model_deadline(9):
            with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                self.reader.read()
        self.adapter.process_identity.assert_not_called()
        self.assertFalse(self.reader._tokens)

    def test_late_range_read_is_rejected_without_further_reads_or_token(self):
        clock = [10.0]
        reads = []
        original = self.adapter.read_attribute
        def read(element, name):
            reads.append(name)
            return original(element, name)
        self.adapter.read_attribute = read
        self.adapter.range_hook = lambda: clock.__setitem__(0, 13.01)
        with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaises(DocumentSnapshotError):
                self.reader.read()
        self.assertEqual(reads, ['AXDocument', 'AXNumberOfCharacters', 'AXValue'])
        self.assertFalse(self.reader._tokens)

    def test_caller_budget_is_not_extended_and_scope_restores_after_timeout(self):
        from nlc.model_budget import model_deadline, remaining_model_seconds
        clock = [10.0]
        self.adapter.range_hook = lambda: clock.__setitem__(0, 10.2)
        with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
            with model_deadline(10.1):
                with self.assertRaises(DocumentSnapshotError):
                    self.reader.read()
            self.assertEqual(remaining_model_seconds(3), 3)
            self.adapter.range_hook = None
            self.assertEqual(self.reader.read()['text'], '한글😀')

    def test_lock_wait_uses_operation_budget_and_reads_nothing_after_expiry(self):
        clock = [10.0]
        class DelayedLock:
            def __enter__(self):
                clock[0] = 14.0
            def __exit__(self, *args):
                pass
        self.reader._lock = DelayedLock()
        self.adapter.process_identity = Mock(side_effect=AssertionError('must not read'))
        with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                self.reader.read()
        self.adapter.process_identity.assert_not_called()
        self.assertFalse(self.reader._tokens)

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

    def test_scoped_observations_expired_before_any_adapter_read(self):
        from nlc.model_budget import model_deadline
        token = self.reader.read()['document_token']
        self.adapter.process_identity = Mock(side_effect=AssertionError('must not read'))
        for operation in (lambda: self.reader.window_for_token('com.apple.TextEdit', token),
                          lambda: self.reader.save_directory(document_token=token),
                          lambda: self.reader.save_panel(document_token=token),
                          lambda: self.reader.save_panel(document_token=token, witness=object())):
            with self.subTest(operation=operation), patch('nlc.native_document.time.monotonic', return_value=10), model_deadline(9):
                with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                    operation()
        self.adapter.process_identity.assert_not_called()
        self.assertIn(token, self.reader._tokens)

    def test_late_panel_read_and_revalidation_do_not_return_witness_or_read_again(self):
        token = self.reader.read()['document_token']
        clock = [10.0]
        witness = object()
        for operation, callback in (('save_directory', 'save_directory'),
                                    ('save_panel', 'save_panel'),
                                    ('save_panel', 'revalidate_save_panel')):
            clock[0] = 10.0
            def late(*args, **kwargs):
                clock[0] = 13.01
                return witness
            setattr(self.adapter, callback, Mock(side_effect=late))
            process = self.adapter.process_identity = Mock(return_value=self.adapter.process)
            kwargs = {'document_token': token}
            if callback.startswith('revalidate_'): kwargs['witness'] = witness
            with self.subTest(callback=callback), patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                    getattr(self.reader, operation)(**kwargs)
            self.assertEqual(process.call_count, 1)  # no post-read validation after expiry
            self.assertIn(token, self.reader._tokens)

    def test_scoped_window_deadline_rejects_late_identity_comparison(self):
        token = self.reader.read()['document_token']
        clock = [10.0]
        def equal(a, b):
            clock[0] = 13.01
            return a is b
        self.adapter.equal = equal
        with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
            with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                self.reader.window_for_token('com.apple.TextEdit', token)

    def test_scoped_observation_lock_wait_consumes_shared_budget(self):
        token = self.reader.read()['document_token']
        clock = [10.0]
        class DelayedLock:
            def __enter__(self): clock[0] = 14.0
            def __exit__(self, *args): pass
        self.reader._lock = DelayedLock()
        self.adapter.process_identity = Mock(side_effect=AssertionError('must not read'))
        for operation in (lambda: self.reader.window_for_token('com.apple.TextEdit', token),
                          lambda: self.reader.save_panel(document_token=token)):
            clock[0] = 10.0
            with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
                with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'): operation()
        self.adapter.process_identity.assert_not_called()

    def test_panel_caller_budget_restores_and_interrupt_is_not_wrapped(self):
        from nlc.model_budget import model_deadline, remaining_model_seconds
        token = self.reader.read()['document_token']
        clock = [10.0]
        def late(window, **kwargs): clock[0] = 10.2
        self.adapter.save_panel = late
        with patch('nlc.native_document.time.monotonic', side_effect=lambda: clock[0]):
            with model_deadline(10.1):
                with self.assertRaisesRegex(DocumentSnapshotError, 'deadline'):
                    self.reader.save_panel(document_token=token)
            self.assertEqual(remaining_model_seconds(3), 3)
            self.adapter.save_panel = Mock(side_effect=KeyboardInterrupt)
            with self.assertRaises(KeyboardInterrupt): self.reader.save_panel(document_token=token)
            self.adapter.save_panel = Mock(return_value='witness')
            self.assertEqual(self.reader.save_panel(document_token=token), 'witness')

    def test_multiple_documents_need_existing_identity_token(self):
        token = self.reader.read()['document_token']
        self.adapter.pairs.append((object(), object()))
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read()
        self.assertEqual(self.reader.read(document_token=token)['document_token'], token)

    def test_saved_document_location_can_change_only_for_unselected_path_token(self):
        first_path = self.path
        second_path = self.path.with_name('saved-as.rtf')
        self.adapter.path = (0, first_path.as_uri())
        flexible = self.reader.read()['document_token']
        selected = self.reader.read(document_path=str(first_path))['document_token']
        self.adapter.path = (0, second_path.as_uri())
        result = self.reader.read(document_token=flexible)
        self.assertEqual(result['document_token'], flexible)
        self.assertEqual(result['document_path'], str(second_path))
        self.assertNotIn('source_format', result)
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read(document_token=selected)

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

    def test_path_selection_reads_only_selected_body_and_pins_path(self):
        own, foreign = self.adapter.window, object()
        own_body, foreign_body = self.adapter.body, object()
        self.adapter.pairs = [(foreign, foreign_body), (own, own_body)]
        original = self.adapter.read_attribute
        reads = []
        def read(element, name):
            reads.append((element, name))
            if name == 'AXDocument':
                return (0, self.path.as_uri()) if element is own else (-25212, None)
            self.assertIs(element, own_body)
            return original(element, name)
        self.adapter.read_attribute = read
        result = self.reader.read(document_path=str(self.path))
        token = result['document_token']
        reads.clear()
        self.assertIs(self.reader.window_for_token('com.apple.TextEdit', token), own)
        self.assertTrue(all(name == 'AXDocument' for _, name in reads))
        self.adapter.read_attribute = lambda element, name: (0, self.path.with_name('other.rtf').as_uri()) if name == 'AXDocument' else self.fail('body read after path drift')
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read(document_token=token)
        with self.assertRaises(DocumentSnapshotError):
            self.reader.window_for_token('com.apple.TextEdit', token)

    def test_path_shape_and_ambiguous_path_are_rejected_before_body_reads(self):
        adapter = Mock()
        reader = TextEditDocumentReader(adapter)
        for path in ('relative', '/tmp/../x', '//tmp/x', '/tmp/x\\y', '/tmp/x\n', '', 1, '/tmp/\ud800'):
            with self.subTest(path=repr(path)), self.assertRaises(DocumentSnapshotError):
                reader.read(document_path=path)
        with self.assertRaises(DocumentSnapshotError):
            reader.read(document_token='token', document_path=str(self.path))
        self.assertEqual(adapter.mock_calls, [])
        self.adapter.path = (0, self.path.as_uri())
        self.adapter.pairs.append((object(), object()))
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read(document_path=str(self.path))

    def test_malformed_fallback_enumeration_is_rejected_before_filtering(self):
        self.adapter.pairs = [(object(),)]
        with self.assertRaises(DocumentSnapshotError):
            self.reader.read(document_path=str(self.path))

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

    def test_save_directory_requires_pinned_document_before_and_after_read(self):
        adapter = self.adapter
        witness = object()
        adapter.save_directory = lambda window: witness
        token = self.reader.read()['document_token']
        self.assertIs(self.reader.save_directory(document_token=token), witness)
        with self.assertRaises(DocumentSnapshotError):
            self.reader.save_directory(document_token='unknown')
        def restarted(window):
            adapter.process = (10, 'launch-2')
            return witness
        adapter.save_directory = restarted
        with self.assertRaises(DocumentSnapshotError):
            self.reader.save_directory(document_token=token)
        adapter.process = (10, 'launch-1')
        with self.assertRaises(DocumentSnapshotError):
            self.reader.save_directory(document_token=token)

    def test_save_panel_read_and_revalidation_pin_the_document(self):
        token = self.reader.read()['document_token']
        witness = object()
        self.adapter.save_panel = Mock(return_value=witness)
        self.adapter.revalidate_save_panel = Mock()
        self.assertIs(self.reader.save_panel(document_token=token, require_format=True), witness)
        self.adapter.save_panel.assert_called_once_with(self.adapter.window, require_format=True)
        self.assertIs(self.reader.save_panel(document_token=token, witness=witness), witness)
        self.adapter.revalidate_save_panel.assert_called_once_with(
            self.adapter.window, witness, require_format=False)
        def replace(*args, **kwargs):
            self.adapter.pairs = [(object(), object())]
        self.adapter.revalidate_save_panel.side_effect = replace
        with self.assertRaises(DocumentSnapshotError):
            self.reader.save_panel(document_token=token, witness=witness)
        self.adapter.pairs = [(self.adapter.window, self.adapter.body)]
        with self.assertRaises(DocumentSnapshotError):
            self.reader.save_panel(document_token=token)


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class DriverDocumentTextTests(unittest.TestCase):
    def test_scoped_element_moved_to_another_window_is_rejected_before_labels(self):
        from nlc.driver import Driver, NotStarted
        driver = Driver.__new__(Driver)
        driver._validate_document_observation = Mock()
        element, own, foreign = object(), object(), object()
        obs = SimpleNamespace(elements=[element], document_token='token', document_window=own)
        with patch('nlc.driver.CFEqual', side_effect=lambda a,b:a is b), patch('nlc.driver._attr', return_value=foreign) as reads:
            with self.assertRaises(NotStarted):
                driver._element(obs, {'element_index':0})
            reads.assert_called_once_with(element, 'AXWindow')

    def test_scoped_observe_walks_only_pinned_window_and_stale_act_reads_nothing(self):
        from nlc.driver import Driver
        driver = Driver.__new__(Driver)
        window = object()
        driver._document_reader = Mock()
        driver._document_reader.window_for_token.return_value = window
        driver._last_action_at = 0
        driver._latest = {}
        driver._resolve = Mock(return_value=10)
        driver._showing_control = Mock()
        driver._custom_selects = Mock(return_value={})
        obs = SimpleNamespace(target='com.apple.TextEdit', pid=10, obs_id='owned',
                              elements=[window], lines=['owned'], truncated=False, walk=Mock())
        with patch('nlc.driver.Observation', return_value=obs), patch('nlc.driver.CFEqual', side_effect=lambda a,b:a is b), patch('nlc.driver._attr', return_value=False), patch('nlc.driver._real_windows') as all_windows:
            reply = driver.observe('com.apple.TextEdit', mode='tree_full', document_token='token')
            self.assertEqual(reply['document_token'], 'token')
            all_windows.assert_not_called()
            obs.walk.assert_called_once_with(window)
            driver._document_reader.window_for_token.side_effect = DocumentSnapshotError('stale')
            driver._mismatch = Mock()
            driver._perform = Mock()
            reply = driver.act('owned', 'com.apple.TextEdit', {'name':'click', 'element_index':0}, expect={'label':'owned'})
            self.assertEqual(reply['effect'], 'not_started')
            driver._mismatch.assert_not_called()
            driver._perform.assert_not_called()

    def test_ax_path_filter_precedes_foreign_subtree_reads(self):
        from nlc.native_document_ax import AXDocumentAdapter
        adapter = AXDocumentAdapter(lambda *a, **kw: 10)
        own, foreign, body = object(), object(), object()
        adapter._process = lambda target: (10, None)
        adapter.equal = lambda a, b: a is b
        reads = []
        def read(element, name):
            reads.append((element, name))
            if name == 'AXWindows': return 0, [foreign, own]
            if name == 'AXSubrole': return 0, 'AXStandardWindow'
            if name == 'AXDocument': return (0, 'file:///private/tmp/own.rtf') if element is own else (-25212, None)
            self.assertIsNot(element, foreign)
            if name == 'AXRole': return 0, 'AXTextArea' if element is body else 'AXWindow'
            if name == 'AXIdentifier': return 0, 'First Text View'
            if name == 'AXChildren': return (0, [body]) if element is own else (-25212, None)
            self.fail(name)
        adapter.read_attribute = read
        self.assertEqual(adapter.document_candidates('com.apple.TextEdit', document_path='/private/tmp/own.rtf'), [(own, body)])
        self.assertNotIn((foreign, 'AXChildren'), reads)
        self.assertNotIn((foreign, 'AXRole'), reads)

    def test_native_checkbox_integral_numbers_are_normalized_without_guessing_float_or_text(self):
        from Foundation import NSNumber
        timeout_patch = patch('nlc.native_document_ax.AXUIElementSetMessagingTimeout', return_value=0)
        timeout_patch.start()
        self.addCleanup(timeout_patch.stop)
        from nlc.native_document_ax import AXDocumentAdapter
        for raw, expected in ((NSNumber.numberWithLongLong_(0), 0),
                              (NSNumber.numberWithLongLong_(1), 1)):
            with patch('nlc.native_document_ax.AXUIElementCopyAttributeValue',
                       side_effect=[(0, raw), (0, 'AXCheckBox')]):
                error, value = AXDocumentAdapter.read_attribute(object(), 'AXValue')
            self.assertEqual((error, value), (0, expected))
            self.assertIs(type(value), int)
        for raw in (NSNumber.numberWithDouble_(1.0), '1'):
            with patch('nlc.native_document_ax.AXUIElementCopyAttributeValue', return_value=(0, raw)):
                result = AXDocumentAdapter.read_attribute(object(), 'AXValue')
            self.assertIs(result[1], raw)
        raw = NSNumber.numberWithLongLong_(1)
        with patch('nlc.native_document_ax.AXUIElementCopyAttributeValue',
                   side_effect=[(0, raw), (0, 'AXTextField')]):
            self.assertIs(AXDocumentAdapter.read_attribute(object(), 'AXValue')[1], raw)

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
