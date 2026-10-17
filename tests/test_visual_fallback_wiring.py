"""The screenshot fallback the protocol names has to be reachable.

PROTOCOL.md: "관찰은 접근성 트리(AX tree) 우선, 스크린샷은 fallback …
스크린샷은 트리가 불완전한 앱에서만 쓴다." `native_visual` implemented that
and nothing imported it — tests reached it, the driver did not (2026-09-20).
An unreachable fallback is not a fallback, and the case it exists for is the
one where a caller has the least to go on: a window that is there and says
nothing about itself.
"""
import importlib.util
from types import SimpleNamespace
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class VisualFallbackWiringTests(unittest.TestCase):
    def setUp(self):
        from tacto import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.d._latest = {}
        self.d._last_action_at = 0
        self.d._indicate = False

    def test_the_capability_is_advertised(self):
        self.assertIn('evidence.visual', self.m.Driver.capabilities)

    def test_a_picture_is_a_read_and_says_so(self):
        with patch.object(self.m.Driver, 'visual_read',
                          return_value={'available': True, 'authorizes_action': False,
                                        'commit_witness': False}):
            allowed = self.d.visual_read()
        self.assertIs(False, allowed['authorizes_action'])
        self.assertIs(False, allowed['commit_witness'])

    def test_a_missing_permission_is_named_rather_than_reported_as_broken(self):
        class Backend:
            def permission(self):
                return False
        with patch('tacto.native_visual.ScreenCaptureBackend', Backend):
            allowed = self.d.visual_read()
        self.assertFalse(allowed['available'])
        self.assertEqual('screen_capture_permission_missing', allowed['reason'])
        self.assertIn('화면 기록', allowed['detail'])

    def test_capture_refuses_a_window_nobody_observed(self):
        answer = self.d.capture('fixture')
        self.assertEqual('stale_observation', answer['error'])
        self.assertEqual('not_started', answer['effect'])

    def test_capture_refuses_an_observation_that_has_moved_on(self):
        self.d._latest['fixture'] = SimpleNamespace(obs_id='new', pid=42)
        answer = self.d.capture('fixture', obs_id='old')
        self.assertEqual('stale_observation', answer['error'])

    def test_capture_refuses_before_asking_when_it_cannot_see(self):
        self.d._latest['fixture'] = SimpleNamespace(obs_id='o', pid=42)
        reached = []
        with patch.object(self.m.Driver, 'visual_read',
                          return_value={'available': False, 'reason': 'nope'}), \
             patch('tacto.native_visual.capture_native',
                   side_effect=lambda *a, **k: reached.append(1)):
            answer = self.d.capture('fixture')
        self.assertEqual('visual_read_unavailable', answer['error'])
        self.assertEqual('nope', answer['reason'])
        self.assertEqual([], reached, 'nothing is captured when it cannot be')

    def test_a_capture_carries_the_rule_it_is_read_under(self):
        self.d._latest['fixture'] = SimpleNamespace(obs_id='o', pid=42)
        shot = SimpleNamespace(to_dict=lambda: {'capture_id': 'c', 'obs_id': 'o',
                                                'authorizes_action': False,
                                                'commit_witness': False},
                               save=lambda path: path)
        with patch.object(self.m.Driver, 'visual_read', return_value={'available': True}), \
             patch('tacto.native_visual.capture_native', return_value=shot):
            answer = self.d.capture('fixture', path='/tmp/x.png')
        self.assertEqual('capture', answer['type'])
        self.assertIs(False, answer['authorizes_action'])
        self.assertIs(False, answer['commit_witness'])
        self.assertEqual('/tmp/x.png', answer['path'], 'pixels are saved, not returned')

    def test_a_capture_failure_is_not_a_half_done_thing(self):
        self.d._latest['fixture'] = SimpleNamespace(obs_id='o', pid=42)
        with patch.object(self.m.Driver, 'visual_read', return_value={'available': True}), \
             patch('tacto.native_visual.capture_native',
                   side_effect=RuntimeError('capture_timeout')):
            answer = self.d.capture('fixture')
        self.assertEqual('visual_read_failed', answer['error'])
        self.assertEqual('not_started', answer['effect'])

    def test_an_ax_opaque_window_is_told_where_to_go_next(self):
        """The case this exists for: a window that is there and says nothing."""
        import inspect
        source = inspect.getsource(self.m.Driver.observe)
        self.assertIn('visual_read()', source)
        self.assertIn('"obs_id": obs.obs_id', source.split('empty_tree')[1][:400],
                      'an empty tree still needs an obs_id to capture against')

    def test_a_truncated_tree_says_a_picture_is_available(self):
        import inspect
        source = inspect.getsource(self.m.Driver.observe)
        after = source.split('if obs.truncated:')[-1]
        self.assertIn('visual_read()', after)
