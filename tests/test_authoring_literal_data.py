"""Newly authored recipes distinguish screen data from reusable references."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from tacto.authoring import _attempt, _to_flow, author
from tacto.flow import Flow, pin_params
try:
    from tests._artifacts import requires_playwright
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_playwright


def recipe(title='{{label}}'):
    return {'name':'authored_data','target':'app', 'params':{'field':'Different'},
            'literals':{'label':'{{field}}'},
            'steps':[{'action':'read','selector':{'role':'StaticText','title':title},
                      'captures':[{'name':'result','field':{'role':'StaticText','title':title}}],
                      'safety':{'effects':['read'],'data_classes':['none']}}]}


class AuthoringLiteralTests(unittest.TestCase):
    tree = '0 StaticText Title: {{field}}\n1 StaticText Title: Different'

    def attempt(self, spec, tree=None, narrate=None):
        with patch('tacto.authoring.reach_model', return_value=json.dumps(spec)):
            return _attempt('prompt','read {{field}}','app','test',
                            {'tree':self.tree if tree is None else tree},narrate)

    def test_literal_alias_survives_authoring_storage_binding_and_override(self):
        flow = self.attempt(recipe())
        self.assertEqual(4, json.loads(flow.to_json())['schema'])
        for compact in (False,True):
            loaded = Flow.from_json(flow.to_json(compact=compact))
            bound = loaded.bind(field='Different again')
            self.assertEqual('{{field}}',bound.steps[0].selector.title)
            self.assertEqual((0,'title'),bound.steps[0].selector.resolve(self.tree))
            self.assertEqual('{{field}}',bound.steps[0].captures[0].field.title)
            self.assertEqual('{{field}}',bound.steps[0].binding.title)
            with self.assertRaisesRegex(ValueError,'cannot be overridden'):
                loaded.bind(label='Different')

    def test_unmarked_visible_reference_is_rejected_before_it_changes_target(self):
        spec = recipe('{{field}}');spec.pop('literals')
        with self.assertRaisesRegex(ValueError,'literal/template name collision: field'):
            self.attempt(spec)

    def test_intended_parameter_defaults_are_checked_without_freezing_template(self):
        spec = recipe('{{field}}');spec.pop('literals')
        flow = self.attempt(spec,tree='1 StaticText Title: Different')
        self.assertEqual('Different',flow.bind().steps[0].selector.title)
        self.assertEqual('New',flow.bind(field='New').steps[0].selector.title)
        self.assertEqual('{{field}}',flow.steps[0].selector.title)

    def test_model_cannot_change_observed_target_or_host_binding_identity(self):
        spec=recipe();spec['target']='other-app'
        with self.assertRaisesRegex(ValueError,'target differs'):
            self.attempt(spec)
        spec=recipe()
        spec['steps'][0]['binding']={'bundle_id':'model-origin'}
        with self.assertRaisesRegex(ValueError,'binding.bundle_id contradicts'):
            _to_flow(spec,target_identity='host-origin')

    def test_missing_parameters_and_runtime_values_are_explicitly_unchecked(self):
        spec = recipe('{{field}}');spec.pop('literals');spec['params']['field']=None
        messages=[]
        flow = self.attempt(spec,tree='0 StaticText Title: Source',narrate=messages.append)
        self.assertTrue(any('확인할 수 없어요' in message for message in messages))
        self.assertEqual('Later',flow.bind(field='Later').steps[0].selector.title)
        spec.pop('params');spec['steps'][0]['selector']['title']='{{result}}'
        self.attempt(spec,tree='0 StaticText Title: Source')

    def test_literals_apply_to_payload_checks_and_contract_without_touching_data_maps(self):
        spec = recipe()
        spec['tables']={'opaque':{'literal':'{{kept}}'}}
        spec['params']['opaque']={'literal':'{{kept}}'}
        step=spec['steps'][0]
        step['action']='set_value';step['params']={'value':'{{label}}'}
        step['postcondition']={'field':deepcopy(step['selector']),'equals':'{{label}}'}
        step['safety']['effects']=['write_ephemeral']
        step['safety']['resource']={'object_identity':'{{label}}'}
        spec['_intent']='literal {{field}} requested'
        flow = _to_flow(spec)
        bound = flow.bind()
        current = bound.steps[0]
        self.assertEqual('{{field}}',current.params['value'])
        self.assertEqual('{{field}}',current.postcondition.equals)
        self.assertEqual('{{field}}',current.safety.resource.object_identity)
        self.assertEqual('literal {{field}} requested',current.safety.evidence[0].value)
        self.assertEqual(pin_params(current.params),current.binding.params_digest)
        self.assertEqual(spec['tables'],flow.tables)
        self.assertEqual(spec['params']['opaque'],flow.params['opaque'])

    def test_literal_names_cannot_alias_inputs_or_captures(self):
        for name in ('field','result'):
            spec=recipe();spec['literals'][name]='some data'
            with self.assertRaisesRegex(ValueError,'literal names conflict'):
                _to_flow(spec)

    def test_invalid_literal_map_is_a_short_authoring_error(self):
        for invalid in (None, [], 'text'):
            spec=recipe();spec['literals']=invalid
            with self.assertRaisesRegex(ValueError,'literals must be an object'):
                _to_flow(spec)

    def test_required_derived_selector_is_deferred_without_freezing_its_value(self):
        spec=recipe('{{answer}}');spec.pop('literals')
        spec['params']={'number':None};spec['derived']={'answer':'{{number}} + 1'}
        flow=self.attempt(spec,tree='0 StaticText Title: Source')
        self.assertEqual('3',flow.bind(number=2).steps[0].selector.title)

    @requires_playwright

    def test_headless_browser_authored_literal_read_survives_saved_flow_replay(self):
        import tempfile
        from tacto.browser import BrowserDriver
        from tacto.flow import replay
        from tacto.store import FlowStore
        driver=BrowserDriver()
        try:
            driver._page.set_content('<label>{{field}}<input value="correct" readonly></label>'
                                     '<label>Different<input value="wrong" readonly></label>')
            observation=driver.observe('page','tree_full')
            spec=recipe();spec['target']='page'
            spec['steps'][0]['selector']['role']='textbox'
            spec['steps'][0]['captures'][0]['field']['role']='textbox'
            with patch('tacto.authoring.reach_model',return_value=json.dumps(spec)):
                flow=author('read literal {{field}}',driver,target='page',provider='test')
            with tempfile.TemporaryDirectory() as directory:
                store=FlowStore(directory);store.save(flow)
                result=replay(driver,store.load(flow.name).bind(field='Different'))
                self.assertTrue(result.ok,result.log)
                self.assertEqual('correct',result.log[-1]['captured']['result'])
        finally:
            driver.close()

    def test_literal_guide_is_loaded_only_when_braces_are_present(self):
        class Driver:
            capabilities=()
            def __init__(self,tree):self.tree=tree
            def observe(self,*args,**kwargs):return {'type':'observation','tree':self.tree}
        for braces in (False,True):
            spec=recipe() if braces else recipe('Normal')
            if not braces:
                spec.pop('literals');spec.pop('params')
            tree=self.tree if braces else '0 StaticText Title: Normal'
            with patch('tacto.authoring.reach_model',return_value=json.dumps(spec)) as model:
                author('read data',Driver(tree),target='app')
            self.assertEqual('"literals":{"label"' in model.call_args.args[0],braces)

    def test_default_author_repairs_a_rejected_literal_without_executing_ui(self):
        class Driver:
            capabilities=()
            def observe(self,*args,**kwargs):return {'type':'observation','tree':AuthoringLiteralTests.tree}
            def act(self,*args,**kwargs):raise AssertionError('compile must not act')
        bad=recipe('{{field}}');bad.pop('literals')
        with patch('tacto.authoring.reach_model',side_effect=[json.dumps(bad),json.dumps(recipe())]) as model, \
             patch('tacto.authoring._verify') as verify:
            flow=author('read {{field}}',Driver(),target='app',verify=False)
        self.assertEqual(model.call_count,2)
        self.assertIn('literal/template name collision',model.call_args.args[0])
        self.assertEqual('{{field}}',flow.bind().steps[0].selector.title)
        verify.assert_not_called()

    def test_zero_corrections_and_invalid_rounds_cannot_make_extra_calls(self):
        class Driver:
            capabilities=()
            def observe(self,*args,**kwargs):return {'type':'observation','tree':AuthoringLiteralTests.tree}
        bad=recipe('{{field}}');bad.pop('literals')
        with patch('tacto.authoring.reach_model',return_value=json.dumps(bad)) as model:
            with self.assertRaisesRegex(ValueError,'literal/template name collision'):
                author('read {{field}}',Driver(),target='app',rounds=0)
            self.assertEqual(model.call_count,1)
        for invalid in (True,-1,1.5):
            with patch('tacto.authoring.reach_model') as model:
                with self.assertRaisesRegex(ValueError,'rounds'):
                    author('read',Driver(),target='app',rounds=invalid)
                model.assert_not_called()

    def test_corrections_share_local_deadline_and_stop_before_next_request(self):
        from tacto.model_budget import model_deadline, remaining_model_seconds
        class Driver:
            capabilities=()
            def observe(self,*args,**kwargs):return {'type':'observation','tree':AuthoringLiteralTests.tree}
        bad=recipe('{{field}}');bad.pop('literals')
        clock=[0.0];remaining=[]
        def answer(*args,**kwargs):
            remaining.append(remaining_model_seconds())
            clock[0]=11
            return json.dumps(bad)
        with patch('tacto.authoring.time.monotonic',side_effect=lambda:clock[0]), \
             model_deadline(10), patch('tacto.authoring.reach_model',side_effect=answer) as model:
            with self.assertRaises(TimeoutError):
                author('read {{field}}',Driver(),target='app',rounds=1)
        self.assertEqual(model.call_count,1)
        self.assertEqual(remaining,[10])

    def test_nested_branch_provenance_is_data_but_opaque_parameter_values_are_references(self):
        from tacto.authoring import _check_authored_literal_ambiguity
        from tacto.flow import Step, Selector, literal_text
        from tacto.contract import Evidence
        from tests.test_runtime_contract import contract
        safety=contract();safety.evidence=[Evidence('user_intent','asked','read {{field}} literally')]
        leaf=Step('read',Selector(title=literal_text('{{field}}')),safety=safety)
        for depth in (1,2):
            step=leaf
            for index in range(depth):
                step=Step('decide',params={'question':'Continue?','choices':['yes'],
                    'into':'decision'+str(index),'branches':{'yes':[step]}},safety=contract())
            flow=Flow('nested','app',steps=[step])
            _check_authored_literal_ambiguity(flow,self.tree)
        opaque=Flow('opaque','app',params={'field':'Different'},steps=[Step('read',params={
            'branches':{'yes':[{'safety':{'evidence':[{'value':'{{field}}'}]}}]}},safety=contract())])
        with self.assertRaisesRegex(ValueError,'literal/template name collision'):
            _check_authored_literal_ambiguity(opaque,self.tree)
