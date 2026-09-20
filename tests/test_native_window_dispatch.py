"""Window preparation asks the window; it does not click its title bar.

What this file used to exercise was a synthetic mouse down/up pair aimed at a
window's title bar and addressed to the process — a gesture mimed at an app in
the hope it read it the way a person's click would. Before that it was
preceded by a synthetic ApplicationActivated event, which made a background app
believe it was active and left that belief behind when the run ended.

`AXMain` and `AXFocused` are the same request written as a question the window
can answer, so the intents below are kept and re-pointed at that: nothing is
sent to a background app, nothing is sent once the observation has moved, and
an element that says it cannot be pressed is refused rather than clicked at.
"""
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

    def test_a_background_app_is_refused_with_nothing_written(self):
        result, wrote = self.prepare(frontmost=False)
        self.assertEqual('error', result['type'])
        self.assertEqual('not_started', result['effect'])
        self.assertIs(True, result.get('needs_foreground'))
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

    def test_a_write_that_fails_everywhere_is_not_reported_as_prepared(self):
        result, wrote = self.prepare(set_error=-25200)
        self.assertEqual('not_started', result['effect'])
        self.assertEqual(2, len(wrote), 'both attributes are tried before giving up')

    def test_only_a_window_can_be_prepared(self):
        with patch.object(self.m, '_attr', return_value='AXButton'):
            result = self.d.act('o1', 'fixture',
                                {'name': 'prepare_window', 'element_index': 0})
        self.assertEqual('not_started', result['effect'])

    def test_extra_fields_are_refused_rather_than_ignored(self):
        result = self.d.act('o1', 'fixture',
                            {'name': 'prepare_window', 'element_index': 0, 'direction': 'down'})
        self.assertEqual('not_started', result['effect'])


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
