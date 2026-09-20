"""Synthetic canvas interaction, no external service or native UI."""
import unittest
from unittest.mock import patch

from nlc.visual import capture_browser
from nlc.visual_actions import VisualClickExecutor, run_visual

try:
    import playwright.sync_api
    WEB = True
except ImportError:
    WEB = False


@unittest.skipUnless(WEB, 'optional playwright dependency not installed')
class VisualActions(unittest.TestCase):
    def setUp(self):
        from nlc.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.addCleanup(self.driver.close)
        self.driver.goto("""data:text/html,<style>body{margin:0}</style><canvas id='c' width='200' height='100'></canvas><script>
          window.clicks=0;
          const ctx=c.getContext('2d'); ctx.fillStyle='red'; ctx.fillRect(0,0,200,100);
          c.onclick=()=>{window.clicks++;ctx.fillStyle='green';ctx.fillRect(0,0,200,100)};
          </script>""")

    def capture(self):
        return capture_browser(self.driver, self.driver.observe()['obs_id'])

    def test_canvas_one_click_host_verified(self):
        calls = []
        def planner(capture, goal, history):
            calls.append(capture.capture_id)
            return {'action': 'click', 'x': 50, 'y': 50}
        result = run_visual(self.driver, 'page', 'make green', planner,
                            lambda proposal, capture: True,
                            lambda capture, history: self.driver._page.evaluate('window.clicks') == 1,
                            max_steps=2)
        self.assertEqual('DONE', result.state, result.reason)
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(result.history))
        self.assertEqual('dispatched', result.history[0]['effect'])
        self.assertNotIn('png', result.to_dict()['capture'])
        self.assertNotEqual(calls[0], result.capture['capture_id'])

    def test_pixel_and_dom_changes_refuse_stale_capture(self):
        for mutation in ("ctx.fillStyle='blue';ctx.fillRect(0,0,200,100)",
                         "document.body.setAttribute('data-version','next')"):
            capture = self.capture()
            self.driver._page.evaluate(mutation)
            result = VisualClickExecutor(self.driver).click(capture, {'action': 'click', 'x': 50, 'y': 50}, lambda *args: True)
            self.assertEqual(('not_started', 'stale_capture'), (result['effect'], result['reason']))
        self.assertEqual(0, self.driver._page.evaluate('window.clicks'))

    def test_invalid_coordinates_and_denied_authorization(self):
        for x, y in ((-1, 1), (1e9, 1), (float('nan'), 1), (True, 1), (1, float('inf'))):
            event = VisualClickExecutor(self.driver).click(self.capture(), {'action': 'click', 'x': x, 'y': y}, lambda *args: True)
            self.assertEqual('invalid_coordinates', event['reason'])
        for authorize in (None, lambda *args: False):
            event = VisualClickExecutor(self.driver).click(self.capture(), {'action': 'click', 'x': 30, 'y': 30}, authorize)
            self.assertEqual('not_started', event['effect'])
        self.assertEqual(0, self.driver._page.evaluate('window.clicks'))

    def test_capture_is_single_use_across_executors(self):
        capture = self.capture()
        action = {'action': 'click', 'x': 30, 'y': 30}
        VisualClickExecutor(self.driver).click(capture, action, lambda *args: True)
        event = VisualClickExecutor(self.driver).click(capture, action, lambda *args: True)
        self.assertEqual('capture_already_used', event['reason'])
        self.assertEqual(1, self.driver._page.evaluate('window.clicks'))

    def test_model_done_is_not_host_evidence(self):
        result = run_visual(self.driver, 'page', 'done', lambda *args: {'action': 'done'},
                            lambda *args: True, lambda *args: False)
        self.assertEqual('UNVERIFIED', result.state)
        self.assertEqual([], result.history)

    def test_timeout_after_dispatch_does_not_retry(self):
        calls = []
        original = self.driver._page.mouse.click
        def uncertain(x, y):
            calls.append((x, y))
            original(x, y)
            raise TimeoutError('transport interrupted after click')
        with patch.object(self.driver._page.mouse, 'click', side_effect=uncertain):
            result = run_visual(self.driver, 'page', 'click', lambda *args: {'action': 'click', 'x': 30, 'y': 30},
                                lambda *args: True, lambda *args: False)
        self.assertEqual('UNVERIFIED', result.state)
        self.assertEqual('unknown', result.history[0]['effect'])
        self.assertEqual(1, len(calls))
        self.assertEqual(1, self.driver._page.evaluate('window.clicks'))

    def test_authorization_mutation_is_rechecked_and_cancel_stops(self):
        def authorize(*args):
            self.driver._page.evaluate("document.body.dataset.changed='yes'")
            return True
        event = VisualClickExecutor(self.driver).click(self.capture(), {'action': 'click', 'x': 30, 'y': 30}, authorize)
        self.assertEqual('stale_capture', event['reason'])
        result = run_visual(self.driver, 'page', 'click', lambda *args: self.fail('planner called'),
                            lambda *args: True, lambda *args: False, cancelled=lambda: True)
        self.assertEqual('cancelled', result.reason)
        self.assertEqual([], result.history)

    def test_step_and_deadline_budget_stop_without_additional_action(self):
        result = run_visual(self.driver, 'page', 'click', lambda *args: {'action': 'click', 'x': 30, 'y': 30},
                            lambda *args: True, lambda *args: False, max_steps=1)
        self.assertEqual('step_limit', result.reason)
        self.assertEqual(1, len(result.history))
        result = run_visual(self.driver, 'page', 'click', lambda *args: self.fail('planner called'),
                            lambda *args: True, lambda *args: False, timeout_seconds=0)
        self.assertEqual('deadline_exceeded', result.reason)

    def test_truthy_callback_values_are_not_authority(self):
        result = run_visual(self.driver, 'page', 'click', lambda *args: {'action': 'click', 'x': 30, 'y': 30},
                            lambda *args: {'approved': True}, lambda *args: 'DONE')
        self.assertEqual('REFUSED', result.state)
        self.assertEqual('authorization_denied', result.reason)
        self.assertEqual(0, self.driver._page.evaluate('window.clicks'))
