import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from tacto.access_log import AccessLog, AccessLoggingDriver, recent_logs, current


class AccessLogTests(unittest.TestCase):
    def test_actual_receipts_exclude_argument_response_and_exception_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            log = AccessLog('first-run', root=Path(folder)/'Tacto/Logs')
            inner = Mock()
            inner.observe.return_value = dict(type='observation', tree='SECRET screen contents')
            inner.act.return_value = dict(type='acted', text='SECRET text', detail='SECRET result')
            inner.ask.side_effect = ValueError('SECRET exception')
            driver = AccessLoggingDriver(inner, log)
            observed = driver.observe('com.apple.TextEdit')
            self.assertIs(observed, inner.observe.return_value)
            action = dict(name='set_value', value='SECRET input')
            driver.act('SECRET observation id', 'com.apple.TextEdit', action)
            with self.assertRaisesRegex(ValueError, 'SECRET exception'):
                driver.ask('SECRET prompt')
            log.task_result(dict(status='failed', stage='verify', error='new_window_not_unique',
                                 text='SECRET document', detail='SECRET detail', identity_reads=8))
            log.finish(1); log.close()
            data = log.path.read_text()
            self.assertNotIn('SECRET', data)
            events = [json.loads(line) for line in data.splitlines()]
            self.assertEqual('command_start', events[0]['event'])
            self.assertEqual('command_end', events[-1]['event'])
            self.assertEqual(1, events[-1]['exit_code'])
            self.assertTrue(any(e.get('action') == 'set_value' for e in events))
            self.assertTrue(any(e.get('error') == 'new_window_not_unique' for e in events))
            inner.act.assert_called_once_with('SECRET observation id', 'com.apple.TextEdit', action)
            if os.name != 'nt':
                self.assertEqual(0o600, log.path.stat().st_mode & 0o777)
                self.assertEqual(0o700, log.path.parent.stat().st_mode & 0o777)

    def test_symlink_directory_and_file_are_not_followed_and_logging_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder); (base/'outside').mkdir(); (base/'Tacto').symlink_to(base/'outside')
            with contextlib.redirect_stderr(io.StringIO()):
                log = AccessLog('observe', root=base/'Tacto/Logs')
            self.assertIsNone(log.fd)
            self.assertFalse((base/'outside/Logs').exists())
            root = base/'Good/Logs'; log = AccessLog('observe', root=root); log.close()
            secret = base/'secret'; secret.write_text('PRIVATE')
            (root/('tacto-20260101T000000000000Z-'+'0'*32+'.jsonl')).symlink_to(secret)
            self.assertEqual(1, len(recent_logs(root)['files']))
            self.assertEqual('PRIVATE', secret.read_text())

    def test_retention_and_truncation_preserve_other_files_and_command_outcome(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'Tacto/Logs'; root.mkdir(parents=True)
            sentinel = root/'keep.jsonl'; sentinel.write_text('KEEP')
            with patch('tacto.access_log.MAX_FILES', 2):
                for _ in range(3):
                    log = AccessLog('observe', root=root); log.close()
            self.assertEqual(2, len(recent_logs(root)['files']))
            with patch('tacto.access_log.MAX_FILE', 1):
                log = AccessLog('observe', root=root)
                log.event('access_start', operation='observe')
                log.event('access_start', operation='observe')
                log.finish(0); log.close()
            events = [json.loads(line) for line in log.path.read_text().splitlines()]
            self.assertEqual(1, sum(e['event']=='log_truncated' for e in events))
            self.assertEqual('command_end', events[-1]['event'])
            self.assertEqual('KEEP', sentinel.read_text())

    def test_main_covers_connection_failure_and_resets_scope_without_stdout_noise(self):
        from tacto import cli
        from tacto.helper import HelperUnavailable
        with tempfile.TemporaryDirectory() as folder, \
             patch('tacto.access_log.log_directory', return_value=Path(folder)/'Tacto/Logs'), \
             patch.object(cli, '_make_driver', side_effect=HelperUnavailable('SECRET')), \
             patch('tacto.session.sandboxed', return_value=False), \
             patch('tacto.session.KeepAwake'), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(1, cli.main(['first-run', '--helper', '--yes']))
            self.assertEqual('helper_unavailable', json.loads(output.getvalue())['error'])
            self.assertIsNone(current.get())
            files = list((Path(folder)/'Tacto/Logs').glob('*.jsonl'))
            self.assertEqual(1, len(files))
            data = files[0].read_text()
            self.assertNotIn('SECRET', data)
            self.assertEqual('command_end', json.loads(data.splitlines()[-1])['event'])

    def test_successful_cli_uses_same_wrapped_connection_and_records_accesses(self):
        from tacto import cli
        native = Mock(); native.capabilities = {'observe.window_identity'}
        native.observe.return_value = dict(type='observation', tree='SECRET screen')
        native.act.return_value = dict(type='acted')
        def task(driver):
            self.assertEqual(native.capabilities, driver.capabilities)
            driver.observe('com.apple.TextEdit')
            driver.act('observation', 'com.apple.TextEdit', dict(name='set_value', value='SECRET input'))
            return dict(type='first_run', status='text_verified', stage='complete', foreground_verified=True)
        with tempfile.TemporaryDirectory() as folder, \
             patch('tacto.access_log.log_directory', return_value=Path(folder)/'Tacto/Logs'), \
             patch('tacto.helper.InheritedHelperDriver', return_value=native), \
             patch('tacto.first_run.run_first_task', side_effect=task), \
             patch('tacto.session.sandboxed', return_value=False), patch('tacto.session.KeepAwake'), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, cli.main(['first-run', '--helper', '--yes']))
            self.assertTrue(json.loads(output.getvalue())['foreground_verified'])
            native.close.assert_called_once()
            records = list((Path(folder)/'Tacto/Logs').glob('*.jsonl'))
            data = records[0].read_text()
            self.assertNotIn('SECRET', data)
            events = [json.loads(line) for line in data.splitlines()]
            self.assertEqual(['observe', 'act'], [e['operation'] for e in events if e['event']=='access_start'])
            self.assertTrue(any(e.get('foreground_verified') is True for e in events))

    def test_storage_failure_does_not_change_action_result_or_repeat_dispatch(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stderr(io.StringIO()), \
             patch('tacto.access_log.os.write', side_effect=OSError('SECRET disk failure')):
            log = AccessLog('observe', root=Path(folder)/'Tacto/Logs')
            inner = Mock(); inner.act.return_value = {'type':'acted'}
            driver = AccessLoggingDriver(inner, log)
            self.assertIs(driver.act('obs', 'com.apple.TextEdit', {'name':'read'}), inner.act.return_value)
            inner.act.assert_called_once()
            log.finish(0); log.close()

    def test_a_failed_run_names_its_reason_without_free_text(self):
        # 29 of 50 failed receipts on 2026-10-24..25 ended with exit 1 and no
        # code, so the history said only "failed".
        from tacto import cli
        with tempfile.TemporaryDirectory() as folder, \
             patch('tacto.access_log.log_directory', return_value=Path(folder)/'Tacto/Logs'), \
             contextlib.redirect_stderr(io.StringIO()):
            flow = Path(folder)/'flow.json'
            flow.write_text('{"SECRET": ')
            self.assertEqual(2, cli.main(['run', str(flow)]))
            rows = [json.loads(line) for line in
                    next((Path(folder)/'Tacto/Logs').glob('*.jsonl')).read_text().splitlines()]
        result = next(r for r in rows if r['event'] == 'task_result')
        self.assertEqual(('failed', 'invalid_flow', 'load'), (result['status'], result['error'], result['stage']))
        self.assertNotIn('SECRET', json.dumps(rows))
        self.assertEqual(2, rows[-1]['exit_code'])

    def test_record_failure_keeps_only_closed_codes(self):
        from tacto.access_log import record_failure
        with tempfile.TemporaryDirectory() as folder:
            log = AccessLog('run', root=Path(folder)/'Logs')
            token = current.set(log)
            try:
                record_failure('Selector "SECRET" missing', 'replay')
            finally:
                current.reset(token)
                log.close()
            row = json.loads(log.path.read_text().splitlines()[-1])
        self.assertEqual(('unknown_failure', 'replay'), (row['error'], row['stage']))

    def test_cli_logs_lists_without_screen_or_new_log(self):
        from tacto import cli
        with tempfile.TemporaryDirectory() as folder, \
             patch('tacto.access_log.log_directory', return_value=Path(folder)/'Tacto/Logs'), \
             patch.object(cli, '_make_driver') as make, contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(0, cli.main(['logs', '--json']))
            self.assertEqual([], json.loads(output.getvalue())['files'])
            make.assert_not_called()
            self.assertFalse((Path(folder)/'Tacto/Logs').exists())
