"""Real socket closure at the native probe boundary and evaluator isolation."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from tacto.helper import InheritedHelperDriver, HelperUnavailable

ROOT = Path(__file__).resolve().parents[1]
PROBE = runpy.run_path(str(ROOT / 'examples/probes/signed-commit-disconnect.py'))


class SignedCommitDisconnectTests(unittest.TestCase):
    def test_buffered_reply_is_discarded_and_no_following_frame_is_written(self):
        client, peer = socket.socketpair()
        peer.settimeout(2)
        self.addCleanup(client.close)
        self.addCleanup(peer.close)
        committed = threading.Event()
        received, errors = [], []
        def server():
            stream = peer.makefile('rb')
            try:
                received.append(json.loads(stream.readline()))
                peer.sendall(b'{"type":"acted"}\n')
                committed.set()
                following = stream.readline()
                if following: received.append(json.loads(following))
            except Exception as exc:
                errors.append(type(exc).__name__)
            finally:
                stream.close()
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        stream = client.makefile('rw', encoding='utf-8', newline='\n')
        self.addCleanup(stream.close)
        wrapped = PROBE['DisconnectAfterCommit'](stream, client, committed.is_set)
        driver = object.__new__(InheritedHelperDriver)
        driver._sock, driver._f = client, wrapped
        with self.assertRaises(HelperUnavailable):
            driver._send({'type':'act', 'target':PROBE['TARGET'],
                          'action':{'name':'press_key', 'key':'Return'}})
        with self.assertRaises(HelperUnavailable):
            driver._send({'type':'act', 'target':PROBE['TARGET'],
                          'action':{'name':'press_key', 'key':'Return'}})
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(received), 1)
        self.assertEqual(wrapped.commit_writes, 1)
        self.assertEqual(wrapped.writes, 1)
        self.assertEqual(wrapped.reads_after_close, 1)
        self.assertTrue(driver._poisoned and wrapped.closed and stream.closed)
        self.assertEqual(client.fileno(), -1)

    def evaluator(self):
        spec = importlib.util.spec_from_file_location('disconnect_eval_test', ROOT / 'examples/eval.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_real_scenario_is_declared_gui_native_isolated_and_skips_offline(self):
        module = self.evaluator()
        _, selected, cases = module._parse_plan(['signed_commit_disconnect', '--offline'])
        self.assertEqual(len(selected), 1)
        self.assertTrue(cases[0]['isolated'])
        self.assertEqual(set(cases[0]['requirements']), {'gui', 'native'})
        self.assertEqual(cases[0]['status'], 'skipped')
        self.assertIn(cases[0]['reason_code'], ('offline', 'native_unavailable'))

    def test_connection_loss_scenario_cannot_share_a_launcher_with_another(self):
        module = self.evaluator()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            module._parse_plan(['signed_commit_disconnect', 'signed_element_click'])
        self.assertEqual(raised.exception.code, 2)

    def test_scenario_failure_output_excludes_private_input_audit_samples(self):
        module = self.evaluator()
        report = {'passed':False, 'error_class':'AssertionError',
                  'input_audit':{'raw_report':{'baseline':'PRIVATE_INPUT_SENTINEL'}}}
        with patch('runpy.run_path', return_value={'main':Mock(return_value=report)}), \
                patch('tacto.helper.InheritedHelperDriver') as driver, \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                self.assertRaises(AssertionError) as failure:
            driver.available.return_value = True
            module.scenario_signed_commit_disconnect()
        self.assertNotIn('PRIVATE_INPUT_SENTINEL', str(failure.exception))
        self.assertNotIn('PRIVATE_INPUT_SENTINEL', out.getvalue())


if __name__ == '__main__':
    unittest.main()
