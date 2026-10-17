"""Submitting a message field cannot hide behind an ephemeral contract."""
import unittest

from tacto.contract import ContextBinding, Envelope
from tacto.flow import Flow, Postcondition, Selector, Step, pin_params, replay


class ReturnTransmitBoundaryTests(unittest.TestCase):
    def test_message_field_submit_requires_transmit_before_any_input(self):
        class MessageBox:
            capabilities = {'action.press_key', 'press_key.element_bound',
                            'press_key.return_bound'}

            def __init__(self): self.calls = 0

            def observe(self, *args, **kwargs):
                return {'type':'observation', 'obs_id':'observed', 'tree':
                    '0 Window Title: Chat\n  1 TextField Identifier: message-field, Value: hello\n'
                    f'  2 StaticText Identifier: delivery-count, Value: {self.calls}'}

            def act(self, *args, **kwargs):
                self.calls += 1
                return {'type':'acted', 'input_route':'window_bound_submit_key',
                        'posted_events':1, 'unverified':'observe_effect',
                        'reobserve_required':True}

            def wait_until(self, target, predicate, **kwargs):
                observation = self.observe()
                return observation if predicate(observation['tree']) else {'type':'timeout'}

        app = MessageBox()
        params = {'key':'Return'}
        step = Step('press_key', Selector(identifier='message-field'), params=params,
                    is_submit=True,
                    safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'submit'),
                    binding=ContextBinding(bundle_id='fixture', identifier='message-field',
                                           operation='press_key', params_digest=pin_params(params)),
                    postcondition=Postcondition(field=Selector(identifier='delivery-count'),
                                                attribute='Value', equals='1'))
        result = replay(app, Flow('hidden_message_send', 'fixture', [step], schema=2))
        self.assertFalse(result.ok, result.log)
        self.assertEqual('contract_effect_mismatch', result.log[-1]['detail']['error'])
        self.assertEqual(['transmit'], result.log[-1]['detail']['detail'])
        self.assertEqual(0, app.calls, 'no approval or transmit contract: no Return')


if __name__ == '__main__':
    unittest.main()
