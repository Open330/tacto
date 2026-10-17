"""Multi-screen recovery boundaries on isolated local Chromium fixtures."""
import unittest
from urllib.parse import quote

try:
    import playwright.sync_api
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False


@unittest.skipUnless(HAS_BROWSER, 'optional playwright dependency not installed')
class RecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        from tacto.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.addCleanup(self.driver.close)

    def page(self, html):
        self.driver.goto('data:text/html,' + quote(html))

    @staticmethod
    def action(candidates, label, operation='click'):
        return {'do': operation, 'candidate': next(c for c in candidates if c['label'] == label)}

    def test_twelve_screen_wizard_finishes_only_after_independent_readback(self):
        from tacto.general import run
        self.page('''<button onclick="advance()">Next 1</button>
            <input aria-label="Result" value="pending" readonly>
            <script>window.steps=0; window.commits=0;
            function advance(){ steps++; const b=document.querySelector('button');
              if(steps===12){ b.remove(); document.querySelector('input').value='wizard-complete'; }
              else { b.textContent='Next '+(steps+1); } }
            </script>''')
        def planner(goal, candidates, done):
            if len(done) < 12:
                return {'actions': [self.action(candidates, f'Next {len(done)+1}')]}
            if len(done) == 12:
                return {'actions': [self.action(candidates, 'Result', 'read')]}
            return {'done': True}
        result = run(self.driver, 'page', 'complete local wizard', planner, max_screens=14)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(result.said, ['wizard-complete'])
        self.assertEqual(self.driver._page.evaluate('window.steps'), 12)
        self.assertEqual(self.driver._page.locator('input').input_value(), 'wizard-complete')
        self.assertEqual(self.driver._page.evaluate('window.commits'), 0)
        self.assertEqual(result.asked, 14)

    def test_unexpected_modal_cuts_batch_then_replans_without_background_click(self):
        from tacto.general import run
        self.page('''<button onclick="document.querySelector('dialog').showModal()">Open overlay</button>
            <button onclick="window.clicks++">Increment</button>
            <dialog><button onclick="document.querySelector('dialog').close()">Close overlay</button></dialog>
            <input aria-label="Result" value="ready" readonly><script>window.clicks=0</script>''')
        asked = 0
        def planner(goal, candidates, done):
            nonlocal asked
            asked += 1
            if asked == 1:
                return {'actions': [self.action(candidates, 'Open overlay'), self.action(candidates, 'Increment')]}
            if asked == 2:
                self.assertEqual(self.driver._page.evaluate('window.clicks'), 0)
                return {'actions': [self.action(candidates, 'Close overlay')]}
            if asked == 3:
                return {'actions': [self.action(candidates, 'Increment'), self.action(candidates, 'Result', 'read')]}
            return {'done': True}
        result = run(self.driver, 'page', 'dismiss overlay then increment', planner)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(self.driver._page.evaluate('window.clicks'), 1)
        self.assertTrue(result.visits[0].cut_short)
        self.assertEqual(len(result.visits[0].actions), 1)

    def test_detached_target_in_batch_never_retargets_same_label_replacement(self):
        from tacto.general import run
        self.page('''<button onclick="document.querySelector('#victim').outerHTML=
          '<button id=victim onclick=window.clicks++>Increment</button>'">Replace control</button>
          <button id="victim" onclick="window.clicks++">Increment</button>
          <script>window.clicks=0</script>''')
        asked = 0
        def planner(goal, candidates, done):
            nonlocal asked
            asked += 1
            if asked == 1:
                return {'actions': [self.action(candidates, 'Replace control'), self.action(candidates, 'Increment')]}
            return {'actions': []}
        result = run(self.driver, 'page', 'inspect replacement boundary', planner)
        self.assertFalse(result.ok)
        self.assertEqual(self.driver._page.evaluate('window.clicks'), 0)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(len(result.visits[0].actions), 1)

    def test_origin_drift_is_observed_and_cannot_be_promoted_as_one_origin(self):
        from tacto.general import run
        from tacto.promote import candidate_from
        def route(request):
            body = ('<button onclick="location.href=\'https://second.invalid/end\'">Next</button>'
                    if request.request.url.startswith('https://first.invalid/')
                    else '<input aria-label="Result" value="arrived" readonly>')
            request.fulfill(status=200, content_type='text/html', body=body)
        self.driver._page.route('**/*', route)
        self.driver.goto('https://first.invalid/start')
        def planner(goal, candidates, done):
            if not done:
                return {'actions': [self.action(candidates, 'Next')]}
            if len(done) == 1:
                return {'actions': [self.action(candidates, 'Result', 'read')]}
            return {'done': True}
        result = run(self.driver, 'page', 'navigate then read', planner)
        self.assertTrue(result.ok, result.why)
        self.assertTrue(result.identity_changed)
        self.assertEqual(result.target_identity, 'https://first.invalid')
        self.assertEqual(self.driver.identity(), 'https://second.invalid')
        self.assertEqual(result.said, ['arrived'])
        self.assertIsNone(candidate_from(result, 'page', 'navigate then read',
                                         target_identity=self.driver.identity()))

    def test_timeout_after_save_dispatch_never_retries_or_claims_done(self):
        from tacto.contract import Envelope
        from tacto.general import run
        from tacto.outcome import spec_for, verify
        self.page('''<input aria-label="location" value="/tmp">
            <input aria-label="filename" value="boundary.txt">
            <input aria-label="overwrite" value="">
            <input aria-label="readback" value="hello">
            <button onclick="window.commits++">Save</button><script>window.commits=0</script>''')
        env = Envelope(quote='save boundary.txt', allows=frozenset({'read','write_persistent'}), scope='item')
        spec = spec_for('save_file', env,
            {'저장 위치':'/tmp','저장한 이름':'boundary.txt','다시 읽은 내용':'hello'},
            {'저장 위치':'request','저장한 이름':'request','다시 읽은 내용':'prior'})
        def where(name, candidates):
            label = {'저장 위치':'location','저장한 이름':'filename',
                     '요청하지 않은 덮어쓰기 없음':'overwrite','다시 읽은 내용':'readback'}[name]
            candidate = next(c for c in candidates if c['label'] == label)
            return {'pick': candidates.index(candidate), 'candidate': candidate}
        original_act = self.driver.act
        dispatches = []
        def timeout_after_dispatch(*args, **kwargs):
            response = original_act(*args, **kwargs)
            self.assertEqual(response['type'], 'acted')
            dispatches.append(args[2])
            raise TimeoutError('lost response after dispatched save')
        self.driver.act = timeout_after_dispatch
        result = run(self.driver, 'page', 'save boundary.txt',
                     lambda goal, candidates, done: {'actions':[self.action(candidates, 'Save')]},
                     envelope=env, spec=spec, ask_where=where, approve=lambda _: True)
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(self.driver._page.evaluate('window.commits'), 1)
        self.assertEqual(result.certainty, 'unknown')
        self.assertEqual(result.events[0]['state'], 'unknown')
        self.assertFalse(result.ok)
        self.assertFalse(verify(spec, result, result.after_tree).ok)


if __name__ == '__main__':
    unittest.main()
