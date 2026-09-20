"""Whatever touches the screen shows that it is happening.

Found 2026-09-20 while verifying browsers: the outline was wired at two CLI
commands and two signed-helper messages, so every other way in — a script
holding a Driver, `nlc observe`, the whole evaluation suite, this session's own
browser and Calculator probes — drove somebody's machine with nothing on screen
to say so. Asking each caller to remember is how it went missing; the driver
owns it now.
"""
import importlib.util
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class ControlIndicatorTests(unittest.TestCase):
    def setUp(self):
        from nlc import driver
        self.m = driver
        self.d = object.__new__(driver.Driver)
        self.d._indicate = True
        self.d._indicator = None
        self.d._indicator_pid = None
        self.d._indicator_beat = None
        self.started = []

    def fake_indicator(self, alive=True):
        started = self.started
        class Fake:
            def __init__(self, pid, heartbeat=None, ttl=4.0, **kw):
                self.pid, self.heartbeat, self.ttl = pid, heartbeat, ttl
                self._alive = alive
            def __enter__(self):
                started.append(self)
                return self
            def __exit__(self, *exc):
                self._alive = False
                return False
            def alive(self):
                return self._alive
        return Fake

    def show(self, pid, **kw):
        import nlc.overlay
        with patch.object(nlc.overlay, 'Indicator', self.fake_indicator(**kw)):
            self.d._showing_control(pid)

    def test_touching_an_app_puts_the_outline_on_it(self):
        self.show(4321)
        self.assertEqual(1, len(self.started))
        self.assertEqual(4321, self.started[0].pid)
        self.assertTrue(self.d.indicator()['showing'])
        self.assertEqual(4321, self.d.indicator()['pid'])

    def test_the_same_app_again_does_not_start_a_second_one(self):
        self.show(4321)
        self.show(4321)
        self.assertEqual(1, len(self.started), 'one outline, kept alive')

    def test_a_different_app_moves_the_outline(self):
        self.show(4321)
        self.show(9876)
        self.assertEqual(2, len(self.started))
        self.assertFalse(self.started[0].alive(), 'the first is taken down')
        self.assertEqual(9876, self.d.indicator()['pid'])

    def test_the_heartbeat_is_touched_every_time(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.assertTrue(os.path.exists(beat))
        os.utime(beat, (0, 0))
        self.show(4321)
        self.assertGreater(os.stat(beat).st_mtime, 1,
                           'a driver still working keeps the outline alive')

    def test_a_dead_child_is_replaced_rather_than_assumed(self):
        self.show(4321, alive=False)
        self.show(4321, alive=False)
        self.assertEqual(2, len(self.started))

    def test_failing_to_draw_never_takes_down_the_task(self):
        import nlc.overlay
        def explode(*a, **kw):
            raise RuntimeError('no window server')
        with patch.object(nlc.overlay, 'Indicator', explode):
            self.d._showing_control(4321)          # must not raise
        self.assertFalse(self.d.indicator()['showing'], 'and it says so')

    def test_nothing_is_drawn_for_a_target_that_is_not_running(self):
        for pid in (None, 0, -1, True, '4321'):
            with self.subTest(pid=pid):
                self.show(pid)
        self.assertEqual([], self.started)

    def test_the_opt_out_is_explicit(self):
        self.d._indicate = False
        self.show(4321)
        self.assertEqual([], self.started)
        self.assertFalse(self.d.indicator()['enabled'])

    def test_closing_takes_the_outline_down_and_cleans_up(self):
        self.show(4321)
        beat = self.d._indicator_beat
        self.d.close()
        self.assertFalse(self.started[0].alive())
        self.assertFalse(os.path.exists(beat))
        self.assertIsNone(self.d.indicator()['pid'])

    def test_observe_and_act_both_show_it(self):
        """The two places that reach another app, checked at the surface."""
        import inspect
        for name in ('observe', 'act'):
            source = inspect.getsource(getattr(self.m.Driver, name))
            self.assertIn('_showing_control(', source,
                          f'{name} must say that it is happening')


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
class IndicatorLifetimeTests(unittest.TestCase):
    """The outline leaves on its own, because one that never does stops being read."""

    def test_the_child_is_told_the_heartbeat_and_the_deadline(self):
        from nlc.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321, heartbeat='/tmp/beat', ttl=2.5):
                pass
        self.assertIn('/tmp/beat', seen['command'])
        self.assertIn('2.5', seen['command'])
        self.assertIn('4321', seen['command'])

    def test_without_a_heartbeat_the_child_is_not_given_one(self):
        from nlc.overlay import Indicator
        seen = {}
        class Popen:
            pid = 999999
            def __init__(self, command, **kw):
                seen['command'] = command
            def poll(self):
                return None
            def terminate(self):
                pass
        with patch('subprocess.Popen', Popen):
            with Indicator(4321):
                pass
        self.assertEqual(['4321'], [c for c in seen['command'] if c == '4321'])
        self.assertEqual(4, len(seen['command']), 'python -m nlc.overlay <pid>')
