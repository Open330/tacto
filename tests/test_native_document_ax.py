"""AX traversal regressions with native calls mocked; no UI operations."""
import importlib.util
import unittest
from unittest.mock import patch
from types import SimpleNamespace


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class AXDocumentAdapterTests(unittest.TestCase):
    def setUp(self):
        from tacto import native_document_ax as module
        self.module = module
        self.adapter = module.AXDocumentAdapter(lambda target, **kw: 123)
        self.window = {'AXSubrole': 'AXStandardWindow', 'AXRole': 'AXWindow'}
        self.body = {'AXRole': 'AXTextArea', 'AXIdentifier': 'First Text View'}
        self.window['AXChildren'] = [self.body]
        self.app = {'AXWindows': [self.window]}
        self.patches = [
            patch.object(self.adapter, '_process', return_value=(123, object())),
            patch.object(module, 'AXUIElementCreateApplication', return_value=self.app),
            patch.object(self.adapter, 'read_attribute', side_effect=self.read),
        ]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)

    @staticmethod
    def read(element, name):
        value = element.get(name)
        if isinstance(value, tuple):
            return value
        return (0, value) if name in element else (-25205, None)

    def test_document_beyond_old_sixty_child_limit_is_not_silently_lost(self):
        self.window['AXChildren'] = [{'AXRole': 'AXStaticText'} for _ in range(70)] + [self.body]
        docs = self.adapter.documents('com.apple.TextEdit')
        self.assertEqual(1, len(docs))
        self.assertIs(self.body, docs[0][1])

    def test_budget_failure_does_not_return_partial_documents(self):
        self.adapter.max_nodes = 1
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            self.adapter.documents('com.apple.TextEdit')

    def test_inaccessible_children_or_ambiguous_body_is_not_complete(self):
        self.window['AXChildren'] = (-25204, None)
        with self.assertRaisesRegex(RuntimeError, 'children unavailable'):
            self.adapter.documents('com.apple.TextEdit')
        self.window['AXChildren'] = [self.body, dict(self.body)]
        with self.assertRaisesRegex(RuntimeError, 'ambiguous'):
            self.adapter.documents('com.apple.TextEdit')

    def test_non_document_dialog_is_excluded_but_unknown_subrole_is_rejected(self):
        self.app['AXWindows'].insert(0, {'AXSubrole': 'AXDialog'})
        self.assertEqual(1, len(self.adapter.documents('com.apple.TextEdit')))
        self.app['AXWindows'].insert(0, {})
        with self.assertRaisesRegex(RuntimeError, 'AXSubrole'):
            self.adapter.documents('com.apple.TextEdit')

    def test_cycle_exceeds_budget_instead_of_hanging(self):
        self.window['AXChildren'] = [self.window]
        self.adapter.max_depth = 3
        with self.assertRaisesRegex(RuntimeError, 'budget'):
            self.adapter.documents('com.apple.TextEdit')

    def test_process_identity_requires_launch_date(self):
        process = SimpleNamespace(launchDate=lambda: None)
        with patch.object(self.adapter, '_process', return_value=(123, process)):
            with self.assertRaisesRegex(RuntimeError, 'launch identity'):
                self.adapter.process_identity('com.apple.TextEdit')

    def test_native_read_caps_timeout_and_resets_after_callback_exception(self):
        calls = []
        def configure(element, timeout):
            calls.append((element, timeout))
            return 0
        element = object()
        with patch.object(self.module, 'AXUIElementSetMessagingTimeout', side_effect=configure):
            with self.assertRaisesRegex(RuntimeError, 'read failed'):
                self.module._bounded_read(element, lambda: (_ for _ in ()).throw(RuntimeError('read failed')))
        self.assertEqual(calls, [(element, .5), (element, 0)])

    def test_failed_configuration_or_reset_never_returns_native_result(self):
        for statuses, expected_calls in (([1], 0), ([0, 1], 1)):
            callback = unittest.mock.Mock(return_value=(0, 'must not return'))
            with patch.object(self.module, 'AXUIElementSetMessagingTimeout', side_effect=statuses):
                with self.assertRaisesRegex(RuntimeError, 'timeout'):
                    self.module._bounded_read(object(), callback)
            self.assertEqual(callback.call_count, expected_calls)

    def test_caller_interrupt_propagates_even_if_timeout_reset_fails(self):
        for reset in (1, RuntimeError('reset failed')):
            callback = unittest.mock.Mock(side_effect=KeyboardInterrupt())
            with patch.object(self.module, 'AXUIElementSetMessagingTimeout', side_effect=[0, reset]) as configure:
                with self.assertRaises(KeyboardInterrupt):
                    self.module._bounded_read(object(), callback)
            self.assertEqual(configure.call_count, 2)

    def test_late_native_read_is_rejected_and_remaining_budget_sets_timeout(self):
        from tacto.model_budget import model_deadline
        clock = [10.0]
        element = object()
        def read():
            clock[0] = 10.3
            return (0, 'late')
        with patch('tacto.model_budget.time.monotonic', side_effect=lambda: clock[0]), \
                model_deadline(10.25), \
                patch.object(self.module, 'AXUIElementSetMessagingTimeout', return_value=0) as configure:
            with self.assertRaises(TimeoutError):
                self.module._bounded_read(element, read)
        self.assertEqual(configure.call_args_list[0].args, (element, .25))
        self.assertEqual(configure.call_args_list[1].args, (element, 0))
