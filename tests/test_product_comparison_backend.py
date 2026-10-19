"""The comparison host requires a signed inherited channel, never a fallback."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import os
import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from tests.test_product_comparison_provenance import comparison


class ComparisonBackendTests(unittest.TestCase):
    def test_missing_channel_never_constructs_the_python_driver(self):
        with patch('tacto.helper.InheritedHelperDriver.available', return_value=False), \
             patch('tacto.driver.Driver') as direct:
            with self.assertRaisesRegex(RuntimeError, '^comparison_requires_signed_launcher$'):
                comparison._driver()
        direct.assert_not_called()

    def test_driver_uses_only_the_inherited_channel(self):
        with patch('tacto.helper.InheritedHelperDriver') as inherited, patch('tacto.driver.Driver') as direct:
            inherited.available.return_value = True
            self.assertIs(inherited.return_value, comparison._driver())
        direct.assert_not_called()

    def test_missing_channel_prevents_setup_agent_and_cleanup(self):
        setup, cleanup = Mock(), Mock()
        task = comparison.Task('owned', 'owned goal', Mock(), setup=setup, cleanup=cleanup)
        with patch('tacto.helper.InheritedHelperDriver.available', return_value=False), \
             patch.object(comparison, '_locked') as locked, \
             patch.object(comparison.subprocess, 'run') as run:
            result = comparison.run_one(task, 'nlc')
        self.assertEqual('comparison_requires_signed_launcher', result['observed'])
        self.assertFalse(result['comparison_eligible'])
        self.assertEqual('not_run', result['status'])
        setup.assert_not_called(); cleanup.assert_not_called(); run.assert_not_called()
        locked.assert_not_called()

    def test_legacy_direct_cleanup_is_refused_before_creating_fixture(self):
        setup = Mock()
        task = comparison.Task('owned', 'owned goal', Mock(), setup=setup,
                               cleanup=comparison.owned_cleanup)
        with patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
             patch.object(comparison.subprocess, 'run') as run:
            result = comparison.run_one(task, 'nlc')
        self.assertEqual('owned_cleanup_requires_signed_window_binding', result['observed'])
        self.assertEqual('not_run', result['status'])
        setup.assert_not_called(); run.assert_not_called()

    def test_backend_check_only_reads_metadata_and_closes_no_app(self):
        stub = Mock(capabilities={'observe', 'act'})
        stub.list_targets.return_value = {'type':'apps', 'apps':[{}]}
        out = StringIO()
        with patch.object(sys, 'argv', ['comparison', '--check-backend']), \
             patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
             patch.object(comparison, '_driver', return_value=stub), \
             patch.object(comparison, 'run_one') as run, redirect_stdout(out):
            self.assertEqual(0, comparison.main())
        self.assertIn('signed_helper', out.getvalue())
        stub.list_targets.assert_called_once()
        stub.act.assert_not_called(); run.assert_not_called()

    def test_backend_failure_is_closed_and_does_not_print_exception_body(self):
        with patch.object(sys, 'argv', ['comparison', '--check-backend']), \
             patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
             patch.object(comparison, '_driver', side_effect=RuntimeError('PRIVATE')), \
             redirect_stdout(StringIO()) as out:
            self.assertEqual(2, comparison.main())
        self.assertEqual('signed_comparison_backend_unavailable', out.getvalue().strip())

    def test_cli_routes_product_eval_in_process_and_restores_argv(self):
        from tacto.cli import _dispatch
        original = sys.argv
        captured = []
        def run(path, **kwargs):
            captured.append((Path(path).name, list(sys.argv), kwargs))
            raise SystemExit(2)
        with patch('runpy.run_path', side_effect=run), self.assertRaises(SystemExit) as error:
            _dispatch('eval', ['--product-comparison', '--only', 'calc_mult'])
        self.assertEqual(2, error.exception.code)
        self.assertIs(original, sys.argv)
        self.assertEqual(('product_comparison.py', ['product_comparison.py', '--only', 'calc_mult'],
                          {'run_name':'__main__'}), captured[0])

    def test_agent_env_removes_channel_markers_without_mutating_parent(self):
        values = {'TACTO_HELPER_FD':'3', 'TACTO_LAUNCHER':'1', 'TACTO_HELPER_TOKEN':'fixture',
                  'TACTO_OTHER_OPTION':'keep'}
        with patch.dict(os.environ, values):
            result = comparison._agent_env()
            for key in ('TACTO_HELPER_FD', 'TACTO_LAUNCHER', 'TACTO_HELPER_TOKEN'):
                self.assertNotIn(key, result)
                self.assertEqual(values[key], os.environ[key])
            self.assertEqual('keep', result['TACTO_OTHER_OPTION'])

    def test_product_flag_position_and_duplicates_are_handled_before_execution(self):
        from tacto.cli import _dispatch
        captured = []
        with patch('runpy.run_path', side_effect=lambda p, **k: captured.append(list(sys.argv))):
            self.assertEqual(0, _dispatch('eval', ['--reps', '2', '--product-comparison']))
        self.assertEqual(['product_comparison.py', '--reps', '2'], captured[0])
        with patch('runpy.run_path') as run, redirect_stdout(StringIO()):
            self.assertEqual(2, _dispatch('eval', ['--product-comparison', '--product-comparison']))
        run.assert_not_called()

    def test_signed_controller_outcome_is_not_a_fair_product_comparison(self):
        from tests.test_product_comparison_provenance import Provenance
        fixture = Provenance()
        fixture.setUp()
        try:
            result = fixture.execute()
        finally:
            fixture.doCleanups()
        self.assertEqual('passed', result['status'])
        self.assertTrue(result['oracle_passed'])
        self.assertFalse(result['comparison_eligible'])
        self.assertIsNone(result['agent_responsible_process_observed'])
        self.assertEqual('agent_responsibility_not_isolated_or_observed',
                         result['comparison_exclusion_reason'])

    def test_setup_menu_refusal_does_not_proceed_to_clear_inputs(self):
        stub = Mock()
        stub.observe.return_value = {'type':'observation','obs_id':'owned', 'tree':'owned'}
        stub.act.return_value = {'type':'error', 'effect':'not_started'}
        with patch.object(comparison, '_driver', return_value=stub), \
             self.assertRaisesRegex(RuntimeError, '^comparison_setup_menu_unverified$'):
            comparison.calc_reset()
        stub.act.assert_called_once()

    def test_setup_uncertain_clear_is_not_repeated(self):
        stub = Mock()
        stub.observe.return_value = {'type':'observation','obs_id':'owned', 'tree':'owned'}
        stub.act.side_effect = [{'type':'acted'}, {'type':'error', 'effect':'unknown'}]
        with patch.object(comparison, '_driver', return_value=stub), \
             patch.object(comparison.time, 'sleep'), \
             patch('tacto.flow.Selector.resolve', return_value=(0, 'owned')), \
             self.assertRaisesRegex(RuntimeError, '^comparison_setup_clear_unverified$'):
            comparison.calc_reset()
        self.assertEqual(2, stub.act.call_count)

    def test_responsibility_probe_cleanup_timeout_is_closed_and_child_reaped(self):
        child = Mock(pid=42)
        child.poll.return_value = None
        child.communicate.side_effect = subprocess.TimeoutExpired('owned', 3)
        process = Mock()
        process.__enter__ = Mock(return_value=child)
        process.__exit__ = Mock(return_value=False)
        with patch.object(comparison.subprocess, 'Popen', return_value=process), \
             patch('tacto.process_responsibility.observe_responsibility', return_value={'status':'observed'}):
            result = comparison._responsibility_probe()
        self.assertEqual('owned_child_cleanup_timeout', result['cleanup_status'])
        self.assertTrue(result['child_started_in_new_session'])
        self.assertFalse(result['comparison_eligible'])
        child.kill.assert_called_once(); child.wait.assert_called_once()

    def test_responsibility_probe_setup_failure_does_not_expose_exception(self):
        with patch.object(sys, 'argv', ['comparison', '--check-responsibility']), \
             patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
             patch.object(comparison, '_responsibility_probe', side_effect=RuntimeError('PRIVATE')), \
             redirect_stdout(StringIO()) as out:
            self.assertEqual(2, comparison.main())
        self.assertEqual('responsibility_probe_unavailable', out.getvalue().strip())


if __name__ == '__main__':
    unittest.main()
