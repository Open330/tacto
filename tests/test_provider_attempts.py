"""Attempt provenance includes rejected replies without leaking content."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nlc.authoring import ask_model_result, _run_provider
from nlc.claude_result import _run_claude
from nlc.provider_result import ProviderError, ProviderResult, parse_provider_result
from nlc.provider_observation import capture_provider_calls, summarize_provider_calls


class ProviderAttemptTests(unittest.TestCase):
    def observed(self, text='not JSON SECRET'):
        return ProviderResult('local', 'claude', 'claude', 'cli-json', 'ok', text, None, .1,
                              input_tokens=4, total_input_tokens=4, input_tokens_basis='total',
                              output_tokens=3, cost=.01, cost_currency='USD', cost_basis='cli_reported_list_estimate')

    def test_rejected_paid_reply_survives_fallback_and_roundtrip(self):
        with patch('nlc.authoring._try_cli', side_effect=lambda provider, *args: self.observed() if provider == 'claude' else None), \
             patch('nlc.authoring._try_muxa', return_value='{}'), capture_provider_calls() as records:
            result = ask_model_result('PRIVATE', expect_json=True)
        self.assertEqual(['invalid_response', 'ok'], [a.status for a in result.attempts])
        self.assertEqual(.01, result.attempts[0].cost)
        self.assertIsNone(result.cost)
        self.assertEqual(result, parse_provider_result(result.to_dict()))
        serialized = json.dumps(records)
        self.assertNotIn('SECRET', serialized)
        self.assertNotIn('PRIVATE', serialized)
        self.assertNotIn('text', records[0]['attempts'][0])
        summary = summarize_provider_calls(records)
        self.assertEqual(2, summary['route_invocations'])
        self.assertEqual(1, summary['unknown_cost_records'])
        self.assertEqual(.01, summary['costs'][0]['observed_sum'])
        self.assertEqual({'observed_sum': 4, 'unknown_records': 1}, summary['tokens']['total_input_tokens'])

    def test_success_summary_does_not_count_final_cost_twice(self):
        with patch('nlc.authoring._try_cli', return_value=self.observed('{}')), capture_provider_calls() as records:
            result = ask_model_result('q', 'claude', expect_json=True)
        self.assertEqual(.01, result.cost)
        self.assertEqual(.01, summarize_provider_calls(records)['costs'][0]['observed_sum'])

    def test_plain_reply_json_rejection_matches_structured_attempt(self):
        with patch('nlc.authoring._try_muxa', return_value='PRIVATE invalid JSON'), \
             patch('nlc.authoring._try_cli', return_value=None):
            result = ask_model_result('q', 'codex', expect_json=True)
        self.assertEqual(['invalid_response', 'no_answer'], [a.status for a in result.attempts])
        self.assertNotIn('PRIVATE', json.dumps(result.to_dict()))

    def test_interrupt_records_prior_and_current_attempt_then_propagates(self):
        with patch('nlc.authoring._try_muxa', return_value=None), \
             patch('nlc.authoring._try_cli', side_effect=KeyboardInterrupt('PRIVATE')), \
             capture_provider_calls() as records:
            with self.assertRaises(KeyboardInterrupt):
                ask_model_result('PRIVATE', 'codex')
        self.assertEqual(1, len(records))
        self.assertEqual(['no_answer', 'error'], [a['status'] for a in records[0]['attempts']])
        self.assertEqual('KeyboardInterrupt', records[0]['attempts'][1]['error_code'])
        self.assertEqual('error', records[0]['status'])
        self.assertFalse(records[0]['cancellation_supported'])
        self.assertNotIn('PRIVATE', json.dumps(records))

    def test_all_failed_attempts_remain_available(self):
        with patch('nlc.authoring._try_cli', side_effect=subprocess.TimeoutExpired('SECRET', 1)), \
             patch('nlc.authoring._try_muxa', return_value=None):
            result = ask_model_result('q', 'codex')
        self.assertEqual('timeout', result.status)
        self.assertEqual(['no_answer', 'timeout'], [a.status for a in result.attempts])
        self.assertNotIn('SECRET', json.dumps(result.to_dict()))

    def test_failed_structured_reply_keeps_usage_and_safe_code(self):
        failed = ProviderResult('local', 'claude', 'claude', 'cli-json', 'error', None,
                                ProviderError('usage_limit', 'PRIVATE OUTPUT'), .1,
                                output_tokens=2, cost=.005, cost_currency='USD', cost_basis='estimate')
        with patch('nlc.authoring._try_cli', return_value=failed):
            result = ask_model_result('q', 'claude')
        self.assertEqual('error', result.status)
        self.assertEqual(2, result.attempts[0].output_tokens)
        self.assertEqual('usage_limit', result.attempts[0].error_code)
        self.assertIsNone(result.output_tokens)
        self.assertNotIn('PRIVATE OUTPUT', json.dumps(result.to_dict()))

    def test_cost_summary_keeps_currency_and_basis_separate(self):
        from dataclasses import replace
        records = []
        for currency, basis in [('USD', 'invoice'), ('USD', 'estimate'), ('EUR', 'estimate')]:
            raw = replace(self.observed('{}'), cost_currency=currency, cost_basis=basis).to_dict()
            raw.pop('text'); records.append(raw)
        summary = summarize_provider_calls(records)
        self.assertEqual(3, len(summary['costs']))
        self.assertEqual(0, summary['unknown_cost_records'])

    def test_attempt_schema_rejects_untrusted_content_and_tokens(self):
        with patch('nlc.authoring._try_cli', return_value=self.observed('{}')):
            raw = ask_model_result('q', 'claude').to_dict()
        for change in ({'text': 'PRIVATE'}, {'input_tokens': True}, {'elapsed_seconds': float('inf')},
                       {'error_code': 'raw provider error'}, {'status': 'invented'}):
            data = json.loads(json.dumps(raw)); data['attempts'][0].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_provider_result(data)

    def test_legacy_summary_reports_missing_attempt_details(self):
        raw = self.observed('{}').to_dict(); raw.pop('attempts'); raw.pop('text')
        summary = summarize_provider_calls([raw])
        self.assertEqual(1, summary['logical_requests_without_attempt_details'])
        self.assertEqual(0, summary['route_invocations'])

    def test_caller_interrupt_cleans_local_process_groups(self):
        # communicate interrupts, while context manager still reaps the process.
        from nlc.codex_result import _run_codex
        for module, invoke in (
                ('nlc.authoring', lambda: _run_provider(['unused'], 1)),
                ('nlc.codex_result', lambda: _run_codex(['unused'], 'payload', 1)),
                ('nlc.claude_result', lambda: _run_claude(['unused'], 'payload', 1, '/tmp'))):
            with patch(module + '.subprocess.Popen') as popen, patch(module + '.os.killpg') as killpg:
                proc = popen.return_value.__enter__.return_value
                proc.pid = 12345
                proc.communicate.side_effect = KeyboardInterrupt
                with self.assertRaises(KeyboardInterrupt): invoke()
                killpg.assert_called_once()
                proc.wait.assert_called_once()

    @unittest.skipUnless(os.name == 'posix', 'POSIX process groups required')
    def test_real_caller_interrupt_stops_delayed_descendant(self):
        with tempfile.TemporaryDirectory() as folder:
            for module, name, extra in (('nlc.authoring', '_run_provider', ''),
                                         ('nlc.codex_result', '_run_codex', "'payload', ")):
                marker = str(Path(folder) / (name + '-descendant-write'))
                child = 'import time,pathlib;time.sleep(1);pathlib.Path(' + repr(marker) + ').write_text("alive")'
                parent = 'import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",' + repr(child) + ']);time.sleep(10)'
                harness = '''import signal,sys
from %s import %s
def interrupt(*args): raise KeyboardInterrupt
signal.signal(signal.SIGALRM, interrupt)
signal.setitimer(signal.ITIMER_REAL, .3)
try: %s([sys.executable, '-c', %s], %s5)
except KeyboardInterrupt: print('interrupted')
''' % (module, name, name, repr(parent), extra)
                with self.subTest(runner=name):
                    done = subprocess.run([sys.executable, '-c', harness], capture_output=True, text=True, timeout=3)
                    self.assertEqual(0, done.returncode, done.stderr)
                    self.assertEqual('interrupted', done.stdout.strip())
                    time.sleep(1)
                    self.assertFalse(Path(marker).exists(), 'descendant survived caller interruption')
