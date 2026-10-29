"""open-url --window brings the intended browser window forward before opening."""
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from tacto import cli


class OpenURLWindowTests(unittest.TestCase):
    def run_cli(self, argv, activate, opened):
        out = io.StringIO()
        with patch('tacto.cli._make_driver', return_value=object()), \
             patch('tacto.app_launch.activate_app', side_effect=activate), \
             patch('tacto.open_url.open_url', side_effect=opened), redirect_stdout(out):
            code = cli.main(argv)
        return code, json.loads(out.getvalue().strip().splitlines()[-1])

    def test_window_is_verified_before_the_page_opens(self):
        calls = []
        def activate(browser, window, driver):
            calls.append(('activate', browser, window)); return {'foreground_verified': True, 'window_verified': True}
        def opened(url, browser, driver):
            calls.append(('open', url)); return {'type': 'url_opened', 'url': url}
        code, result = self.run_cli(['open-url', 'https://example.com/', '--window', 'OOC', '--helper'], activate, opened)
        self.assertEqual(0, code)
        self.assertEqual([('activate', 'com.apple.Safari', 'OOC'), ('open', 'https://example.com/')], calls)

    def test_an_unverified_window_opens_nothing(self):
        opened = []
        code, result = self.run_cli(['open-url', 'https://example.com/', '--window', 'Nope', '--helper'],
                                    lambda b, window, driver: {'foreground_verified': True, 'window_verified': False,
                                                               'error': 'window_not_found', 'windows': ['A']},
                                    lambda *a, **k: opened.append(1))
        self.assertEqual((1, 'window_not_found', 'not_started', ['A']),
                         (code, result['error'], result['effect'], result['windows']))
        self.assertEqual([], opened)


if __name__ == '__main__':
    unittest.main()
