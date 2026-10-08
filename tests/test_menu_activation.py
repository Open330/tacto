"""A menu command that makes the app come forward is said, not passed over.

Measured 2026-10-08: Finder's Go > Go to Folder… brings Finder to the front.
The helper reports `activated_target`; replay narrates it and keeps it on the
step's event."""
import pathlib
import unittest

from nlc.contract import Envelope
from nlc.flow import Flow, Step, Postcondition, replay

ROOT = pathlib.Path(__file__).resolve().parents[1]


class App:
    capabilities = {'action.menu'}

    def __init__(self, activated):
        self.activated = activated; self.opened = False

    def observe(self, *a, **k):
        tree = '0 Window  Title: Docs\n' + ('  1 Sheet  Identifier: GoToWindow\n' if self.opened else '')
        return {'type': 'observation', 'obs_id': 'o', 'tree': tree}

    def act(self, obs, target, action, **k):
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
        self.assertTrue(any('스스로 앞으로' in s for s in said), said)
        self.assertTrue(result.events[0].get('activated_target'))

    def test_quiet_when_the_app_stays_behind(self):
        said = []
        result = replay(App(False), flow(), narrate=said.append)
        self.assertTrue(result.ok, result.log)
        self.assertFalse(any('스스로 앞으로' in s for s in said))
        self.assertNotIn('activated_target', result.events[0])

    def test_helper_watches_for_it_after_the_press(self):
        swift = (ROOT / 'helper/NLCHelper.swift').read_text()
        case = swift[swift.index('    case "menu":'):swift.index('    case "select_option":')]
        self.assertIn('if !wasFront {', case)
        self.assertIn('if isFrontmost(pid: p) { activated = true; break }', case)
        self.assertIn('out["activated_target"] = true', case)


if __name__ == '__main__':
    unittest.main()
