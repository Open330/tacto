"""Native evaluation passes declared host limits; no GUI or model input."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import examples.probe_native_select as probe


class NativeModelPagingLimitTests(unittest.TestCase):
    def test_invalid_limit_refuses_before_native_lookup(self):
        for value in (False,0,-1,33,1.5,'8'):
            with self.subTest(value=value), patch('tacto.driver.Driver') as driver, self.assertRaises(ValueError):
                probe.run_model_probe(Mock(),provider='codex',max_pages=value)
            driver.assert_not_called()

    def test_default_and_explicit_lower_limits_reach_planner_without_dispatch(self):
        class Audit:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def report(self): return {'status':'UNCHANGED_SAMPLED'}
        for limit in (None,4):
            with self.subTest(limit=limit):
                driver=Mock()
                driver.observe.return_value={'type':'observation','tree':
                    '0 StaticText  Value: Alpha, Identifier: tacto-fixture-result\n'
                    '1 StaticText  Value: 0, Identifier: tacto-fixture-count'}
                def run(d,target,goal,planner,**kwargs):
                    planner(goal,[{'label':'owned','can':['read']}],[])
                    return SimpleNamespace(ok=False,why='test_without_dispatch',said=[],events=[],provider_calls=[])
                with patch('tacto.driver.Driver') as reader,patch('tacto.driver.AXUIElementCreateApplication'), \
                     patch('tacto.driver._attr',return_value=False), \
                     patch('tacto.input_audit_process.ProcessInputAudit',return_value=Audit()), \
                     patch('tacto.general.run',side_effect=run),patch('tacto.ask.ask_here_paged',return_value={'actions':[]}) as ask:
                    reader.return_value._resolve.return_value=42
                    result=probe.run_model_probe(driver,provider='codex',**({} if limit is None else {'max_pages':limit}))
                expected=8 if limit is None else limit
                self.assertEqual(expected,ask.call_args.kwargs['max_pages'])
                self.assertEqual(expected,result['planner_request_limit'])
                self.assertFalse(result['passed'])
                driver.act.assert_not_called()

    def test_failed_final_readback_preserves_loop_and_observed_provider_cost(self):
        self._assert_failed_run_preserves_evidence(fail_inside_run=False)

    def test_run_exception_preserves_calls_already_observed(self):
        self._assert_failed_run_preserves_evidence(fail_inside_run=True)

    def _assert_failed_run_preserves_evidence(self, *, fail_inside_run):
        from tacto.provider_observation import record_provider_result
        from tacto.provider_result import ProviderResult
        class Audit:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def report(self): return {'status': 'INCONCLUSIVE'}
        driver = Mock()
        initial = {'type':'observation','tree':
            '0 StaticText  Value: Alpha, Identifier: tacto-fixture-result\n'
            '1 StaticText  Value: 0, Identifier: tacto-fixture-count'}
        driver.observe.side_effect = [initial, RuntimeError('disconnected')]
        events = [{'state':'dispatched'}]
        def run(*args, **kwargs):
            record_provider_result(ProviderResult(
                'owned-call', 'codex', 'codex', 'cli-json', 'ok',
                'PRIVATE_RESPONSE', None, 1.2, total_input_tokens=42,
                output_tokens=7, cost=.01, cost_currency='USD', cost_basis='list_estimate'))
            if fail_inside_run:
                raise RuntimeError('interrupted-run')
            return SimpleNamespace(ok=False, why='deadline_exceeded', said=[],
                                   events=events, provider_calls=[])
        with patch('tacto.driver.Driver') as reader, \
             patch('tacto.driver.AXUIElementCreateApplication'), \
             patch('tacto.driver._attr', return_value=False), \
             patch('tacto.input_audit_process.ProcessInputAudit', return_value=Audit()), \
             patch('tacto.general.run', side_effect=run):
            reader.return_value._resolve.return_value = 42
            result = probe.run_model_probe(driver, provider='codex')
        self.assertFalse(result['passed'])
        self.assertEqual('INCONCLUSIVE', result['audit']['status'])
        self.assertEqual(1, len(result['provider_calls']))
        self.assertEqual(42, result['provider_calls'][0]['total_input_tokens'])
        self.assertEqual(.01, result['provider_calls'][0]['cost'])
        self.assertEqual(1, result['usage']['logical_requests'])
        self.assertEqual(42, result['usage']['tokens']['total_input_tokens']['observed_sum'])
        self.assertEqual(7, result['usage']['tokens']['output_tokens']['observed_sum'])
        self.assertEqual([{'currency':'USD','basis':'list_estimate','observed_sum':.01}],
                         result['usage']['costs'])
        self.assertNotIn('PRIVATE_RESPONSE', repr(result))
        if not fail_inside_run:
            self.assertEqual('deadline_exceeded', result['why'])
            self.assertEqual(events, result['events'])
            self.assertEqual('RuntimeError', result['readback_error_class'])
            self.assertIn('loop_not_completed', result['failure_reasons'])
            self.assertIn('final_readback_unavailable', result['failure_reasons'])
            self.assertNotIn('probe_exception', result['failure_reasons'])
        else:
            self.assertEqual(['probe_exception'], result['failure_reasons'])
        self.assertEqual(1 if fail_inside_run else 2, driver.observe.call_count)
        driver.act.assert_not_called()


if __name__ == '__main__': unittest.main()
