"""Process diagnostics and private channel cleanup never become model authority."""
import os
import sys
import subprocess
import unittest
from unittest.mock import Mock, patch

from tacto.authoring import _provider_env
from tacto.codex_result import _run_codex, codex_result
from tacto.process_responsibility import OwnedProcessSampler


class ProviderProcessObservationTests(unittest.TestCase):
    def test_channel_markers_are_removed_even_outside_claude_session(self):
        env = {'TACTO_HELPER_FD':'3', 'TACTO_LAUNCHER':'1', 'TACTO_HELPER_TOKEN':'fixture',
               'CODEX_HOME':'/owned/profile', 'ANTHROPIC_API_KEY':'fixture-credential'}
        for claude in ('', '1'):
            with self.subTest(claude=claude), patch.dict(os.environ, {**env, 'CLAUDECODE':claude}):
                result = _provider_env()
                for key in ('TACTO_HELPER_FD', 'TACTO_LAUNCHER', 'TACTO_HELPER_TOKEN'):
                    self.assertNotIn(key, result)
                    self.assertEqual(env[key], os.environ[key])
                self.assertEqual(env['CODEX_HOME'], result['CODEX_HOME'])
                self.assertEqual(env['ANTHROPIC_API_KEY'], result['ANTHROPIC_API_KEY'])

    def test_observer_failure_does_not_change_actual_child_output(self):
        records = []
        with patch('tacto.process_responsibility.observe_responsibility', side_effect=RuntimeError('PRIVATE')):
            result = _run_codex([sys.executable, '-c', 'import sys;print(sys.stdin.read())'],
                                'owned input', 3, records)
        self.assertEqual(0, result.returncode)
        self.assertEqual('owned input', result.stdout.strip())
        self.assertEqual(1, len(records))
        self.assertEqual('process_observation_failed', records[0]['error_code'])
        self.assertNotIn('PRIVATE', str(records))

    def test_process_exiting_during_read_cannot_supply_a_sample(self):
        process = Mock(pid=42)
        process.poll.side_effect = [None, 0]
        sampler = OwnedProcessSampler(process)
        with patch('tacto.process_responsibility.observe_responsibility', return_value={'status':'observed'}):
            sampler._observe()
        self.assertEqual([], sampler.snapshot()['samples'])
        self.assertEqual('owned_process_exited_during_read', sampler.snapshot()['error_code'])

    def test_sample_limit_and_snapshot_copy_preserve_diagnostic_scope(self):
        sampler = OwnedProcessSampler(Mock(pid=42, poll=Mock(return_value=None)))
        sampler.stop = Mock(is_set=Mock(return_value=False), wait=Mock(return_value=False))
        with patch('tacto.process_responsibility.observe_responsibility', return_value={'status':'observed'}):
            sampler._observe()
        result = sampler.snapshot()
        self.assertTrue(result['sample_limit_reached'])
        self.assertEqual(64, len(result['samples']))
        result['samples'][0]['status'] = 'changed by caller'
        self.assertEqual('observed', sampler.snapshot()['samples'][0]['status'])
        self.assertFalse(result['descendants_observed'])
        self.assertFalse(result['tcc_permission_verified'])

    def test_invalid_host_observation_sink_refuses_before_process_launch(self):
        with patch('tacto.codex_result._run_codex') as run:
            result = codex_result('owned', process_observations={})
        self.assertEqual('error', result.status)
        run.assert_not_called()

    def test_timeout_reaps_owned_child_and_stops_observer_without_losing_diagnostics(self):
        records, children = [], []
        launch = subprocess.Popen
        def capture(*args, **kwargs):
            child = launch(*args, **kwargs)
            children.append(child)
            return child
        with patch('tacto.codex_result.subprocess.Popen', side_effect=capture), \
             patch('tacto.process_responsibility.observe_responsibility', return_value={'status':'observed'}), \
             self.assertRaises(subprocess.TimeoutExpired):
            _run_codex([sys.executable, '-c', 'import time;time.sleep(10)'],
                       'owned', .03, records)
        self.assertEqual(1, len(children))
        self.assertIsNotNone(children[0].poll())
        self.assertEqual(1, len(records))
        self.assertTrue(records[0]['observer_stopped'])


if __name__ == '__main__':
    unittest.main()
