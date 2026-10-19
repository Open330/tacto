"""A pinned model request must not mutate shared Muxa Ask state."""
import json
import unittest
from unittest.mock import patch
from tacto.authoring import _try_muxa,ask_model_result,MuxaRouteUnavailable
from tacto.provider_result import ProviderResult


class MuxaModelRouteBoundaryTests(unittest.TestCase):
    def test_legacy_route_rejects_before_prompt_or_process_submission(self):
        for provider in ('codex','opencode','cxweb'):
            with self.subTest(provider=provider),patch('tacto.authoring._run_provider') as run:
                with self.assertRaises(MuxaRouteUnavailable) as error:
                    _try_muxa(provider,'PRIVATE_SCREEN_CONTENT',10)
                self.assertEqual(error.exception.reason,'muxa_isolated_ask_unavailable')
                self.assertNotIn('PRIVATE',str(error.exception));run.assert_not_called()

    def test_same_provider_cli_fallback_preserves_usage_and_safe_rejection(self):
        answer=ProviderResult('local','codex','codex','cli-json','ok','{}',None,.1,
            input_tokens=123,total_input_tokens=123,input_tokens_basis='total',output_tokens=8)
        with patch('tacto.authoring._run_provider') as run, \
                patch('tacto.authoring._try_cli',return_value=answer) as cli:
            result=ask_model_result('PRIVATE_SCREEN_CONTENT','codex',expect_json=True)
        run.assert_not_called();self.assertEqual(cli.call_args.args[0],'codex')
        self.assertEqual(result.status,'ok');self.assertEqual(result.input_tokens,123)
        self.assertEqual(result.attempts[0].status,'error')
        self.assertEqual(result.attempts[0].error_code,'muxa_isolated_ask_unavailable')
        self.assertEqual(result.attempts[1].transport,'cli-json')
        self.assertIsNone(result.attempts[0].input_tokens)
        self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
        from tacto.provider_observation import summarize_provider_calls
        record=result.to_dict();record.pop('text')
        summary=summarize_provider_calls([record])
        self.assertEqual(summary['route_invocations'],2)
        self.assertEqual(summary['reported_local_route_rejections'],1)
        self.assertEqual(summary['tokens']['total_input_tokens']['observed_sum'],123)
        self.assertEqual(summary['unknown_cost_records'],2)

    def test_unavailable_cli_keeps_both_failures_without_changing_provider(self):
        with patch('tacto.authoring._run_provider') as run, \
                patch('tacto.authoring._try_cli',return_value=None) as cli:
            result=ask_model_result('PRIVATE_SCREEN_CONTENT','codex')
        self.assertEqual(result.status,'error');run.assert_not_called()
        cli.assert_called_once();self.assertEqual(cli.call_args.args[0],'codex')
        self.assertEqual([a.status for a in result.attempts],['error','no_answer'])
        self.assertFalse(result.cancellation_supported)
        self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
