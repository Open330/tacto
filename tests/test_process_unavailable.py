"""An app macOS lists with process identifier -1 is reported, not driven."""
import unittest
from tacto.app_launch import activate_app, launch_app


class Backend:
    path = '/Applications/Safari.app'
    def __init__(self):
        self.starts = self.activations = 0
    def session_ready(self): return True
    def resolve(self, target): return self.path
    def running(self, target): return [dict(pid=-1, path=self.path, ready=True)]
    def start(self, path):
        self.starts += 1
        return {'error': None}
    def pump(self, seconds): pass
    def frontmost_pid(self): return 1
    def activate(self, pid):
        self.activations += 1
        return True


class ProcessUnavailableTests(unittest.TestCase):
    def test_launch_does_not_report_already_running_with_pid_minus_one(self):
        backend = Backend()
        result = launch_app('com.apple.Safari', backend=backend, clock=lambda: 0)
        self.assertEqual(('application_process_unavailable', 'not_started'), (result['error'], result['effect']))
        self.assertIn('다시 열면', result['hint'])
        self.assertEqual(0, backend.starts)

    def test_activate_names_the_state_instead_of_an_invalid_pid(self):
        backend = Backend()
        result = activate_app('com.apple.Safari', backend=backend)
        self.assertEqual('application_process_unavailable', result['error'])
        self.assertEqual(0, backend.activations)


if __name__ == '__main__':
    unittest.main()
