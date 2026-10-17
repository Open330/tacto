"""Custom ARIA selects through checked planner, general loop and promoted flow."""
import unittest
from urllib.parse import quote
from tacto.ask import what_to_do_here
from tacto.safety import effects_of

try:
    import playwright.sync_api
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

PAGE = '''<section aria-label="Billing"><button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false"
 onclick="this.setAttribute('aria-expanded','true');document.getElementById('choices').hidden=false">Choose</button>
<div id="choices" role="listbox" hidden>
 <div role="option" aria-selected="false" onclick="choose(this)">Alpha</div>
 <div role="option" aria-selected="false" onclick="choose(this)">Beta</div>
</div></section><input aria-label="Result" value="pending" readonly>
<script>window.writes=0;function choose(el){window.writes++;document.querySelector('[aria-label=Result]').value=el.textContent;
 el.parentElement.querySelectorAll('[role=option]').forEach(option=>option.setAttribute('aria-selected',String(option===el)));document.getElementById('choices').hidden=true;
 document.querySelector('[role=combobox]').setAttribute('aria-expanded','false')}</script>'''


class CustomSelectSchemaTests(unittest.TestCase):
    def candidate(self, can, options=None):
        return {'role': 'combobox', 'label': 'Choice', 'can': can, 'options': options or [], 'disabled': False}

    def test_closed_requires_open_and_separate_observation(self):
        q = what_to_do_here('Choose Beta', [self.candidate(['open_select'])])
        self.assertIsNotNone(q.check({'actions': [{'pick': 0, 'do': 'open_select'}]}))
        for actions in [
            [{'pick': 0, 'do': 'open_select', 'option_label': 'Beta'}],
            [{'pick': 0, 'do': 'select_custom_option', 'option_label': 'Beta'}],
            [{'pick': 0, 'do': 'open_select'}, {'pick': 0, 'do': 'open_select'}]]:
            self.assertIsNone(q.check({'actions': actions}))

    def test_open_option_labels_exact_unique_and_typed(self):
        c = self.candidate(['select_custom_option'], [{'label': 'Beta', 'value': None, 'disabled': False}])
        q = what_to_do_here('Choose Beta', [c])
        self.assertIsNotNone(q.check({'actions': [{'pick': 0, 'do': 'select_custom_option', 'option_label': 'Beta'}]}))
        for fields in ({'option_label': 'Bet'}, {'option_label': True}, {'option_label': 'Beta', 'value': None},
                       {'option_label': 'Beta', 'value': 'guessed'}, {'value': 'Beta'}, {'option_label': 'Beta', 'key': 'Tab'}):
            self.assertIsNone(q.check({'actions': [{'pick': 0, 'do': 'select_custom_option', **fields}]}))
        c['options'].append(dict(c['options'][0]))
        self.assertIsNone(what_to_do_here('Choose Beta', [c]).check({'actions': [{'pick': 0, 'do': 'select_custom_option', 'option_label': 'Beta'}]}))

    def test_selection_option_effects_cannot_lower_parent_or_hide_command(self):
        from tacto.safety import classify
        for operation in ('select_option', 'select_custom_option'):
            for parent, label, expected in (
                    ('Actions', 'Delete', {'delete'}),
                    ('Actions', 'Send', {'transmit'}),
                    ('Actions', 'Save', {'write_persistent'}),
                    ('Actions', 'Password', {'credential'}),
                    ('Send', 'Normal', {'transmit'}),
                    ('Send', 'Delete', {'transmit', 'delete'}),
                    ('Password', 'Normal', {'credential'}),
                    ('Choice', 'Normal', {'write_ephemeral'})):
                with self.subTest(operation=operation, parent=parent, label=label):
                    action = {'name': operation, 'option_label': label}
                    self.assertEqual(expected, effects_of(action, 'combobox Title: ' + parent)[0])
            self.assertEqual('confirm', classify({'name': operation, 'option_label': 'Delete'}, 'combobox Title: Actions')[0])
            self.assertEqual('confirm', classify({'name': operation, 'option_label': 'Normal'}, 'combobox Title: Send')[0])
            self.assertEqual('handoff', classify({'name': operation, 'option_label': 'Password'}, 'combobox Title: Actions')[0])

    def test_custom_selection_retains_credential_effect(self):
        self.assertEqual({'credential'}, effects_of({'name': 'select_custom_option', 'option_label': 'Beta'}, 'combobox Title: password')[0])


@unittest.skipUnless(AVAILABLE, 'optional playwright dependency not installed')
class CustomSelectLoopTests(unittest.TestCase):
    def setUp(self):
        from tacto.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.driver.goto('data:text/html,' + quote(PAGE))

    def tearDown(self):
        self.driver.close()

    def test_general_checked_model_open_choose_read_promote_and_replay(self):
        from tacto.general import run
        from tacto.promote import candidate_from, to_flow
        from tacto.flow import replay, Flow
        seen = []
        def planner(goal, candidates, done):
            seen.append(candidates)
            q = what_to_do_here(goal, candidates, done)
            if len(done) >= 3:
                return q.check({'done': True})
            label = 'Choice' if len(done) < 2 else 'Result'
            pick = next(i for i,c in enumerate(candidates) if c['label'] == label)
            operation = ('open_select', 'select_custom_option', 'read')[len(done)]
            payload = {'pick': pick, 'do': operation}
            if operation == 'select_custom_option':
                payload['option_label'] = 'Beta'
            return q.check({'actions': [payload]})
        result = run(self.driver, 'page', 'Choose Beta and read Result', planner)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(['Beta'], result.said)
        self.assertEqual(1, self.driver._page.evaluate('window.writes'))
        self.assertIn('open_select', next(c for c in seen[0] if c['label']=='Choice')['can'])
        self.assertNotIn('select_custom_option', next(c for c in seen[0] if c['label']=='Choice')['can'])
        self.assertIn('select_custom_option', next(c for c in seen[1] if c['label']=='Choice')['can'])
        flow = to_flow(candidate_from(result, 'page', 'Choose Beta and read Result'), 'custom-select')
        flow = Flow.from_json(flow.to_json())
        self.assertEqual('open_select', flow.steps[0].action)
        self.assertEqual({'option_label': 'Beta'}, flow.steps[1].params)
        self.assertEqual('Billing', flow.steps[1].selector.ancestor)
        self.driver.goto('data:text/html,' + quote(PAGE))
        replayed = replay(self.driver, flow)
        self.assertTrue(replayed.ok, replayed.log)
        self.assertEqual('Beta', self.driver._page.locator('[aria-label=Result]').input_value())
        self.assertEqual(1, self.driver._page.evaluate('window.writes'))

    def opened(self):
        obs = self.driver.observe()
        index = next(int(i) for i,s in obs['custom_selects'].items() if s['state']=='closed')
        self.assertEqual('acted', self.driver.act(obs['obs_id'], 'page', {'name':'open_select','element_index':index})['type'])
        obs = self.driver.observe()
        index = next(int(i) for i,s in obs['custom_selects'].items() if s['state']=='open')
        return obs,index

    def test_changed_option_or_scope_refuses_without_option_click(self):
        for mutation in ["document.querySelectorAll('[role=option]')[1].textContent='Changed'",
                         "document.querySelector('[role=combobox]').setAttribute('aria-controls','other')"]:
            with self.subTest(mutation=mutation):
                self.driver.goto('data:text/html,' + quote(PAGE))
                obs,index=self.opened()
                self.driver._page.evaluate(mutation)
                result=self.driver.act(obs['obs_id'],'page',{'name':'select_custom_option','element_index':index,'option_label':'Beta'})
                self.assertEqual('not_started',result['effect'])
                self.assertEqual(0,self.driver._page.evaluate('window.writes'))

    def test_general_stops_after_noop_selection_without_retry_or_read(self):
        from tacto.general import run
        self.driver._page.evaluate("() => {window.choose=()=>{window.writes++}}")
        plans = []
        def planner(goal, candidates, done):
            plans.append(len(done))
            self.assertLess(len(done), 2, 'planned again after unverified selection')
            pick = next(i for i,c in enumerate(candidates) if c['label'] == 'Choice')
            payload = {'pick': pick, 'do': 'open_select' if not done else 'select_custom_option'}
            if done:
                payload['option_label'] = 'Beta'
            return what_to_do_here(goal, candidates, done).check({'actions': [payload]})
        result = run(self.driver, 'page', 'Choose Beta', planner)
        self.assertFalse(result.ok)
        self.assertEqual([0, 1], plans)
        self.assertEqual([], result.said)
        self.assertEqual(1, self.driver._page.evaluate('window.writes'))
        self.assertEqual('pending', self.driver._page.locator('[aria-label=Result]').input_value())

    def test_observation_does_not_expose_snapshot_handles(self):
        obs,index=self.opened()
        self.assertNotIn('snapshot_id',obs['custom_selects'][str(index)])
        self.assertIsNone(obs['custom_selects'][str(index)]['options'][1]['value'])

@unittest.skipUnless(AVAILABLE, 'optional playwright dependency not installed')
class BrowserModalScopeTests(unittest.TestCase):
    def test_aria_modal_background_and_multiple_dialogs_are_refused(self):
        from tacto.browser import BrowserDriver
        from tacto.general import blocking_sheet
        driver = BrowserDriver()
        try:
            driver.goto('data:text/html,' + quote('''<button aria-label="Background" onclick="window.writes++">Background</button>
              <div role="alertdialog" aria-modal="true" aria-label="Notice"><button aria-label="Inside" onclick="window.writes++">Inside</button></div>
              <script>window.writes=0</script>'''))
            obs=driver.observe()
            self.assertTrue(blocking_sheet(obs['tree']))
            nodes=driver._latest['page']['nodes']
            background=next(i for i,n in enumerate(nodes) if n['name']=='Background')
            inside=next(i for i,n in enumerate(nodes) if n['name']=='Inside')
            result=driver.act(obs['obs_id'],'page',{'name':'click','element_index':background},expect={'modal_scope':True})
            self.assertEqual('not_started',result['effect'])
            self.assertEqual(0,driver._page.evaluate('window.writes'))
            driver._page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=dialog aria-label=Second><button>Other</button></div>')")
            result=driver.act(obs['obs_id'],'page',{'name':'click','element_index':inside},expect={'modal_scope':True})
            self.assertEqual('not_started',result['effect'])
            self.assertEqual(0,driver._page.evaluate('window.writes'))
        finally:
            driver.close()
