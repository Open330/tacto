"""Optional local-page integration checks, with no external website or GUI."""
import unittest

try:
    import playwright.sync_api
    AVAILABLE = True
except ImportError:
    AVAILABLE = False


@unittest.skipUnless(AVAILABLE, "optional playwright dependency not installed")
class BrowserContract(unittest.TestCase):
    def setUp(self):
        from nlc.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.addCleanup(self.driver.close)

    def test_select_option_reads_back_and_refuses_changed_options(self):
        d = self.driver
        d.goto("data:text/html,<select aria-label='Delivery'><option value='slow'>Standard</option><option value='fast'>Express</option><option value='no' disabled>Unavailable</option></select>")
        obs = d.observe()
        idx = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='combobox')
        action = {'name':'select_option', 'element_index':idx, 'value':'fast'}
        result = d.act(obs['obs_id'], 'page', action)
        self.assertEqual('acted', result['type'], result)
        self.assertEqual('fast', d._page.locator('select').input_value())
        denied = d.act(obs['obs_id'], 'page', {**action, 'value':'no'})
        self.assertEqual(('invalid_option','not_started'), (denied['error'],denied['effect']))
        d._page.locator('option').nth(1).evaluate("el => el.label = 'Changed destination'")
        denied = d.act(obs['obs_id'], 'page', action)
        self.assertEqual(('target_changed','not_started'), (denied['error'],denied['effect']))

    def test_select_option_refuses_ambiguous_values_and_disabled_control(self):
        d = self.driver
        d.goto("data:text/html,<select aria-label='Account'><option value='a'>First</option><option value='a'>Second</option><option value='b'>Third</option></select>")
        obs = d.observe()
        idx = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='combobox')
        action = {'name':'select_option', 'element_index':idx, 'value':'a'}
        denied = d.act(obs['obs_id'], 'page', action)
        self.assertEqual('not_started',denied['effect'])
        d._page.locator('select').evaluate('el => el.disabled = true')
        denied = d.act(obs['obs_id'], 'page', {**action,'value':'b'})
        self.assertEqual(('target_changed','not_started'), (denied['error'],denied['effect']))

    def test_browser_key_api_cannot_submit_or_invoke_arbitrary_chords(self):
        d = self.driver
        d.goto("data:text/html,<form onsubmit='window.submitted=true;return false'><input aria-label='first'><input aria-label='second'></form>")
        obs = d.observe()
        d._page.locator('input').first.focus()
        for key in ('Return','Enter','Control+a','Meta+q',None,[]):
            result = d.act(obs['obs_id'],'page',{'name':'press_key','key':key})
            self.assertEqual(('unsupported_key','not_started'), (result['error'],result['effect']))
        self.assertFalse(d._page.evaluate('!!window.submitted'))
        refused = d.act(obs['obs_id'],'page',{'name':'press_key','key':'Tab'},expect={'role':'textbox'})
        self.assertEqual('not_started',refused['effect'])
        refused = d.act(obs['obs_id'],'page',{'name':'press_key','key':'Tab','element_index':0})
        self.assertEqual('not_started',refused['effect'])
        result = d.act(obs['obs_id'],'page',{'name':'press_key','key':'Tab'})
        self.assertEqual('acted',result['type'])
        self.assertEqual('second',d._page.evaluate("document.activeElement.getAttribute('aria-label')"))

    def test_duplicate_names_target_the_observed_dom_node(self):
        d = self.driver
        d.goto("data:text/html,<section aria-label='first'><button onclick=\"document.body.dataset.clicked='first'\">Save</button></section><section aria-label='second'><button onclick=\"document.body.dataset.clicked='second'\">Save</button></section>")
        obs = d.observe()
        targets = [i for i,n in enumerate(d._latest['page']['nodes']) if n['role'] == 'button']
        self.assertEqual(2, len(targets))
        result = d.act(obs['obs_id'], 'page', {'name':'click', 'element_index':targets[1]}, expect={'role':'button','label':'Save'})
        self.assertEqual('acted', result['type'], result)
        self.assertEqual('second', d._page.evaluate('document.body.dataset.clicked'))
        self.assertEqual([],d._page.evaluate("Object.getOwnPropertyNames(globalThis).filter(k => k.startsWith('__nlc_node_'))"))

    def test_named_regions_keep_their_own_same_named_inputs(self):
        from nlc.general import candidates
        from nlc.flow import Selector
        d = self.driver
        d.goto("data:text/html,<section aria-label='billing'><input aria-label='name'></section><section aria-label='shipping'><input aria-label='name'></section><button>Next</button>")
        obs = d.observe()
        fields = [c for c in candidates(obs['tree']) if c['role']=='textbox']
        self.assertEqual(['billing','shipping'],[c['container'] for c in fields])
        selected, _ = Selector(role='textbox',title='name',ancestor='billing').resolve(obs['tree'])
        result = d.act(obs['obs_id'],'page',{'name':'set_value','element_index':selected,'value':'Alice'})
        self.assertEqual('acted',result['type'],result)
        self.assertEqual('Alice',d._page.locator('section').nth(0).locator('input').input_value())
        self.assertEqual('',d._page.locator('section').nth(1).locator('input').input_value())

    def test_value_probe_and_tracing(self):
        from nlc.trace import TracingDriver
        d = self.driver
        d.goto("data:text/html,<input aria-label='name'>")
        traced = TracingDriver(d)
        obs = traced.observe('page', require_window=False)
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='textbox')
        r = traced.act(obs['obs_id'],'page',{'name':'set_value','element_index':index,'value':'hello'},expect={'role':'textbox','label':'name'})
        self.assertEqual('acted',r['type'],r)
        self.assertIn('Value: hello',traced.probe(obs['obs_id'],'page',index)['line'])
        self.assertIn('Value: hello',traced.observe('page')['tree'])

    def test_empty_value_is_observed_but_missing_value_stays_unknown(self):
        d = self.driver
        d.goto("data:text/html,<input aria-label='empty'><div role='textbox' aria-label='unknown'></div>")
        obs = d.observe()
        nodes = d._latest['page']['nodes']
        empty = next(i for i,n in enumerate(nodes) if n['name']=='empty')
        unknown = next(i for i,n in enumerate(nodes) if n['name']=='unknown')
        self.assertEqual('',nodes[empty]['value'])
        self.assertIsNone(nodes[unknown]['value'])
        self.assertEqual('',d.probe(obs['obs_id'],'page',empty)['node']['value'])
        self.assertIsNone(d.probe(obs['obs_id'],'page',unknown)['node']['value'])
        d._page.locator('input').fill('changed')
        self.assertEqual('changed',d.probe(obs['obs_id'],'page',empty)['node']['value'])
        d._page.locator('input').fill('')
        self.assertEqual('',d.probe(obs['obs_id'],'page',empty)['node']['value'])

    def test_type_text_inserts_without_replacing_existing_content(self):
        d = self.driver
        d.goto("data:text/html,<input aria-label='name' value='abcd'>")
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='textbox')
        d._page.locator('input').evaluate('el => {el.focus(); el.setSelectionRange(2,2)}')
        result = d.act(obs['obs_id'],'page',{'name':'type_text','element_index':index,'text':'XY'})
        self.assertEqual('acted',result['type'],result)
        self.assertEqual('abXYcd',d._page.locator('input').input_value())

    def test_scroll_respects_direction_and_page_count(self):
        d = self.driver
        d.goto("data:text/html,<div role='region' aria-label='list' style='overflow:auto;width:100px;height:100px'><div style='width:1000px;height:1000px'>content</div></div>")
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='region')
        for direction, pages, expected in [('down',2,[0,200]),('up',1,[0,100]),('right',3,[300,100]),('left',1,[200,100])]:
            result = d.act(obs['obs_id'],'page',{'name':'scroll','element_index':index,'direction':direction,'pages':pages})
            self.assertEqual('acted',result['type'],result)
            self.assertEqual(expected,d._page.locator('[role=region]').evaluate('el => [el.scrollLeft,el.scrollTop]'))
        result = d.act(obs['obs_id'],'page',{'name':'scroll','element_index':index,'pages':-1})
        self.assertEqual('not_started',result.get('effect'))

    def test_wait_refuses_another_target(self):
        self.driver.goto('data:text/html,<p>ready</p>')
        result = self.driver.wait_until('another-app',lambda tree: True,timeout_ms=100)
        self.assertEqual('error',result['type'])

    def test_invalid_read_index_and_unknown_action_are_not_started(self):
        d = self.driver
        d.goto('data:text/html,<button>Continue</button>')
        obs = d.observe()
        for action in [{'name':'read','element_index':-1}, {'name':'read','element_index':True}, {'name':'unsupported'}]:
            result = d.act(obs['obs_id'],'page',action)
            self.assertEqual('error',result['type'],result)
            self.assertEqual('not_started',result.get('effect'))

    def test_secondary_action_only_dispatches_supported_menu_operation(self):
        d = self.driver
        d.goto("data:text/html,<button oncontextmenu=\"event.preventDefault();document.body.dataset.menu='shown'\">Menu</button>")
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='button')
        for unsupported in [None,'AXPress','AXDelete']:
            result = d.act(obs['obs_id'],'page',{'name':'secondary_action','element_index':index,'action':unsupported})
            self.assertEqual('not_started',result.get('effect'))
            self.assertIsNone(d._page.evaluate('document.body.dataset.menu'))
        result = d.act(obs['obs_id'],'page',{'name':'secondary_action','element_index':index,'action':'AXShowMenu'})
        self.assertEqual('acted',result['type'],result)
        self.assertEqual('shown',d._page.evaluate('document.body.dataset.menu'))

    def test_dispose_failure_does_not_replace_action_result(self):
        from unittest.mock import Mock, patch
        d = self.driver
        d.goto('data:text/html,<button>Continue</button>')
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='button')
        handle = Mock()
        handle.dispose.side_effect = RuntimeError('page closed during cleanup')
        with patch.object(d,'_locator',return_value=handle):
            result = d.act(obs['obs_id'],'page',{'name':'click','element_index':index})
        self.assertEqual('acted',result['type'],result)

    def test_old_observation_cannot_act_after_navigation(self):
        d = self.driver
        d.goto("data:text/html,<button>Next</button>")
        obs = d.observe()
        d._page.goto("data:text/html,<button>Different page</button>")
        result = d.act(obs['obs_id'],'page',{'name':'click','element_index':0})
        self.assertEqual('stale_observation',result.get('error'))

    def test_removed_node_does_not_retarget_same_named_replacement(self):
        d = self.driver
        d.goto("data:text/html,<button>Continue</button>")
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='button')
        d._page.evaluate("document.querySelector('button').outerHTML = '<button onclick=\"document.body.dataset.clicked=1\">Continue</button>'")
        result = d.act(obs['obs_id'],'page',{'name':'click','element_index':index})
        self.assertEqual('error',result['type'])
        self.assertEqual('not_started',result.get('effect'))
        self.assertIsNone(d._page.evaluate('document.body.dataset.clicked'))

    def test_disabled_or_changed_label_is_refused_before_dispatch(self):
        d = self.driver
        d.goto("data:text/html,<button onclick=\"document.body.dataset.clicked=1\">Continue</button>")
        obs = d.observe()
        index = next(i for i,n in enumerate(d._latest['page']['nodes']) if n['role']=='button')
        action = {'name':'click','element_index':index}
        d._page.evaluate("document.querySelector('button').disabled=true")
        result = d.act(obs['obs_id'],'page',action,
                       expect={'role':'button','label':'Continue','attribute':'Title','enabled':True})
        self.assertEqual('not_started',result.get('effect'))
        d._page.evaluate("document.querySelector('button').disabled=false; document.querySelector('button').textContent='Send'")
        result = d.act(obs['obs_id'],'page',action,
                       expect={'role':'button','label':'Continue','attribute':'Title','enabled':True})
        self.assertEqual('target_changed',result.get('error'))
        self.assertIsNone(d._page.evaluate('document.body.dataset.clicked'))

    def test_full_flow_replay_with_trace_checks_input_value(self):
        from nlc.contract import Resource, SafetyContract
        from nlc.flow import Flow, Postcondition, Selector, Step, replay
        from nlc.trace import TraceDriver, TracingDriver
        d = self.driver
        d.goto("data:text/html,<input aria-label='name'>")
        selector = Selector(role='textbox', title='name')
        flow = Flow('fill', 'page', [Step('set_value', selector, params={'value':'hello'},
                    safety=SafetyContract(effects=['write_ephemeral'],
                                          resource=Resource(scope='entry'),reversibility='reversible'),
                    postcondition=Postcondition(field=selector,equals='hello',attribute='Value'))])
        traced = TracingDriver(d)
        result = replay(traced,flow)
        self.assertTrue(result.ok,result.log)
        result = replay(TraceDriver(traced.trace),flow)
        self.assertTrue(result.ok,result.log)

    def test_general_batch_reads_the_value_it_just_typed(self):
        from nlc.general import run
        d = self.driver
        d.goto("data:text/html,<input aria-label='name'>")
        def plan(goal, candidates, done):
            if done:
                return {'done':True}
            field = next(c for c in candidates if c['role']=='textbox')
            return {'actions':[{'do':'set_value','value':'hello','candidate':field},
                               {'do':'read','candidate':field}]}
        result = run(d,'page','type and read',plan)
        self.assertTrue(result.ok,result.why)
        self.assertEqual(['hello'],result.said)

    def test_upload_rejects_negative_index_before_opening_chooser(self):
        d = self.driver
        d.goto("data:text/html,<input type='file' aria-label='attachment'>")
        obs = d.observe()
        result = d.upload(obs['obs_id'],'page',-1,[],approved=True)
        self.assertEqual('target_missing',result.get('error'))
        self.assertEqual('not_started',result.get('effect'))

    def test_local_file_chooser_uses_the_observed_input(self):
        import tempfile
        from pathlib import Path
        d = self.driver
        d.goto("data:text/html,<input type='file' aria-label='attachment'><input type='file' aria-label='attachment'>")
        obs = d.observe()
        indices = [i for i,n in enumerate(d._latest['page']['nodes']) if n['name']=='attachment']
        self.assertEqual(2,len(indices))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.txt'
            path.write_text('local browser contract test')
            result = d.upload(obs['obs_id'],'page',indices[1],[str(path)],approved=True)
        self.assertEqual('acted',result['type'],result)
        self.assertEqual([0,1],d._page.evaluate("[...document.querySelectorAll('input')].map(e => e.files.length)"))
