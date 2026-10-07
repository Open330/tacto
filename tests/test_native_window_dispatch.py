"""Foreground AX preparation and guarded background dispatch delegation."""
from contextlib import ExitStack
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class WindowPreparationTests(unittest.TestCase):
    def setUp(self):
        from nlc import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.el = object()
        self.obs = SimpleNamespace(pid=42, obs_id='o1', elements=[self.el],
                                   process_launch_identity=123.0)
        self.d._latest = {'fixture': self.obs}
        self.d._last_action_at = 0

    def prepare(self, *, frontmost=True, minimized=False, settable=True,
                set_error=0, reports_key=True, stopped=False):
        m = self.m
        wrote = []
        def attr(el, key):
            return {'AXRole': 'AXWindow', 'AXMinimized': minimized,
                    'AXMain': reports_key, 'AXFocused': reports_key}.get(key)
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, '_attr', side_effect=attr))
            stack.enter_context(patch.object(m, '_aborted_now', return_value=stopped))
            stack.enter_context(patch.object(m.Driver, '_frontmost',
                side_effect=frontmost if callable(frontmost) else None,
                return_value=frontmost if not callable(frontmost) else None))
            stack.enter_context(patch.object(m, 'AXUIElementIsAttributeSettable',
                                             return_value=(0, settable)))
            stack.enter_context(patch.object(m, 'AXUIElementSetAttributeValue',
                side_effect=lambda el, key, value: (wrote.append((key, value)), set_error)[1]))
            stack.enter_context(patch.object(self.d, '_prepare_background_window',
                return_value={'input_route':'window_prepare','mechanism':'pid_window_events',
                              'unverified':'observe_effect','reobserve_required':True}))
            result = self.d.act('o1', 'fixture',
                                {'name': 'prepare_window', 'element_index': 0})
        return result, wrote

    def test_a_prepared_window_is_asked_not_clicked(self):
        result, wrote = self.prepare()
        self.assertEqual('acted', result['type'], result)
        self.assertEqual('window_prepare', result['input_route'])
        self.assertEqual([('AXMain', True), ('AXFocused', True)], wrote)
        self.assertIs(True, result['window_reports_key'])
        # It said so itself, so there is nothing left to go and look at.
        self.assertNotIn('unverified', result)

    def test_a_window_that_does_not_report_key_asks_for_a_new_observation(self):
        result, _ = self.prepare(reports_key=False)
        self.assertEqual('acted', result['type'])
        self.assertIs(False, result['window_reports_key'])
        self.assertEqual('observe_effect', result['unverified'])
        self.assertIs(True, result['reobserve_required'])

    def test_background_preparation_delegates_without_foreground_ax_writes(self):
        result, wrote = self.prepare(frontmost=False)
        self.assertEqual('acted', result['type'])
        self.assertEqual('pid_window_events', result['mechanism'])
        self.assertIs(True, result['reobserve_required'])
        self.assertEqual([], wrote)

    def test_a_minimised_window_and_an_unwritable_one_are_both_refused(self):
        for options in ({'minimized': True}, {'settable': False}):
            with self.subTest(options=options):
                result, wrote = self.prepare(**options)
                self.assertEqual('not_started', result['effect'])
                self.assertEqual([], wrote)

    def test_a_stop_is_honoured_before_anything_is_written(self):
        result, wrote = self.prepare(stopped=True)
        self.assertEqual('not_started', result['effect'])
        self.assertEqual([], wrote)

    def test_failed_ax_write_does_not_certify_no_effect(self):
        result, wrote = self.prepare(set_error=-25200)
        self.assertEqual('unknown', result['effect'])
        self.assertEqual(1, len(wrote), 'do not continue after an uncertain write')

    def test_only_a_window_can_be_prepared(self):
        with patch.object(self.m, '_attr', return_value='AXButton'):
            result = self.d.act('o1', 'fixture',
                                {'name': 'prepare_window', 'element_index': 0})
        self.assertEqual('not_started', result['effect'])

    def test_extra_fields_are_refused_rather_than_ignored(self):
        result = self.d.act('o1', 'fixture',
                            {'name': 'prepare_window', 'element_index': 0, 'direction': 'down'})
        self.assertEqual('not_started', result['effect'])

    def test_background_dispatch_effect_and_stop_propagate_through_act(self):
        from nlc.native_window_dispatch import WindowDispatchFailure
        for attempted, stopped in ((0,False),(1,False),(1,True)):
            with self.subTest(attempted=attempted,stopped=stopped), ExitStack() as stack:
                stack.enter_context(patch.object(self.m,'_attr',return_value='AXWindow'))
                stack.enter_context(patch.object(self.m,'_aborted_now',return_value=False))
                stack.enter_context(patch.object(self.d,'_frontmost',return_value=False))
                stack.enter_context(patch.object(self.d,'_prepare_background_window',
                    side_effect=WindowDispatchFailure('interrupted',attempted=attempted,stopped=stopped)))
                result=self.d.act('o1','fixture',{'name':'prepare_window','element_index':0})
                self.assertEqual(result['effect'],'unknown' if attempted else 'not_started')
                self.assertEqual(result['error'],'aborted_by_user' if stopped else 'action_failed')
                self.assertEqual(result['posted_or_attempted_events'],attempted)

    def test_background_context_rejects_observation_process_and_map_drift(self):
        from nlc.native_window_dispatch import WindowDispatchFailure
        self.obs.target = 'fixture'
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.m,'_aborted_now',return_value=False))
            resolver = stack.enter_context(patch.object(self.d,'_resolve',return_value=42))
            start = stack.enter_context(patch('nlc.native_process_identity.process_start_identity',return_value=123.0))
            binding = SimpleNamespace(_backend=SimpleNamespace(equal=lambda a,b:a is b))
            stack.enter_context(patch('nlc.native_window_binding.capture_binding',return_value=binding))
            def dispatched(binding, **kwargs):
                context = kwargs['context']
                context()
                self.d._latest['fixture'] = object()
                with self.assertRaises(RuntimeError): context()
                self.d._latest['fixture'] = self.obs
                resolver.return_value = 99
                with self.assertRaises(RuntimeError): context()
                resolver.return_value = 42
                start.return_value = 124.0
                with self.assertRaises(RuntimeError): context()
                start.return_value = 123.0
                self.obs.elements = [object()]
                with self.assertRaises(RuntimeError): context()
                self.obs.elements = [self.el]
                context()
                raise WindowDispatchFailure('interrupted after post',attempted=1)
            stack.enter_context(patch('nlc.native_window_dispatch.dispatch_preparation',side_effect=dispatched))
            with self.assertRaises(WindowDispatchFailure) as error:
                self.d._prepare_background_window(self.obs,self.el)
            self.assertEqual(error.exception.attempted,1)


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class UnpressableElementTests(unittest.TestCase):
    """An element that says it cannot be pressed is not clicked at instead."""
    def setUp(self):
        from nlc import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.el = object()
        self.obs = SimpleNamespace(pid=42, obs_id='o1', elements=[self.el],
                                   process_launch_identity=123.0)
        self.d._latest = {'fixture': self.obs}
        self.d._last_action_at = 0

    def click(self, *, advertised=None, press_error=0):
        m = self.m
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, 'AXUIElementCopyActionNames',
                return_value=(0, advertised) if advertised is not None else (-1, None)))
            press = stack.enter_context(patch.object(m, 'AXUIElementPerformAction',
                                                     return_value=press_error))
            stack.enter_context(patch.object(m, '_attr', return_value=True))
            result = self.d.act('o1', 'fixture', {'name': 'click', 'element_index': 0})
        return result, press

    def test_an_advertised_refusal_to_press_is_taken_at_its_word(self):
        result, press = self.click(advertised=())
        self.assertEqual('not_started', result['effect'])
        self.assertIs(True, result.get('needs_pointer'))
        press.assert_not_called()

    def test_a_definite_unsupported_is_a_refusal_not_a_coordinate_click(self):
        result, press = self.click(press_error=-25206)
        self.assertEqual('not_started', result['effect'])
        self.assertIs(True, result.get('needs_pointer'))
        self.assertEqual(1, press.call_count)

    def test_an_ordinary_press_still_goes_through(self):
        result, press = self.click()
        self.assertEqual('acted', result['type'], result)
        self.assertEqual(1, press.call_count)


if __name__ == '__main__':
    unittest.main()
