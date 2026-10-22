"""A locked screen is named as such, and idle locks can be prevented on request."""
import json
import tempfile
import unittest
from unittest import mock

from tests.test_app_launch import Backend, PresentationTests
from tacto.app_launch import activate_app, launch_app, present_app


class LockedBackend(PresentationTests.PresentationBackend):
    def __init__(self, problem):
        super().__init__()
        self.problem = problem
        self.apps = [dict(pid=42, path=self.path, ready=True)]
    def session_problem(self):
        return self.problem


class LockedScreenTests(unittest.TestCase):
    def test_a_locked_screen_is_said_plainly_and_nothing_is_attempted(self):
        for call in (lambda b: launch_app('com.apple.Safari', backend=b),
                     lambda b: activate_app('com.apple.Safari', backend=b),
                     lambda b: present_app('com.apple.Safari', 42, backend=b)):
            b = LockedBackend('screen_locked')
            r = call(b)
            self.assertEqual('screen_locked', r['error'])
            self.assertIn('잠금을 풀지 않습니다', r['hint'])
            self.assertEqual((0, 0), (b.starts, b.activations))

    def test_no_console_session_stays_its_own_error(self):
        r = activate_app('com.apple.Safari', backend=LockedBackend('gui_session_unavailable'))
        self.assertEqual('gui_session_unavailable', r['error'])
        self.assertNotIn('hint', r)

    def test_native_backend_reads_lock_before_console(self):
        from tacto.app_launch import NativeLaunchBackend
        backend = NativeLaunchBackend.__new__(NativeLaunchBackend)
        for state, problem in (({'locked': True, 'on_console': True}, 'screen_locked'),
                               ({'locked': False, 'on_console': True}, None),
                               ({'locked': None, 'on_console': True}, 'gui_session_unavailable'),
                               ({'locked': False, 'on_console': None}, 'gui_session_unavailable')):
            with self.subTest(state=state), mock.patch('tacto.session.lock_state', return_value=state):
                self.assertEqual(problem, backend.session_problem())
                self.assertEqual(problem is None, backend.session_ready())


class KeepAwakeTests(unittest.TestCase):
    def test_holds_for_the_asked_minutes_and_replaces_its_own_previous_holder(self):
        from tacto import session
        started, killed = [], []

        class Proc:
            def __init__(self, pid): self.pid = pid
        def run(argv, **kwargs):
            started.append(argv); return Proc(1000 + len(started))
        ps = mock.Mock(return_value=mock.Mock(stdout='/usr/bin/caffeinate\n'))
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(session.subprocess, 'run', ps), \
             mock.patch.object(session.os, 'kill', lambda pid, sig: killed.append(pid)):
            first = session.keep_awake_for(30, state_dir=folder, run=run, now=lambda: 0)
            second = session.keep_awake_for(90, state_dir=folder, run=run, now=lambda: 0)
            stopped = session.stop_keep_awake(state_dir=folder)
            again = session.stop_keep_awake(state_dir=folder)
        self.assertEqual(['caffeinate', '-di', '-t', '1800'], started[0])
        self.assertEqual(['caffeinate', '-di', '-t', '5400'], started[1])
        self.assertEqual((1800, 5400), (first['until'], second['until']))
        self.assertEqual([1001, 1002], killed)          # the previous holder, then on --stop
        self.assertTrue(stopped['stopped']);self.assertFalse(again['stopped'])

    def test_a_reused_pid_that_is_not_caffeinate_is_left_alone(self):
        from tacto import session
        killed = []
        with tempfile.TemporaryDirectory() as folder:
            (__import__('pathlib').Path(folder)/'keep-awake.json').write_text(json.dumps({'pid': 77, 'until': 0}))
            with mock.patch.object(session.subprocess, 'run', return_value=mock.Mock(stdout='/bin/zsh\n')), \
                 mock.patch.object(session.os, 'kill', lambda pid, sig: killed.append(pid)):
                self.assertFalse(session.stop_keep_awake(state_dir=folder)['stopped'])
        self.assertEqual([], killed)

    def test_bounds_and_usage(self):
        from tacto import session, cli
        for bad in (0, 481, 1.5, '60'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                session.keep_awake_for(bad, state_dir='/nonexistent', run=None)
        with mock.patch('builtins.print'):
            for argv in (['keep-awake', '--minutes'], ['keep-awake', '--minutes', 'x'], ['keep-awake', '--minutes', '999'],
                         ['keep-awake', 'now']):
                with self.subTest(argv=argv):
                    self.assertEqual(2, cli.main(argv))


if __name__ == '__main__':
    unittest.main()
