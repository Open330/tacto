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
        tree = ast.parse((ROOT / 'tacto/driver.py').read_text())
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

    def test_python_window_dispatch_has_one_pid_boundary_and_no_global_input(self):
        source = (ROOT / 'tacto/native_window_dispatch.py').read_text()
        tree = ast.parse(source)
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        attributes = [n.func.attr for n in calls if isinstance(n.func, ast.Attribute)]
        self.assertEqual(attributes.count('CGEventPostToPid'), 1)
        for banned in ('CGEventPost','CGWarpMouseCursorPosition','CGDisplayMoveCursorToPoint',
                       'activateWithOptions_', 'activate'):
            self.assertNotIn(banned,attributes)

    def test_signed_input_has_no_global_post_activation_or_pointer_warp(self):
        """Window preparation has one PID post boundary; global input is absent."""
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        # Comments explain why these are absent, so only real code counts.
        code = '\n'.join(line for line in source.splitlines()
                         if not line.lstrip().startswith(('//', '///')))
        for banned in ('postToPid', 'CGEvent(', 'keyboardSetUnicodeString',
                       'CGEventSource(', 'kAXFrontmostAttribute as CFString'):
            self.assertNotIn(banned, code, f'{banned} is back in the signed helper')
        self.assertIsNone(re.search(r'\bNSApplication\.shared\.activate\b', code))
        for path in (ROOT / 'helper').glob('*.swift'):
            code = '\n'.join(line for line in path.read_text().splitlines()
                             if not line.lstrip().startswith(('//', '///')))
            if path.name == 'PointerHover.swift':
                # The one global route: hover moves the cursor and nothing
                # else — one warp, one posted move, no button, no activation.
                self.assertEqual(1, code.count('CGWarpMouseCursorPosition('))
                self.assertEqual(1, code.count('.post(tap:'))
                self.assertEqual(['mouseType: .mouseMoved'], re.findall(r'mouseType: \.\w+', code))
                for banned in ('CGEventPost(', 'CGDisplayMoveCursorToPoint(', 'keyboardEvent', 'MouseDown',
                               'MouseUp', 'MouseDragged', 'scrollWheel', 'activate(', 'kAXRaiseAction',
                               'postToPid'):
                    self.assertNotIn(banned, code, f'{banned} in hover')
                continue
            for banned in ('CGEventPost(', 'CGWarpMouseCursorPosition(',
                           'CGDisplayMoveCursorToPoint(', '.post(tap:',
                           'NSApplication.shared.activate('):
                self.assertNotIn(banned, code, f'global input/activation in {path.name}')
            # One LaunchServices open, for open_folder, that may not activate
            # (2026-10-08): Finder shows the folder, the person keeps focus.
            opens = code.count('NSWorkspace.shared.open(')
            if path.name == 'TactoHelper.swift':
                self.assertEqual(opens, 1, 'only open_folder may open through LaunchServices')
                case = code[code.index('case "open_folder":'):code.index('case "select_option":')]
                self.assertIn('NSWorkspace.shared.open(', case)
                self.assertIn('configuration.activates = false', case)
                self.assertNotIn('activates = true', code)
            else:
                self.assertEqual(opens, 0, f'LaunchServices open in {path.name}')
            if path.name != 'WindowDispatch.swift':
                self.assertNotIn('.postToPid(', code, f'ungated PID posting in {path.name}')
        dispatch = (ROOT / 'helper/WindowDispatch.swift').read_text()
        self.assertEqual(dispatch.count('event.postToPid(pid)'), 1)


@unittest.skipUnless(NATIVE_AVAILABLE, 'PyObjC unavailable; source routing regression still runs')
class NativeInputDispatchTests(unittest.TestCase):
    def setUp(self):
        from tacto import driver
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

    def type_text(self, text, *, settable=True, set_error=0, lands=True,
                  focusable=True, already_focused=False):
        """`lands` is what a web field does when the write did not take.

        Measured on Safari 2026-09-20: a web text field reports `AXValue` and
        `AXSelectedText` as settable, returns success from the write, and keeps
        its old value — until `AXFocused` is set on it. Success that changed
        nothing is the same trap as an AXPress returning 0 and doing nothing.
        """
        m = self.module
        wrote = []
        value = ["before"]
        def attr(el, key):
            return {"AXRole": "AXTextField", "AXValue": value[0],
                    "AXFocused": already_focused}.get(key)
        def write(el, key, new):
            wrote.append((key, new))
            if key == "AXFocused":
                return 0
            if lands and set_error == 0:
                value[0] = text if key == "AXSelectedText" else new
            return set_error
        def is_settable(el, key, _out):
            return (0, focusable if key == "AXFocused" else settable)
        with ExitStack() as stack:
            stack.enter_context(patch.object(m, '_attr', side_effect=attr))
            stack.enter_context(patch.object(m, '_aborted_now', return_value=False))
            stack.enter_context(patch.object(m, 'AXUIElementIsAttributeSettable',
                                             side_effect=is_settable))
            stack.enter_context(patch.object(m, 'AXUIElementSetAttributeValue',
                                             side_effect=write))
            result = self.driver.act('test', 'test-app',
                                     {'name': 'type_text', 'element_index': 0, 'text': text})
        return result, wrote

    def test_text_is_written_to_the_named_element_in_one_addressed_call(self):
        """No keystrokes, and no half-typed state to report.

        The path this replaced posted one keystroke per character and had to
        re-check the focused element between every pair, because a keystroke
        carries no address. `AXSelectedText` names the element.
        """
        result, wrote = self.type_text('hello')
        self.assertEqual('acted', result['type'], result)
        self.assertIn(('AXSelectedText', 'hello'), wrote)

    def test_the_element_is_focused_first_because_a_web_field_needs_it(self):
        result, wrote = self.type_text('hello')
        self.assertEqual('acted', result['type'], result)
        self.assertEqual(('AXFocused', True), wrote[0], 'focus comes before the write')

    def test_an_already_focused_element_is_not_focused_again(self):
        _, wrote = self.type_text('hello', already_focused=True)
        self.assertNotIn('AXFocused', [key for key, _ in wrote])

    def test_a_field_that_does_not_offer_focus_is_written_anyway(self):
        """Most native fields have no settable AXFocused, and never needed one."""
        result, wrote = self.type_text('hello', focusable=False)
        self.assertEqual('acted', result['type'], result)
        self.assertEqual([('AXSelectedText', 'hello')], wrote)

    def test_a_write_that_reports_success_and_changes_nothing_is_refused(self):
        result, wrote = self.type_text('hello', lands=False)
        self.assertEqual('not_started', result['effect'], result)
        self.assertIn(('AXSelectedText', 'hello'), wrote, 'it was attempted')

    def test_astral_characters_survive_because_nothing_counts_code_units(self):
        result, wrote = self.type_text('😀 한글')
        self.assertEqual('acted', result['type'], result)
        self.assertIn(('AXSelectedText', '😀 한글'), wrote)

    def test_an_element_that_cannot_take_text_is_refused_with_nothing_written(self):
        result, wrote = self.type_text('hello', settable=False)
        self.assertEqual('not_started', result['effect'])
        self.assertEqual([], [w for w in wrote if w[0] == 'AXSelectedText'])

    def test_a_timed_out_write_is_unknown_and_not_written_again(self):
        m = self.module
        result, wrote = self.type_text('hello', set_error=m.AX_CANNOT_COMPLETE)
        self.assertEqual('unknown', result['effect'])
        self.assertEqual(1, len([w for w in wrote if w[0] == 'AXSelectedText']),
                         'writing again is how one sentence becomes two')

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
