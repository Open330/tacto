import io
import json
import unittest
from unittest.mock import patch

from tacto import ask_provider
from tacto.provider_result import ProviderError, ProviderResult


class AskProviderEnvelopeTests(unittest.TestCase):
    def invoke(self, arguments):
        output, errors = io.StringIO(), io.StringIO()
        with (patch('sys.argv', ['ask_provider', *arguments]), patch('sys.stdin', io.StringIO('host prompt')),
              patch('sys.stdout', output), patch('sys.stderr', errors)):
            code = ask_provider.main()
        return code, output.getvalue(), errors.getvalue()

    def test_success_preserves_observed_metadata_and_json_answer_option(self):
        result = ProviderResult(request_id='local-id', requested_provider='claude',
            provider='claude', transport='cli-json', status='ok', text='{"ok":true}',
            error=None, elapsed_seconds=1.25, model='observed-model', input_tokens=12,
            output_tokens=3, cost=0.001, cost_currency='USD', cost_basis='cli_reported_list_estimate')
        with (patch('tacto.authoring.ask_model_result', return_value=result) as ask,
              patch('tacto.authoring._ask_model', side_effect=AssertionError('wrong provider boundary'))):
            code, output, errors = self.invoke(['--result-envelope','--json','--provider','claude','--timeout','2.5'])
        self.assertEqual((code, errors), (0, ''))
        self.assertEqual(json.loads(output), result.to_dict())
        ask.assert_called_once_with('host prompt', 'claude', timeout=2.5, expect_json=True)

    def test_structured_error_and_timeout_exit_zero_with_unknown_telemetry(self):
        for status in ('error', 'timeout'):
            result = ProviderResult(request_id='local-id', requested_provider='auto',
                provider=None, transport=None, status=status, text=None,
                error=ProviderError('Failure', 'Model request failed'), elapsed_seconds=1)
            with self.subTest(status=status), patch('tacto.authoring.ask_model_result', return_value=result):
                code, output, errors = self.invoke(['--result-envelope'])
            self.assertEqual((code, errors), (0, ''))
            parsed=json.loads(output)
            self.assertEqual(parsed['status'],status)
            for key in ('model','provider','cost','input_tokens','provider_request_id'):
                self.assertIsNone(parsed[key])

    def test_unexpected_error_does_not_leak_exception(self):
        with patch('tacto.authoring.ask_model_result', side_effect=RuntimeError('secret prompt stderr')):
            code, output, errors = self.invoke(['--result-envelope'])
        self.assertEqual(code, 1)
        self.assertEqual(output, '')
        self.assertEqual(errors, 'tacto.ask_provider: model envelope unavailable\n')

    def test_legacy_text_mode_keeps_json_option_and_timeout_exit(self):
        with (patch('tacto.authoring._ask_model', return_value='{"answer":1}') as ask,
              patch('tacto.authoring.ask_model_result', side_effect=AssertionError('wrong boundary'))):
            code, output, errors = self.invoke(['--json','--timeout','4'])
        self.assertEqual((code,output,errors),(0,'{"answer":1}',''))
        ask.assert_called_once_with('host prompt','auto',timeout=4,expect_json=True)
        with patch('tacto.authoring._ask_model', side_effect=TimeoutError('secret')):
            code, output, errors = self.invoke([])
        self.assertEqual(code,124)
        self.assertEqual(output,'')
        self.assertNotIn('secret', errors)


if __name__ == '__main__': unittest.main()
