"""Predicate polling reads immediately; ordinary observations retain settling."""
import unittest
from unittest.mock import Mock, patch
from tacto.helper import HelperDriver
from tacto.driver import Driver
from tacto.flow import Flow, Step, Selector, Postcondition, replay
from tacto.contract import Envelope


class Clock:
    def __init__(self): self.value = 100.; self.sleeps = []
    def now(self): return self.value
    def sleep(self, seconds): self.sleeps.append(seconds); self.value += seconds


class ObservationPollLatencyTests(unittest.TestCase):
    def helper(self, replies):
        driver = object.__new__(HelperDriver)
        driver._last_action_at = 100.
        driver._send = Mock(side_effect=replies)
        return driver

    def test_ready_result_has_no_fixed_wait(self):
        ready = {'type': 'observation', 'tree': 'ready'}
        driver = self.helper([ready])
        clock = Clock()
        with patch('tacto.helper.time.monotonic', side_effect=clock.now), \
             patch('tacto.helper.time.sleep', side_effect=clock.sleep):
            self.assertEqual(ready, driver.wait_until('fixture', lambda tree: tree == 'ready'))
        self.assertEqual([], clock.sleeps)
        self.assertEqual(1, driver._send.call_count)

    def test_delayed_result_is_polled_not_assumed_ready(self):
        driver = self.helper([{'type': 'observation', 'tree': 'old'},
                              {'type': 'observation', 'tree': 'ready'}])
        clock = Clock()
        with patch('tacto.helper.time.monotonic', side_effect=clock.now), \
             patch('tacto.helper.time.sleep', side_effect=clock.sleep):
            result = driver.wait_until('fixture', lambda tree: tree == 'ready', poll_ms=50)
        self.assertEqual('ready', result['tree'])
        self.assertEqual([.05], clock.sleeps)

    def test_false_result_times_out_and_no_new_action_is_sent(self):
        driver = self.helper([{'type': 'observation', 'tree': 'old'}] * 3)
        clock = Clock()
        with patch('tacto.helper.time.monotonic', side_effect=clock.now), \
             patch('tacto.helper.time.sleep', side_effect=clock.sleep):
            result = driver.wait_until('fixture', lambda tree: tree == 'ready', timeout_ms=100, poll_ms=60)
        self.assertEqual('timeout', result['type'])
        self.assertAlmostEqual(.1, sum(clock.sleeps))
        self.assertTrue(all(call.args[0]['type'] == 'observe' for call in driver._send.call_args_list))

    def test_default_observation_keeps_settle_and_uses_monotonic_clock(self):
        driver = self.helper([{'type': 'observation', 'tree': 'ready'}])
        clock = Clock()
        with patch('tacto.helper.time.monotonic', side_effect=clock.now), \
             patch('tacto.helper.time.time', return_value=999999999.), \
             patch('tacto.helper.time.sleep', side_effect=clock.sleep):
            driver.observe('fixture')
        self.assertEqual([1.], clock.sleeps)

    def test_python_observer_has_same_optional_settle_behavior(self):
        class StopRead(Exception): pass
        for settle in (True, False):
            driver = object.__new__(Driver)
            driver._last_action_at = 100.
            driver._resolve = Mock(side_effect=StopRead)
            clock = Clock()
            with patch('tacto.driver.time.monotonic', side_effect=clock.now), \
                 patch('tacto.driver.time.sleep', side_effect=clock.sleep), self.assertRaises(StopRead):
                driver.observe('fixture', _settle_after_action=settle)
            self.assertEqual([1.] if settle else [], clock.sleeps)

    def test_already_true_toggle_does_not_pass_on_the_stale_pre_action_value(self):
        self._delayed_toggle(initial='1', wanted='1', expected_ok=False)

    def test_new_toggle_result_is_polled_without_a_fixed_second(self):
        self._delayed_toggle(initial='0', wanted='1', expected_ok=True)

    def test_general_walk_also_rejects_a_stale_already_true_expectation(self):
        self._delayed_toggle(initial='1', wanted='1', expected_ok=False, general=True)

    def test_general_walk_polls_a_new_expectation_immediately(self):
        self._delayed_toggle(initial='0', wanted='1', expected_ok=True, general=True)

    def _delayed_toggle(self, initial, wanted, expected_ok, general=False):
        clock = Clock()
        class App:
            def __init__(self):
                self.sent = 0; self.changed_at = None
                self.helper = object.__new__(HelperDriver)
                self.helper._last_action_at = 0
                self.helper._send = self.send
            def send(self, message):
                value = initial
                if self.changed_at is not None and clock.now() >= self.changed_at:
                    value = '0' if initial == '1' else '1'
                return {'type':'observation', 'obs_id':'owned', 'target':'fixture',
                        'tree':'0 Window Title: Owned\n  1 CheckBox Identifier: toggle, Value: '+value}
            def identity(self, target): return target
            def selection(self, target): return {'type':'selection', 'digest':'stable'}
            def probe(self, obs_id, target, element_index):
                return {'type':'probe', 'line':self.send({})['tree'].splitlines()[element_index]}
            def observe(self, *args, **kwargs): return self.helper.observe(*args, **kwargs)
            def wait_until(self, *args, **kwargs): return self.helper.wait_until(*args, **kwargs)
            def act(self, *args, **kwargs):
                self.sent += 1; self.changed_at = clock.now()+.2
                self.helper._last_action_at = clock.now()
                return {'type':'acted'}
        app = App()
        step = Step('click', Selector(identifier='toggle'),
                    safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'entry'),
                    postcondition=Postcondition(field=Selector(identifier='toggle'), equals=wanted, attribute='Value'))
        with patch('tacto.helper.time.monotonic', side_effect=clock.now), \
                patch('tacto.helper.time.sleep', side_effect=clock.sleep):
            if general:
                from tacto.general import run
                def planner(goal, candidates, history):
                    chosen = next(c for c in candidates if 'click' in c['can'])
                    return {'actions':[{'do':'click', 'candidate':chosen}], 'expect':'Value: '+wanted}
                result = run(app, 'fixture', 'toggle', planner, max_screens=1)
                self.assertEqual(expected_ok, result.visits[0].verified, result.why)
            else:
                result = replay(app, Flow('delayed-toggle', 'fixture', [step], schema=2))
                self.assertEqual(expected_ok, result.ok, result.log)
        self.assertEqual(1, app.sent)
        if expected_ok:
            # General's separate next-screen observation deliberately retains
            # its ordinary settle; this check is about predicate polling.
            self.assertEqual([.25, .75] if general else [.25], clock.sleeps)
        else:
            self.assertEqual(1., clock.sleeps[0])
            if not general:
                self.assertEqual('postcondition_timeout', result.log[-1]['error'])
