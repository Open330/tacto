"""Pure injected observations; never inspect or modify the user's native UI."""
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import Mock

from nlc.input_audit import NO_FOCUS, InputAudit, InputSnapshot, MacInputReader

BASE = InputSnapshot(10, (100, 200), 'focus-a')


class InputAuditTests(unittest.TestCase):
    def clock(self):
        self.now = getattr(self, 'now', 0) + 0.01
        return self.now

    def test_unchanged_is_only_sampled_evidence(self):
        with InputAudit(lambda: BASE, clock=self.clock, background=False) as audit:
            audit.sample()
        result = audit.report()
        self.assertEqual('UNCHANGED_SAMPLED', result['status'])
        self.assertFalse(result['proves_no_intermediate_change'])
        self.assertEqual(3, len(result['samples']))
        self.assertEqual([], result['errors'])
        self.assertEqual([], result['missing'])

    def test_pointer_restore_does_not_erase_intermediate_change(self):
        values = iter([BASE, InputSnapshot(10, (101, 200), 'focus-a'), BASE])
        with InputAudit(lambda: next(values), clock=self.clock, background=False) as audit:
            audit.sample()
        result = audit.report()
        self.assertEqual('CHANGED', result['status'])
        self.assertEqual(BASE.pointer, result['samples'][-1]['values']['pointer'])
        self.assertEqual(['pointer'], [c['field'] for c in result['changes']])
        self.assertIsInstance(result['changes'][0]['at'], float)

    def test_foreground_and_focus_transitions_detected_independently(self):
        values = iter([BASE, InputSnapshot(11, BASE.pointer, 'focus-b'), BASE])
        with InputAudit(lambda: next(values), clock=self.clock, background=False) as audit:
            audit.sample()
        self.assertEqual({'frontmost_pid', 'focus_identity'}, {c['field'] for c in audit.report()['changes']})

    def test_missing_and_reader_errors_are_separate_not_success(self):
        values = iter([BASE, InputSnapshot(10, None, None, {'pointer': 'read_failed'}), BASE])
        with InputAudit(lambda: next(values), clock=self.clock, background=False) as audit:
            audit.sample()
        result = audit.report()
        self.assertEqual('INCONCLUSIVE', result['status'])
        self.assertEqual({'pointer': 'read_failed'}, result['errors'][0]['fields'])
        self.assertEqual(['pointer', 'focus_identity'], result['missing'][0]['fields'])

    def test_exception_and_invalid_readings_cannot_pass(self):
        for snapshot in (InputSnapshot(True, (float('nan'), 2), ''), {'frontmost_pid': 10}):
            with InputAudit(lambda: snapshot, background=False) as audit:
                pass
            self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        def broken():
            raise OSError('read failed')
        with InputAudit(broken, background=False) as audit:
            pass
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])

    def test_invalid_or_backwards_clock_is_inconclusive(self):
        for timestamps in ([float('nan')] * 4, [2, 1, 4, 3], [2, 3, 1, 4]):
            with self.subTest(timestamps=timestamps):
                clock = iter(timestamps)
                with InputAudit(lambda: BASE, clock=lambda: next(clock), background=False) as audit:
                    pass
                report = audit.report()
                self.assertEqual('INCONCLUSIVE', report['status'])
                self.assertTrue(any('clock' in item.get('fields', {}) for item in report['errors']))

    def test_budget_and_unfinished_audit_do_not_pass(self):
        audit = InputAudit(lambda: BASE, background=False, max_samples=2)
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        with audit:
            audit.sample()
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        self.assertIn({'error': 'sample_budget_exceeded'}, audit.report()['errors'])

    def test_operation_exception_is_preserved(self):
        with self.assertRaises(ValueError):
            with InputAudit(lambda: BASE, background=False) as audit:
                raise ValueError('operation failed')
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        self.assertIn({'error': 'observed_operation_raised', 'exception': 'ValueError'}, audit.report()['errors'])

    def test_background_sampler_records_transient_change(self):
        observed = threading.Event()
        calls = []
        def reader():
            calls.append(1)
            if len(calls) == 2:
                observed.set()
                return InputSnapshot(10, (150, 200), 'focus-a')
            return BASE
        with InputAudit(reader, interval=0.001) as audit:
            self.assertTrue(observed.wait(1))
        self.assertEqual('CHANGED', audit.report()['status'])

    def test_hung_background_reader_does_not_turn_into_success(self):
        blocked, release = threading.Event(), threading.Event()
        calls = []
        def reader():
            calls.append(1)
            if len(calls) > 1:
                blocked.set()
                release.wait(1)
            return BASE
        try:
            with InputAudit(reader, interval=0.001, join_timeout=0.001) as audit:
                self.assertTrue(blocked.wait(1))
            self.assertEqual('INCONCLUSIVE', audit.report()['status'])
            self.assertIn({'error': 'reader_did_not_stop'}, audit.report()['errors'])
        finally:
            release.set()


class MacReaderContract(unittest.TestCase):
    def reader(self, trusted=True):
        reader = MacInputReader()
        appkit, quartz, ax, cf = Mock(), Mock(), Mock(), Mock()
        appkit.NSWorkspace.sharedWorkspace.return_value.frontmostApplication.return_value.processIdentifier.return_value = 10
        quartz.CGEventGetLocation.return_value = SimpleNamespace(x=100, y=200)
        ax.AXIsProcessTrusted.return_value = trusted
        ax.AXUIElementGetPid.return_value = (0, 10)
        ax.AXUIElementCopyAttributeValue.return_value = (0, SimpleNamespace(identity=1))
        cf.CFEqual.side_effect = lambda first, second: first.identity == second.identity
        reader._frameworks = (appkit, quartz, ax, cf)
        reader._foreground_pid = Mock(return_value=10)
        return reader, appkit, quartz, ax

    def test_focus_uses_cf_identity_not_wrapper_identity(self):
        reader, appkit, quartz, ax = self.reader()
        first = reader()
        ax.AXUIElementCopyAttributeValue.return_value = (0, SimpleNamespace(identity=1))
        self.assertEqual(first.focus_identity, reader().focus_identity)
        ax.AXUIElementCopyAttributeValue.return_value = (0, SimpleNamespace(identity=2))
        self.assertNotEqual(first.focus_identity, reader().focus_identity)
        quartz.CGEventPost.assert_not_called()
        appkit.NSRunningApplication.assert_not_called()

    def test_missing_accessibility_permission_never_prompts(self):
        reader, appkit, quartz, ax = self.reader(trusted=False)
        result = reader()
        self.assertEqual('accessibility_permission_missing', result.errors['focus_identity'])
        self.assertIsNone(result.focus_identity)
        ax.AXUIElementCopyAttributeValue.assert_not_called()
        ax.AXIsProcessTrustedWithOptions.assert_not_called()
        quartz.CGEventPost.assert_not_called()

    def test_native_foreground_disagreement_is_reported(self):
        reader, appkit, quartz, ax = self.reader()
        ax.AXUIElementGetPid.return_value = (0, 11)
        snapshot = reader()
        self.assertEqual(11, snapshot.frontmost_pid)
        self.assertEqual('native_ax_frontmost_disagree', snapshot.errors['frontmost_pid'])

    def test_ax_failure_preserves_code_without_success_fallback(self):
        reader, appkit, quartz, ax = self.reader()
        ax.AXUIElementCopyAttributeValue.return_value = (-25212, None)
        snapshot = reader()
        self.assertIsNone(snapshot.frontmost_pid)
        self.assertIsNone(snapshot.focus_identity)
        self.assertIn('AXError=-25212', snapshot.errors['frontmost_pid'])
        self.assertIn('native_pid=10', snapshot.errors['frontmost_pid'])
        with InputAudit(lambda: snapshot, background=False) as audit:
            pass
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])

    def test_nothing_focused_is_a_reading_in_both_strategies(self):
        # A frontmost app with every document closed answers -25212 for its
        # focused element. That is "nothing is focused", a value an audit can
        # compare, not a reader failure that stops the collector from starting.
        for strategy in ('systemwide', 'application_frontmost'):
            for code in (-25205, -25212):
                reader, appkit, quartz, ax = self.reader()
                reader.strategy = strategy
                def copy(element, key, out, code=code):
                    if key == 'AXFocusedUIElement':
                        return (code, None)
                    if key == 'AXFrontmost':
                        return (0, True)
                    return (0, SimpleNamespace(identity=1))
                ax.AXUIElementCopyAttributeValue.side_effect = copy
                snapshot = reader()
                self.assertEqual(NO_FOCUS, snapshot.focus_identity, (strategy, code))
                self.assertNotIn('focus_identity', snapshot.errors)
                with InputAudit(reader, background=False) as audit:
                    pass
                self.assertEqual('UNCHANGED_SAMPLED', audit.report()['status'])

    def test_other_focus_errors_stay_failures(self):
        reader, appkit, quartz, ax = self.reader()
        ax.AXUIElementCopyAttributeValue.side_effect = lambda element, key, out: (
            (-25204, None) if key == 'AXFocusedUIElement' else (0, SimpleNamespace(identity=1)))
        snapshot = reader()
        self.assertIsNone(snapshot.focus_identity)
        self.assertEqual('AXError:-25204', snapshot.errors['focus_identity'])

    def test_explicit_application_frontmost_strategy_and_sample_source(self):
        reader, appkit, quartz, ax = self.reader()
        reader.strategy = 'application_frontmost'
        element = SimpleNamespace(identity=1)
        ax.AXUIElementCopyAttributeValue.side_effect = lambda app, key, out: (0, True if key=='AXFrontmost' else element)
        with InputAudit(reader, background=False) as audit:
            pass
        report = audit.report()
        self.assertEqual('UNCHANGED_SAMPLED', report['status'])
        self.assertEqual(['application_frontmost'], report['reader_strategies'])
        self.assertTrue(all(s['source']=='application_frontmost' for s in report['samples']))
        ax.AXUIElementCreateSystemWide.assert_not_called()
        self.assertEqual(4, sum(c.args[1]=='AXFrontmost' for c in ax.AXUIElementCopyAttributeValue.call_args_list))

    def test_application_strategy_rejects_cached_wrong_app_and_lost_frontmost(self):
        for states in ((False,), (True, False), (1,)):
            reader, appkit, quartz, ax = self.reader()
            reader.strategy = 'application_frontmost'
            frontmost = iter(states)
            ax.AXUIElementCopyAttributeValue.side_effect = lambda app,key,out: (0, next(frontmost) if key=='AXFrontmost' else SimpleNamespace(identity=1))
            snapshot = reader()
            self.assertIn('frontmost_pid', snapshot.errors)
            with InputAudit(lambda: snapshot, background=False) as audit:
                pass
            self.assertEqual('INCONCLUSIVE', audit.report()['status'])
            ax.AXUIElementCreateSystemWide.assert_not_called()

    def test_application_strategy_rejects_native_foreground_change(self):
        reader, appkit, quartz, ax = self.reader()
        reader.strategy = 'application_frontmost'
        app_before, app_after = Mock(), Mock()
        app_before.processIdentifier.return_value = 10
        app_after.processIdentifier.return_value = 11
        reader._foreground_pid.side_effect = [10, 11]
        ax.AXUIElementCopyAttributeValue.side_effect = lambda app,key,out: (0, True if key=='AXFrontmost' else SimpleNamespace(identity=1))
        self.assertEqual('native_frontmost_changed_during_read', reader().errors['frontmost_pid'])

    def test_cached_workspace_is_never_used_as_foreground_authority(self):
        reader, appkit, quartz, ax = self.reader()
        reader._foreground_pid.return_value = 12
        ax.AXUIElementGetPid.return_value = (0, 12)
        snapshot = reader()
        self.assertEqual(snapshot.frontmost_pid, 12)
        self.assertEqual(snapshot.errors, {})
        appkit.NSWorkspace.sharedWorkspace.assert_not_called()

    def test_unavailable_fresh_read_cannot_use_cached_pid(self):
        from nlc.native_frontmost import FrontmostUnavailable
        reader, appkit, quartz, ax = self.reader()
        reader._foreground_pid.side_effect = FrontmostUnavailable('unavailable')
        snapshot = reader()
        self.assertIsNone(snapshot.frontmost_pid)
        self.assertTrue(snapshot.errors)
        appkit.NSWorkspace.sharedWorkspace.assert_not_called()

    def test_strategy_is_explicit_and_mixing_sources_is_inconclusive(self):
        self.assertEqual('systemwide', MacInputReader().strategy)
        self.assertEqual('application_frontmost', MacInputReader(strategy='application_frontmost').strategy)
        with self.assertRaises(ValueError):
            MacInputReader(strategy='automatic_fallback')
        snapshots = iter([BASE, InputSnapshot(10, (100, 200), 'focus-a', source='other')])
        with InputAudit(lambda: next(snapshots), background=False) as audit:
            pass
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        self.assertIn({'error': 'reader_strategy_changed'}, audit.report()['errors'])

class SelectionAuditTests(unittest.TestCase):
    def test_same_focus_caret_move_and_restore_is_changed_when_required(self):
        values = iter([InputSnapshot(10, (1, 2), 'same', selected_text_range=pair)
                       for pair in ((3, 0), (4, 0), (3, 0))])
        with InputAudit(lambda: next(values), background=False, require_selection=True) as audit:
            audit.sample()
        report = audit.report()
        self.assertEqual('CHANGED', report['status'])
        self.assertEqual(['selected_text_range'], [c['field'] for c in report['changes']])
        self.assertIn('selected_text_range', report['required_fields'])

    def test_missing_or_invalid_range_is_not_preserved_caret(self):
        for pair in (None, (-1, 0), (True, 0), (1.0, 0), (0, -1), (0,), 'secret'):
            snapshot = InputSnapshot(10, (1, 2), 'same', selected_text_range=pair)
            with InputAudit(lambda: snapshot, background=False, require_selection=True) as audit:
                pass
            self.assertEqual('INCONCLUSIVE', audit.report()['status'])
            self.assertIn('selected_text_range', audit.report()['missing'][0]['fields'])

    def test_default_does_not_claim_to_check_selection_and_positional_api_survives(self):
        snapshot = InputSnapshot(10, (1, 2), 'same', {}, 'custom')
        with InputAudit(lambda: snapshot, background=False) as audit:
            pass
        self.assertEqual('UNCHANGED_SAMPLED', audit.report()['status'])
        self.assertNotIn('selected_text_range', audit.report()['required_fields'])
        self.assertNotIn('selected_text_range', audit.report()['samples'][0]['values'])

    def native_reader(self, strategy):
        reader, appkit, quartz, ax = MacReaderContract().reader()
        reader.strategy = strategy
        reader.require_selection = True
        cf = reader._frameworks[3]
        focus = SimpleNamespace(identity=1)
        application = SimpleNamespace(identity=100)
        ax.kAXValueCFRangeType = 4
        cf.CFGetTypeID.return_value = 1000
        ax.AXValueGetTypeID.return_value = 1000
        ax.AXValueGetType.return_value = 4
        ax.AXValueGetValue.return_value = (True, SimpleNamespace(location=3, length=0))
        def attribute(element, key, out):
            return (0, {'AXFrontmost': True, 'AXFocusedApplication': application,
                        'AXFocusedUIElement': focus, 'AXSelectedTextRange': object()}[key])
        ax.AXUIElementCopyAttributeValue.side_effect = attribute
        return reader, ax, cf, focus, attribute

    def test_both_actual_reader_strategies_decode_only_numeric_cfrange(self):
        for strategy in ('systemwide', 'application_frontmost'):
            reader, ax, cf, focus, attribute = self.native_reader(strategy)
            snapshot = reader()
            self.assertEqual((3, 0), snapshot.selected_text_range, snapshot.errors)
            self.assertEqual({}, snapshot.errors)
            self.assertEqual(3, sum(c.args[1]=='AXFocusedUIElement' for c in ax.AXUIElementCopyAttributeValue.call_args_list))
            names = [call.args[1] for call in ax.AXUIElementCopyAttributeValue.call_args_list]
            self.assertNotIn('AXSelectedText', names)
            self.assertNotIn('AXValue', names)
            ax.AXValueGetValue.return_value = (True, (0, 2))
            self.assertEqual((0, 2), reader().selected_text_range)

    def test_wrong_type_unsupported_and_invalid_native_range_are_errors(self):
        for failure in ('cf_type', 'ax_type', 'unsupported', 'negative', 'bool', 'float', 'decode'):
            reader, ax, cf, focus, attribute = self.native_reader('application_frontmost')
            if failure == 'cf_type':
                cf.CFGetTypeID.return_value = 2000
            elif failure == 'ax_type':
                ax.AXValueGetType.return_value = 1
            elif failure == 'unsupported':
                ax.AXUIElementCopyAttributeValue.side_effect = lambda element,key,out: (-25205, None) if key=='AXSelectedTextRange' else attribute(element,key,out)
            elif failure == 'decode':
                ax.AXValueGetValue.return_value = (False, None)
            else:
                value = {'negative': -1, 'bool': True, 'float': 1.0}[failure]
                ax.AXValueGetValue.return_value = (True, (value, 0))
            snapshot = reader()
            self.assertIsNone(snapshot.selected_text_range)
            self.assertIn('selected_text_range', snapshot.errors)

    def test_focus_change_before_or_after_selection_refuses_wrong_field_range(self):
        for change_at in (2, 3):
            reader, ax, cf, focus, attribute = self.native_reader('application_frontmost')
            count = []
            def moving(element, key, out):
                if key == 'AXFocusedUIElement':
                    count.append(1)
                    if len(count) == change_at:
                        return 0, SimpleNamespace(identity=2)
                return attribute(element, key, out)
            ax.AXUIElementCopyAttributeValue.side_effect = moving
            snapshot = reader()
            self.assertIsNone(snapshot.selected_text_range)
            self.assertIn('focus_changed_', snapshot.errors['selected_text_range'])
