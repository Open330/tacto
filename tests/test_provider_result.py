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
from tacto.provider_result import ProviderResult
from unittest.mock import patch

from tacto.authoring import ask_model_result, reach_model_result, _run_provider


class ProviderResultTests(unittest.TestCase):
    def test_opencode_zero_exit_auth_error_is_never_an_answer(self):
        frame={'type':'error','timestamp':1791684300370,'sessionID':'ses_owned',
            'error':{'name':'UnknownError','data':{'message':'Error: Token refresh failed: 401'}}}
        from tacto.provider_result import classify_unavailable
        output=json.dumps(frame)
        self.assertEqual('authentication_failed',classify_unavailable(output,provider='opencode'))
        self.assertIsNone(classify_unavailable(output,provider='claude'))
        for want_json in (False,True):
            with patch('tacto.authoring.shutil.which',return_value='/owned/opencode'), \
                 patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],0,output,'')):
                result=ask_model_result('q','opencode',expect_json=want_json)
            self.assertEqual('authentication_failed',result.error.code)
            self.assertEqual('error',result.status);self.assertIsNone(result.text)
            self.assertIsNone(result.cost);self.assertIsNone(result.input_tokens)

    def test_opencode_generic_model_error_object_is_not_cli_auth_refusal(self):
        from tacto.provider_result import classify_unavailable
        for value in ('Token refresh failed: 401','an explanation of Error: Token refresh failed: 401',
                      json.dumps({'type':'error','error':{'message':'Error: Token refresh failed: 401'}})):
            self.assertIsNone(classify_unavailable(value,provider='opencode'))


    def test_observed_unsupported_client_is_closed_and_keeps_usage_unknown(self):
        from tacto.provider_result import classify_unavailable
        from tacto.provider_observation import provider_failure_reason
        output='Error authenticating: IneligibleTierError: PRIVATE account details UNSUPPORTED_CLIENT'
        self.assertEqual('unsupported_client',classify_unavailable('',output,provider='gemini'))
        for provider in (None,'claude','codex','opencode'):
            self.assertIsNone(classify_unavailable(output,provider=provider))
        for value in ('UNSUPPORTED_CLIENT','This client is no longer supported',
                      'Error authenticating: IneligibleTierError: other reason'):
            self.assertIsNone(classify_unavailable(value,provider='gemini'))
        with patch('tacto.authoring.shutil.which',return_value='/owned/gemini'), \
             patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],55,'',output)):
            result=ask_model_result('private prompt','gemini',expect_json=True)
        self.assertEqual('error',result.status)
        self.assertEqual('unsupported_client',result.error.code)
        self.assertIsNone(result.input_tokens);self.assertIsNone(result.cost)
        self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
        self.assertIn('클라이언트',provider_failure_reason([result.to_dict()]))


    def test_auto_records_actual_fallback_without_inventing_telemetry(self):
        def muxa(provider, prompt, timeout):
            return '{"ok":true}' if provider == 'codex' else None
        with patch('tacto.authoring._try_muxa', side_effect=muxa), \
             patch('tacto.authoring._try_cli', return_value=None):
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
        with patch('tacto.authoring._try_muxa', side_effect=ValueError('SECRET')) , \
             patch('tacto.authoring._try_cli', return_value='malformed SECRET'):
            result = ask_model_result('question', 'codex', expect_json=True)
        self.assertEqual('error', result.status)
        self.assertEqual('codex', result.provider)
        self.assertIsNone(result.text)
        self.assertNotIn('SECRET', json.dumps(result.to_dict()))

    def test_deadline_is_timeout_even_if_last_transport_expires(self):
        with patch('tacto.authoring._try_muxa', return_value=None), \
             patch('tacto.authoring._try_cli', side_effect=subprocess.TimeoutExpired('private', 1)):
            result = ask_model_result('q', 'codex')
        self.assertEqual('timeout', result.status)
        self.assertIsNone(result.text)
        self.assertEqual('TimeoutError', result.error.code)
        self.assertFalse(result.cancellation_supported)

    def test_launcher_auto_cannot_claim_provider_identity(self):
        with patch('tacto.helper.InheritedHelperDriver') as helper:
            helper.available.return_value = True
            helper.return_value.ask_launcher_result.return_value = ProviderResult(
                str(uuid.uuid4()), 'auto', None, 'launcher', 'ok', '{}', None, 0)
            result = reach_model_result('q', expect_json=True)
            helper.return_value.ask_launcher_result.assert_called_once_with('q', True, provider='auto', timeout_seconds=420)
        self.assertEqual('ok', result.status)
        self.assertEqual('launcher', result.transport)
        self.assertIsNone(result.provider)

    def test_launcher_pinned_and_request_ids_are_local_unique(self):
        with patch('tacto.helper.InheritedHelperDriver') as helper:
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


class CliUnavailableTests(unittest.TestCase):
    def run_cli(self, code, stdout, stderr=''):
        from tacto.authoring import _try_cli
        done = subprocess.CompletedProcess([], code, stdout, stderr)
        with patch('tacto.authoring.shutil.which', return_value='/bin/codex'), \
             patch('tacto.authoring._run_provider', return_value=done):
            return _try_cli('opencode', 'q', 5)

    def test_failed_run_is_named_when_a_person_can_fix_it(self):
        for code, out, err, reason in ((1, '', 'usage limit reached SECRET', 'usage_limit'),
                                       (0, '', 'Please run /login', 'not_logged_in')):
            result=self.run_cli(code,out,err)
            self.assertEqual('error',result.status)
            self.assertEqual(reason,result.error.code)
            self.assertNotIn('SECRET',str(result.to_dict()))

    def test_unknown_failure_and_unframed_text_are_not_answers(self):
        for code,text in ((1,''),(0,'ordinary unframed answer'),(0,'Please run /login')):
            result=self.run_cli(code,text,'something else')
            self.assertEqual('error',result.status)
            self.assertIsNone(result.text)
            self.assertEqual('OpenCodeProtocolError',result.error.code)

    def test_attempts_carry_the_reason_code_not_provider_output(self):
        from tacto.provider_result import ProviderUnavailable
        with patch('tacto.authoring._try_muxa', return_value=None), \
             patch('tacto.authoring._try_cli', side_effect=ProviderUnavailable('usage_limit')):
            result = ask_model_result('q', 'codex', expect_json=True)
        self.assertEqual('error', result.status)
        self.assertIn('usage_limit', result.error.message)


if __name__ == '__main__':
    unittest.main()
