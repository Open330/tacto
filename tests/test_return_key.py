"""Return is allowed narrowly: the Go to Folder sheet's path field, a step that
declares it submits, an exact postcondition, and never when the outcome was
already true. Approved by the user 2026-10-08; scope per Codex's criteria."""
from dataclasses import replace
import pathlib
import unittest

from nlc.contract import Envelope, ContextBinding
from nlc.flow import Flow, Step, Selector, Postcondition, replay, pin_params
from nlc.protocol import RETURN_CAPABILITY, window_key_reobservation

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOOD = {'type': 'acted', 'input_route': 'window_key', 'posted_events': 2,
        'unverified': 'observe_effect', 'reobserve_required': True}


class Finder:
    capabilities = {'action.press_key', 'press_key.element_bound', 'press_key.no_escape',
                    RETURN_CAPABILITY}

    def __init__(self, *, navigates=True, title='Documents', response=None, caps=None):
        self.title = title; self.navigates = navigates; self.calls = []
        self.response = response
        if caps is not None:
            self.capabilities = caps

    def observe(self, *a, **k):
        return {'type': 'observation', 'obs_id': 'obs', 'tree':
                f'0 Window  Title: {self.title}, Identifier: FinderWindow\n'
                '  1 Sheet  Identifier: GoToWindow\n'
                '    2 TextField  Identifier: PathTextField, Value: /tmp/run-folder'}

    def act(self, obs, target, action, **kwargs):
        self.calls.append(action)
        if self.navigates:
            self.title = 'run-folder'
        return self.response or dict(GOOD)

    def wait_until(self, target, predicate, **kwargs):
        result = self.observe()
        return result if predicate(result['tree']) else {'type': 'timeout', 'last_obs': result}


def flow(**changes):
    env = Envelope.minimal()
    field = Selector(role='TextField', identifier='PathTextField')
    window = Selector(role='Window', identifier='FinderWindow')
    step = Step('press_key', field, params={'key': 'Return'}, is_submit=True,
                safety=env.contract_for({'write_ephemeral'}, 'go to folder'),
                binding=ContextBinding(bundle_id='com.apple.finder', role='TextField',
                                       identifier='PathTextField', operation='press_key',
                                       params_digest=pin_params({'key': 'Return'})),
                postcondition=Postcondition(field=window, attribute='Title', equals='run-folder'))
    return Flow('go', 'com.apple.finder', [replace(step, **changes)], schema=2)


class ReturnReplayTests(unittest.TestCase):
    def test_go_to_folder_return_is_sent_once_and_verified_by_the_window(self):
        app = Finder()
        result = replay(app, flow())
        self.assertTrue(result.ok, result.log)
        self.assertEqual([{'name': 'press_key', 'key': 'Return'}],
                         [{k: a[k] for k in ('name', 'key')} for a in app.calls])

    def test_refused_before_input(self):
        cases = {
            'no capability': (Finder(caps=Finder.capabilities - {RETURN_CAPABILITY}), flow()),
            'not a submit step': (Finder(), flow(is_submit=False)),
            'contains only': (Finder(), flow(postcondition=Postcondition(
                field=Selector(role='Window', identifier='FinderWindow'),
                attribute='Title', contains='run'))),
            'no postcondition': (Finder(), flow(postcondition=None)),
            'already true': (Finder(title='run-folder'), flow()),
            'shortcut, not Return': (Finder(), flow(params={'key': 'cmd+Return'})),
            'Escape': (Finder(), flow(params={'key': 'Escape'})),
        }
        for name, (app, f) in cases.items():
            with self.subTest(name):
                result = replay(app, f)
                self.assertFalse(result.ok, result.log)
                self.assertEqual([], app.calls)

    def test_no_receiving_effect_or_partial_pair_never_passes_or_repeats(self):
        for app in (Finder(navigates=False),
                    Finder(response={**GOOD, 'posted_events': 1}),
                    Finder(response={**GOOD, 'input_route': 'window_prepare'})):
            with self.subTest(response=app.response, navigates=app.navigates):
                result = replay(app, flow())
                self.assertFalse(result.ok, result.log)
                self.assertEqual(1, len(app.calls))

    def test_a_sheet_that_closes_onto_another_folder_fails(self):
        app = Finder()
        app.act = lambda obs, target, action, **k: (app.calls.append(action),
                                                    setattr(app, 'title', 'Downloads'), dict(GOOD))[-1]
        self.assertFalse(replay(app, flow()).ok)
        self.assertEqual(1, len(app.calls))


class ReturnProtocolTests(unittest.TestCase):
    def test_return_is_reobserved_only_with_the_capability(self):
        action = {'name': 'press_key', 'key': 'Return', 'element_index': 2}
        self.assertTrue(window_key_reobservation(action, GOOD, Finder.capabilities))
        self.assertFalse(window_key_reobservation(
            action, GOOD, Finder.capabilities - {RETURN_CAPABILITY}))

    def test_return_is_not_navigation(self):
        from nlc.protocol import NAVIGATION_KEYS
        self.assertNotIn('Return', NAVIGATION_KEYS)


class HelperReturnBoundaryTests(unittest.TestCase):
    """Source checks: the helper's own scoping of Return."""
    swift = (ROOT / 'helper/NLCHelper.swift').read_text()
    window_input = (ROOT / 'helper/WindowInput.swift').read_text()
    dispatch = (ROOT / 'helper/WindowDispatch.swift').read_text()

    def body(self, text, start):
        body = text[text.index(start):]
        return body[:body.index('\n}\n')]

    def test_only_the_go_to_folder_path_field(self):
        body = self.body(self.swift, 'func returnTargetRefusal(')
        for needed in ('kAXTextFieldRole', 'kAXSecureTextFieldSubrole',
                       '"PathTextField"', '"GoToWindow"', '"AXSheet"'):
            self.assertIn(needed, body)

    def test_an_unreadable_value_is_never_taken_as_a_safe_one(self):
        # Codex review of 0eb24cf: a failed subrole read was "not secure".
        body = self.body(self.swift, 'func returnTargetRefusal(')
        for reason in ('"role unreadable"', '"subrole unreadable"', '"identifier unreadable"',
                       '"ancestry unreadable"', '"ancestor role unreadable"',
                       '"sheet identifier unreadable"'):
            self.assertIn(reason, body)
        # Only "no value"/"unsupported" may stand for absent, and only where
        # absence is legitimate (subrole, identifier) — never for a role.
        self.assertIn('err == .noValue || err == .attributeUnsupported', body)
        self.assertIn('read(element, kAXRoleAttribute as String)\n', body)
        self.assertIn('read(element, kAXSubroleAttribute as String, optional: true)', body)
        self.assertNotIn('str(', body)

    def test_checked_before_and_again_at_the_last_moment(self):
        case = self.swift[self.swift.index('    case "press_key":'):]
        case = case[:case.index('    case "scroll":')]
        self.assertIn('if submit, let why = returnTargetRefusal(el)', case)
        self.assertIn('finalCheck: submit ? { returnTargetRefusal(el) } : nil', case)
        self.assertIn('if let finalCheck, let why = finalCheck()', self.swift)

    def test_window_input_and_navigation_never_carry_return(self):
        nav = self.body(self.window_input, 'func nativeNavigationKey(')
        self.assertNotIn('Return', nav)
        self.assertIn('case .key(let key): return nativeNavigationKey(key) != nil', self.window_input)
        self.assertIn('case .key(let value): pair = try buildNativeWindowKey(binding.windowID, key: value)\n',
                      self.dispatch)
        self.assertEqual(1, self.dispatch.count('submit: true'))


if __name__ == '__main__':
    unittest.main()
