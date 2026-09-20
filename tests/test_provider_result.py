"""Observable provider envelopes and bounded local process lifetime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from nlc.provider_result import ProviderResult
from unittest.mock import patch

from nlc.authoring import ask_model_result, reach_model_result, _run_provider


class ProviderResultTests(unittest.TestCase):
    def test_auto_records_actual_fallback_without_inventing_telemetry(self):
        def muxa(provider, prompt, timeout):
            return '{"ok":true}' if provider == 'codex' else None
        with patch('nlc.authoring._try_muxa', side_effect=muxa), \
             patch('nlc.authoring._try_cli', return_value=None):
            result = ask_model_result('private prompt', expect_json=True)
        self.assertEqual(('ok', 'codex', 'muxa'),
                         (result.status, result.provider, result.transport))
        self.assertEqual('auto', result.requested_provider)
        self.assertGreaterEqual(result.elapsed_seconds, 0)
        for field in ('model', 'provider_request_id', 'input_tokens',
                      'output_tokens', 'cost', 'cost_currency'):
            self.assertIsNone(result.to_dict()[field])
        self.assertFalse(result.cancellation_supported)
        self.assertNotIn('private prompt', json.dumps(result.to_dict()))

    def test_failure_is_structured_and_does_not_leak_provider_output(self):
        with patch('nlc.authoring._try_muxa', side_effect=ValueError('SECRET')) , \
             patch('nlc.authoring._try_cli', return_value='malformed SECRET'):
            result = ask_model_result('question', 'codex', expect_json=True)
        self.assertEqual('error', result.status)
        self.assertEqual('codex', result.provider)
        self.assertIsNone(result.text)
        self.assertNotIn('SECRET', json.dumps(result.to_dict()))

    def test_deadline_is_timeout_even_if_last_transport_expires(self):
        with patch('nlc.authoring._try_muxa', return_value=None), \
             patch('nlc.authoring._try_cli', side_effect=subprocess.TimeoutExpired('private', 1)):
            result = ask_model_result('q', 'codex')
        self.assertEqual('timeout', result.status)
        self.assertIsNone(result.text)
        self.assertEqual('TimeoutError', result.error.code)
        self.assertFalse(result.cancellation_supported)

    def test_launcher_auto_cannot_claim_provider_identity(self):
        with patch('nlc.helper.InheritedHelperDriver') as helper:
            helper.available.return_value = True
            helper.return_value.ask_launcher_result.return_value = ProviderResult(
                str(uuid.uuid4()), 'auto', None, 'launcher', 'ok', '{}', None, 0)
            result = reach_model_result('q', expect_json=True)
            helper.return_value.ask_launcher_result.assert_called_once_with('q', True, provider='auto', timeout_seconds=420)
        self.assertEqual('ok', result.status)
        self.assertEqual('launcher', result.transport)
        self.assertIsNone(result.provider)

    def test_launcher_pinned_and_request_ids_are_local_unique(self):
        with patch('nlc.helper.InheritedHelperDriver') as helper:
            helper.available.return_value = True
            helper.return_value.ask_launcher_result.side_effect = lambda *a, **k: ProviderResult(
                str(uuid.uuid4()), 'gemini', 'gemini', 'launcher', 'ok', 'answer', None, 0)
            a = reach_model_result('q', 'gemini')
            b = reach_model_result('q', 'gemini')
        self.assertEqual('gemini', a.provider)
        self.assertNotEqual(a.request_id, b.request_id)
        self.assertIsNone(a.provider_request_id)

    @unittest.skipUnless(os.name == 'posix', 'process groups require POSIX')
    def test_timeout_kills_child_process_group_before_delayed_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / 'unexpected-write')
            child = 'import time,pathlib; time.sleep(1.2); pathlib.Path(' + repr(marker) + ').write_text("alive")'
            parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(10)'
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_provider([sys.executable, '-c', parent], timeout=0.4)
            self.assertLess(time.monotonic() - started, 3)
            time.sleep(1.2)
            self.assertFalse(Path(marker).exists(), 'provider descendant survived timeout')


if __name__ == '__main__':
    unittest.main()
