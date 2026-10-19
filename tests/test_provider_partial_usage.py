"""Partial paid/failed usage survives summary without inventing currencies."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from tacto.authoring import ask_model_result
from tacto.provider_result import ProviderResult, ProviderAttempt, ProviderError
from tacto.provider_observation import capture_provider_calls, summarize_provider_calls
from tests.test_opencode_result import frames, stream


class ProviderPartialUsageTests(unittest.TestCase):
    def record(self, **fields):
        result = ProviderResult('local','owned','owned','cli-json','error',None,
            ProviderError('OwnedFailure','request failed'),.1,**fields).to_dict()
        result.pop('text')
        return result

    def test_partial_costs_preserve_zero_amount_and_missing_units_individually(self):
        records=[self.record(cost=.2,cost_basis='cli_reported_step_estimate'),
                 self.record(cost=0,cost_currency='USD'), self.record()]
        summary=summarize_provider_calls(records)
        self.assertEqual([],summary['costs'])
        self.assertEqual(3,summary['unknown_cost_records'])
        self.assertEqual([.2,0],[r['observed_amount'] for r in summary['unaggregated_costs']])
        self.assertEqual([['currency'],['basis']],[r['missing_fields'] for r in summary['unaggregated_costs']])
        self.assertEqual([0,1],[r['logical_request_index'] for r in summary['unaggregated_costs']])
        self.assertTrue(all(r['attempt_index'] is None for r in summary['unaggregated_costs']))
        json.dumps(summary,allow_nan=False)

    def test_partial_cache_components_are_kept_separate_from_totals_and_bases(self):
        records=[self.record(input_tokens=10,cached_input_tokens=3,input_tokens_basis='total',total_input_tokens=10),
                 self.record(input_tokens=10,cached_input_tokens=3,cache_creation_input_tokens=7,input_tokens_basis='uncached'),
                 self.record(cache_creation_input_tokens=5)]
        summary=summarize_provider_calls(records)
        self.assertEqual({'observed_sum':10,'unknown_records':2},summary['tokens']['total_input_tokens'])
        groups={r['input_tokens_basis']:r for r in summary['input_token_components']}
        self.assertEqual({'observed_sum':7,'unknown_records':0},groups['uncached']['tokens']['cache_creation_input_tokens'])
        self.assertEqual({'observed_sum':5,'unknown_records':0},groups[None]['tokens']['cache_creation_input_tokens'])
        self.assertEqual({'observed_sum':None,'unknown_records':1},groups['total']['tokens']['cache_creation_input_tokens'])
        self.assertEqual({'observed_sum':10,'unknown_records':0},groups['total']['tokens']['input_tokens'])
        self.assertEqual({'observed_sum':10,'unknown_records':0},groups['uncached']['tokens']['input_tokens'])

    def test_failed_attempt_is_not_double_counted_as_final_result(self):
        record=self.record(cost=.2,cost_basis='cli_reported_step_estimate')
        attempt=ProviderAttempt('owned','cli-json','invalid_response',.1,
                                cost=.2,cost_basis='cli_reported_step_estimate')
        record['attempts']=[asdict(attempt)]
        summary=summarize_provider_calls([record])
        self.assertEqual(1,summary['route_invocations'])
        self.assertEqual(1,len(summary['unaggregated_costs']))
        self.assertEqual(0,summary['unaggregated_costs'][0]['attempt_index'])
        self.assertEqual('invalid_response',summary['unaggregated_costs'][0]['status'])

    def test_cache_creation_telemetry_cannot_be_classified_as_no_telemetry_local_rejection(self):
        record=self.record()
        record['attempts']=[asdict(ProviderAttempt('codex','muxa','error',.1,
            error_code='muxa_isolated_ask_unavailable',cache_creation_input_tokens=7))]
        summary=summarize_provider_calls([record])
        self.assertEqual(0,summary['reported_local_route_rejections'])
        self.assertEqual(7,summary['input_token_components'][0]['tokens']['cache_creation_input_tokens']['observed_sum'])

    @unittest.skipUnless(os.name=='posix','owned local executable required')
    def test_actual_local_cli_failed_paid_reply_reaches_common_partial_summary(self):
        events=frames()
        events.append({'type':'error','timestamp':102,'sessionID':'ses_owned',
                       'error':{'name':'UnknownError','data':{'message':'PRIVATE_PROVIDER_ERROR'}}})
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'opencode'
            executable.write_text('#!'+sys.executable+'\nimport sys\nsys.stdin.read()\nprint('+repr(stream(events))+')\n')
            executable.chmod(0o700)
            env={**os.environ,'PATH':directory+os.pathsep+os.environ.get('PATH','')}
            with patch('tacto.authoring.shutil.which',return_value=str(executable)), \
                    patch('tacto.authoring._provider_env',return_value=env),capture_provider_calls() as records:
                result=ask_model_result('PRIVATE_PROMPT','opencode',timeout=5,expect_json=True)
        self.assertEqual('error',result.status)
        self.assertEqual(2,len(result.attempts))
        self.assertEqual('error',result.attempts[-1].status)
        summary=summarize_provider_calls(records)
        self.assertEqual(18,summary['tokens']['total_input_tokens']['observed_sum'])
        self.assertEqual(.001,summary['unaggregated_costs'][0]['observed_amount'])
        self.assertIsNone(summary['unaggregated_costs'][0]['currency'])
        self.assertEqual('cli_reported_step_estimate',summary['unaggregated_costs'][0]['basis'])
        self.assertEqual(1,summary['unaggregated_costs'][0]['attempt_index'])
        self.assertEqual([],summary['costs'])
        self.assertNotIn('PRIVATE',json.dumps(records)+json.dumps(summary))
