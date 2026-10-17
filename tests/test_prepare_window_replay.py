from dataclasses import replace
import unittest
from tacto.contract import Envelope, ContextBinding
from tacto.flow import Flow, Step, Selector, Postcondition, Capture, replay, pin_params

class App:
    capabilities={'action.prepare_window'}
    def __init__(self, *, changes=True, response=None):
        self.ready=False;self.changes=changes;self.calls=[];self.response=response
    def tree(self):return '0 Window Title: Fixture\n  1 StaticText Identifier: state, Value: '+('ready' if self.ready else 'waiting')
    def observe(self,*a,**k):return {'type':'observation','obs_id':'obs','tree':self.tree()}
    def act(self,obs,target,action,**kwargs):
        self.calls.append((action,kwargs))
        if action['name']=='prepare_window':
            self.ready=self.changes
            return self.response or {'type':'acted','input_route':'window_prepare','unverified':'observe_effect','reobserve_required':True}
        return {'type':'acted'}
    def wait_until(self,target,predicate,**kwargs):
        return self.observe() if predicate(self.tree()) else {'type':'timeout','last_obs':self.observe()}

class PrepareWindowReplayTests(unittest.TestCase):
    def flow(self):
        env=Envelope.minimal();sel=Selector(role='Window',title='Fixture')
        prep=Step('prepare_window',sel,safety=env.contract_for({'write_ephemeral'},'prepare'),
                  binding=ContextBinding(bundle_id='fixture',role='Window',title='Fixture',operation='prepare_window',params_digest=pin_params({})),
                  postcondition=Postcondition(field=Selector(identifier='state'),equals='ready',attribute='Value'))
        read=Step('read',Selector(identifier='state'),safety=env.contract_for({'read'},'read'),
                  captures=[Capture(name='state',field=Selector(identifier='state'),attribute='Value')],unverified_ok=True)
        return Flow('prepare','fixture',[prep,read],schema=2)
    def test_preparation_reobserves_before_next_step(self):
        app=App();result=replay(app,self.flow())
        self.assertTrue(result.ok,result.log)
        self.assertEqual([a['name'] for a,k in app.calls],['prepare_window','read'])
        self.assertNotIn('enabled',app.calls[0][1]['expect'])
        self.assertEqual(result.events[0]['state'],'unknown')
        self.assertIs(result.events[0]['observation_required'],True)
    def test_no_effect_or_unknown_route_cannot_advance(self):
        for app in (App(changes=False),App(response={'type':'acted'}),
                    App(response={'type':'acted','input_route':'other','unverified':'observe_effect','reobserve_required':True})):
            result=replay(app,self.flow())
            self.assertFalse(result.ok)
            self.assertEqual(len(app.calls),1)
    def test_promoted_prepare_requires_host_result_contract(self):
        from tacto.promote import Candidate, deployable
        c=Candidate(goal='prepare',target='fixture',verified=True,actions=[{
            'do':'prepare_window','role':'Window','attr':'Title','match':'Fixture','label':'Fixture',
            'line':'0 Window Title: Fixture'}])
        self.assertIsNone(deployable(c,'prepare','prepare')[0])
        flow,reasons=deployable(c,'prepare','prepare',preparation_postconditions={0:self.flow().steps[0].postcondition})
        self.assertEqual(reasons,[])
        app=App();result=replay(app,flow)
        self.assertTrue(result.ok,result.log)

    def test_missing_contract_or_capability_never_dispatches(self):
        flow=self.flow();prep=flow.steps[0]
        for changed in (replace(prep,postcondition=None),replace(prep,selector=None),replace(prep,unverified_ok=True),
                        replace(prep,postcondition=Postcondition(contains='ready'))):
            app=App();result=replay(app,replace(flow,steps=[changed,*flow.steps[1:]]))
            self.assertFalse(result.ok)
            self.assertEqual(app.calls,[])
        app=App();app.capabilities=set()
        self.assertFalse(replay(app,flow).ok)
        self.assertEqual(app.calls,[])
