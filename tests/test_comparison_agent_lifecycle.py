"""Both execution arms keep owned process diagnostics without granting parity."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from unittest.mock import patch,Mock

from tests.test_product_comparison_provenance import comparison, Provenance


class AgentLifecycle(unittest.TestCase):
    def test_missing_executable_live_probe_reports_preflight_without_model_or_process_observation(self):
        with patch.object(comparison,'_agent_executable_available',return_value=False), \
             patch.object(comparison,'_run_agent') as run, \
             patch('tacto.process_responsibility.observe_responsibility') as observe:
            result=comparison._live_agent_probe()
        self.assertEqual('agent_executable_unavailable_in_spawn_environment',result['reason'])
        self.assertFalse(result['agent_executed']);self.assertFalse(result['comparison_eligible'])
        self.assertFalse(result['source_changed']);run.assert_not_called();observe.assert_not_called()

    def test_missing_executable_preflight_never_opens_fixture_or_starts_model(self):
        from tacto.helper import InheritedHelperDriver
        setup,cleanup,oracle=Mock(),Mock(),Mock()
        task=comparison.Task('owned','goal',oracle,setup=setup,cleanup=cleanup)
        with patch.object(comparison,'_agent_executable_available',return_value=False), \
             patch.object(comparison,'_locked',return_value=False), \
             patch.object(InheritedHelperDriver,'available',return_value=True), \
             patch.object(comparison,'_run_agent') as run:
            result=comparison.run_one(task,'tacto')
        self.assertEqual('not_run',result['status'])
        self.assertIsNone(result['oracle_passed'])
        self.assertEqual('not_requested',result['cleanup_status'])
        self.assertEqual('agent_executable_unavailable_before_setup',result['observed'])
        for operation in (setup,cleanup,oracle,run):operation.assert_not_called()

    def test_availability_uses_spawn_path_and_absolute_executable_without_running(self):
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(comparison,'_agent_env',return_value={'PATH':folder}), \
             patch.object(comparison.subprocess,'Popen',side_effect=AssertionError('preflight must not execute')):
            with patch.object(comparison,'_AGENT_EXECUTABLE','codex'):
                self.assertFalse(comparison._agent_executable_available())
                executable=Path(folder)/'codex';executable.write_text('not a real CLI')
                executable.chmod(0o600)
                self.assertFalse(comparison._agent_executable_available())
                executable.chmod(0o700)
                self.assertTrue(comparison._agent_executable_available())
            with patch.object(comparison,'_AGENT_EXECUTABLE',sys.executable):
                self.assertTrue(comparison._agent_executable_available())

    def test_host_absolute_executable_is_used_without_changing_prompt_or_environment(self):
        records = []
        with patch.object(comparison, '_AGENT_EXECUTABLE', '/host/codex'), \
             patch('tacto.codex_result._run_codex') as run:
            comparison._run_agent(['codex', 'exec', '-'], 'owned prompt', 12, records)
        run.assert_called_once_with(['/host/codex', 'exec', '-'], 'owned prompt', 12, records, cwd='/tmp')

    def test_actual_owned_cli_starts_in_requested_comparison_directory(self):
        with patch.object(comparison, '_AGENT_EXECUTABLE', sys.executable):
            result = comparison._run_agent(['codex', '-c', 'import os;print(os.getcwd())'], '', 3, [])
        self.assertEqual(0, result.returncode)
        self.assertEqual(Path('/tmp').resolve(), Path(result.stdout.strip()).resolve())

    def test_relative_host_path_refused_before_any_agent_or_gui(self):
        with patch.object(sys, 'argv', ['comparison', '--agent-executable', './codex']), \
             patch.object(comparison, '_run_agent') as run, \
             redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            comparison.main()
        self.assertEqual(2, error.exception.code)
        run.assert_not_called()

    def test_diagnostics_do_not_turn_oracle_success_into_comparison_eligibility(self):
        fixture = Provenance()
        fixture.setUp()
        diagnostic = {'samples':[{'status':'observed'}], 'descendants_observed':False}
        try:
            for arm in ('tacto', 'sky'):
                result = fixture.execute(observations=[diagnostic], arm=arm)
                self.assertTrue(result['oracle_passed'])
                self.assertEqual([diagnostic], result['agent_primary_process_diagnostics'])
                self.assertFalse(result['comparison_eligible'])
        finally:
            fixture.doCleanups()

    def test_missing_agent_is_recorded_without_reading_oracle_or_private_error(self):
        fixture = Provenance()
        fixture.setUp()
        try:
            result = fixture.execute(launch_error=True)
        finally:
            fixture.doCleanups()
        self.assertEqual('not_run', result['status'])
        self.assertEqual('agent_launch_unavailable', result['agent_launch_error'])
        self.assertFalse(result['oracle_passed'])
        self.assertIsNone(result['process_exit'])
        self.assertNotIn('PRIVATE', str(result))

    def test_oserror_after_owned_spawn_is_failed_not_not_run(self):
        fixture = Provenance()
        fixture.setUp()
        try:
            result = fixture.execute(launch_error=True, observations=[{'samples':[]}])
        finally:
            fixture.doCleanups()
        self.assertEqual('failed', result['status'])
        self.assertEqual('agent_transport_failed', result['agent_launch_error'])
        self.assertFalse(result['oracle_passed'])

    def test_owned_real_timeout_uses_group_cleanup_and_keeps_observations(self):
        records = []
        with patch.object(comparison, '_AGENT_EXECUTABLE', sys.executable), \
             patch('tacto.process_responsibility.observe_responsibility', return_value={'status':'observed'}), \
             self.assertRaises(subprocess.TimeoutExpired):
            comparison._run_agent(['codex', '-c', 'import time;time.sleep(10)'], '', .03, records)
        self.assertEqual(1, len(records))
        self.assertTrue(records[0]['observer_stopped'])
        self.assertFalse(records[0]['tcc_permission_verified'])

    def test_timeout_stops_owned_descendant_delayed_write(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'delayed.txt'
            descendant = "import time,pathlib;time.sleep(.3);pathlib.Path(" + repr(str(output)) + ").write_text('unexpected')"
            parent = "import subprocess,sys,time;subprocess.Popen([sys.executable,'-c'," + repr(descendant) + "]);print('ready',flush=True);time.sleep(10)"
            with patch.object(comparison, '_AGENT_EXECUTABLE', sys.executable), \
                 self.assertRaises(subprocess.TimeoutExpired) as error:
                comparison._run_agent(['codex', '-c', parent], '', .15, [])
            self.assertIn(b'ready', error.exception.stdout)
            time.sleep(.4)
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
