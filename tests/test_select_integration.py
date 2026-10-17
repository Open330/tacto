"""Selection is planned from observed options and survives recipe promotion."""
import unittest
from tacto.ask import what_to_do_here

try:
    import playwright.sync_api
    WEB = True
except ImportError:
    WEB = False


class SelectPlanner(unittest.TestCase):
    def test_replay_requires_option_label_before_any_observation(self):
        from unittest.mock import Mock
        from tacto.flow import Flow, Step, replay
        for operation in ('select_option', 'select_custom_option'):
            flow = Flow(name='missing-label', target='page',
                        steps=[Step(action=operation, params={'value': 'opaque-value'})])
            driver = Mock()
            result = replay(driver, flow)
            self.assertFalse(result.ok)
            self.assertEqual('selection_label_required', result.log[0]['error'])
            driver.observe.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'option_label'):
                Flow.from_json(flow.to_json())

    def test_promotion_rejects_changed_origin_and_unverified_focus(self):
        from tacto.general import GeneralResult, Visit
        from tacto.promote import candidate_from, to_flow, gate
        action = {'do':'press_key','key':'Tab','role':'TextField','attr':'Title',
                  'label':'name','match':'name','line':'1 TextField Title: name'}
        result = GeneralResult(ok=True,verified=True,
            visits=[Visit(screen='one',actions=[action])],
            target_identity='https://first.example', identity_changed=True)
        self.assertIsNone(candidate_from(result,'page','navigate'))
        result.identity_changed = False
        candidate = candidate_from(result,'page','navigate')
        self.assertTrue(any('포커스' in why for why in gate(candidate,to_flow(candidate,'tab'),'navigate')))

    def test_exact_observed_option_and_end_of_batch(self):
        c = {'label':'Delivery', 'can':['select_option','read'], 'options':[
            {'value':'fast','label':'Express','disabled':False},
            {'value':'no','label':'Unavailable','disabled':True}]}
        q = what_to_do_here('Express delivery', [c])
        self.assertIn('Express', q.prompt)
        action = {'pick':0,'do':'select_option','value':'fast'}
        self.assertIsNotNone(q.check({'actions':[action]}))
        for value in ('Express','no','missing'):
            self.assertIsNone(q.check({'actions':[{**action,'value':value}]}))
        self.assertIsNone(q.check({'actions':[action,{'pick':0,'do':'read'}]}))

    @unittest.skipUnless(WEB, 'optional playwright dependency not installed')
    def test_cross_origin_run_cannot_be_promoted_with_final_origin(self):
        from tacto.browser import BrowserDriver
        from tacto.general import run
        from tacto.promote import candidate_from
        d = BrowserDriver()
        self.addCleanup(d.close)
        d._page.route('**/*', lambda route: route.fulfill(content_type='text/html', body=(
            '<a href="https://second.test/">Next</a>' if 'first.test' in route.request.url
            else '<input aria-label="result" readonly value="finished">')))
        d.goto('https://first.test/')
        def here(goal, cs, done):
            if len(done) >= 2:
                return {'done':True}
            label, action = ('Next','click') if not done else ('result','read')
            return {'actions':[{'candidate':next(c for c in cs if c['label']==label),'do':action}]}
        result = run(d,'page','read after Next',here)
        self.assertTrue(result.ok,result.why)
        self.assertTrue(result.identity_changed)
        self.assertEqual('https://first.test',result.target_identity)
        self.assertIsNone(candidate_from(result,'page','read after Next',target_identity=d.identity()))

    @unittest.skipUnless(WEB, 'optional playwright dependency not installed')
    def test_general_select_reobserves_and_promoted_flow_replays(self):
        from tacto.browser import BrowserDriver
        from tacto.general import run
        from tacto.promote import candidate_from, to_flow
        from tacto.flow import replay
        d = BrowserDriver()
        self.addCleanup(d.close)
        d.goto("data:text/html,<select aria-label='Delivery'><option value='slow'>Standard</option><option value='fast'>Express</option></select>")
        def here(goal, cands, done):
            if len(done) >= 2:
                return {'done':True}
            index = next(i for i,c in enumerate(cands) if c['label']=='Delivery')
            action = {'pick':index,'do':'read'} if done else {
                'pick':index,'do':'select_option','value':'fast'}
            return what_to_do_here(goal,cands,done).check({'actions':[action]})
        result = run(d,'page','Express delivery',here)
        self.assertTrue(result.ok, result.why)
        self.assertEqual('fast', d._page.locator('select').input_value())
        self.assertIn('Express', result.said)
        candidate = candidate_from(result, 'page', 'Express delivery', target_identity=d.identity('page'))
        flow = to_flow(candidate, "delivery_choice")
        self.assertEqual('fast', flow.steps[0].params['value'])
        self.assertEqual('Express', flow.steps[0].params['option_label'])
        d._page.locator('select').select_option('slow')
        played = replay(d, flow)
        self.assertTrue(played.ok, played.log)
        self.assertEqual('fast', d._page.locator('select').input_value())
        d._page.locator('select').select_option('slow')
        d._page.locator('option').nth(1).evaluate("el => el.label = 'Different service'")
        self.assertFalse(replay(d, flow).ok)
        self.assertEqual('slow', d._page.locator('select').input_value())
