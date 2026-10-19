import unittest
import sys
from unittest.mock import Mock
from tacto.app_launch import launch_app


class Backend:
    def __init__(self):
        self.time = 0
        self.starts = 0
        self.apps = []
        self.ready = True
        self.path = '/Applications/Example.app'
        self.finish = True
        self.request = {'error': None}

    def session_ready(self): return self.ready
    def resolve(self, target): return self.path
    def running(self, target): return self.apps
    def start(self, path):
        self.starts += 1
        return self.request
    def pump(self, seconds):
        self.time += seconds
        if self.finish:
            self.apps = [dict(pid=42, path=self.path, ready=True)]


class AppLaunchTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'darwin', 'native AppKit configuration')
    def test_native_launch_configuration_does_not_activate_or_create_duplicate_instance(self):
        from tacto.app_launch import NativeLaunchBackend
        backend = NativeLaunchBackend.__new__(NativeLaunchBackend)
        backend.workspace = Mock()
        result = backend.start('/Applications/Calculator.app')
        url, config, callback = backend.workspace.openApplicationAtURL_configuration_completionHandler_.call_args.args
        self.assertFalse(config.activates())
        self.assertFalse(config.createsNewApplicationInstance())
        self.assertFalse(config.addsToRecentItems())
        self.assertTrue(url.isFileURL())
        callback(None, object())
        self.assertEqual('launch_failed', result['error'])

    def run_launch(self, backend, **kwargs):
        return launch_app('com.example.App', backend=backend, clock=lambda: backend.time, **kwargs)

    def test_cold_launch_waits_for_process_without_claiming_a_window(self):
        b = Backend(); r = self.run_launch(b)
        self.assertEqual('launched', r['status'])
        self.assertFalse(r['window_verified'])
        self.assertEqual(1, b.starts)

    def test_already_running_does_not_relaunch(self):
        b = Backend(); b.pump(0)
        self.assertEqual('already_running', self.run_launch(b)['status'])
        self.assertEqual(0, b.starts)

    def test_paths_urls_arguments_and_malformed_ids_are_not_launch_targets(self):
        for target in ['/Applications/App.app', 'file:///App.app', 'https://example.org',
                       'com.app --flag', 'com..app', 'com.app\n', '', None]:
            self.assertEqual('invalid_bundle_id', launch_app(target, backend=object())['error'])

    def test_missing_app_and_unavailable_session_do_not_start(self):
        for field in ['path', 'ready']:
            b = Backend(); setattr(b, field, None)
            self.assertEqual('not_started', self.run_launch(b)['effect'])
            self.assertEqual(0, b.starts)

    def test_mismatched_or_ambiguous_process_identity_is_refused(self):
        for apps in [[dict(pid=42, path='/Other.app', ready=True)],
                     [dict(pid=i, path='/Applications/Example.app', ready=True) for i in [1, 2]]]:
            b = Backend(); b.apps = apps
            self.assertEqual('application_identity_ambiguous', self.run_launch(b)['error'])
            self.assertEqual(0, b.starts)

    def test_timeout_and_dispatch_error_never_repeat_launch(self):
        b = Backend(); b.finish = False
        r = self.run_launch(b, timeout=.1)
        self.assertEqual(('launch_timeout', 'unknown', 1), (r['error'], r['effect'], b.starts))
        b = Backend(); b.request['error'] = 'denied'
        self.assertEqual('launch_failed', self.run_launch(b)['error'])
        self.assertEqual(1, b.starts)

    def test_native_setup_lookup_and_poll_errors_are_short_without_retry(self):
        for name in ('session_ready', 'resolve', 'running'):
            b = Backend()
            setattr(b, name, Mock(side_effect=OSError('PRIVATE native path')))
            with self.subTest(name=name):
                result = self.run_launch(b)
                self.assertEqual('native_launch_unavailable', result['error'])
                self.assertEqual('not_started', result['effect'])
                self.assertNotIn('PRIVATE', str(result))
                self.assertEqual(0, b.starts)

    def test_dispatch_or_later_poll_exception_keeps_unknown_effect_and_never_resends(self):
        class FailingStart(Backend):
            def start(self, path):
                self.starts += 1
                raise OSError('PRIVATE dispatch detail')
        class FailingPump(Backend):
            def pump(self, seconds):
                raise RuntimeError('PRIVATE runloop detail')
        for b in (FailingStart(), FailingPump()):
            with self.subTest(backend=type(b).__name__):
                r = self.run_launch(b)
                self.assertEqual(('launch_failed', 'unknown', 1), (r['error'], r['effect'], b.starts))
                self.assertNotIn('PRIVATE', str(r))

    def test_missing_callback_receipt_does_not_relaunch(self):
        b = Backend(); b.request = None; b.finish = False
        r = self.run_launch(b, timeout=.1)
        self.assertEqual(('launch_timeout', 'unknown', 1), (r['error'], r['effect'], b.starts))

    def test_interruption_is_not_converted_to_an_ordinary_failure(self):
        b = Backend(); b.start = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt): self.run_launch(b)
