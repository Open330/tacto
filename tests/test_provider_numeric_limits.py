"""Out-of-range JSON telemetry fails closed instead of escaping or summing to Infinity."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest
from tacto.provider_result import ProviderResult,parse_provider_result
from tacto.provider_observation import summarize_provider_calls
from tacto.claude_result import ClaudeProtocolError,parse_claude_events
from tests.test_claude_result import events,stream

class ProviderNumericLimitsTests(unittest.TestCase):
    def result(self):
        return ProviderResult('local','claude','claude','cli-json','ok','answer',None,.1,
            cost=.01,cost_currency='USD',cost_basis='cli_reported_list_estimate')

    def test_unrepresentable_integer_metadata_has_validation_error(self):
        for field in ('elapsed_seconds','cost'):
            with self.subTest(field=field),self.assertRaises(ValueError):
                parse_provider_result({**self.result().to_dict(),field:10**1000})

    def test_claude_unrepresentable_cost_is_a_protocol_error(self):
        data=events();data[-1]['total_cost_usd']=10**1000
        with self.assertRaises(ClaudeProtocolError) as error:parse_claude_events(stream(data),'session-1')
        self.assertEqual(error.exception.code,'ClaudeProtocolError_invalid_cost')

    def test_finite_costs_cannot_produce_nonfinite_aggregate(self):
        records=[replace(self.result(),cost=1e308).to_dict() for _ in range(2)]
        for record in records:record.pop('text')
        summary=summarize_provider_calls(records)
        self.assertEqual(summary['logical_requests'],2)
        self.assertEqual(summary['unknown_cost_records'],0)  # Individual observations are known.
        self.assertIsNone(summary['costs'][0]['observed_sum'])
        self.assertEqual(summary['costs'][0]['aggregation_error'],'cost_sum_out_of_range')

    def test_overflow_bucket_does_not_restore_or_poison_other_currencies(self):
        records=[replace(self.result(),cost=value).to_dict() for value in (1e308,1e308,.01)]
        records.append(replace(self.result(),cost=.5,cost_currency='EUR').to_dict())
        for record in records:record.pop('text')
        summary=summarize_provider_calls(records)
        usd=next(c for c in summary['costs'] if c['currency']=='USD')
        eur=next(c for c in summary['costs'] if c['currency']=='EUR')
        self.assertIsNone(usd['observed_sum']);self.assertEqual(eur['observed_sum'],.5)
        self.assertNotIn('aggregation_error',eur)
        json.dumps(summary,allow_nan=False)

    def test_valid_zero_and_finite_values_preserve_exact_types(self):
        for value in (0,0.0,1,1.25,1e308):
            result=parse_provider_result({**self.result().to_dict(),'cost':value})
            self.assertEqual(result.cost,value);self.assertIs(type(result.cost),type(value))
        for value in (True,'0',-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):parse_provider_result({**self.result().to_dict(),'cost':value})

    @unittest.skipUnless(os.name=='posix','executable local fixture requires POSIX')
    def test_actual_local_cli_overflow_is_closed_without_text_usage_or_cost(self):
        from tacto.claude_result import claude_result
        data=events();data[-1]['total_cost_usd']=10**1000
        with tempfile.TemporaryDirectory() as directory:
            executable=Path(directory)/'claude'
            executable.write_text('#!'+sys.executable+'\nimport json,sys\n'
                +'sys.stdin.read()\ndata=json.loads('+repr(json.dumps(data))+')\n'
                +'sid=sys.argv[sys.argv.index("--session-id")+1]\n'
                +'for row in data: row["session_id"]=sid\n'
                +'print("\\n".join(json.dumps(row) for row in data))\n')
            executable.chmod(0o700)
            env={**os.environ,'PATH':directory+os.pathsep+os.environ.get('PATH','')}
            with patch('tacto.claude_result._provider_env',return_value=env):
                result=claude_result('PRIVATE_INPUT',timeout=5)
        self.assertEqual(result.status,'error')
        self.assertEqual(result.error.code,'ClaudeProtocolError_invalid_cost')
        self.assertIsNone(result.text);self.assertIsNone(result.cost);self.assertIsNone(result.input_tokens)
        self.assertNotIn('PRIVATE_INPUT',json.dumps(result.to_dict()))
        self.assertEqual(parse_provider_result(result.to_dict()),result)


class ProviderCacheAccountingTests(unittest.TestCase):
    def result(self, **changes):
        base=ProviderResult('local','codex','codex','cli-json','ok','answer',None,.1,
            input_tokens=10,total_input_tokens=10,input_tokens_basis='total',cached_input_tokens=4)
        return replace(base,**changes).to_dict()

    def test_total_basis_rejects_cache_larger_than_input_even_without_total_field(self):
        for total in (10,None):
            with self.subTest(total=total),self.assertRaises(ValueError):
                parse_provider_result(self.result(cached_input_tokens=11,total_input_tokens=total))

    def test_attempt_and_summary_cannot_accept_contradictory_cache_telemetry(self):
        from tacto.provider_result import ProviderAttempt
        from dataclasses import asdict
        attempt=ProviderAttempt('codex','cli-json','invalid_response',.1,
            input_tokens=10,total_input_tokens=10,input_tokens_basis='total',cached_input_tokens=11)
        raw=self.result();raw['attempts']=[asdict(attempt)]
        with self.assertRaises(ValueError):parse_provider_result(raw)
        raw.pop('text')
        with self.assertRaises(ValueError):summarize_provider_calls([raw])

    def test_uncached_unknown_and_large_valid_counts_are_preserved(self):
        cases=[self.result(cached_input_tokens=10),self.result(cached_input_tokens=0),
            self.result(input_tokens_basis='uncached',total_input_tokens=14,cache_creation_input_tokens=0),
            self.result(input_tokens_basis=None,total_input_tokens=None,cached_input_tokens=11),
            self.result(input_tokens=None,total_input_tokens=None,cached_input_tokens=11),
            self.result(input_tokens=10**1000,total_input_tokens=10**1000,cached_input_tokens=10**1000)]
        for raw in cases:
            with self.subTest(basis=raw['input_tokens_basis']):
                parsed=parse_provider_result(raw)
                self.assertEqual(raw['cached_input_tokens'],parsed.cached_input_tokens)
