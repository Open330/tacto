"""No real input: the native runtime does not synthesise events at all.

These checks used to mock CGEventPost and assert it was never called. The
calls are gone now — every action addresses the element it names through the
accessibility API — so the same intents are checked where they are now true:
in the source, structurally, for both the Python driver and the signed helper.
"""
import ast
from contextlib import ExitStack
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
NATIVE_AVAILABLE = importlib.util.find_spec('ApplicationServices') is not None

# Anything that manufactures input or takes the front. Naming the builders as
# well as the senders matters: a path that can still BUILD an event is a path
# the next caller reaches for.
FORBIDDEN_CALLS = {
    'CGEventPost', 'CGEventPostToPid', 'CGEventCreateMouseEvent',
    'CGEventCreateKeyboardEvent', 'CGEventCreateScrollWheelEvent',
    'CGEventKeyboardSetUnicodeString', 'CGWarpMouseCursorPosition',
    'CGDisplayMoveCursorToPoint', 'activateWithOptions_', 'activate',
}


class InputRoutingSourceTests(unittest.TestCase):
    def test_python_builds_or_sends_no_input_and_never_activates(self):
        tree = ast.parse((ROOT / 'nlc/driver.py').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        offenders = [f'{n.func.attr}:{n.lineno}' for n in calls
                     if isinstance(n.func, ast.Attribute) and n.func.attr in FORBIDDEN_CALLS]
        offenders += [f'{n.func.id}:{n.lineno}' for n in calls
                      if isinstance(n.func, ast.Name) and n.func.id in FORBIDDEN_CALLS]
        front_writes = [n.lineno for n in calls
                        if isinstance(n.func, ast.Name)
                        and n.func.id == 'AXUIElementSetAttributeValue'
                        and len(n.args) > 1 and isinstance(n.args[1], ast.Constant)
                        and n.args[1].value == 'AXFrontmost']
        self.assertEqual([], offenders, 'native runtime must not build, send, or activate')
        self.assertEqual([], front_writes, 'native runtime must not write AXFrontmost')

    def test_the_signed_helper_synthesises_nothing_either(self):
        """The transport that ships has to hold the same line.

        Measured 2026-09-20: Codex Computer Use ships zero input synthesis
        across all 56 Mach-O binaries in its bundle, and drives macOS through
        AXUIElementPerformAction / AXUIElementSetAttributeValue.
        """
        source = (ROOT / 'helper/NLCHelper.swift').read_text()
        # Comments explain why these are absent, so only real code counts.
        code = '\n'.join(line for line in source.splitlines()
                         if not line.lstrip().startswith(('//', '///')))
        for banned in ('postToPid', 'CGEvent(', 'keyboardSetUnicodeString',
                       'CGEventSource(', 'kAXFrontmostAttribute as CFString'):
            self.assertNotIn(banned, code, f'{banned} is back in the signed helper')
        self.assertIsNone(re.search(r'\bNSApplication\.shared\.activate\b', code))


@unittest.skipUnless(NATIVE_AVAILABLE, 'PyObjC unavailable; source routing regression still runs')
class NativeInputDispatchTests(unittest.TestCase):
    def setUp(self):
        from nlc import driver
        self.module = driver
        self.driver = object.__new__(driver.Driver)
        self.obs = SimpleNamespace(pid=4321, elements=[object()], obs_id='test')
        self.driver._latest = {'test-app': self.obs}
        self.driver._last_action_at = 0
        self.driver._select = None

    def test_there_is_no_event_posting_method_left_to_reach_for(self):
        for gone in ('_post', '_press_key', '_window_click', '_scroll_at',
                     '_activate', '_mouse_click'):
            self.assertFalse(hasattr(self.driver, gone), f'{gone} is back')

    def test_an_unpressable_element_is_refused_rather_than_clicked_by_coordinate(self):
        m = self.module
        for advertised, press_error in (((), None), (('AXPress',), -25206)):
            with self.subTest(advertised=advertised):
                with ExitStack() as stack:
                    stack.enter_context(patch.object(m, 'AXUIElementCopyActionNames',
                                                     return_value=(0, advertised)))
                    if press_error is not None:
                        stack.enter_context(patch.object(m, 'AXUIElementPerformAction',
                                                         return_value=press_error))
                    result = self.driver.act('test', 'test-app',
                                             {'name': 'click', 'element_index': 0})
                self.assertEqual('error', result['type'])
                self.assertEqual('not_started', result['effect'])
                self.assertIs(True, result.get('needs_pointer'))

    # ---------------------------------------------------------------- typing

    def type_text(self, text, *, settable=True, set_error=0):
        m = self.module
        wrote = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, '_attr', return_value='AXTextField'))
            stack.enter_context(patch.object(m, '_aborted_now', return_value=False))
            stack.enter_context(patch.object(m, 'AXUIElementIsAttributeSettable',
                                             return_value=(0, settable)))
            stack.enter_context(patch.object(m, 'AXUIElementSetAttributeValue',
                side_effect=lambda el, key, value: (wrote.append((key, value)), set_error)[1]))
            result = self.driver.act('test', 'test-app',
                                     {'name': 'type_text', 'element_index': 0, 'text': text})
        return result, wrote

    def test_text_is_written_to_the_named_element_in_one_addressed_call(self):
        """No focus to require, and no half-typed state to report.

        The path this replaced posted one keystroke per character and had to
        re-check the focused element between every pair, because a keystroke
        carries no address. `AXSelectedText` names the element.
        """
        result, wrote = self.type_text('hello')
        self.assertEqual('acted', result['type'], result)
        self.assertEqual([('AXSelectedText', 'hello')], wrote)

    def test_astral_characters_survive_because_nothing_counts_code_units(self):
        result, wrote = self.type_text('😀 한글')
        self.assertEqual('acted', result['type'], result)
        self.assertEqual([('AXSelectedText', '😀 한글')], wrote)

    def test_an_element_that_cannot_take_text_is_refused_with_nothing_written(self):
        result, wrote = self.type_text('hello', settable=False)
        self.assertEqual('not_started', result['effect'])
        self.assertEqual([], wrote)

    def test_a_timed_out_write_is_unknown_and_not_written_again(self):
        m = self.module
        result, wrote = self.type_text('hello', set_error=m.AX_CANNOT_COMPLETE)
        self.assertEqual('unknown', result['effect'])
        self.assertEqual(1, len(wrote), 'writing again is how one sentence becomes two')

    def test_a_failed_write_says_nothing_was_written(self):
        result, _ = self.type_text('hello', set_error=-25200)
        self.assertEqual('not_started', result['effect'])

    # ------------------------------------------------------- AX error shapes

    def test_popup_open_timeout_is_not_retried_with_another_action(self):
        m = self.module
        def attr(el, key):
            return 'AXPopUpButton' if key == 'AXRole' else True if key == 'AXEnabled' else None
        with patch.object(m, '_attr', side_effect=attr), \
             patch.object(self.driver, '_frontmost', return_value=False), \
             patch.object(self.driver, '_app_menus', return_value=[]), \
             patch.object(m.time, 'sleep'), \
             patch.object(m, 'AXUIElementPerformAction', return_value=m.AX_CANNOT_COMPLETE) as press:
            result = self.driver.act('test', 'test-app', {'name': 'open_select', 'element_index': 0})
        self.assertEqual(1, press.call_count, 'a timed-out open may already have happened')
        self.assertEqual('unknown', result['effect'])

    def test_ax_timeout_is_unknown_and_never_reclicked(self):
        m = self.module
        with patch.object(m, 'AXUIElementPerformAction', return_value=m.AX_CANNOT_COMPLETE) as press:
            result = self.driver.act('test', 'test-app', {'name': 'click', 'element_index': 0})
        self.assertEqual('acted', result['type'])
        self.assertTrue(result.get('unverified'))
        self.assertEqual(1, press.call_count)

    def test_other_ax_errors_do_not_claim_no_effect_or_retry(self):
        m = self.module
        for error in (-25200, -25201, -25202, -25208, -25211, -99999):
            with self.subTest(error=error), \
                 patch.object(m, 'AXUIElementPerformAction', return_value=error) as press:
                result = self.driver.act('test', 'test-app', {'name': 'click', 'element_index': 0})
            self.assertEqual('error', result['type'])
            self.assertEqual('unknown', result['effect'])
            self.assertFalse(result.get('needs_pointer', False))
            self.assertEqual(1, press.call_count)

    def test_secondary_action_unsupported_and_uncertain_effects(self):
        for error in (-25206, -25204, -25200, -25202, -25208):
            with self.subTest(error=error), \
                 patch.object(self.module, 'AXUIElementPerformAction', return_value=error) as perform:
                result = self.driver.act('test', 'test-app',
                    {'name': 'secondary_action', 'action': 'AXShowMenu', 'element_index': 0})
            self.assertEqual('not_started' if error == -25206 else 'unknown', result['effect'])
            self.assertEqual(1, perform.call_count)
