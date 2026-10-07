from dataclasses import replace
from unittest.mock import Mock, patch
import unittest

from nlc.contract import Envelope
from nlc.flow import Selector
from nlc.general import run
from nlc.general_visual import NativeVisualScope
from tests.test_native_visual_actions import shot


class App:
    capabilities={'action.click_point','click_point.window_bound','click_point.host_authorization'}
    def __init__(self):
        self.calls=[]
        self.reply={'type':'acted','posted_events':2,'window_id':17,'obs_id':'o1',
            'mechanism':'pid_window_events','input_route':'window_bound_mouse',
            'unverified':'observe_effect','reobserve_required':True}
    def observe(self,*args,**kwargs):
        return {'type':'observation','obs_id':'o1','tree':'3 Window Title: Owned'}
    def act(self,obs_id,target,action,**kwargs):
        self.calls.append((action,kwargs));return dict(self.reply)


class GeneralVisual(unittest.TestCase):
    def setUp(self):
        self.app=App()
        self.scope=NativeVisualScope(Selector(role='Window',title='Owned'),
            Mock(return_value={'action':'click','x':560,'y':240}),lambda *args:True,
            lambda capture,history:len(history)==1,frozenset({'write_ephemeral'}))
        self.counter=0
    def image(self,*args,**kwargs):
        self.counter+=1;return shot('general'+str(id(self))+str(self.counter))
    def execute(self,scope=None,**kwargs):
        with patch('nlc.general_visual.capture_window',side_effect=self.image), \
             patch('nlc.native_visual_actions.capture_window',side_effect=self.image):
            return run(self.app,'owned','green once',lambda *args:self.fail('AX planner used'),
                visual_scope=scope or self.scope,**kwargs)

    def test_general_result_has_one_point_pair_and_independent_host_verification(self):
        result=self.execute()
        self.assertTrue(result.ok,result.why);self.assertTrue(result.verified)
        self.assertEqual(1,result.asked);self.assertEqual(1,len(self.app.calls))
        self.assertEqual({'x':280,'y':120},self.app.calls[0][0]['point'])
        self.assertEqual('confirmed',result.certainty)
        self.assertTrue(result.events[0]['visual_host_verified'])
        self.assertIs(True,self.app.calls[0][1]['approved'])
        self.assertTrue(result.visual_used);self.assertEqual(1,len(result.visual_history))
        from nlc.promote import candidate_from
        self.assertIsNone(candidate_from(result,'owned','green once'))

    def test_envelope_and_host_region_denials_send_no_input(self):
        result=self.execute(envelope=Envelope(allows=frozenset({'read'})))
        self.assertFalse(result.ok);self.assertFalse(self.app.calls);self.scope.planner.assert_not_called()
        result=self.execute(replace(self.scope,authorize=lambda *args:False))
        self.assertFalse(result.ok);self.assertFalse(self.app.calls)

    def test_scope_cannot_declare_unknown_or_consequential_effects(self):
        for effects in (frozenset(),frozenset({'read'}),frozenset({'write_persistent'}),
                        frozenset({'write_ephemeral','transmit'})):
            with self.assertRaises(ValueError):replace(self.scope,effects=effects)
        with self.assertRaises(ValueError):replace(self.scope,window_selector=Selector(role='Button',title='Send'))

    def test_predicate_can_leave_normal_ax_planning_in_place(self):
        seen=[]
        scope=replace(self.scope,when=lambda obs,candidates:False)
        result=run(self.app,'owned','read',lambda *args:(seen.append(args) or {'done':True}),visual_scope=scope)
        self.assertFalse(result.visual_used);self.assertFalse(self.app.calls)
        # The fixture has only a Window without AX prepare capability, so no
        # candidate is fabricated merely because a visual scope exists.
        self.assertEqual([],seen)

    def test_unknown_partial_or_forged_receipt_cannot_replan_or_verify(self):
        for change in ({'type':'error','effect':'unknown','posted_or_attempted_events':1},
                       {'posted_events':1},{'window_id':18},{'obs_id':'other'},
                       {'reobserve_required':False},{'input_route':'other'}):
            self.app=App();self.app.reply.update(change)
            result=self.execute()
            self.assertFalse(result.ok);self.assertEqual('unknown',result.certainty)
            self.assertEqual(1,len(self.app.calls));self.assertEqual(1,len(result.events))
            self.assertEqual('unknown',result.events[0]['state'])

    def test_previous_unverified_input_stops_before_another_model_request(self):
        scope=replace(self.scope,verify=lambda *args:False)
        result=self.execute(scope)
        self.assertFalse(result.ok);self.assertEqual(1,result.asked)
        self.assertEqual(1,len(self.app.calls));self.assertEqual('unknown',result.certainty)

    def test_pending_receipt_cannot_be_bypassed_by_switching_back_to_ax(self):
        scope=replace(self.scope,when=lambda *args:not self.app.calls,verify=lambda *args:False)
        with patch('nlc.general_visual.capture_window',side_effect=self.image), \
             patch('nlc.native_visual_actions.capture_window',side_effect=self.image):
            result=run(self.app,'owned','green',lambda *args:self.fail('pending input bypassed'),visual_scope=scope)
        self.assertFalse(result.ok);self.assertEqual(1,len(self.app.calls))
        self.assertEqual('unknown',result.certainty)

    def test_verified_visual_step_can_return_to_ax_and_preserves_action_history(self):
        class HybridApp(App):
            def observe(self,*args,**kwargs):
                return {'type':'observation','obs_id':'o1','tree':
                    '3 Window Title: Owned\n  4 StaticText Value: ready, Identifier: status'}
            def act(self,obs_id,target,action,**kwargs):
                if action['name']=='read':
                    self.calls.append((action,kwargs));return {'type':'acted'}
                return super().act(obs_id,target,action,**kwargs)
        self.app=HybridApp()
        scope=replace(self.scope,when=lambda *args:not self.app.calls,
            verify=lambda *args:False,verify_step=lambda *args:True)
        def ax(goal,candidates,history):
            self.assertIn('(click_point)',history[0])
            if len(self.app.calls)==1:
                return {'actions':[{'do':'read','candidate':candidates[0]}]}
            return {'done':True}
        with patch('nlc.general_visual.capture_window',side_effect=self.image), \
             patch('nlc.native_visual_actions.capture_window',side_effect=self.image):
            result=run(self.app,'owned','green then read status',ax,visual_scope=scope)
        self.assertTrue(result.ok,result.why);self.assertTrue(result.verified)
        self.assertEqual(['click_point','read'],[action['name'] for action,_ in self.app.calls])
        self.assertEqual(['ready'],result.said);self.assertEqual('confirmed',result.certainty)

    def test_host_step_receipt_allows_a_second_input_under_the_same_budget(self):
        scope=replace(self.scope,verify=lambda capture,history:len(history)==2,verify_step=lambda *args:True)
        result=self.execute(scope)
        self.assertTrue(result.ok,result.why);self.assertEqual(2,len(self.app.calls))
        self.assertEqual(2,result.asked);self.assertEqual('confirmed',result.certainty)
        self.assertTrue(result.events[0]['visual_step_verified'])

    def test_model_done_never_substitutes_for_receiving_result(self):
        result=self.execute(replace(self.scope,planner=lambda *args:{'action':'done'},verify=lambda *args:False))
        self.assertFalse(result.ok);self.assertFalse(result.verified);self.assertFalse(self.app.calls)

    def test_target_replacement_stops_before_input_to_new_window(self):
        count=[0]
        def image(*args,**kwargs):
            count[0]+=1
            capture=self.image()
            return replace(capture,window_id=18) if count[0]==3 else capture
        with patch('nlc.general_visual.capture_window',side_effect=image), \
             patch('nlc.native_visual_actions.capture_window',side_effect=image):
            result=run(self.app,'owned','green',None,visual_scope=self.scope)
        self.assertFalse(result.ok);self.assertTrue(result.identity_changed)
        self.assertEqual(1,len(self.app.calls));self.assertEqual('unknown',result.certainty)

    def test_zero_budget_or_cancel_does_not_capture_or_dispatch(self):
        for kwargs in ({'timeout_seconds':0},{'cancelled':lambda:True}):
            result=self.execute(**kwargs)
            self.assertFalse(result.ok);self.assertEqual([],self.app.calls)
            self.assertEqual(0,self.counter)
