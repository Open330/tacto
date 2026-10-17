"""Probe honesty: a routed menu/no-op or incomplete audit is not success."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('background_probe_fixture',Path(__file__).resolve().parents[1]/'examples/probe_background_input.py')
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class Target:
    def __init__(self, no_menu=False):
        self.value='0';self.mode='basic';self.actions=[];self.no_menu=no_menu
    def _resolve(self,*args,**kwargs): return 42
    def observe(self,*args,**kwargs):
        tree=(f'0 Window Title: Calculator\n  1 ScrollArea Identifier: StandardInputView;value:{self.value}\n'
              f'    5 StaticText Value: {self.value}\n'
              '  2 Button Identifier: Seven\n'
              f'  3 Button Identifier: {"Clear" if self.value != "0" else "AllClear"}\n'
              f'  4 Button Identifier: Mode: {self.mode}; unitConversion: false')
        return dict(type='observation',obs_id='obs',tree=tree)
    def act(self,obs,target,action):
        self.actions.append(action)
        if action['name']=='click':self.value='7' if action['element_index']==2 else '0'
        elif not self.no_menu:self.mode=action['path'][-1].lower()
        return {'type':'acted'}


class Audit:
    status='UNCHANGED_SAMPLED'
    def __init__(self,*args,**kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def report(self):return {'status':self.status,'raw_report':{'samples':[{}, {}, {}]}}


class ProbeTests(unittest.TestCase):
    def run_probe(self,target,audit=Audit,**kwargs):
        native=types.SimpleNamespace(Driver=lambda:target,_attr=lambda *args:False,
                                     AXUIElementCreateApplication=lambda pid:pid)
        process=types.SimpleNamespace(ProcessInputAudit=audit)
        with patch.dict(sys.modules,{'tacto.driver':native,'tacto.input_audit_process':process}), \
             patch.object(probe.time,'sleep'):
            return probe.run_probe(target,transport='fixture-only',**kwargs)
    def test_readback_and_sampling_required_for_each_of_four_steps(self):
        target=Target();result=self.run_probe(target)
        self.assertTrue(result['passed'],result)
        self.assertEqual(4,len(target.actions))
        self.assertEqual(('0','basic'),(target.value,target.mode))
        self.assertTrue(all(s['effect_verified'] for s in result['steps']))
    def test_menu_acted_without_mode_change_fails(self):
        target=Target(no_menu=True);result=self.run_probe(target)
        self.assertFalse(result['passed'])
        self.assertEqual(3,len(target.actions))
        self.assertIn('target_effect_not_verified',result['error'])
    def test_observed_focus_change_stops_without_cleanup(self):
        class Changed(Audit):status='CHANGED'
        target=Target();result=self.run_probe(target,Changed)
        self.assertFalse(result['passed'])
        self.assertEqual(1,len(target.actions))
        self.assertEqual('7',target.value)
    def test_sampler_startup_failure_dispatches_nothing(self):
        class Unavailable(Audit):
            status='INCONCLUSIVE'
            def __enter__(self):raise RuntimeError('sampler_unavailable')
        target=Target();result=self.run_probe(target,Unavailable)
        self.assertFalse(result['passed'])
        self.assertEqual([],target.actions)
        self.assertEqual('INCONCLUSIVE',result['steps'][0]['audit']['status'])
    def test_non_fixture_calculator_is_not_reset(self):
        target=Target();target.value='42';result=self.run_probe(target)
        self.assertFalse(result['passed'])
        self.assertEqual([],target.actions)
        self.assertEqual('42',target.value)

    def test_selection_requirement_reaches_every_collector(self):
        calls=[]
        class SelectionAudit(Audit):
            def __init__(self, **kwargs):
                calls.append(kwargs['require_selection'])
        result=self.run_probe(Target(),SelectionAudit,require_selection=True)
        self.assertTrue(result['passed'],result)
        self.assertTrue(result['selection_observation_required'])
        self.assertEqual([True]*4,calls)

    def test_missing_required_selection_prevents_target_action(self):
        class MissingSelection(Audit):
            status='INCONCLUSIVE'
            def __init__(self, **kwargs):
                self.required=kwargs['require_selection']
            def __enter__(self):
                if self.required:
                    raise RuntimeError('required_selection_unavailable')
                return self
        target=Target()
        result=self.run_probe(target,MissingSelection,require_selection=True)
        self.assertFalse(result['passed'])
        self.assertEqual([],target.actions)
        self.assertIn('required_selection_unavailable',result['error'])
