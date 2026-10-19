"""The public map probe's host checks, without a network, browser or model."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('public_map_visual',
                                              ROOT/'examples/probes/public_map_visual.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

START = 'https://www.openstreetmap.org/#map=15/37.5665/126.9780'
ZOOMED = 'https://www.openstreetmap.org/#map=16/37.5665/126.9780'


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Control:
    def __init__(self, count=1, visible=True, box=None, hit=True):
        self.count_value, self.visible, self.hit = count, visible, hit
        self.box = box if box is not None else {'x': 10, 'y': 20, 'width': 30, 'height': 30}

    def count(self):
        return self.count_value

    def is_visible(self):
        return self.visible

    def bounding_box(self):
        return self.box

    def evaluate(self, script, point):
        return self.hit


class Page:
    def __init__(self, clock, urls=(START,), control=None):
        self.clock, self.urls, self.control = clock, list(urls), control or Control()
        self.waits = []

    @property
    def url(self):
        return self.urls[0]

    def set_default_timeout(self, ms):
        pass

    def goto(self, url, **kwargs):
        return SimpleNamespace(status=200)

    def locator(self, selector):
        return self.control

    def evaluate(self, expression):
        return {'navigator.language': 'en-US', 'navigator.userAgent': 'TestAgent/1'}[expression]

    viewport_size = {'width': 1280, 'height': 720}

    def wait_for_timeout(self, ms):
        self.waits.append(ms)
        self.clock.now += ms / 1000
        if len(self.urls) > 1:
            self.urls.pop(0)


class Driver:
    def __init__(self, page, close_error=None):
        self._page, self.close_error = page, close_error
        self._browser = SimpleNamespace(version='test')
        self.observed = 0

    def observe(self, target):
        self.observed += 1
        return {'type': 'observation', 'obs_id': f'o{self.observed}'}

    def close(self):
        if self.close_error:
            raise self.close_error


class Capture:
    def save(self, path):
        pass

    def to_dict(self):
        return {'capture_id': 'c'}


class ProbeUnitTests(unittest.TestCase):
    def test_map_state_accepts_only_the_owned_origin_and_finite_state(self):
        self.assertEqual((15, 37.5665, 126.978), probe.map_state(START))
        for url in ('http://www.openstreetmap.org/#map=15/37.5/126.9',
                    'https://openstreetmap.org/#map=15/37.5/126.9',
                    'https://www.openstreetmap.org/edit#map=15/37.5/126.9',
                    'https://www.openstreetmap.org/?mlat=1#map=15/37.5/126.9',
                    'https://www.openstreetmap.org/#15/37.5/126.9',
                    'https://www.openstreetmap.org/#map=15/37.5',
                    'https://www.openstreetmap.org/#map=15/37.5/126.9/1',
                    'https://www.openstreetmap.org/#map=x/37.5/126.9',
                    'https://www.openstreetmap.org/#map=-1/37.5/126.9',
                    'https://www.openstreetmap.org/#map=21/37.5/126.9',
                    'https://www.openstreetmap.org/#map=15/nan/126.9',
                    'https://www.openstreetmap.org/#map=15/37.5/inf',
                    'https://www.openstreetmap.org/#map=15/91/126.9',
                    'https://www.openstreetmap.org/#map=15/37.5/181',
                    'https://www.openstreetmap.org/#map=15/a/126.9'):
            with self.subTest(url), self.assertRaises(ValueError):
                probe.map_state(url)

    def authorize(self, *, control=None, url=START, attempts=0, point=(20, 30)):
        page = Page(Clock(), urls=(url,), control=control)
        report = {'host_click_attempts': attempts}
        return probe.authorize_click(page, page.control, (15, 37.5665, 126.978), report,
                                     {'action': 'click', 'x': point[0], 'y': point[1]})

    def test_authorize_refuses_six_conditions_and_accepts_only_the_control(self):
        self.assertIs(True, self.authorize())
        refusals = {
            'no control': {'control': Control(count=0)},
            'two controls': {'control': Control(count=2)},
            'invisible': {'control': Control(visible=False)},
            'outside box': {'point': (40, 30)},
            'state changed': {'url': ZOOMED},
            'second click': {'attempts': 1},
            'hit elsewhere': {'control': Control(hit=False)},
            'hit not boolean': {'control': Control(hit='true')},
            'no box': {'control': Control(box=False)},
        }
        refusals['no box']['control'].box = None
        for name, options in refusals.items():
            with self.subTest(name):
                self.assertIs(False, self.authorize(**options))

    def test_capture_retries_only_a_changed_page_and_records_every_attempt(self):
        class VisualError(RuntimeError):
            pass
        def run(codes):
            codes = list(codes)
            def capture(driver, obs_id, target):
                code = codes.pop(0)
                if code:
                    raise VisualError(code)
                return Capture()
            page = Page(Clock())
            driver, report = Driver(page), {'capture_attempts': []}
            try:
                result = probe.capture_with_retry(driver, page, report, capture, VisualError)
            except VisualError as error:
                result = str(error)
            return result, report['capture_attempts'], driver.observed, page.waits
        result, attempts, observed, waits = run(['page_changed_during_capture', None])
        self.assertIsInstance(result, Capture)
        self.assertEqual([{'attempt': 1, 'accepted': False, 'error_code': 'page_changed_during_capture'},
                          {'attempt': 2, 'accepted': True}], attempts)
        self.assertEqual((2, [200]), (observed, waits))
        result, attempts, observed, _ = run(['page_changed_during_capture'] * 3)
        self.assertEqual('page_changed_during_capture', result)
        self.assertEqual(3, len(attempts))
        self.assertEqual(3, observed)
        result, attempts, observed, waits = run(['stale_document'])
        self.assertEqual(('stale_document', 1, []), (result, observed, waits))
        result, attempts, _, _ = run(['PRIVATE detail'])
        self.assertEqual('other', attempts[0]['error_code'])

    def test_zoom_readback_times_out_with_the_last_state_and_checks_the_center(self):
        clock = Clock()
        page = Page(clock, urls=(START,))
        report = {}
        with self.assertRaises(TimeoutError):
            probe.read_zoom(page, (15, 37.5665, 126.978), report, clock=clock)
        self.assertEqual((15, 37.5665, 126.978), report['final_state'])
        self.assertGreaterEqual(clock.now - 100, probe.READ_SECONDS)
        moved = 'https://www.openstreetmap.org/#map=16/37.6000/126.9780'
        page = Page(Clock(), urls=(START, moved))
        final = probe.read_zoom(page, (15, 37.5665, 126.978), {}, clock=page.clock)
        self.assertFalse(probe.zoomed_in_place((15, 37.5665, 126.978), final))
        self.assertTrue(probe.zoomed_in_place((15, 37.5665, 126.978), (16, 37.5665, 126.978)))


class ProbeRunTests(unittest.TestCase):
    def run_probe(self, *, plan=None, close_error=None, digests=None):
        clock = Clock()
        page = Page(clock)
        driver = Driver(page, close_error)
        class Executor:
            def __init__(self, driver, target):
                pass
            def click(self, capture, proposal, authorize):
                if authorize(proposal, capture) is not True:
                    return {'effect': 'not_started'}
                page.urls = [ZOOMED]
                return {'effect': 'dispatched'}
        def planner(capture, goal, **kwargs):
            return {'action': 'click', 'x': 20, 'y': 30}
        with tempfile.TemporaryDirectory() as directory, \
             patch('tacto.browser.BrowserDriver', return_value=driver), \
             patch('tacto.visual.capture_browser', return_value=Capture()), \
             patch('tacto.visual.plan_visual', side_effect=plan or planner), \
             patch('tacto.visual_actions.VisualClickExecutor', Executor):
            output = Path(directory)/'report.json'
            if digests:
                with patch.object(probe.hashlib, 'sha256', side_effect=digests):
                    result = probe.run('codex', output, clock=clock)
            else:
                result = probe.run('codex', output, clock=clock)
            saved = json.loads(output.read_text())
        self.assertEqual(json.loads(json.dumps(result)), saved)
        return result

    def test_one_authorized_click_passes_with_environment_metadata(self):
        result = self.run_probe()
        self.assertTrue(result['passed'])
        self.assertEqual(1, result['host_click_attempts'])
        self.assertEqual({'locale': 'en-US', 'user_agent': 'TestAgent/1',
                          'viewport': {'width': 1280, 'height': 720}}, result['environment'])
        self.assertRegex(result['started_at_utc'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00$')
        self.assertTrue(result['source_subset_stable'])

    def test_source_mismatch_or_cleanup_failure_cannot_pass(self):
        count = len(sorted((ROOT/'tacto').glob('*.py'))) + 1
        hexes = iter(['a'] * count + ['b'] * count)
        def digest(data):
            value = next(hexes)
            return SimpleNamespace(hexdigest=lambda: value)
        result = self.run_probe(digests=digest)
        self.assertFalse(result['source_subset_stable'])
        self.assertFalse(result['passed'])
        result = self.run_probe(close_error=RuntimeError('PRIVATE close'))
        self.assertFalse(result['passed'])
        self.assertEqual('RuntimeError', result['cleanup_error_class'])
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_planner_failure_keeps_observed_provider_cost_and_no_click(self):
        from tacto.provider_observation import record_provider_result
        from tacto.provider_result import ProviderResult
        def plan(capture, goal, **kwargs):
            record_provider_result(ProviderResult(
                'owned-call', 'codex', 'codex', 'cli-json', 'ok', 'PRIVATE_RESPONSE', None, 1.2,
                total_input_tokens=42, output_tokens=7, cost=.01, cost_currency='USD',
                cost_basis='list_estimate'))
            raise RuntimeError('PRIVATE provider failure')
        result = self.run_probe(plan=plan)
        self.assertFalse(result['passed'])
        self.assertEqual('planning', result['stage'])
        self.assertEqual(0, result['host_click_attempts'])
        self.assertEqual(1, len(result['provider_calls']))
        self.assertEqual(42, result['provider_calls'][0]['total_input_tokens'])
        self.assertEqual(1, result['usage']['logical_requests'])
        self.assertNotIn('PRIVATE', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
