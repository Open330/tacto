import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from nlc.helper import InheritedHelperDriver
from nlc.provider_result import ProviderResult, parse_provider_result


class LauncherProviderEnvelopeTests(unittest.TestCase):
    def result(self):
        return ProviderResult('local-id', 'auto', 'claude', 'cli', 'ok', '{}', None, .2,
            model='observed-model', input_tokens=123, output_tokens=45, cost=.01,
            cost_currency='USD', provider_request_id='remote-id').to_dict()

    def test_metadata_and_legacy_text_share_single_response_call(self):
        raw = self.result()
        driver = SimpleNamespace(_ask_launcher_response=Mock(return_value={
            'type':'answer','text':'{}','provider_result_v1':raw}))
        result = InheritedHelperDriver.ask_launcher_result(driver, 'q')
        self.assertEqual(('claude','observed-model',123,45,.01,'remote-id','local-id'),
            (result.provider,result.model,result.input_tokens,result.output_tokens,result.cost,
             result.provider_request_id,result.request_id))
        self.assertEqual('launcher',result.transport)
        driver._ask_launcher_response.assert_called_once()
        driver._ask_launcher_response.reset_mock()
        self.assertEqual('{}',InheritedHelperDriver.ask_launcher(driver,'q'))
        driver._ask_launcher_response.assert_called_once()

    def test_old_launcher_leaves_unknown_metadata_null(self):
        driver=SimpleNamespace(_ask_launcher_response=Mock(return_value={'type':'answer','text':'ok'}))
        result=InheritedHelperDriver.ask_launcher_result(driver,'q')
        self.assertIsNone(result.provider)
        self.assertIsNone(result.model)
        self.assertIsNone(result.input_tokens)
        self.assertIsNone(result.cost)
        self.assertIsNone(result.provider_request_id)

    def test_malformed_metadata_and_mismatched_response_are_rejected(self):
        for change in ({'input_tokens':True},{'cost':float('nan')},{'output_tokens':-1},
                       {'status':'invented'},{'provider_request_id':123}):
            with self.subTest(change=change):
                raw=self.result();raw.update(change)
                with self.assertRaises(ValueError):parse_provider_result(raw)
        driver=SimpleNamespace(_ask_launcher_response=Mock(return_value={
            'type':'answer','text':'different','provider_result_v1':self.result()}))
        with self.assertRaises(ValueError):InheritedHelperDriver.ask_launcher_result(driver,'q')
        driver._ask_launcher_response.assert_called_once()

    def test_timeout_envelope_survives_poison_exception_and_mismatch_rejected(self):
        raw=self.result()
        raw.update(status='timeout', text=None, error={'code':'TimeoutError','message':'Deadline expired'})
        response={'type':'error','error':'model_timeout','provider_result_v1':raw}
        error=TimeoutError('poisoned')
        error.provider_response=response
        driver=SimpleNamespace(_ask_launcher_response=Mock(side_effect=error))
        result=InheritedHelperDriver.ask_launcher_result(driver,'q')
        self.assertEqual(('timeout','local-id','remote-id'),
                         (result.status,result.request_id,result.provider_request_id))
        driver._ask_launcher_response.assert_called_once()
        driver._ask_launcher_response=Mock(return_value={'type':'answer','text':'oops','provider_result_v1':raw})
        with self.assertRaises(ValueError):InheritedHelperDriver.ask_launcher_result(driver,'q')

    def test_token_components_survive_signed_response_and_reject_false_total(self):
        raw=self.result()
        raw.update(input_tokens=2,cached_input_tokens=10,cache_creation_input_tokens=30,
                   total_input_tokens=42,input_tokens_basis='uncached')
        driver=SimpleNamespace(_ask_launcher_response=Mock(return_value={
            'type':'answer','text':'{}','provider_result_v1':raw}))
        result=InheritedHelperDriver.ask_launcher_result(driver,'q')
        self.assertEqual((2,10,30,42,'uncached'),(result.input_tokens,result.cached_input_tokens,
            result.cache_creation_input_tokens,result.total_input_tokens,result.input_tokens_basis))
        raw['total_input_tokens']=12
        with self.assertRaises(ValueError):parse_provider_result(raw)
        raw['total_input_tokens']=42;raw['cache_creation_input_tokens']=None
        with self.assertRaises(ValueError):parse_provider_result(raw)
