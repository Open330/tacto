"""Spawned fake readers only: no access to any native UI or permissions."""
import multiprocessing
import os
import sys
import time
import unittest

from tacto.input_audit import InputSnapshot
from tacto.input_audit_process import ProcessInputAudit, InputAuditProcessError


def unchanged_reader():
    return InputSnapshot(10, (1, 2), 'same')


def missing_reader():
    return InputSnapshot(10, (1, 2), None)


def exiting_reader():
    os._exit(7)


def slow_reader():
    time.sleep(10)
    return unchanged_reader()


class ChangingReader:
    def __init__(self):
        self.count = 0
    def __call__(self):
        self.count += 1
        return InputSnapshot(10, (99, 2) if self.count == 5 else (1, 2), 'same')


class HangsAfterReady:
    def __init__(self):
        self.count = 0
    def __call__(self):
        self.count += 1
        if self.count > 3:
            time.sleep(10)
        return unchanged_reader()


class ProcessAuditTests(unittest.TestCase):
    def assert_child_cleaned(self, audit):
        pid = audit.report()['child_pid']
        self.assertFalse(any(child.pid == pid for child in multiprocessing.active_children()))
        if audit._process is not None and audit._process.pid is not None:
            self.assertFalse(audit._process.is_alive())

    def test_samples_continue_while_parent_holds_gil(self):
        audit = ProcessInputAudit(unchanged_reader, interval=.005, startup_timeout=3)
        with audit:
            old = sys.getswitchinterval()
            sys.setswitchinterval(1)
            try:
                start = time.monotonic()
                value = 0
                while time.monotonic() - start < .15:
                    value = (value + 1) % 100003
                end = time.monotonic()
            finally:
                sys.setswitchinterval(old)
        report = audit.report()
        self.assertEqual('UNCHANGED_SAMPLED', report['status'], report['errors'])
        samples = report['raw_report']['samples']
        self.assertGreaterEqual(sum(start < s['started_at'] < end for s in samples), 3)
        self.assertFalse(report['proves_no_intermediate_change'])
        self.assert_child_cleaned(audit)

    def test_transient_pointer_restore_remains_changed(self):
        with ProcessInputAudit(ChangingReader(), interval=.005, startup_timeout=3) as audit:
            time.sleep(.06)
        report = audit.report()
        self.assertEqual('CHANGED', report['status'])
        self.assertEqual((1, 2), report['raw_report']['samples'][-1]['values']['pointer'])
        self.assertTrue(report['raw_report']['changes'])
        self.assert_child_cleaned(audit)

    def test_missing_initial_samples_prevent_operation_and_preserve_raw_report(self):
        audit = ProcessInputAudit(missing_reader, interval=.005, startup_timeout=3)
        with self.assertRaises(InputAuditProcessError):
            with audit:
                self.fail('operation executed without ready samples')
        report = audit.report()
        self.assertEqual('INCONCLUSIVE', report['status'])
        self.assertIsNotNone(report['raw_report'])
        self.assertGreaterEqual(len(report['raw_report']['samples']), 3)
        self.assert_child_cleaned(audit)

    def test_startup_timeout_and_child_exit_never_pass_or_leak(self):
        for reader in (slow_reader, exiting_reader):
            with self.subTest(reader=reader):
                audit = ProcessInputAudit(reader, startup_timeout=.15, stop_timeout=.05)
                start = time.monotonic()
                with self.assertRaises(InputAuditProcessError):
                    with audit:
                        self.fail('operation executed')
                self.assertLess(time.monotonic() - start, 2)
                self.assertEqual('INCONCLUSIVE', audit.report()['status'])
                self.assert_child_cleaned(audit)

    def test_hung_reader_stop_timeout_never_passes_or_leaks(self):
        with ProcessInputAudit(HangsAfterReady(), interval=.005, startup_timeout=3,
                               stop_timeout=.05) as audit:
            time.sleep(.03)
        report = audit.report()
        self.assertEqual('INCONCLUSIVE', report['status'])
        self.assertIn({'error': 'collector_stop_timeout'}, report['errors'])
        self.assert_child_cleaned(audit)

    def test_parent_exception_preserved_and_child_cleaned(self):
        with self.assertRaises(ValueError):
            with ProcessInputAudit(unchanged_reader, startup_timeout=3) as audit:
                raise ValueError('operation failed')
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        self.assert_child_cleaned(audit)

    def test_sample_budget_stops_child_and_is_inconclusive(self):
        with ProcessInputAudit(unchanged_reader, interval=.001, max_samples=6,
                               startup_timeout=3) as audit:
            time.sleep(.03)
        report = audit.report()
        self.assertEqual('INCONCLUSIVE', report['status'])
        self.assertIn({'error': 'sample_budget_exceeded'}, report['errors'])
        self.assert_child_cleaned(audit)

    def test_nonpicklable_reader_start_failure_and_parameter_validation(self):
        audit = ProcessInputAudit(lambda: unchanged_reader())
        with self.assertRaises(InputAuditProcessError):
            with audit:
                self.fail('operation executed')
        self.assertEqual('INCONCLUSIVE', audit.report()['status'])
        for kwargs in ({'interval': float('nan')}, {'ready_samples': True},
                       {'ready_samples': 1}, {'max_samples': 4}, {'strategy': 'automatic'}):
            with self.assertRaises(ValueError):
                ProcessInputAudit(unchanged_reader, **kwargs)

class ChangingSelectionReader:
    def __init__(self):
        self.count = 0
    def __call__(self):
        self.count += 1
        return InputSnapshot(10, (1, 2), 'same', selected_text_range=(4 if self.count == 5 else 3, 0))


class ProcessSelectionTests(unittest.TestCase):
    def test_required_selection_collected_in_child_and_transient_change_detected(self):
        with ProcessInputAudit(ChangingSelectionReader(), require_selection=True,
                               interval=.005, startup_timeout=3) as audit:
            time.sleep(.05)
        report = audit.report()
        self.assertEqual('CHANGED', report['status'])
        self.assertTrue(report['require_selection'])
        self.assertEqual(['selected_text_range'], [c['field'] for c in report['raw_report']['changes']])
        self.assertFalse(audit._process.is_alive())

    def test_missing_required_selection_blocks_readiness(self):
        audit = ProcessInputAudit(unchanged_reader, require_selection=True, startup_timeout=3)
        with self.assertRaises(InputAuditProcessError):
            with audit:
                self.fail('operation started without required caret evidence')
        report = audit.report()
        self.assertEqual('INCONCLUSIVE', report['status'])
        self.assertIn('selected_text_range', report['raw_report']['missing'][0]['fields'])
        self.assertFalse(audit._process.is_alive())
