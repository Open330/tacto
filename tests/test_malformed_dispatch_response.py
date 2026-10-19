"""Actual browser delivery survives malformed adapter acknowledgements."""
import unittest
from tacto.general import run
from tests.test_general_budget import App,plan


class MalformedDispatchTests(unittest.TestCase):
    def test_general_keeps_unknown_event_and_stops_batch(self):
        for value in (None,{},[],{'type':'observation','text':'PRIVATE'},'PRIVATE'):
            with self.subTest(value=value):
                app=App();app.result=value
                result=run(app,'owned','read Entry',plan)
                self.assertFalse(result.ok)
                self.assertEqual(1,len(app.calls))
                self.assertEqual('unknown',result.certainty)
                self.assertEqual('unknown',result.events[0]['state'])
                self.assertNotIn('PRIVATE',str(result.visits))

    def test_driver_exception_also_keeps_result_certainty_unknown(self):
        app=App();app.result=RuntimeError('lost response')
        result=run(app,'owned','read Entry',plan)
        self.assertFalse(result.ok)
        self.assertEqual('unknown',result.certainty)
        self.assertEqual('unknown',result.events[0]['state'])

    def test_cancellation_after_malformed_reply_preserves_unknown_event(self):
        app=App();app.result=None;cancelled=[False]
        app.on_act=lambda:cancelled.__setitem__(0,True)
        result=run(app,'owned','read Entry',plan,cancelled=lambda:cancelled[0])
        self.assertEqual('cancelled',result.stop_reason)
        self.assertEqual(1,len(app.calls))
        self.assertEqual('unknown',result.events[0]['state'])
        self.assertEqual('unknown',result.certainty)

    def test_known_gates_and_pre_dispatch_refusal_are_preserved(self):
        from tacto.protocol import normalize_action_response
        for response in ({'type':'confirm_request'}, {'type':'handoff_request'},
                         {'type':'error','effect':'not_started'}, {'type':'acted'}):
            self.assertIs(response,normalize_action_response(response))
        app=App();app.result={'type':'error','error':'refused','effect':'not_started'}
        result=run(app,'owned','read Entry',plan)
        self.assertEqual([],result.events)
        self.assertEqual(set(),result.effects)
        self.assertEqual('confirmed',result.certainty)


try:
    import playwright.sync_api
    HAS_BROWSER=True
except ImportError:
    HAS_BROWSER=False


@unittest.skipUnless(HAS_BROWSER,'optional playwright dependency not installed')
class BrowserMalformedDispatchTests(unittest.TestCase):
    def test_real_click_is_not_repeated_or_promoted_in_general_and_replay(self):
        from tacto.browser import BrowserDriver
        from tacto.flow import Flow,Step,Selector,replay
        from urllib.parse import quote
        for mode in ('general','replay'):
            with self.subTest(mode=mode):
                driver=BrowserDriver()
                try:
                    driver.goto('data:text/html,'+quote('<button onclick="window.received++">Next</button><script>window.received=0</script>'))
                    original=driver.act
                    def lose_reply(*args,**kwargs):
                        response=original(*args,**kwargs)
                        self.assertEqual('acted',response['type'])
                        return None
                    driver.act=lose_reply
                    if mode=='general':
                        asked=[]
                        def planner(*args):
                            asked.append(True)
                            return {'actions':[{'do':'click','candidate':next(c for c in args[1] if c['label']=='Next' and 'click' in c['can'])}]}
                        result=run(driver,'page','click Next once',planner)
                        self.assertEqual(1,len(asked))
                        self.assertEqual('unknown',result.certainty)
                    else:
                        from tacto.contract import Envelope,ContextBinding
                        flow=Flow('one_click','page',[Step('click',selector=Selector(role='Button',title='Next'),
                            safety=Envelope.minimal().contract_for({'write_ephemeral'},'owned button'),
                            binding=ContextBinding(bundle_id='data:',role='Button',operation='click'))],schema=2)
                        result=replay(driver,flow)
                    self.assertFalse(result.ok)
                    self.assertEqual(1,driver._page.evaluate('window.received'),getattr(result,'log',None))
                    self.assertEqual(1,len(result.events))
                    self.assertEqual('unknown',result.events[0]['state'])
                finally:driver.close()
