"""Never start or clean up GUI work on an unobservable/locked session."""
import contextlib
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from tests.test_product_comparison_provenance import comparison


class ProductReadinessTests(unittest.TestCase):
    def setUp(self):
        channel=patch('tacto.helper.InheritedHelperDriver.available',return_value=True)
        channel.start();self.addCleanup(channel.stop)
        spawn=patch('tacto.codex_result.subprocess.Popen',side_effect=AssertionError('readiness unit tests must not spawn an external process'))
        spawn.start();self.addCleanup(spawn.stop)

    def execute(self, locks, exit_code=0):
        setup, cleanup, oracle = Mock(), Mock(), Mock(return_value=(True, 'independent pass'))
        task = comparison.Task('probe', 'goal', oracle, setup=setup, cleanup=cleanup)

        class Witness:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def summary(self): return {'activations': []}

        def agent(argv, prompt, timeout, diagnostics):
            Path(argv[argv.index('-o') + 1]).write_text('answer')
            return subprocess.CompletedProcess(argv, exit_code, '', '')

        with patch.dict(sys.modules, {'focus_witness': types.SimpleNamespace(FocusWitness=Witness)}), \
             patch.object(comparison, '_locked', side_effect=locks), \
             patch.object(comparison, 'source_manifest', return_value={'sha256':'fixed', 'files':{}}), \
             patch.object(comparison, '_run_agent', side_effect=agent) as model:
            result = comparison.run_one(task, 'tacto')
        return result, setup, cleanup, oracle, model

    def test_no_setup_model_or_cleanup_when_initial_session_unavailable(self):
        for lock in (True, None):
            with self.subTest(lock=lock):
                result, setup, cleanup, oracle, model = self.execute([lock])
                self.assertEqual('not_run', result['status'])
                self.assertIsNone(result['oracle_passed'])
                for call in (setup, cleanup, oracle, model): call.assert_not_called()

    def test_lock_after_setup_stops_before_model_and_leaves_fixture(self):
        result, setup, cleanup, oracle, model = self.execute([False, True])
        setup.assert_called_once()
        for call in (cleanup, oracle, model): call.assert_not_called()
        self.assertEqual('environment_unavailable_left_as_is', result['cleanup_status'])

    def test_failed_attempt_stays_failed_when_lock_is_observed(self):
        result, setup, cleanup, oracle, model = self.execute([False, False, True], exit_code=1)
        model.assert_called_once()
        cleanup.assert_not_called()
        self.assertEqual('failed', result['status'])
        self.assertTrue(result['oracle_passed'])
        self.assertFalse(result['comparison_eligible'])

    def test_successful_attempt_can_be_excluded_without_erasing_outcome(self):
        result, _, cleanup, _, _ = self.execute([False, False, None])
        self.assertEqual('passed', result['status'])
        self.assertFalse(result['comparison_eligible'])
        cleanup.assert_not_called()

    def test_observable_success_allows_owned_cleanup(self):
        result, _, cleanup, _, _ = self.execute([False, False, False])
        self.assertEqual('passed', result['status'])
        self.assertFalse(result['comparison_eligible'])
        self.assertEqual('agent_responsibility_not_isolated_or_observed',result['comparison_exclusion_reason'])
        cleanup.assert_called_once()

    def test_invalid_cli_filters_cannot_call_run_one(self):
        for args in (['--only', 'unknown'], ['--only'], ['--reps', '0'], ['--reps', '-2']):
            with self.subTest(args=args), patch.object(sys, 'argv', ['comparison'] + args), \
                 patch.object(comparison, 'run_one') as run, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    comparison.main()
                self.assertEqual(2, error.exception.code)
                run.assert_not_called()

    def test_cli_outcomes_are_not_always_exit_zero(self):
        for status, eligible, expected in [('passed', True, 0), ('failed', True, 1),
                                            ('timeout', False, 1), ('invalid', False, 1),
                                            ('not_run', False, 2), ('passed', False, 2)]:
            result = dict(status=status, comparison_eligible=eligible, seconds=0,
                          usage=None, activations=[], observed='probe')
            with self.subTest(status=status, eligible=eligible), tempfile.TemporaryDirectory() as tmp, \
                 patch.object(comparison, 'ROOT', Path(tmp)), \
                 patch.object(comparison, '_locked', return_value=False), \
                 patch.object(comparison, 'run_one', return_value=result), \
                 patch.object(sys, 'argv', ['comparison', '--only', 'appearance_mode', '--arms', 'tacto']), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(expected, comparison.main())


if __name__ == '__main__':
    unittest.main()
