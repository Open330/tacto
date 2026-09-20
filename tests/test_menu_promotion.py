"""A menu's observed path can be reviewed, but replay needs an explicit result contract."""
from dataclasses import replace
import unittest
from nlc.flow import Postcondition, Selector, replay, pin_params
from nlc.general import run
from nlc.promote import candidate_from, deployable, gate, to_flow
import test_general_menu as fixture

class MenuPromotionTests(unittest.TestCase):
    def candidate(self):
        app=fixture.App()
        planner=fixture.GeneralMenuTests().planner([
            ('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),('menu','Increment'),('read','1')])
        result=run(app,'fixture','increment once and read',planner)
        self.assertTrue(result.ok,result.why)
        return candidate_from(result,'fixture','increment once and read')

    def post(self):
        return Postcondition(field=Selector(identifier='count'),equals='1',attribute='Value')

    def test_path_preserved_and_blind_menu_cannot_deploy_despite_later_capture(self):
        c=self.candidate();flow=to_flow(c,'increment')
        self.assertEqual(flow.steps[0].params,{'path':['Probe','Increment']})
        self.assertIsNone(flow.steps[0].selector)
        self.assertEqual(flow.steps[0].binding.operation,'menu')
        self.assertTrue(flow.steps[1].captures)
        self.assertTrue(gate(c,flow,c.goal))
        self.assertIsNone(deployable(c,'increment',c.goal)[0])

    def test_explicit_exact_result_contract_replays_and_noop_fails(self):
        c=self.candidate()
        flow,reasons=deployable(c,'increment',c.goal,menu_postconditions={0:self.post()})
        self.assertEqual(reasons,[])
        self.assertIsNotNone(flow)
        for noop in (False,True):
            app=fixture.App()
            if noop:
                def act(obs,target,action,**kwargs):
                    app.calls.append((action,kwargs));return {'type':'acted'}
                app.act=act
            app.wait_until=lambda target,predicate,**kwargs: ({'type':'observation','obs_id':'obs','tree':app.tree()}
                if predicate(app.tree()) else {'type':'timeout','last_obs':app.observe()})
            result=replay(app,flow)
            self.assertEqual(result.ok,not noop,result)
            self.assertEqual(len([a for a,k in app.calls if a['name']=='menu']),1)

    def test_tampered_path_binding_or_weak_postconditions_fail_gate(self):
        c=self.candidate();flow=to_flow(c,'increment',menu_postconditions={0:self.post()})
        menu=flow.steps[0]
        for changed in (replace(menu,params={'path':['Probe','Other']}),
                        replace(menu,params={'path':['Probe','Other']},binding=replace(menu.binding,params_digest=pin_params({'path':['Probe','Other']}))),
                        replace(menu,binding=None),replace(menu,unverified_ok=True),
                        replace(menu,postcondition=Postcondition(contains='1')),
                        replace(menu,postcondition=None,expect='1')):
            self.assertTrue(gate(c,replace(flow,steps=[changed,*flow.steps[1:]]),c.goal))
