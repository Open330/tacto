from dataclasses import replace
import unittest
from nlc.contract import Envelope, ContextBinding
from nlc.flow import Flow, Step, Selector, Postcondition, Capture, replay, pin_params
from nlc.protocol import window_key_reobservation


class App:
    capabilities = {'action.press_key', 'press_key.element_bound', 'press_key.no_escape'}
    def __init__(self, *, changes=True, response=None):
        self.value='0'; self.changes=changes; self.calls=[]; self.response=response
    def observe(self,*args,**kwargs):
        return {'type':'observation','obs_id':'obs','tree':
            '0 Window Title: Fixture\n  1 TextArea Identifier: input, Value: '+self.value}
    def act(self,obs,target,action,**kwargs):
        self.calls.append((action,kwargs))
        if action['name']=='press_key':
            if self.changes: self.value='1'
            return self.response or {'type':'acted','input_route':'window_key','posted_events':2,
                'unverified':'observe_effect','reobserve_required':True}
        return {'type':'acted'}
    def wait_until(self,target,predicate,**kwargs):
        result=self.observe()
        return result if predicate(result['tree']) else {'type':'timeout','last_obs':result}


class NavigationReplayTests(unittest.TestCase):
    def flow(self):
        env=Envelope.minimal(); field=Selector(role='TextArea',identifier='input')
        key=Step('press_key',field,params={'key':'Right'},
            safety=env.contract_for({'write_ephemeral'},'navigate'),
            binding=ContextBinding(bundle_id='fixture',role='TextArea',identifier='input',
                operation='press_key',params_digest=pin_params({'key':'Right'})),
            postcondition=Postcondition(field=field,attribute='Value',equals='1'))
        read=Step('read',field,safety=env.contract_for({'read'},'read'),unverified_ok=True,
            captures=[Capture(name='value',field=field,attribute='Value')])
        return Flow('navigate','fixture',[key,read],schema=2)

    def test_bound_pair_advances_only_after_explicit_result(self):
        app=App(); result=replay(app,self.flow())
        self.assertTrue(result.ok,result.log)
        self.assertEqual([a['name'] for a,k in app.calls],['press_key','read'])
        self.assertEqual(app.calls[0][0]['element_index'],1)
        self.assertEqual(app.calls[0][1]['expect']['role'],'TextArea')
        self.assertEqual(result.events[0]['state'],'unknown')
        self.assertIs(result.events[0]['observation_required'],True)

    def test_missing_result_or_malformed_receipt_never_advances_or_retries(self):
        good={'type':'acted','input_route':'window_key','posted_events':2,
              'unverified':'observe_effect','reobserve_required':True}
        cases=[App(changes=False)]
        for key,value in [('posted_events',1),('posted_events',True),('posted_events','2'),
                ('input_route','window_prepare'),('reobserve_required',False)]:
            cases.append(App(response={**good,key:value}))
        for app in cases:
            with self.subTest(response=app.response):
                result=replay(app,self.flow())
                self.assertFalse(result.ok,result.log)
                self.assertEqual(len(app.calls),1)

    def test_missing_binding_or_result_is_rejected_before_input(self):
        flow=self.flow(); step=flow.steps[0]
        for changed in [replace(step,selector=None),replace(step,postcondition=None),
                replace(step,unverified_ok=True),replace(step,safety=None),
                replace(step,binding=None),replace(step,params={'key':['Right']}),
                replace(step,params={'key':'Escape'}),
                replace(step,postcondition=Postcondition(contains='1'))]:
            app=App()
            with self.subTest(step=changed):
                self.assertFalse(replay(app,replace(flow,steps=[changed])).ok)
                self.assertEqual(app.calls,[])

    def test_response_contract_cannot_authorize_unbound_or_escape_input(self):
        response={'type':'acted','input_route':'window_key','posted_events':2,
                  'unverified':'observe_effect','reobserve_required':True}
        for action in ({'name':'press_key','key':'Right'},
                       {'name':'press_key','key':'Escape','element_index':1},
                       {'name':'press_key','key':['Right'],'element_index':1},
                       {'name':'press_key','key':'Right','element_index':True}):
            self.assertFalse(window_key_reobservation(action,response,App.capabilities))
