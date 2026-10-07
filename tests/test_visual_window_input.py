from dataclasses import replace
import json
from unittest.mock import Mock, patch
import unittest
from nlc.native_visual_actions import NativeVisualClickExecutor
from nlc.native_window_payload import WINDOW_INPUT_CAPABILITIES
from nlc.general_visual import NativeVisualScope
from nlc.flow import Flow, Step, Selector, replay
from nlc.contract import Envelope
from nlc.general import run
from nlc.visual import plan_native_input, VisualError
from tests.test_native_visual_actions import shot
from tests.test_general_visual import App


class VisualWindowInputTests(unittest.TestCase):
    def setUp(self):
        self.app=App();self.app.capabilities=set(self.app.capabilities)|set(WINDOW_INPUT_CAPABILITIES)
        self.app.reply['input_route']='window_input'
        self.counter=0
        self.scope=NativeVisualScope(Selector(role='Window',title='Owned'),
            Mock(side_effect=[{'action':'key','key':'Right'},{'action':'text','text':'A😀한글'}]),
            lambda *args:True,lambda c,h:len(h)==2,frozenset({'write_ephemeral'}),
            verify_step=lambda c,h:len(h)==1,input_actions=frozenset({'key','text'}))
    def image(self,*args,**kwargs):
        self.counter+=1
        return shot('input'+str(id(self))+str(self.counter))
    def execute(self,*,flow=False,scope=None):
        scope=scope or self.scope
        with patch('nlc.general_visual.capture_window',side_effect=self.image),patch('nlc.native_visual_actions.capture_window',side_effect=self.image):
            if flow:
                step=Step('visual',params={'scope':'input','max_screens':3},intent_text='right then text',
                    safety=Envelope.minimal().contract_for({'write_ephemeral'},'owned input'))
                return replay(self.app,Flow('input','owned',[step],schema=2),visual_scopes={'input':scope})
            return run(self.app,'owned','right then text',lambda *args:self.fail('AX fallback'),visual_scope=scope)
    def test_general_and_flow_preserve_confirmed_receiving_events(self):
        for flow in (False,True):
            self.setUp();result=self.execute(flow=flow)
            self.assertTrue(result.ok,getattr(result,'log',getattr(result,'why',None)))
            self.assertEqual(2,len(result.events));self.assertEqual(['confirmed']*2,[e['state'] for e in result.events])
            self.assertEqual([{'name':'window_input','element_index':3,'key':'Right'},
                {'name':'window_input','element_index':3,'text':'A😀한글'}],[a for a,k in self.app.calls])
            self.assertEqual(2,len(result.visual_history))
            self.assertTrue(all(k['approved'] is True for a,k in self.app.calls))
    def test_default_click_scope_does_not_authorize_keyboard(self):
        result=self.execute(scope=replace(self.scope,input_actions=frozenset({'click'})))
        self.assertFalse(result.ok);self.assertFalse(self.app.calls)
    def test_text_only_scope_does_not_admit_a_key(self):
        self.assertFalse(self.execute(scope=replace(self.scope,input_actions=frozenset({'text'}))).ok)
        self.assertFalse(self.app.calls)
    def test_incomplete_receiving_proof_stops_before_second_input(self):
        scope=replace(self.scope,verify=lambda *args:False,verify_step=lambda *args:False)
        result=self.execute(flow=True,scope=scope)
        self.assertFalse(result.ok);self.assertEqual(1,len(self.app.calls))
        self.assertEqual('unknown',result.events[0]['state'])
        self.assertEqual(1,self.scope.planner.call_count)
    def test_forged_pair_never_replans_or_verifies(self):
        for field,value in [('posted_events',1),('window_id',18),('obs_id','other'),('reobserve_required',False)]:
            self.setUp();self.app.reply[field]=value
            result=self.execute(flow=True)
            self.assertFalse(result.ok);self.assertEqual(1,len(self.app.calls));self.assertEqual('unknown',result.events[0]['state'])
    def test_host_input_kinds_are_strict_and_capabilities_checked_before_model(self):
        for kinds in (set({'key'}),frozenset(),frozenset({'shortcut'})):
            with self.assertRaises(ValueError):replace(self.scope,input_actions=kinds)
        self.app.capabilities=set()
        self.assertFalse(self.execute(flow=True).ok);self.scope.planner.assert_not_called()
        self.assertEqual(0,self.counter)


class VisualInputExecutorTests(unittest.TestCase):
    def setUp(self):
        self.app=App();self.app.capabilities=set(WINDOW_INPUT_CAPABILITIES)
        self.app.reply['input_route']='window_input'
        self.executor=NativeVisualClickExecutor(self.app,'owned')
        self.counter=0
    def execute(self,proposal,*,authorize=lambda *args:True,fresh_change=None,cancelled=None):
        self.counter+=1;c=shot('executor'+str(id(self))+str(self.counter))
        with patch('nlc.native_visual_actions.capture_window',return_value=replace(c,**(fresh_change or {}))):
            return self.executor.input(c,proposal,authorize,cancelled=cancelled)
    def test_shapes_controls_and_nonliteral_authority_never_dispatch(self):
        for proposal in ({'action':'key','key':'Escape'},{'action':'text','text':'\n'},
                {'action':'text','text':'a','element_index':7},{'action':'text','text':'😀'*33}):
            self.assertEqual('not_started',self.execute(proposal)['effect'])
        for authority in (None,lambda *args:False,lambda *args:1):
            self.assertEqual('not_started',self.execute({'action':'text','text':'a'},authorize=authority)['effect'])
        self.assertFalse(self.app.calls)
    def test_pixels_identity_and_cancel_reject_before_input(self):
        for changes in ({'sha256':'changed'},{'pid':43},{'window_id':18},{'window_frame':(101,200,400,200)}):
            self.assertEqual('not_started',self.execute({'action':'key','key':'Right'},fresh_change=changes)['effect'])
        self.assertEqual('not_started',self.execute({'action':'text','text':'a'},cancelled=lambda:True)['effect'])
        self.assertFalse(self.app.calls)
    def test_capture_is_one_use_across_click_and_keyboard_even_after_uncertainty(self):
        c=shot('spent'+str(id(self)));self.app.reply['posted_events']=1
        with patch('nlc.native_visual_actions.capture_window',return_value=c):
            event=self.executor.input(c,{'action':'text','text':'a'},lambda *args:True)
        self.assertEqual('unknown',event['effect'])
        self.assertEqual('capture_already_used',self.executor.click(c,{'action':'click','x':1,'y':1},lambda *args:True)['reason'])
        self.assertEqual(1,len(self.app.calls))
    def test_authority_cannot_mutate_the_pinned_proposal(self):
        proposal={'action':'text','text':'A😀한글'}
        def authorize(*args):proposal['text']='changed';return True
        self.assertEqual('dispatched',self.execute(proposal,authorize=authorize)['effect'])
        self.assertEqual('A😀한글',self.app.calls[0][0]['text'])


class NativeInputPlannerTests(unittest.TestCase):
    def test_parser_only_accepts_host_input_kinds_and_bounded_payloads(self):
        proposals=[{'action':'key','key':'Right'},{'action':'text','text':'A😀한글'},{'action':'done'},{'action':'stop'}]
        for proposal in proposals:
            with patch('nlc.visual.query_visual',return_value={'answer':json.dumps(proposal)}):
                self.assertEqual(proposal,plan_native_input(shot(),'owned'))
        for proposal in ({'action':'key','key':'cmd+s'},{'action':'text','text':'\n'},{'action':'click','x':1,'y':1},
                {'action':'text','text':'a','approved':True},{'action':'text','text':'😀'*33}):
            with patch('nlc.visual.query_visual',return_value={'answer':json.dumps(proposal)}),self.assertRaises(VisualError):
                plan_native_input(shot(),'owned')
        with patch('nlc.visual.query_visual',return_value={'answer':'{"action":"key","key":"Right"}'}),self.assertRaises(VisualError):
            plan_native_input(shot(),'owned',input_actions=frozenset({'text'}))
