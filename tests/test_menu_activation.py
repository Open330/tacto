"""A menu command that makes the app come forward is said, not passed over.

Measured 2026-10-08: Finder's Go > Go to Folder… brings Finder to the front.
The helper reports `activated_target`; replay narrates it and keeps it on the
step's event."""
import pathlib
import unittest
from unittest.mock import patch

from nlc.contract import Envelope
from nlc.flow import Flow, Step, Selector, Capture, Postcondition, Resolution, replay

ROOT = pathlib.Path(__file__).resolve().parents[1]


class App:
    capabilities = {'action.menu'}

    def __init__(self, activated):
        self.activated = activated; self.opened = False

    def observe(self, *a, **k):
        tree = '0 Window  Title: Docs\n' + ('  1 Sheet  Identifier: GoToWindow\n' if self.opened else '')
        return {'type': 'observation', 'obs_id': 'o', 'tree': tree}

    def act(self, obs, target, action, **k):
        self.sent = getattr(self, 'sent', []) + [action['name']]
        if action['name'] != 'menu':
            return {'type': 'acted'}
        self.opened = True
        out = {'type': 'acted', 'action': 'menu', 'note': 'not verified — observe again'}
        if self.activated:
            out['activated_target'] = True
        return out

    def wait_until(self, target, predicate, **k):
        r = self.observe()
        return r if predicate(r['tree']) else {'type': 'timeout', 'last_obs': r}


def flow():
    env = Envelope.minimal()
    return Flow('go', 'com.apple.finder', [Step('menu', None, params={'path': ['Go', 'Go to Folder…']},
        safety=env.contract_for({'write_ephemeral'}, 'open go to folder'),
        postcondition=Postcondition(contains='GoToWindow'))], schema=2)


class MenuActivationTests(unittest.TestCase):
    def test_self_activation_is_narrated_and_recorded(self):
        said = []
        result = replay(App(True), flow(), narrate=said.append)
        self.assertTrue(result.ok, result.log)
        self.assertTrue(any('맨 앞에 있는 것이 관찰' in s for s in said), said)
        self.assertTrue(result.events[0].get('activated_target'))

    def test_quiet_when_the_app_stays_behind(self):
        said = []
        result = replay(App(False), flow(), narrate=said.append)
        self.assertTrue(result.ok, result.log)
        self.assertFalse(any('맨 앞에 있는 것이 관찰' in s for s in said))
        self.assertNotIn('activated_target', result.events[0])

    def two_steps(self, second):
        f = flow()
        return Flow('go', 'com.apple.finder', [f.steps[0], second], schema=2)

    def test_no_input_after_the_target_is_seen_in_front(self):
        env = Envelope.minimal()
        field = Selector(role='TextField', identifier='PathTextField')
        typing = Step('set_value', field, params={'value': '/tmp/x'},
                      safety=env.contract_for({'write_ephemeral'}, 'type path'),
                      postcondition=Postcondition(field=field, attribute='Value', equals='/tmp/x'))
        app = App(True)
        result = replay(app, self.two_steps(typing))
        self.assertFalse(result.ok)
        self.assertEqual(['menu'], app.sent)
        self.assertEqual('target_seen_in_front', result.log[-1].get('error'))
        self.assertEqual('not_started', result.log[-1].get('effect'))
        # The earlier step's event is kept as it was: not undone, not retried,
        # and nothing is sent to bring focus back (Codex review of the cohort).
        self.assertEqual(1, len(result.events))
        self.assertTrue(result.events[0].get('activated_target'))
        self.assertEqual('menu', result.log[0].get('action'))
        self.assertEqual('ok', result.log[0].get('status'))

    def test_reads_still_run_after_it(self):
        env = Envelope.minimal()
        read = Step('read', Selector(role='Window', title='Docs'), safety=env.contract_for({'read'}, 'read'),
                    unverified_ok=True, captures=[Capture(name='t', field=Selector(role='Window', title='Docs'), attribute='Title')])
        app = App(True)
        result = replay(app, self.two_steps(read))
        self.assertTrue(result.ok, result.log)

    def recovering(self, app, step_flow):
        from dataclasses import replace as dc_replace
        # Declared, so that without the latch these WOULD escalate.
        step_flow = dc_replace(step_flow, steps=[dc_replace(
            step_flow.steps[0], on_uncertain=['postcondition_timeout', 'action_failed'])])
        asked, fixes = [], []
        def escalate(uncertainty):
            asked.append(uncertainty.kind)
            return Resolution(kind='recover', recovery='dismiss_dialog')
        with patch('nlc.flow._run_fix', side_effect=lambda *a, **k: fixes.append(a[2]) or True):
            result = replay(app, step_flow, escalate=escalate, budget=3)
        return result, asked, fixes

    def test_no_recovery_or_retry_in_the_same_step_after_it(self):
        # Codex review of 74f33e2: a postcondition_timeout after the flag
        # reached dismiss_dialog. Now: no escalation, no fix, no retry.
        app = App(True)
        app.wait_until = lambda target, predicate, **k: {'type': 'timeout', 'last_obs': app.observe()}
        result, asked, fixes = self.recovering(app, flow())
        self.assertEqual(([], []), (asked, fixes))
        self.assertEqual(['menu'], app.sent)
        self.assertEqual('postcondition_timeout', result.log[-1].get('error'))
        self.assertTrue(result.events[0].get('activated_target'))

    def test_an_unknown_error_seen_in_front_is_not_recovered_either(self):
        app = App(True)
        app.act = lambda obs, target, action, **k: (app.__dict__.setdefault('sent', []).append(action['name']),
            {'type': 'error', 'error': 'action_failed', 'effect': 'unknown', 'activated_target': True})[-1]
        result, asked, fixes = self.recovering(app, flow())
        self.assertEqual(([], []), (asked, fixes))
        self.assertEqual(['menu'], app.sent)
        self.assertFalse(result.ok)

    def test_without_the_flag_recovery_is_still_offered(self):
        app = App(False)
        app.wait_until = lambda target, predicate, **k: {'type': 'timeout', 'last_obs': app.observe()}
        result, asked, fixes = self.recovering(app, flow())
        self.assertEqual(['postcondition_timeout'], asked)

    def test_helper_watches_for_it_after_the_press(self):
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "menu":'):swift.index('    case "select_option":')]
        self.assertIn('if !wasFront {', case)
        self.assertIn('if isFrontmost(pid: p) { activated = true; break }', case)
        self.assertIn('out["activated_target"] = true', case)

    def test_open_folder_asks_through_the_bounded_request(self):
        # The stop/deadline check is inside openFolderBounded, right before the
        # request (run for real in tests/test_open_folder_swift.py).
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "open_folder":'):swift.index('    case "select_option":')]
        self.assertEqual(1, case.count('NSWorkspace.shared.open('))
        self.assertIn('switch openFolderBounded(deadline: deadline, stopped: { abortState.isStopped }', case)
        self.assertIn('case .notStarted(let why):', case)
        self.assertIn('"effect": "unknown"', case)
        after = case.split('case .opened:', 1)[1]
        self.assertNotIn('activate', after.replace('activatedByOpen', '').replace('activated_target', '')
                         .replace('activates', ''))

if __name__ == '__main__':
    unittest.main()
