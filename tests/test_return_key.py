"""Return is allowed narrowly: the Go to Folder sheet's path field, a step that
declares it submits, an exact postcondition, and never when the outcome was
already true. Approved by the user 2026-10-08; scope per Codex's criteria."""
from dataclasses import replace
import json
import pathlib
import subprocess
import tempfile
import unittest

from tacto.contract import Envelope, ContextBinding
from tacto.flow import Flow, Step, Selector, Postcondition, replay, pin_params
from tacto.protocol import RETURN_CAPABILITY, window_key_reobservation

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOOD = {'type': 'acted', 'input_route': 'window_submit_key', 'posted_events': 1,
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
                    Finder(response={**GOOD, 'posted_events': 2}),
                    Finder(response={**GOOD, 'posted_events': 0}),
                    Finder(response={**GOOD, 'input_route': 'window_key'}),
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

    def test_malformed_submit_receipts_are_not_reobserved(self):
        ret = {'name': 'press_key', 'key': 'Return', 'element_index': 2}
        for bad in ({**GOOD, 'posted_events': True}, {**GOOD, 'posted_events': 1.0},
                    {**GOOD, 'posted_events': '1'}, {**GOOD, 'reobserve_required': 1},
                    {**GOOD, 'unverified': None}, {**GOOD, 'type': 'error'},
                    {k: v for k, v in GOOD.items() if k != 'input_route'}):
            with self.subTest(bad=bad):
                self.assertFalse(window_key_reobservation(ret, bad, Finder.capabilities))
        for action in ({'name': 'press_key', 'key': 'Return'},
                       {'name': 'press_key', 'key': 'Return', 'element_index': True},
                       {'name': 'press_key', 'key': 'Return', 'element_index': -1},
                       {'name': 'click', 'key': 'Return', 'element_index': 2},
                       {'name': 'press_key', 'key': 'Escape', 'element_index': 2},
                       {'name': 'press_key', 'key': 'cmd+Return', 'element_index': 2}):
            with self.subTest(action=action):
                self.assertFalse(window_key_reobservation(action, GOOD, Finder.capabilities))

    def test_routes_do_not_cross(self):
        nav = {'name': 'press_key', 'key': 'Tab', 'element_index': 2}
        ret = {'name': 'press_key', 'key': 'Return', 'element_index': 2}
        pair = {**GOOD, 'input_route': 'window_key', 'posted_events': 2}
        self.assertTrue(window_key_reobservation(nav, pair, Finder.capabilities))
        self.assertFalse(window_key_reobservation(nav, GOOD, Finder.capabilities))
        self.assertFalse(window_key_reobservation(ret, pair, Finder.capabilities))

    def test_return_is_not_navigation(self):
        from tacto.protocol import NAVIGATION_KEYS
        self.assertNotIn('Return', NAVIGATION_KEYS)


class HelperReturnBoundaryTests(unittest.TestCase):
    """Source checks: the helper's own scoping of Return."""
    swift = (ROOT / 'helper/TactoHelper.swift').read_text()
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

    def test_checked_on_arrival_then_sent_only_by_the_sheet_path(self):
        case = self.swift[self.swift.index('    case "press_key":'):]
        case = case[:case.index('    case "scroll":')]
        self.assertIn('if submit, let why = returnTargetRefusal(el, read: boundedAXRead(until: deadline))', case)
        self.assertIn('return submitReturnBackground(obsID, target, el, p, deadline: deadline)', case)
        sheet = self.dispatch[self.dispatch.index('func submitReturnInSheet('):]
        # the last check before the keyDown: readiness, focus, target, geometry
        for needed in ('nativeSheetSubmitReady(tops: now, sheet: sheet, read: read)',
                       'CFEqual(focusedWindow, sheet), CFEqual(focused, element)',
                       'if let why = refusal(element, read)',
                       'try dispatchSheetReturn(pair.down,'):
            self.assertIn(needed, sheet)
        self.assertNotIn('pair.up', sheet)
        # Codex review of 479acd0: against the frames fixed at the start, and
        # the field still under the same sheet under the same parent. Source
        # check only — this glue reads real AX and is not run by a fake.
        for needed in ('sheetNow == sheetFrame, fieldNow == fieldFrame',
                       '== parentFrame', 'CFEqual(owner, sheet)',
                       'kAXParentAttribute, scope: scope)), parentWindow)'):
            self.assertIn(needed, sheet)

    def test_window_input_and_navigation_never_carry_return(self):
        nav = self.body(self.window_input, 'func nativeNavigationKey(')
        self.assertNotIn('Return', nav)
        self.assertIn('case .key(let key): return nativeNavigationKey(key) != nil', self.window_input)
        self.assertIn('case .key(let value): pair = try buildNativeWindowKey(binding.windowID, key: value)\n',
                      self.dispatch)
        # Return is built in the sheet path, and in the shared path only in
        # the bound-submit branch (host-vouched, keyDown only) — never in the
        # navigation or window_input branches.
        self.assertEqual(2, self.dispatch.count('submit: true'))
        shared = self.dispatch[self.dispatch.index('func dispatchNativeWindowPreparation('):]
        shared = shared[:shared.index('\n}\n')]
        self.assertEqual(1, shared.count('submit: true'))
        branch = shared[shared.index('} else if let key, submitKey {'):]
        branch = branch[:branch.index('} else if let key {')]
        self.assertIn('submit: true', branch)
        self.assertIn('events = [pair.down]\n', branch)
        self.assertIn('guard key == "Return"', shared)


class HelperReturnJudgementTests(unittest.TestCase):
    """The helper's real returnTargetRefusal, run against a fake AX read."""

    def test_cases(self):
        swift = (ROOT / 'helper/TactoHelper.swift').read_text()
        code = ('typealias AXRead = (AXUIElement, String) throws -> (AXError, CFTypeRef?)\n'
                + swift[swift.index('// AXRead: see WindowInput.swift.'):swift.index('// Keys that only move the focus ring.')])
        harness = r'''
import Foundation
import ApplicationServices
enum NativeWindowInputError: Error { case unavailable(String) }
struct Timeout: Error {}
''' + code + r'''
// Fake elements: application elements with distinct pids, attributes by pid.
func el(_ n: Int32) -> AXUIElement { AXUIElementCreateApplication(n) }
func pidOf(_ e: AXUIElement) -> Int32 { var p: pid_t = 0; AXUIElementGetPid(e, &p); return p }
typealias Attrs = [String: Any]   // value, or AXError, or "throw"
func run(_ tree: [Int32: Attrs]) -> String {
    let read: AXRead = { e, name in
        guard let attrs = tree[pidOf(e)], let v = attrs[name] else { return (.attributeUnsupported, nil) }
        if let s = v as? String, s == "throw" { throw Timeout() }
        if let err = v as? AXError { return (err, nil) }
        if let n = v as? Int32 { return (.success, el(n)) }
        return (.success, v as CFTypeRef)
    }
    return returnTargetRefusal(el(1), read: read) ?? "ALLOWED"
}
let field: Attrs = ["AXRole": "AXTextField", "AXSubrole": AXError.noValue,
                    "AXIdentifier": "PathTextField", "AXParent": Int32(2)]
let sheet: Attrs = ["AXRole": "AXSheet", "AXIdentifier": "GoToWindow", "AXParent": Int32(3)]
let window: Attrs = ["AXRole": "AXWindow", "AXParent": Int32(4)]
func with(_ a: Attrs, _ k: String, _ v: Any) -> Attrs { var a = a; a[k] = v; return a }
let cases: [(String, [Int32: Attrs])] = [
    ("path field", [1: field, 2: sheet, 3: window]),
    ("secure", [1: with(field, "AXSubrole", "AXSecureTextField"), 2: sheet, 3: window]),
    ("subrole read fails", [1: with(field, "AXSubrole", AXError.cannotComplete), 2: sheet, 3: window]),
    ("save name field", [1: with(field, "AXIdentifier", "saveAsNameTextField"), 2: with(sheet, "AXIdentifier", "SavePanel"), 3: window]),
    ("other sheet", [1: field, 2: with(sheet, "AXIdentifier", "LoginSheet"), 3: window]),
    ("no sheet", [1: with(field, "AXParent", Int32(3)), 3: window]),
    ("default button", [1: with(field, "AXRole", "AXButton"), 2: sheet, 3: window]),
    ("role read fails", [1: with(field, "AXRole", AXError.cannotComplete), 2: sheet, 3: window]),
    ("role read times out", [1: with(field, "AXRole", "throw"), 2: sheet, 3: window]),
    ("parent read fails", [1: with(field, "AXParent", AXError.cannotComplete), 2: sheet, 3: window]),
    ("ancestor role fails", [1: field, 2: with(sheet, "AXRole", AXError.cannotComplete), 3: window]),
    ("sheet id fails", [1: field, 2: with(sheet, "AXIdentifier", AXError.cannotComplete), 3: window]),
    ("identifier read fails", [1: with(field, "AXIdentifier", AXError.cannotComplete), 2: sheet, 3: window]),
]
var out: [String: String] = [:]
for (name, tree) in cases { out[name] = run(tree) }
// The arrival read with its overall deadline already spent reads nothing.
out["deadline spent"] = returnTargetRefusal(el(1), read: boundedAXRead(until: 0)) ?? "ALLOWED"
print(String(data: try! JSONSerialization.data(withJSONObject: out), encoding: .utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-return-judge-') as directory:
            script = pathlib.Path(directory) / 'main.swift'
            script.write_text(harness)
            result = subprocess.run(['swift', str(script)], capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = json.loads(result.stdout)
        self.assertEqual({
            'path field': 'ALLOWED',
            'secure': 'secure text field',
            'subrole read fails': 'subrole unreadable',
            'save name field': 'not the Go to Folder path field',
            'other sheet': 'not in the Go to Folder sheet',
            'no sheet': 'not in the Go to Folder sheet',
            'default button': 'not a text field (AXButton)',
            'role read fails': 'role unreadable',
            'role read times out': 'role unreadable',
            'parent read fails': 'ancestry unreadable',
            'ancestor role fails': 'ancestor role unreadable',
            'sheet id fails': 'sheet identifier unreadable',
            'identifier read fails': 'identifier unreadable',
            'deadline spent': 'role unreadable',
        }, got)


if __name__ == '__main__':
    unittest.main()
