"""Promoted window keys retain their observed input and verify a fresh effect."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from tacto.general import run
from tacto.flow import Postcondition, Selector, replay, pin_params
from tacto.promote import candidate_from, deployable, gate, to_flow
import test_navigation_replay as fixture


class NavigationPromotionTests(unittest.TestCase):
    def candidate(self):
        app = fixture.App()
        actions = iter(['press_key', 'read', None])
        def planner(goal, candidates, history):
            action = next(actions)
            if action is None:
                return {'done': True}
            chosen = next(c for c in candidates if c['match'] == 'input' and action in c['can'])
            return {'actions': [{'do': action, 'candidate': chosen, **({'key':'Right'} if action == 'press_key' else {})}]}
        result = run(app, 'fixture', 'navigate once and read', planner)
        self.assertTrue(result.ok, result.why)
        candidate = candidate_from(result, 'fixture', 'navigate once and read')
        self.assertIsNotNone(candidate)
        return candidate

    def post(self):
        return Postcondition(field=Selector(role='TextArea', identifier='input'), attribute='Value', equals='1')

    def test_successful_run_still_needs_explicit_navigation_result(self):
        candidate = self.candidate()
        self.assertIs(candidate.actions[0]['window_key_bound'], True)
        self.assertIsNone(deployable(candidate, 'navigate', candidate.goal)[0])
        flow, reasons = deployable(candidate, 'navigate', candidate.goal, navigation_postconditions={0:self.post()})
        self.assertEqual(reasons, [])
        self.assertEqual(flow.steps[0].selector.identifier, 'input')
        self.assertEqual(flow.steps[0].binding.operation, 'press_key')
        for changes in (True, False):
            app = fixture.App(changes=changes)
            result = replay(app, flow)
            self.assertEqual(result.ok, changes, result.log)
            self.assertEqual(len([a for a,_ in app.calls if a['name']=='press_key']), 1)
            self.assertEqual([a['name'] for a,_ in app.calls], ['press_key','read'] if changes else ['press_key'])

    def test_tampered_input_key_binding_and_weak_result_fail_gate(self):
        candidate = self.candidate()
        flow = to_flow(candidate, 'navigate', navigation_postconditions={0:self.post()})
        key = flow.steps[0]
        for altered in (replace(key, selector=None),
                        replace(key, selector=Selector(role='TextArea', identifier='other')),
                        replace(key, params={'key':'Left'}),
                        replace(key, params={'key':'Escape'}, binding=replace(key.binding, params_digest=pin_params({'key':'Escape'}))),
                        replace(key, binding=replace(key.binding, identifier='other')),
                        replace(key, binding=replace(key.binding, bundle_id='foreign')),
                        replace(key, postcondition=Postcondition(contains='1')),
                        replace(key, postcondition=None, expect='1'),
                        replace(key, unverified_ok=True)):
            with self.subTest(step=altered):
                self.assertTrue(gate(candidate, replace(flow, steps=[altered, *flow.steps[1:]]), candidate.goal))
        legacy = replace(candidate, actions=[{k:v for k,v in a.items() if k!='window_key_bound'} for a in candidate.actions])
        self.assertTrue(gate(legacy, flow, candidate.goal))

    def test_different_key_or_old_unbound_history_is_not_the_same_path(self):
        candidate = self.candidate()
        for update in ({'key':'Left'}, {'window_key_bound':False}):
            changed = replace(candidate, actions=[{**candidate.actions[0], **update}, *candidate.actions[1:]])
            self.assertFalse(candidate.same_path_as(changed))

    def test_author_sees_actual_backend_capabilities(self):
        from tacto.authoring import author
        app = fixture.App()
        expected = fixture.NavigationReplayTests().flow()
        with patch('tacto.authoring._attempt', return_value=expected) as attempt:
            self.assertIs(author('navigate', app, target='fixture'), expected)
        prompt = attempt.call_args.args[0]
        self.assertIn('press_key.element_bound', prompt)
        self.assertIn('Backend capabilities', prompt)
        self.assertNotIn('helper does not implement one', prompt)
