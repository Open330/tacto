"""Timing observes calls, preserving results, exceptions and scoped signatures."""
import contextlib
import inspect
import io
import json
import unittest
from unittest.mock import patch

from tacto.command_help import calculator_example
from tacto.flow import Flow
from tacto.runtime_timing import TimedDriver
from tests.test_flow_safety_defaults import Calculator


class RuntimeTimingTests(unittest.TestCase):
    def test_scoped_signature_result_and_attributes_are_preserved(self):
        class Driver:
            capabilities = {'document_token'}
            def observe(self, target, mode='tree_full', *, document_token=None):
                return {'private': target, 'token': document_token}
        driver = Driver()
        wrapper = TimedDriver(driver, clock=iter([10., 12.]).__next__)
        self.assertEqual(inspect.signature(driver.observe), inspect.signature(wrapper.observe))
        self.assertIs(driver.capabilities, wrapper.capabilities)
        self.assertEqual({'private': 'private target', 'token': 'private token'},
                         wrapper.observe('private target', document_token='private token'))
        report = wrapper.snapshot()
        self.assertEqual({'count': 1, 'seconds': 2., 'exceptions': 0}, report['calls']['observe'])
        self.assertNotIn('private', json.dumps(report))

    def test_exception_propagates_and_is_timed_without_message(self):
        class Driver:
            def act(self): raise RuntimeError('private action text')
        wrapper = TimedDriver(Driver(), clock=iter([10., 13.]).__next__)
        with self.assertRaisesRegex(RuntimeError, 'private action text'):
            wrapper.act()
        report = wrapper.snapshot()
        self.assertEqual({'count': 1, 'seconds': 3., 'exceptions': 1}, report['calls']['act'])
        self.assertNotIn('private', json.dumps(report))

    def test_cli_timing_keeps_result_and_default_has_no_timing_output(self):
        from tacto import cli
        for timed in (False, True):
            app = Calculator()
            with patch('tacto.cli._make_driver', return_value=app), \
                 patch('tacto.cli._indicator_for', return_value=contextlib.nullcontext()), \
                 contextlib.redirect_stdout(io.StringIO()) as output, \
                 contextlib.redirect_stderr(io.StringIO()) as errors:
                flow = Flow.from_json(json.dumps(calculator_example()))
                self.assertEqual(0, cli._run_flow(flow, ['--timings'] if timed else []))
            self.assertEqual('408', app.value)
            self.assertIn('408', output.getvalue())
            if timed:
                report = json.loads(errors.getvalue().removeprefix('TACTO_TIMINGS '))
                self.assertEqual(6, report['calls']['act']['count'])
                self.assertGreater(report['replay_seconds'], 0)
            else:
                self.assertEqual('', errors.getvalue())
