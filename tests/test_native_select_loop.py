"""Portable native-shaped select metadata through the real planner/general loop.

The fake driver does not validate AX menu lifetime; native integration tests do.
"""
import unittest
from tacto.ask import what_to_do_here
from tacto.contract import Resource, SafetyContract
from tacto.flow import Flow, Selector, Step, replay
from tacto.general import run


class Popup:
    capabilities = {'action.open_select', 'action.select_custom_option'}
    def __init__(self, role='PopUpButton', uncertain=False):
        self.role, self.uncertain = role, uncertain
        self.opened = False
        self.value = 'Alpha'
        self.calls = []
        self.epochs = 0
    def identity(self, target): return target
    @property
    def tree(self):
        return (f'0 Window Title: Native\n  1 {self.role} Title: Choice, Value: {self.value}\n'
                f'  2 StaticText Identifier: result, Value: {self.value}')
    def observe(self, *args, **kwargs):
        self.epochs += 1
        return dict(type='observation', obs_id=str(self.epochs), tree=self.tree,
                    custom_selects={'1': {'state': 'open' if self.opened else 'closed',
                        'options': [{'label':'Alpha','disabled':False}, {'label':'Beta','disabled':False}]
                                   if self.opened else []}})
    def probe(self, obs_id, target, element_index):
        return dict(type='probe', line=next(line for line in self.tree.splitlines()
                    if line.strip().startswith(str(element_index)+' ')))
    def act(self, obs_id, target, action, **kwargs):
        self.calls.append((obs_id,dict(action)))
        if action['name']=='open_select': self.opened=True
        if action['name']=='select_custom_option':
            self.opened=False
            if self.uncertain: return dict(type='acted',unverified='selection_unconfirmed')
            self.value=action['option_label']
        return dict(type='acted')


class NativeSelectLoopTests(unittest.TestCase):
    def planner(self, seen):
        def plan(goal, candidates, done):
            seen.append(candidates)
            q=what_to_do_here(goal,candidates,done)
            if len(done)==3: return q.check({'done':True})
            pick=next(i for i,c in enumerate(candidates)
                      if c['match']=='Choice')
            action={'pick':pick,'do':('open_select','select_custom_option','read')[len(done)]}
            if len(done)==1:action['option_label']='Beta'
            return q.check({'actions':[action]})
        return plan
    def test_popup_and_combobox_use_observed_label_without_inventing_value(self):
        for role in ('PopUpButton','ComboBox'):
            with self.subTest(role=role):
                d=Popup(role);seen=[]
                result=run(d,'native','Choose Beta then read result',self.planner(seen))
                self.assertTrue(result.ok,result.why)
                self.assertTrue(result.verified)
                self.assertEqual(['Beta'],result.said)
                self.assertEqual(['open_select','select_custom_option','read'],[a['name'] for _,a in d.calls])
                self.assertNotEqual(d.calls[0][0],d.calls[1][0])
                self.assertNotIn('value',d.calls[1][1])
                closed=next(c for c in seen[0] if c['match']=='Choice')
                self.assertNotIn('click',closed['can'])
                self.assertNotIn('select_custom_option',closed['can'])
    def test_unknown_readback_stops_before_model_done_or_an_unrelated_read(self):
        d=Popup(uncertain=True);seen=[]
        result=run(d,'native','Choose Beta',self.planner(seen))
        self.assertFalse(result.ok)
        self.assertEqual('unknown',result.certainty)
        self.assertEqual('unknown',result.events[-1]['state'])
        self.assertEqual(2,len(seen))
        self.assertEqual(2,len(d.calls))
    def test_replay_unknown_readback_does_not_escalate_or_retry(self):
        d=Popup(uncertain=True);d.opened=True
        flow=Flow('choose','native',[Step('select_custom_option',Selector(role='PopUpButton',title='Choice'),
                  params={'option_label':'Beta'},safety=SafetyContract(effects=['write_ephemeral'],
                    resource=Resource(scope='entry'),reversibility='reversible'))])
        attempts=[]
        result=replay(d,flow,escalate=lambda why:attempts.append(why),budget=3)
        self.assertFalse(result.ok)
        self.assertEqual('unverified',result.log[-1]['status'])
        self.assertEqual('action_unverified',result.log[-1]['error'])
        self.assertEqual('unknown',result.events[-1]['state'])
        self.assertEqual(1,len(d.calls))
        self.assertEqual([],attempts)

    def test_opened_menu_error_is_not_retried_by_replay_recovery(self):
        from unittest.mock import patch
        from tacto.flow import Resolution
        class FailedSelection(Popup):
            def act(self, obs_id, target, action, **kwargs):
                self.calls.append((obs_id,dict(action)))
                self.opened=True
                return dict(type='error',error='action_failed',effect='unknown',
                            detail='menu opened; option unavailable; menu left open')
        d=FailedSelection()
        flow=Flow('choose','native',[Step('select_option',Selector(role='PopUpButton',title='Choice'),
                  params={'option_label':'Beta'},on_uncertain=['action_failed'],
                  safety=SafetyContract(effects=['write_ephemeral'],
                    resource=Resource(scope='entry'),reversibility='reversible'))])
        with patch('tacto.flow._run_fix') as recover:
            result=replay(d,flow,budget=3,escalate=lambda why:Resolution(kind='recover',recovery='dismiss_dialog'))
        self.assertFalse(result.ok)
        self.assertEqual(1,len(d.calls))
        self.assertTrue(d.opened)
        recover.assert_not_called()
        self.assertEqual('unknown',result.events[-1]['state'])
