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
    """Fake processes: pid -> (start time, command) as /bin/ps reports them."""

    def harness(self, folder):
        from tacto import session
        live, killed, started = {}, [], []

        class Proc:
            def __init__(self, pid):self.pid = pid
            def terminate(self):live.pop(self.pid, None)
        def run(argv, **kwargs):
            pid = 1000 + len(started); started.append(argv)
            live[pid] = (f'start-{pid}', ' '.join(argv))
            return Proc(pid)
        def kill(pid, sig):
            killed.append(pid); live.pop(pid, None)
        ps = {'down': False}
        identify = lambda pid: None if ps['down'] else live.get(pid, False)
        start = lambda minutes, now=0: session.keep_awake_for(minutes, state_dir=folder, run=run,
                                                            now=lambda: now, identify=identify, kill=kill)
        stop = lambda now=0: session.stop_keep_awake(state_dir=folder, kill=kill, now=lambda: now,
                                                     identify=identify)
        self.ps = ps
        return session, live, killed, started, start, stop

    def test_holds_for_the_asked_minutes_and_replaces_its_own_previous_holder(self):
        with tempfile.TemporaryDirectory() as folder:
            session, live, killed, started, start, stop = self.harness(folder)
            first = start(30); second = start(90)
            self.assertEqual(['/usr/bin/caffeinate', '-di', '-t', '1800'], started[0])
            self.assertEqual((1800, 5400), (first['until'], second['until']))
            self.assertEqual([1000], killed)                    # the previous holder only
            self.assertTrue(stop()['stopped']); self.assertEqual([1000, 1001], killed)
            self.assertFalse(stop()['stopped'])
            self.assertIn('screen-saver', first['note'])

    def test_a_recycled_pid_or_an_expired_record_is_never_signalled(self):
        with tempfile.TemporaryDirectory() as folder:
            session, live, killed, started, start, stop = self.harness(folder)
            start(30)
            live[1000] = ('later-start', '/usr/bin/caffeinate -di -t 1800')   # same command, new process
            self.assertFalse(stop()['stopped'])
            start(30)
            self.assertFalse(stop(now=10**6)['stopped'])        # past its own end: nothing to stop
            self.assertEqual([], killed)

    def test_a_holder_that_cannot_be_checked_is_kept_and_not_doubled(self):
        with tempfile.TemporaryDirectory() as folder:
            session, live, killed, started, start, stop = self.harness(folder)
            start(30)
            self.ps['down'] = True                       # ps times out for a moment
            result = stop()
            self.assertEqual((False, True), (result['stopped'], result.get('unverified')))
            with self.assertRaises(RuntimeError):start(30)   # no second, untracked holder
            self.assertEqual(1, len(started))
            self.ps['down'] = False
            self.assertTrue(stop()['stopped'])
            self.assertEqual({}, live)

    def test_concurrent_starts_and_stops_leave_no_untracked_holder(self):
        import threading
        with tempfile.TemporaryDirectory() as folder:
            session, live, killed, started, start, stop = self.harness(folder)
            barrier = threading.Barrier(4)
            def work(action):
                barrier.wait()
                action()
            threads = [threading.Thread(target=work, args=(a,)) for a in
                       (lambda: start(30), lambda: start(60), lambda: stop(), lambda: start(45))]
            for t in threads:t.start()
            for t in threads:t.join()
            stop()
            self.assertEqual({}, live)      # every holder started was stopped, none left running

    def test_a_holder_that_cannot_be_recorded_is_ended(self):
        from tacto import session
        ended = []

        class Proc:
            pid = 9
            def terminate(self):ended.append(9)
        with tempfile.TemporaryDirectory() as folder, self.assertRaises(OSError):
            session.keep_awake_for(5, state_dir=folder, run=lambda *a, **k: Proc(), identify=lambda pid: None)
        self.assertEqual([9], ended)

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
            with mock.patch('sys.platform', 'darwin'), \
                 mock.patch('tacto.session.keep_awake_for', side_effect=RuntimeError('unverified')):
                self.assertEqual(1, cli.main(['keep-awake', '--minutes', '30']))
            with mock.patch('sys.platform', 'darwin'), \
                 mock.patch('tacto.session.stop_keep_awake', return_value={'stopped': False, 'unverified': True}):
                self.assertEqual(1, cli.main(['keep-awake', '--stop']))


if __name__ == '__main__':
    unittest.main()
