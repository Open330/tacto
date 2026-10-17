"""No model calls: all provider boundaries are mocked."""
import unittest
from dataclasses import replace
from unittest.mock import patch
from tacto.authoring import _ask_model, _try_muxa, ask_model_result
from tacto.provider_result import ProviderResult


def answer(**updates):
    base = ProviderResult('adapter-request', 'claude', 'claude', 'cli-json',
                          'ok', '{"ok":true}', None, .1, model='observed-model',
                          input_tokens=12, output_tokens=4, cached_input_tokens=2,
                          cost=.01, cost_currency='USD',
                          cost_basis='cli_reported_list_estimate',
                          provider_thread_id='observed-session')
    return replace(base, **updates)


class PlanningBoundaryTests(unittest.TestCase):
    def test_codex_cli_uses_structured_adapter_and_preserves_usage(self):
        observed = answer(requested_provider='codex', provider='codex', model=None,
                          input_tokens=100, output_tokens=10, cached_input_tokens=60,
                          input_tokens_basis='total', total_input_tokens=100,
                          cost=None, cost_currency=None, cost_basis=None)
        with patch('tacto.codex_result.codex_result', return_value=observed) as adapter, \
             patch('tacto.authoring._try_muxa', return_value=None), \
             patch('tacto.authoring.shutil.which', return_value='/bin/codex'), \
             patch('tacto.authoring._run_provider') as old_cli:
            result = ask_model_result('private prompt', 'codex', expect_json=True)
        self.assertEqual(result.status, 'ok')
        self.assertEqual(result.transport, 'cli-json')
        self.assertEqual((result.input_tokens, result.total_input_tokens, result.cached_input_tokens),
                         (100, 100, 60))
        self.assertIsNone(result.model)
        self.assertIsNone(result.cost)
        self.assertEqual(result.attempts[-1].input_tokens, 100)
        adapter.assert_called_once()
        old_cli.assert_not_called()

    def test_claude_only_uses_restricted_adapter_and_propagates_observed_metadata(self):
        with patch('tacto.claude_result.claude_result', return_value=answer()) as adapter, \
             patch('tacto.authoring._try_muxa') as muxa, \
             patch('tacto.authoring._run_provider') as old_cli:
            result = ask_model_result('private prompt', 'claude', expect_json=True)
        self.assertEqual('ok', result.status)
        self.assertEqual('observed-model', result.model)
        self.assertEqual((12,4,2,.01), (result.input_tokens,result.output_tokens,
                                     result.cached_input_tokens,result.cost))
        self.assertEqual('cli_reported_list_estimate', result.cost_basis)
        self.assertEqual('observed-session', result.provider_thread_id)
        self.assertNotEqual('adapter-request', result.request_id)
        self.assertIsNone(result.provider_request_id)
        self.assertFalse(result.cancellation_supported)
        adapter.assert_called_once()
        muxa.assert_not_called(); old_cli.assert_not_called()

    def test_failure_timeout_and_malformed_result_never_use_unsafe_fallback(self):
        for response in (answer(status='error', text=None),
                         answer(status='timeout', text=None), answer(text='SECRET malformed')):
            with self.subTest(status=response.status), \
                 patch('tacto.claude_result.claude_result', return_value=response), \
                 patch('tacto.authoring._try_muxa') as muxa, \
                 patch('tacto.authoring._run_provider') as old_cli:
                result = ask_model_result('q', 'claude', expect_json=True)
                self.assertEqual('timeout' if response.status=='timeout' else 'error',result.status)
                self.assertNotIn('SECRET',str(result.to_dict()))
                self.assertIsNone(result.model)
                muxa.assert_not_called(); old_cli.assert_not_called()

    def test_cli_mock_remains_a_complete_interception_boundary(self):
        with patch('tacto.authoring._try_cli', return_value='{}'), \
             patch('tacto.claude_result.claude_result') as adapter, \
             patch('tacto.authoring._try_muxa') as muxa:
            self.assertEqual('{}',_ask_model('q','claude',expect_json=True))
        adapter.assert_not_called(); muxa.assert_not_called()

    def test_auto_fallback_is_logged_and_does_not_retain_claude_telemetry(self):
        metadata={}
        with patch('tacto.claude_result.claude_result', return_value=answer(text='invalid')), \
             patch('tacto.authoring._try_muxa', return_value='{}') as muxa, \
             self.assertLogs('tacto.authoring',level='WARNING') as logs:
            self.assertEqual('{}',_ask_model('q',expect_json=True,_metadata=metadata))
        self.assertEqual('codex',metadata['provider'])
        self.assertNotIn('observed',metadata)
        self.assertTrue(metadata['attempts'])
        self.assertIn('provider=codex',logs.output[0])
        self.assertEqual('codex',muxa.call_args.args[0])

    def test_direct_muxa_claude_is_disabled(self):
        with patch('tacto.authoring._run_provider') as runner:
            self.assertIsNone(_try_muxa('claude','q',10))
        runner.assert_not_called()

    def test_claude_receives_remaining_total_deadline(self):
        with patch('tacto.authoring.time.monotonic',side_effect=[100,103,103,104,104]), \
             patch('tacto.authoring._try_cli',return_value='{}') as cli:
            self.assertEqual('{}',_ask_model('q','claude',timeout=10))
        cli.assert_called_once_with('claude','q',7)
