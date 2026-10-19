"""Failed browser startup must not strand the synchronous Playwright runtime."""
import unittest
from unittest.mock import Mock, patch
from tacto.browser import BrowserDriver


class BrowserLifecycleTests(unittest.TestCase):
    def test_launch_failure_stops_runtime_and_preserves_original_error(self):
        runtime=Mock();runtime.chromium.launch.side_effect=RuntimeError('missing browser')
        with patch('tacto.browser.sync_playwright') as start:
            start.return_value.start.return_value=runtime
            with self.assertRaisesRegex(RuntimeError,'missing browser'):
                BrowserDriver()
        runtime.stop.assert_called_once_with()

    def test_page_setup_failure_closes_browser_even_if_close_raises(self):
        runtime=Mock();browser=runtime.chromium.launch.return_value
        browser.new_page.side_effect=RuntimeError('page setup')
        browser.close.side_effect=RuntimeError('close failure')
        with patch('tacto.browser.sync_playwright') as start:
            start.return_value.start.return_value=runtime
            with self.assertRaisesRegex(RuntimeError,'page setup'):
                BrowserDriver()
        browser.close.assert_called_once_with()
        runtime.stop.assert_called_once_with()

    def test_close_releases_each_owned_resource_once_despite_select_cleanup_failure(self):
        driver=BrowserDriver.__new__(BrowserDriver)
        select=Mock();browser=Mock();runtime=Mock()
        select.close.side_effect=RuntimeError('select cleanup')
        driver._custom_select=select;driver._browser=browser;driver._pw=runtime
        driver.close();driver.close()
        select.close.assert_called_once_with();browser.close.assert_called_once_with()
        runtime.stop.assert_called_once_with()
