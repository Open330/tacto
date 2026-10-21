"""Bringing an app or one of its windows forward, only as asked."""
import json
import unittest
from unittest import mock

from tests.test_app_launch import PresentationTests
from tacto.app_launch import activate_app



class Browser:
    """Two Safari windows; prepare_window makes the chosen one main."""
    def __init__(self, focused='Apple', prepares=True):
        self.focused = focused; self.prepares = prepares; self.acts = []
    def observe(self, target):
        lines = []
        for index, title in ((0, 'Apple'), (5, 'Inbox - Gmail')):
            line = f'{index} Window  Title: {json.dumps(title)}'
            if title == self.focused:
                line += ' [focused]'
            lines += [line, f'  {index + 1} Button  Title: "Close"']
        return {'type': 'observation', 'obs_id': f'o{len(self.acts)}', 'tree': '\n'.join(lines)}
    def act(self, obs_id, target, action, **kwargs):
        self.acts.append((obs_id, action, kwargs))
        if self.prepares:
            self.focused = 'Inbox - Gmail'
        return {'type': 'acted', 'action': 'prepare_window'}


class ActivateTests(unittest.TestCase):
    def backend(self, apps=1):
        b = PresentationTests.PresentationBackend()
        b.apps = [dict(pid=42 + i, path=b.path, ready=True) for i in range(apps)]
        return b

    def run_activate(self, b, **kwargs):
        return activate_app('com.apple.Safari', backend=b, clock=lambda: b.time,
                            sleep=lambda s: setattr(b, 'time', b.time + s), **kwargs)

    def test_brings_the_running_app_forward_without_launching(self):
        b = self.backend()
        r = self.run_activate(b)
        self.assertEqual(('app_activation', True, 42, 'running_application'),
                         (r['type'], r['foreground_verified'], r['pid'], r['route']))
        self.assertEqual((1, 0), (b.activations, b.starts))

    def test_a_window_named_by_part_of_its_title_is_made_main(self):
        b, browser = self.backend(), Browser()
        r = self.run_activate(b, window='Gmail', driver=browser)
        self.assertEqual('app_activation', r['type'], r)
        self.assertEqual(('Inbox - Gmail', True), (r['window'], r['window_verified']))
        self.assertEqual([{'name': 'prepare_window', 'element_index': 5}], [a for _, a, _ in browser.acts])

    def test_an_already_main_window_is_not_prepared_again(self):
        b, browser = self.backend(), Browser(focused='Inbox - Gmail')
        r = self.run_activate(b, window='Gmail', driver=browser)
        self.assertTrue(r['window_verified']); self.assertEqual([], browser.acts)

    def test_a_window_that_does_not_become_main_is_reported_unverified(self):
        b, browser = self.backend(), Browser(prepares=False)
        r = self.run_activate(b, window='Gmail', driver=browser)
        self.assertEqual('app_activation', r['type']); self.assertFalse(r['window_verified'])
        self.assertEqual(1, len(browser.acts))

    def test_missing_or_ambiguous_windows_are_listed_not_guessed(self):
        b, browser = self.backend(), Browser()
        missing = self.run_activate(b, window='Calendar', driver=browser)
        self.assertEqual(('window_not_found', ['Apple', 'Inbox - Gmail']), (missing['error'], missing['windows']))
        ambiguous = self.run_activate(self.backend(), window='l', driver=Browser())
        self.assertEqual('window_ambiguous', ambiguous['error'])
        self.assertEqual([], browser.acts)

    def test_not_running_ambiguous_or_locked_never_activates(self):
        for b, code in ((self.backend(apps=0), 'application_not_running'),
                        (self.backend(apps=2), 'application_identity_ambiguous')):
            with self.subTest(code=code):
                r = self.run_activate(b)
                self.assertEqual((code, 'not_started', 0), (r['error'], r['effect'], b.activations))
        locked = self.backend(); locked.ready = False
        self.assertEqual('gui_session_unavailable', self.run_activate(locked)['error'])
        self.assertEqual(0, locked.activations)

    def test_a_declined_request_is_retried_once_through_launch_services(self):
        b = self.backend(); b.accept = False; opened = []
        def open_activating(path):
            opened.append(path); b.front = 42
        b.open_activating = open_activating
        r = self.run_activate(b)
        self.assertEqual(('app_activation', 'launch_services'), (r['type'], r['route']))
        self.assertEqual(([b.path], 0), (opened, b.starts))

    def test_activation_that_is_not_observed_is_unknown_and_skips_the_window(self):
        b, browser = self.backend(), Browser()
        b.show = False; opened = []
        b.open_activating = opened.append
        r = self.run_activate(b, window='Gmail', driver=browser)
        self.assertEqual(('activation_not_observed', 'unknown'), (r['error'], r['effect']))
        self.assertEqual(1, len(opened))
        self.assertEqual([], browser.acts)

    def test_bad_input_is_refused_before_any_native_call(self):
        b = self.backend()
        for kwargs in ({'window': ''}, {'window': 'a\nb'}, {'window': 'x' * 201}, {'window': 'Gmail'}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual('not_started', self.run_activate(b, **kwargs)['effect'])
        self.assertEqual('invalid_bundle_id', activate_app('../Safari', backend=b)['error'])
        self.assertEqual(0, b.activations)


class ActivateCommandTests(unittest.TestCase):
    def test_window_requires_the_helper_and_usage_is_strict(self):
        from tacto import cli
        for argv in (['activate'], ['activate', 'a.b', 'c.d'], ['activate', 'a.b', '--window'],
                     ['activate', 'com.apple.Safari', '--window', 'Gmail']):
            with self.subTest(argv=argv), mock.patch('tacto.session.sandboxed', return_value=False), \
                    mock.patch('tacto.app_launch.activate_app') as activate, mock.patch('builtins.print'):
                self.assertEqual(2, cli.main(argv))
                activate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
