"""Return beyond the Go to Folder sheet: a declared submit, bound, whose field
postcondition does not hold before the key (2026-10-10: open a chat from its
selected row, send the message in its field). The helper is told the host
vouched (submit_authorized); everything else stays as for a bound key."""
from dataclasses import replace
import unittest
from tacto.contract import Envelope, ContextBinding
from tacto.flow import Flow, Step, Selector, Postcondition, replay, pin_params
from tacto.helper import HelperDriver
from tacto.protocol import window_key_reobservation

CAPS = {'action.press_key', 'press_key.element_bound', 'press_key.no_escape', 'press_key.return_bound'}
SENT = {'type': 'acted', 'input_route': 'window_bound_submit_key', 'posted_events': 1,
        'unverified': 'observe_effect', 'reobserve_required': True}


class App:
    capabilities = CAPS

    def __init__(self, *, opens=True, already=False, response=None):
        self.opened = already; self.opens = opens; self.calls = []; self.response = response

    def observe(self, *args, **kwargs):
        tree = ('0 Window  Title: Chats\n  1 Table  Identifier: rooms\n'
                '    2 Row [selected]\n      3 StaticText  Value: Room A')
        if self.opened:
            tree += '\n4 Window  Title: Room A'
        return {'type': 'observation', 'obs_id': 'obs', 'tree': tree}

    def act(self, obs, target, action, **kwargs):
        if kwargs.get('risk_class') in ('confirm', 'preapproved') and kwargs.get('approved') is not True:
            # What the real transports do: ask, send nothing.
            return {'type': 'confirm_request', 'what': kwargs.get('intent_text'),
                    'risk_class': kwargs['risk_class']}
        self.calls.append((action, kwargs))
        if self.opens:
            self.opened = True
        return dict(self.response or SENT)

    def wait_until(self, target, predicate, **kwargs):
        result = self.observe()
        return result if predicate(result['tree']) else {'type': 'timeout', 'last_obs': result}


def step(effects=frozenset({'write_ephemeral'}), **changes):
    env = Envelope.minimal()
    params = {'key': 'Return'}
    base = Step('press_key', Selector(value='Room A', click_ancestor=['Row']), params=params,
                is_submit=True, safety=env.contract_for(set(effects), 'open room'),
                binding=ContextBinding(bundle_id='fixture', value='Room A', operation='press_key',
                                       params_digest=pin_params(params)),
                postcondition=Postcondition(field=Selector(role='Window', title='Room A'),
                                            attribute='Title', equals='Room A'))
    return replace(base, **changes)


class BoundReturnReplayTests(unittest.TestCase):
    def run_step(self, app, st):
        return replay(app, Flow('open', 'fixture', [st], schema=2))

    def test_return_on_selected_row_is_authorized_and_observed(self):
        app = App(); result = self.run_step(app, step())
        self.assertTrue(result.ok, result.log)
        self.assertEqual(len(app.calls), 1)
        action, kwargs = app.calls[0]
        self.assertEqual(action, {'name': 'press_key', 'key': 'Return', 'element_index': 2})
        self.assertIs(kwargs.get('submit_authorized'), True)
        self.assertIs(result.events[0]['observation_required'], True)

    def test_postcondition_already_true_never_sends(self):
        app = App(already=True); result = self.run_step(app, step())
        self.assertFalse(result.ok, result.log)
        self.assertEqual(app.calls, [])

    def test_contract_is_required_before_input(self):
        for changed in (step(is_submit=False), step(binding=None), step(postcondition=None),
                        step(unverified_ok=True),
                        step(effects={'credential'})):
            app = App()
            with self.subTest(step=changed):
                self.assertFalse(self.run_step(app, changed).ok)
                self.assertEqual(app.calls, [])

    def test_text_input_return_cannot_be_declared_ephemeral(self):
        # A message field's Return sends; declaring it a UI change is refused
        # before any key (Codex review of f339633).
        class Field(App):
            def observe(self, *args, **kwargs):
                tree = '0 Window  Title: Room A\n  1 TextArea  Value: hi, Identifier: message-field'
                return {'type': 'observation', 'obs_id': 'obs', 'tree': tree}
        params = {'key': 'Return'}
        st = step(selector=Selector(identifier='message-field'),
                  binding=ContextBinding(bundle_id='fixture', identifier='message-field',
                                         operation='press_key', params_digest=pin_params(params)),
                  postcondition=Postcondition(field=Selector(identifier='message-field'),
                                              attribute='Value', equals=''))
        app = Field(); result = self.run_step(app, st)
        self.assertFalse(result.ok, result.log)
        self.assertEqual(app.calls, [])
        self.assertIn('contract_effect_mismatch', str(result.log))

    def test_without_capability_return_stays_refused(self):
        app = App(); app.capabilities = CAPS - {'press_key.return_bound'}
        self.assertFalse(self.run_step(app, step()).ok)
        self.assertEqual(app.calls, [])

    def test_transmit_is_gated_for_approval_before_any_key(self):
        for caps in (CAPS, CAPS | {'press_key.return_in_go_to_folder'}):
            with self.subTest(capabilities=caps):
                app = App(); app.capabilities = caps
                result = self.run_step(app, step(effects={'transmit'}))
                self.assertFalse(result.ok, result.log)
                self.assertEqual(app.calls, [])
                self.assertTrue(any(entry.get('status') == 'gated' for entry in result.log), result.log)
                self.assertNotIn('navigation_contract_required', str(result.log))

    def test_trace_preserves_bound_submit_and_replays_the_same_contract(self):
        from tacto.trace import TracingDriver, TraceDriver
        class NamedArgumentsApp(App):
            def act(self, obs_id, target, action, **kwargs):
                return super().act(obs_id, target, action, **kwargs)
            def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
                return super().wait_until(target, predicate, timeout_ms=timeout_ms, poll_ms=poll_ms)
        app = NamedArgumentsApp()
        traced = TracingDriver(app)
        first = self.run_step(traced, step())
        self.assertTrue(first.ok, first.log)
        self.assertEqual(1, len(app.calls))
        self.assertIs(app.calls[0][1].get('submit_authorized'), True)
        requests = [e['request'] for e in traced.trace.events if e['kind'] == 'act']
        self.assertIs(requests[0]['submit_authorized'], True)
        second = self.run_step(TraceDriver(traced.trace), step())
        self.assertTrue(second.ok, second.log)
        self.assertEqual(1, len(app.calls))

    def test_no_effect_or_malformed_receipt_never_retries(self):
        cases = [App(opens=False)]
        for key, value in [('posted_events', 2), ('input_route', 'window_key'),
                           ('reobserve_required', False)]:
            cases.append(App(opens=False, response={**SENT, key: value}))
        for app in cases:
            with self.subTest(response=app.response):
                self.assertFalse(self.run_step(app, step()).ok)
                self.assertEqual(len(app.calls), 1)

    def test_receipt_contract(self):
        action = {'name': 'press_key', 'key': 'Return', 'element_index': 2}
        self.assertTrue(window_key_reobservation(action, SENT, CAPS))
        self.assertFalse(window_key_reobservation(action, SENT, CAPS - {'press_key.return_bound'}))
        self.assertFalse(window_key_reobservation(action, {**SENT, 'input_route': 'window_submit_key'},
                                                  CAPS))


class HelperTransportTests(unittest.TestCase):
    def driver(self, caps=CAPS):
        sent = []
        driver = HelperDriver.__new__(HelperDriver)
        driver.capabilities = frozenset(caps)
        driver._send = lambda msg: sent.append(msg) or {'type': 'acted'}
        return driver, sent

    def test_flag_travels_only_with_return(self):
        driver, sent = self.driver()
        driver.act('obs', 't', {'name': 'press_key', 'key': 'Return', 'element_index': 2},
                   submit_authorized=True)
        self.assertIs(sent[-1].get('submit_authorized'), True)
        for action in ({'name': 'press_key', 'key': 'Tab', 'element_index': 2},
                       {'name': 'click', 'element_index': 2}):
            result = driver.act('obs', 't', action, submit_authorized=True)
            self.assertEqual(result.get('effect'), 'not_started')
        self.assertEqual(len(sent), 1)

    def test_flag_needs_capability_and_true(self):
        driver, sent = self.driver(CAPS - {'press_key.return_bound'})
        result = driver.act('obs', 't', {'name': 'press_key', 'key': 'Return', 'element_index': 2},
                            submit_authorized=True)
        self.assertEqual(result.get('error'), 'submit_authorization_invalid')
        driver, sent = self.driver()
        result = driver.act('obs', 't', {'name': 'press_key', 'key': 'Return', 'element_index': 2},
                            submit_authorized=1)
        self.assertEqual(result.get('error'), 'submit_authorization_invalid')
        self.assertEqual(sent, [])


if __name__ == '__main__':
    unittest.main()
