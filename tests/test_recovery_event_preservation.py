"""Execute the owned recovery recipe; its effects must survive in the parent."""
import unittest
from unittest.mock import patch

from tacto.contract import Envelope
from tacto.flow import Flow, Step, Selector, Postcondition, Resolution, replay
from tacto.helper import HelperUnavailable


class DialogDriver:
    capabilities = {'click.window_bound_element'}

    def __init__(self, *, activated=False, recovery_reader_fails=False, initial_timeout=False, uncertain_cancel=False):
        self.activated = activated
        self.recovery_reader_fails = recovery_reader_fails
        self.initial_timeout = initial_timeout
        self.uncertain_cancel = uncertain_cancel
        self.sheet = True
        self.done = False
        self.calls = []

    def observe(self, *args, **kwargs):
        if not self.sheet and self.recovery_reader_fails:
            raise HelperUnavailable('PRIVATE_RECOVERY_READER')
        tree = '0 Window Title: Work\n  1 TextField Identifier: work, Value: ' + ('done' if self.done else 'pending')
        if self.sheet:
            tree += '\n  2 Sheet Title: Dialog\n    3 Button Title: Cancel'
        return {'type':'observation','obs_id':'current','tree':tree}

    def act(self, obs, target, action, **kwargs):
        self.calls.append(dict(action))
        if action['element_index'] == 3:
            self.sheet = False
            if self.uncertain_cancel:
                return {'type':'acted', 'unverified':'observe_effect',
                        'input_route':'window_bound_element_click', 'posted_events':2,
                        'reobserve_required':True}
            return {'type':'acted', **({'activated_target':True} if self.activated else {})}
        if self.sheet:
            if self.initial_timeout:
                return {'type':'acted'}
            return {'type':'error','error':'dialog_blocked','effect':'not_started'}
        self.done = True
        return {'type':'acted'}

    def wait_until(self, target, predicate, **kwargs):
        obs = self.observe()
        return obs if predicate(obs['tree']) else {'type':'timeout','last_obs':obs}


def run(driver):
    step = Step('set_value', Selector(identifier='work'), params={'value':'done'},
                safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'work'),
                postcondition=Postcondition(field=Selector(identifier='work'), attribute='Value', equals='done'),
                on_uncertain=['action_failed','postcondition_timeout'])
    return replay(driver, Flow('parent','owned',[step],schema=2), budget=1,
                  escalate=lambda why: Resolution(kind='recover',recovery='dismiss_dialog'))


class RecoveryEventTests(unittest.TestCase):
    def test_successful_recovery_event_is_kept_in_dispatch_order(self):
        driver = DialogDriver()
        result = run(driver)
        self.assertTrue(result.ok, result.log)
        self.assertEqual([a['element_index'] for a in driver.calls], [1,3,1])
        self.assertEqual([e['action']['element_index'] for e in result.events], [3,1])
        self.assertEqual(result.events[0]['recovery'], 'dismiss_dialog')
        self.assertEqual(result.events[0]['parent_step'], 0)
        self.assertEqual(len({e['event_id'] for e in result.events}),2)

    def test_failed_recovery_keeps_dispatched_cancel_and_reader_failure(self):
        driver = DialogDriver(recovery_reader_fails=True)
        result = run(driver)
        self.assertFalse(result.ok)
        self.assertEqual([a['element_index'] for a in driver.calls], [1,3])
        self.assertEqual([e['action']['element_index'] for e in result.events], [3])
        recovery = next(e for e in result.log if e.get('stage') == 'recovery')
        self.assertEqual(recovery['step'], 0)
        self.assertTrue(any(e.get('error') == 'observation_unavailable'
                            for e in recovery['detail']['log']))
        self.assertNotIn('PRIVATE_RECOVERY_READER', str(result.log))

    def test_recovery_activation_stops_same_step_retry_for_both_failure_paths(self):
        for timeout in (False,True):
            with self.subTest(timeout=timeout):
                driver = DialogDriver(activated=True, initial_timeout=timeout)
                result = run(driver)
                self.assertFalse(result.ok)
                self.assertEqual([a['element_index'] for a in driver.calls], [1,3])
                self.assertTrue(result.events[-1]['activated_target'])
                self.assertEqual(result.log[-1]['error'],'target_seen_in_front')
                self.assertEqual(result.log[-1]['effect'],'not_started')

    def test_uncertain_recovery_effect_does_not_license_parent_retry(self):
        driver = DialogDriver(uncertain_cancel=True)
        result = run(driver)
        self.assertFalse(result.ok)
        self.assertEqual([a['element_index'] for a in driver.calls], [1,3])
        self.assertEqual(result.events[-1]['state'],'unknown')
        self.assertTrue(any(e.get('error') == 'recovery_unverified' for e in result.log))

    def test_child_logs_are_bounded_redacted_and_not_parent_step_numbers(self):
        from tacto.flow import ReplayResult, _run_fix
        parent, child = ReplayResult(), ReplayResult()
        child.protect('PRIVATE_CHILD_VALUE')
        for i in range(70):
            child.record(status='ok',step=i,detail='PRIVATE_CHILD_VALUE')
        parent.preparation_diagnostics = [{'kept':True}] * 63
        child.preparation_diagnostics = [{'child':True}] * 4
        with patch('tacto.flow.replay',return_value=child):
            self.assertTrue(_run_fix(None,Flow('parent','owned',[]),'dismiss_dialog',None,
                                    result=parent,parent_step=8))
        self.assertEqual(len(parent.log),1)
        entry = parent.log[0]
        self.assertEqual(entry['step'],8)
        self.assertEqual(len(entry['detail']['log']),64)
        self.assertEqual(entry['detail']['log_total'],70)
        self.assertTrue(entry['detail']['log_truncated'])
        self.assertEqual(entry['detail']['log'][0]['recovery_step'],0)
        self.assertNotIn('step',entry['detail']['log'][0])
        self.assertNotIn('error',entry)
        self.assertNotIn('PRIVATE_CHILD_VALUE',str(parent.log))
        self.assertEqual(len(parent.preparation_diagnostics),64)


if __name__ == '__main__': unittest.main()
