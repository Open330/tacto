"""Opening a web page on request: one LaunchServices hand-off, confirmed by the page."""
import json
import unittest
from unittest import mock

from tacto.open_url import open_url


class Backend:
    def __init__(self, problem=None):
        self.problem, self.opened, self.time = problem, [], 0
    def session_problem(self): return self.problem
    def resolve(self, bundle): return '/Applications/Safari.app' if bundle == 'com.apple.Safari' else None
    def open_url(self, url, path):
        self.opened.append((url, path)); return {'error': None}
    def pump(self, seconds): self.time += seconds


def page(url):
    return ('0 Window  Title: "Page", Identifier: "SafariWindow?IsSecure=true&UUID=F94CC087-2879-417A-9D6D-2A317F3981BE" [focused]\n'
            f'  2 WebArea  Title: "Page", URL: {json.dumps(url)}')


class Browser:
    def __init__(self, backend, shows):
        self.backend, self.shows = backend, shows
    def observe(self, target):
        if not self.backend.opened:
            return {'type': 'observation', 'obs_id': 'o', 'tree': page('https://old.example/')}
        return {'type': 'observation', 'obs_id': 'o', 'tree': page(self.shows)}


class OpenURLTests(unittest.TestCase):
    def run_open(self, url, shows=None, backend=None, **kwargs):
        b = backend or Backend()
        driver = Browser(b, shows) if shows is not None else None
        return open_url(url, driver=driver, backend=b, clock=lambda: b.time, sleep=lambda s: None, **kwargs), b

    def test_the_page_is_confirmed_by_its_web_area(self):
        r, b = self.run_open('HTTPS://Example.COM', shows='https://example.com/')
        self.assertEqual(('url_opened', True, 'https://example.com/'), (r['type'], r['loaded_verified'], r['url']))
        self.assertEqual([('https://example.com/', '/Applications/Safari.app')], b.opened)

    def test_a_redirect_is_not_adopted_and_nothing_is_opened_twice(self):
        r, b = self.run_open('https://example.com/a', shows='https://example.com/login')
        self.assertEqual(('page_not_shown', 'unknown', False), (r['error'], r['effect'], r['loaded_verified']))
        self.assertEqual(1, len(b.opened))

    def test_without_an_observer_it_says_only_dispatched(self):
        r, b = self.run_open('https://example.com/')
        self.assertEqual(('url_opened', False), (r['type'], r['loaded_verified']))

    def test_unsafe_or_relative_addresses_and_a_locked_screen_open_nothing(self):
        for url in ('javascript:alert(1)', 'file:///etc/hosts', 'https://u:p@example.com/', 'example.com', ''):
            with self.subTest(url=url):
                r, b = self.run_open(url)
                self.assertEqual(('invalid_url', []), (r['error'], b.opened))
        r, b = self.run_open('https://example.com/', backend=Backend('screen_locked'))
        self.assertEqual(('screen_locked', []), (r['error'], b.opened))
        self.assertIn('잠금을 풀지 않습니다', r['hint'])
        r, b = self.run_open('https://example.com/', browser='com.example.NotInstalled')
        self.assertEqual(('application_not_installed', []), (r['error'], b.opened))

    def test_command_usage(self):
        from tacto import cli
        with mock.patch('tacto.session.sandboxed', return_value=False), mock.patch('builtins.print'), \
             mock.patch('tacto.open_url.open_url') as opener:
            for argv in (['open-url'], ['open-url', 'https://a.example/', 'https://b.example/'],
                         ['open-url', 'https://a.example/', '--browser']):
                with self.subTest(argv=argv):
                    self.assertEqual(2, cli.main(argv))
            opener.assert_not_called()


if __name__ == '__main__':
    unittest.main()
