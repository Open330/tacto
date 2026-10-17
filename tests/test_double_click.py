"""double_click: two window-bound click pairs on one observed element (signed
helper), or AXOpen where the element offers it. Like a bound key, a posted
double click proves nothing by itself — replay must reobserve, never retry."""
from dataclasses import replace
import unittest
from tacto.contract import Envelope, ContextBinding
from tacto.flow import Flow, Step, Selector, Postcondition, replay, pin_params
from tacto.protocol import ACTION_PARAMS, double_click_reobservation

POSTED = {'type': 'acted', 'input_route': 'window_bound_double_click', 'posted_events': 4,
          'unverified': 'observe_effect', 'reobserve_required': True}


class App:
    capabilities = {'action.double_click'}

    def __init__(self, *, opens=True, response=None):
        self.opened = False; self.opens = opens; self.calls = []; self.response = response

    def observe(self, *args, **kwargs):
        tree = '0 Window Title: Chats\n  1 Row\n    2 Cell  Title: Room A'
        if self.opened:
            tree += '\n3 Window Title: Room A'
        return {'type': 'observation', 'obs_id': 'obs', 'tree': tree}

    def act(self, obs, target, action, **kwargs):
        self.calls.append((action, kwargs))
        if self.opens:
            self.opened = True
        return self.response or dict(POSTED)

    def wait_until(self, target, predicate, **kwargs):
        result = self.observe()
        return result if predicate(result['tree']) else {'type': 'timeout', 'last_obs': result}


class DoubleClickReplayTests(unittest.TestCase):
    def flow(self):
        env = Envelope.minimal(); cell = Selector(role='Cell', title='Room A')
        step = Step('double_click', cell,
                    safety=env.contract_for({'write_ephemeral'}, 'open'),
                    binding=ContextBinding(bundle_id='fixture', role='Cell', title='Room A',
                                           operation='double_click', params_digest=pin_params({})),
                    postcondition=Postcondition(contains='Window Title: Room A'))
        return Flow('open', 'fixture', [step], schema=2)

    def test_action_is_known_without_parameters(self):
        self.assertEqual(ACTION_PARAMS['double_click'], frozenset())

    def test_posted_pair_advances_only_after_reobservation(self):
        app = App(); result = replay(app, self.flow())
        self.assertTrue(result.ok, result.log)
        self.assertEqual(len(app.calls), 1)
        self.assertEqual(app.calls[0][0], {'name': 'double_click', 'element_index': 2})
        self.assertEqual(result.events[0]['state'], 'unknown')
        self.assertIs(result.events[0]['observation_required'], True)

    def test_no_effect_or_malformed_receipt_never_retries(self):
        cases = [App(opens=False)]
        for key, value in [('posted_events', 2), ('posted_events', True), ('posted_events', '4'),
                           ('input_route', 'window_key'), ('reobserve_required', False),
                           ('unverified', None)]:
            cases.append(App(opens=False, response={**POSTED, key: value}))
        for app in cases:
            with self.subTest(response=app.response):
                result = replay(app, self.flow())
                self.assertFalse(result.ok, result.log)
                self.assertEqual(len(app.calls), 1)

    def test_consequential_effects_are_not_authorized_by_the_receipt(self):
        env = Envelope.minimal(); flow = self.flow()
        step = replace(flow.steps[0], safety=env.contract_for({'transmit'}, 'open'))
        app = App(); result = replay(app, replace(flow, steps=[step]))
        self.assertFalse(result.ok, result.log)

    def test_contract_requires_capability_and_exact_action(self):
        caps = App.capabilities
        self.assertTrue(double_click_reobservation(
            {'name': 'double_click', 'element_index': 0}, POSTED, caps))
        self.assertFalse(double_click_reobservation(
            {'name': 'double_click', 'element_index': 0}, POSTED, set()))
        for action in ({'name': 'double_click'},
                       {'name': 'double_click', 'element_index': -1},
                       {'name': 'double_click', 'element_index': True},
                       {'name': 'double_click', 'element_index': 0, 'point': [1, 1]},
                       {'name': 'click', 'element_index': 0}):
            self.assertFalse(double_click_reobservation(action, POSTED, caps), action)


if __name__ == '__main__':
    unittest.main()
