"""Flow image input must keep host authority and receiving evidence."""
from dataclasses import replace
from unittest.mock import Mock, patch
import json
import unittest

from nlc.contract import Envelope
from nlc.flow import Flow, Step, Selector, replay
from nlc.general_visual import NativeVisualScope
from nlc.model_budget import model_deadline
from tests.test_general_visual import App
from tests.test_native_visual_actions import shot


class VisualReplayTests(unittest.TestCase):
    def setUp(self):
        self.app = App()
        self.observations = 0
        observe = self.app.observe
        def counted(*args, **kwargs):
            self.observations += 1
            return observe(*args, **kwargs)
        self.app.observe = counted
        self.scope = NativeVisualScope(Selector(role='Window', title='Owned'),
            Mock(return_value={'action':'click', 'x':560, 'y':240}),
            lambda *args:True, lambda capture, history:len(history)==1,
            frozenset({'write_ephemeral'}))
        self.step = Step('visual', params={'scope':'green'}, intent_text='green once',
            safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'green once'))
        self.flow = Flow('green', 'owned', [self.step], schema=2)
        self.count = 0

    def image(self, *args, **kwargs):
        self.count += 1
        return shot('flow'+str(id(self))+str(self.count))

    def execute(self, flow=None, scopes=None, **kwargs):
        with patch('nlc.general_visual.capture_window', side_effect=self.image), \
             patch('nlc.native_visual_actions.capture_window', side_effect=self.image):
            return replay(self.app, flow or self.flow,
                visual_scopes=scopes if scopes is not None else {'green':self.scope}, **kwargs)

    def test_round_trip_and_host_receipt_preserve_one_input_pair(self):
        flow = Flow.from_json(self.flow.to_json())
        result = self.execute(flow)
        self.assertTrue(result.ok, result.log)
        self.assertTrue(result.visual_used)
        self.assertEqual(1, len(result.visual_history))
        self.assertEqual(1, len(self.app.calls))
        self.assertEqual('confirmed', result.events[0]['state'])
        self.assertEqual('green', result.events[0]['visual_flow_scope'])
        self.assertTrue(result.events[0]['visual_host_verified'])
        self.assertTrue(next(row for row in result.log if row.get('action')=='visual')['receiving_host_verified'])

    def test_missing_scope_or_capability_refuses_before_observation(self):
        for scopes in ({}, {'green':object()}, []):
            result = self.execute(scopes=scopes)
            self.assertFalse(result.ok)
            self.assertEqual('visual_scope_required', result.log[-1]['error'])
        self.app.capabilities = set()
        self.assertFalse(self.execute().ok)
        self.assertEqual(0, self.observations)
        self.assertFalse(self.app.calls)

    def test_serialized_pixels_and_invalid_budgets_are_rejected(self):
        for params in ({'scope':'green','point':{'x':1,'y':2}}, {'scope':'green','max_screens':True},
                {'scope':'green','max_screens':21}, {'scope':'green','timeout_seconds':float('inf')},
                {'scope':'green','timeout_seconds':0}, {'scope':'../green'}, {'scope':'green','timeout_seconds':True}):
            flow = replace(self.flow, steps=[replace(self.step, params=params)])
            with self.subTest(params=params), self.assertRaises(ValueError):
                Flow.from_json(flow.to_json())
            self.assertFalse(self.execute(flow).ok)
        self.assertEqual(0, self.observations)

    def test_unverified_and_partial_input_cannot_reach_next_flow_step(self):
        tail = replace(self.step, params={'scope':'green'}, intent_text='second input')
        flow = replace(self.flow, steps=[self.step, tail])
        for partial in (False, True):
            self.app.calls.clear()
            scope = replace(self.scope, verify=lambda *args:False)
            if partial:
                self.app.reply['posted_events'] = 1
            result = self.execute(flow, scopes={'green':scope})
            self.assertFalse(result.ok)
            self.assertEqual('visual_step_unverified', result.log[-1]['error'])
            self.assertEqual(1, len(self.app.calls))
            self.assertEqual('unknown', result.events[0]['state'])

    def test_done_and_denial_are_not_host_receipts(self):
        for scope in (replace(self.scope, authorize=lambda *args:False),
                replace(self.scope, planner=lambda *args:{'action':'done'}, verify=lambda *args:False)):
            self.assertFalse(self.execute(scopes={'green':scope}).ok)
        self.assertFalse(self.app.calls)

    def test_effect_mismatch_or_other_contract_cannot_expand_authority(self):
        changes = [replace(self.step, selector=Selector(role='Window', title='Owned')),
            replace(self.step, safety=Envelope.minimal().contract_for({'read'}, 'read')),
            replace(self.step, safety=None), replace(self.step, unverified_ok=True),
            replace(self.step, risk_class='confirm')]
        for step in changes:
            self.assertFalse(self.execute(replace(self.flow, steps=[step])).ok)
        self.assertEqual(0, self.observations)

    def test_expired_parent_deadline_has_no_capture_planner_or_input(self):
        import time
        with model_deadline(time.monotonic()-1):
            result = self.execute()
        self.assertFalse(result.ok)
        self.assertEqual(0, self.count)
        self.scope.planner.assert_not_called()
        self.assertFalse(self.app.calls)

    def test_selected_branch_retains_visual_event_and_history(self):
        decision = Step('decide', params={'question':'which?', 'choices':['green','other'], 'into':'color',
            'branches':{'green':[self.step]}, 'goal':'green once'})
        flow = replace(self.flow, steps=[decision])
        result = self.execute(flow, decide=lambda *args:'green')
        self.assertTrue(result.ok, result.log)
        self.assertTrue(result.visual_used)
        self.assertEqual(1, len(result.events))
        self.assertEqual(1, len(result.visual_history))
        self.assertTrue(result.events[0]['visual_host_verified'])

    def test_host_registration_is_fixed_during_replay(self):
        scopes = {'green':self.scope}
        original = self.app.observe
        def mutate(*args, **kwargs):
            scopes['green'] = object()
            return original(*args, **kwargs)
        self.app.observe = mutate
        result = self.execute(scopes=scopes)
        self.assertTrue(result.ok, result.log)
        self.assertEqual(1, len(self.app.calls))

    def test_provider_call_metadata_is_retained(self):
        from nlc.provider_observation import record_provider_result
        from nlc.provider_result import ProviderResult
        original = self.scope.planner
        def planner(*args):
            record_provider_result(ProviderResult(request_id='owned-test-1', requested_provider='owned-test', provider='owned-test', transport='fixture', model='fixed', status='ok', text='private test response', error=None, elapsed_seconds=0))
            return original(*args)
        result = self.execute(scopes={'green':replace(self.scope, planner=planner)})
        self.assertTrue(result.ok, result.log)
        self.assertEqual('owned-test', result.provider_calls[0]['provider'])

    def test_fresh_ax_observation_after_verified_image_step(self):
        observe = self.app.observe
        def hybrid(*args, **kwargs):
            result = observe(*args, **kwargs)
            result['tree'] += '\n  4 StaticText Identifier: status, Value: ready'
            return result
        self.app.observe = hybrid
        act = self.app.act
        def mixed(obs, target, action, **kwargs):
            if action['name'] == 'read':
                self.app.calls.append((action, kwargs))
                return {'type':'acted'}
            return act(obs, target, action, **kwargs)
        self.app.act = mixed
        read = Step('read', Selector(role='StaticText', identifier='status'),
            safety=Envelope.minimal().contract_for({'read'}, 'read ready'), unverified_ok=True)
        result = self.execute(replace(self.flow, steps=[self.step, read]))
        self.assertTrue(result.ok, result.log)
        self.assertEqual(['click_point','read'], [action['name'] for action, _ in self.app.calls])
        self.assertGreaterEqual(self.observations, 4)
        self.assertEqual('confirmed', result.events[0]['state'])

    def test_changed_capabilities_are_rechecked_before_the_visual_loop(self):
        original = self.app.observe
        def revoke(*args, **kwargs):
            self.app.capabilities = set()
            return original(*args, **kwargs)
        self.app.observe = revoke
        result = self.execute()
        self.assertFalse(result.ok)
        self.assertEqual('visual_scope_required', result.log[-1]['error'])
        self.assertEqual(0, self.count)
        self.assertFalse(self.app.calls)

    def test_host_proof_can_skip_already_completed_operation(self):
        result = self.execute(scopes={'green':replace(self.scope, verify=lambda *args:True)})
        self.assertTrue(result.ok, result.log)
        self.assertFalse(self.app.calls)
        self.assertFalse(result.events)
        self.scope.planner.assert_not_called()

    def test_ax_predicate_cannot_bypass_explicit_image_scope(self):
        result = self.execute(scopes={'green':replace(self.scope, when=lambda *args:False)})
        self.assertFalse(result.ok)
        self.assertEqual(0, self.observations)
        self.assertFalse(self.app.calls)
