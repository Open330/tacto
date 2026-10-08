"""A menu command that makes the app come forward is said, not passed over.

Measured 2026-10-08: Finder's Go > Go to Folder… brings Finder to the front.
The helper reports `activated_target`; replay narrates it and keeps it on the
step's event."""
import pathlib
import unittest

from nlc.contract import Envelope
from nlc.flow import Flow, Step, Selector, Capture, Postcondition, replay

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

    def test_helper_watches_for_it_after_the_press(self):
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "menu":'):swift.index('    case "select_option":')]
        self.assertIn('if !wasFront {', case)
        self.assertIn('if isFrontmost(pid: p) { activated = true; break }', case)
        self.assertIn('out["activated_target"] = true', case)

    def test_open_folder_checks_stop_and_deadline_right_before_opening(self):
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "open_folder":'):swift.index('    case "select_option":')]
        guard = case.index('guard !abortState.isStopped, remainingForOpen > 0')
        self.assertLess(guard, case.index('NSWorkspace.shared.open('))
        self.assertIn('"effect": "not_started", "detail": "폴더를 열기 전에', case)
        self.assertNotIn('max(0.1,', case)
        # Before the open: not_started. After it, an unconfirmed completion is
        # "unknown" — the folder may be showing.
        before, after = case.split('NSWorkspace.shared.open(', 1)
        self.assertIn('"effect": "not_started"', before)
        self.assertIn('"effect": "unknown"', after)
        self.assertNotIn('"effect": "not_started"', after)
        self.assertNotIn('activate', after.replace('activatedByOpen', '').replace('activated_target', ''))


if __name__ == '__main__':
    unittest.main()
