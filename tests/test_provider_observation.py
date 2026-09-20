import unittest
from unittest.mock import patch
from nlc.provider_observation import capture_provider_calls
from nlc.authoring import reach_model
from nlc.ask import Q, ask


class ProviderObservationTests(unittest.TestCase):
    def test_existing_planner_text_path_calls_provider_once_without_text_in_log(self):
        with patch('nlc.helper.InheritedHelperDriver.available',return_value=False), \
             patch('nlc.authoring._try_muxa',return_value='{"answer":"PRIVATE"}') as provider:
            with capture_provider_calls() as records:
                result=ask(Q('probe','PRIVATE PROMPT',lambda obj:obj),provider='codex')
            self.assertEqual({'answer':'PRIVATE'},result)
            provider.assert_called_once()
            self.assertEqual(1,len(records))
            self.assertEqual('ok',records[0]['status'])
            self.assertNotIn('PRIVATE',repr(records))
            self.assertNotIn('text',records[0])

    def test_nested_collection_isolated_and_context_restored(self):
        with patch('nlc.helper.InheritedHelperDriver.available',return_value=False), \
             patch('nlc.authoring._try_muxa',return_value='{}') as provider:
            with capture_provider_calls() as outer:
                with capture_provider_calls() as inner:
                    reach_model('q','codex')
                reach_model('q','codex')
            reach_model('q','codex')
        self.assertEqual(3,provider.call_count)
        self.assertEqual(2,len(outer))
        self.assertEqual(1,len(inner))
        self.assertEqual(inner[0]['request_id'],outer[0]['request_id'])
        inner[0]['status']='mutated'
        self.assertEqual('ok',outer[0]['status'])

    def test_failure_and_timeout_logged_without_retry_or_error_text_leak(self):
        for failure, expected in [(ValueError('SECRET'),'error'),(TimeoutError('SECRET'),'timeout')]:
            with self.subTest(expected=expected), \
                 patch('nlc.helper.InheritedHelperDriver.available',return_value=False), \
                 patch('nlc.authoring._ask_model',side_effect=failure) as provider:
                with capture_provider_calls() as records:
                    with self.assertRaises((RuntimeError,TimeoutError)):
                        reach_model('q','codex')
                provider.assert_called_once()
                self.assertEqual(expected,records[0]['status'])
                self.assertNotIn('SECRET',repr(records))

    def test_general_exposes_each_planner_request_without_extra_calls(self):
        from nlc.general import run
        class App:
            def observe(self,*a,**k):
                return {'type':'observation','obs_id':'o','tree':'0 Window Title: Test\n  1 StaticText Value: ready, Identifier: status'}
            def probe(self,*a,**k):
                return {'type':'probe','line':'  1 StaticText Value: ready, Identifier: status'}
            def act(self,*a,**k):
                return {'type':'acted'}
        def planner(goal,candidates,history):
            return ask(Q('step','q',lambda answer: {'done':True} if answer['done'] else
                         {'actions':[{'do':'read','candidate':candidates[0]}]}),provider='codex')
        with patch('nlc.helper.InheritedHelperDriver.available',return_value=False), \
             patch('nlc.authoring._try_muxa',side_effect=['{"done":false}','{"done":true}']) as provider:
            result=run(App(),'fixture','read',planner)
        self.assertTrue(result.ok,result.why)
        self.assertEqual(['ready'],result.said)
        self.assertEqual(2,provider.call_count)
        self.assertEqual(2,len(result.provider_calls))
        self.assertEqual(2,len({r['request_id'] for r in result.provider_calls}))
        self.assertTrue(all('text' not in r for r in result.provider_calls))
