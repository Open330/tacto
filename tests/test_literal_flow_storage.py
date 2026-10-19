"""Observed text stays data through promotion, JSON, binding, and replay."""
from copy import deepcopy
import json
import tempfile
from pathlib import Path
import unittest
from tacto.flow import (Capture, Flow, Selector, Step, Postcondition, _RuntimeReference,
                        _RuntimeText, literal_text, replay)
from tacto.promote import Candidate, to_flow, _basis_read, _menu_check, with_decisions
from tacto.contract import Envelope
from tacto.repair_packet import patch_selector
from tests.test_runtime_contract import App, contract


class LiteralStorageTests(unittest.TestCase):
    def assert_roundtrip(self,flow,verify):
        for compact in (False,True):
            stored=flow.to_json(compact=compact)
            loaded=Flow.from_json(stored)
            verify(loaded.bind())
            # Export the bound flow again; repeated round trips retain meaning.
            verify(Flow.from_json(loaded.bind().to_json(compact=compact)).bind())

    def test_random_mixed_spans_roundtrip_like_one_runtime_render(self):
        import random
        from tacto.flow import _render_step
        rng=random.Random(20261018)
        atoms=['{','}','{{','}}','x',' ','{{unknown}}','한글']
        for iteration in range(100):
            parts=[]
            for _ in range(rng.randrange(1,8)):
                parts.append(_RuntimeReference('live') if rng.randrange(3)==0 else rng.choice(atoms))
            text=''.join(part.text() if isinstance(part,_RuntimeReference) else part for part in parts)
            value=_RuntimeText(text,parts)
            step=Step('set_value',Selector(title='Target'),params={'value':value},safety=contract(('write_ephemeral',)))
            expected=_render_step(step,{'live':'actual {{data}}'}).params['value']
            flow=Flow('random','app',steps=[Step('read',Selector(title='Source'),safety=contract(),
                      captures=[Capture('live',Selector(title='Source'))]),step])
            for compact in (False,True):
                loaded=Flow.from_json(flow.to_json(compact=compact)).bind()
                actual=_render_step(loaded.steps[1],{'live':'actual {{data}}'}).params['value']
                self.assertEqual(expected,actual,(iteration,parts))

    def test_menu_payload_and_consent_binding_digest_survive_literal_storage(self):
        from tacto.flow import pin_params
        c=Candidate(goal='menu',target='app',verified=True,envelope=Envelope.minimal(),actions=[
            {'do':'menu','menu_path':['Tools','{{command}}'],'label':'{{command}}','line':'MenuItem',
             'effect':{'role':'Button','attr':'Title','match':'{{status}}','attribute':'Value','equals':'{{done}}'}}])
        flow=to_flow(c,'menu-data')
        def verify(bound):
            step=bound.steps[0]
            self.assertEqual(['Tools','{{command}}'],step.params['path'])
            self.assertEqual(pin_params(step.params),step.binding.params_digest)
            self.assertEqual('{{status}}',step.postcondition.field.title)
            self.assertEqual('{{done}}',step.postcondition.equals)
        self.assert_roundtrip(flow,verify)

    def test_stored_literal_data_is_not_an_overridable_task_parameter(self):
        flow=Flow('readonly','app',steps=[Step('read',Selector(title=literal_text('{{field}}')),safety=contract())])
        stored=json.loads(flow.to_json())
        loaded=Flow.from_json(json.dumps(stored))
        name=next(iter(loaded.literal_params))
        self.assertNotIn(name,loaded.params)
        with self.assertRaisesRegex(ValueError,'cannot be overridden'):
            loaded.bind(**{name:'different control'})
        self.assertEqual('{{field}}',loaded.bind().steps[0].selector.title)
        stored['schema']=3
        with self.assertRaisesRegex(ValueError,'schema 4'):
            Flow.from_json(json.dumps(stored))

    def test_literal_schema_survives_grouped_checks_and_repeat_serialization(self):
        post=Postcondition(field=Selector(title=literal_text('{{field}}')),equals='expected',
                           all_of=[Postcondition(contains='Window')])
        flow=Flow('schema','app',steps=[Step('read',Selector(title=literal_text('{{field}}')),
                    postcondition=post,safety=contract())])
        saved=flow.to_json()
        self.assertEqual(4,json.loads(saved)['schema'])
        loaded=Flow.from_json(saved)
        self.assertEqual(4,loaded.schema)
        self.assertEqual(4,json.loads(loaded.to_json())['schema'])
        self.assertEqual('{{field}}',loaded.bind().steps[0].selector.title)

    def test_literal_and_authored_reference_spans_remain_distinct(self):
        text=_RuntimeText('prefix {{secret}} / {{live}}',('prefix {{secret}} / ',_RuntimeReference('live')))
        flow=Flow(name='mix',target='app',params={'_tacto_data_1':'user-owned'},steps=[
            Step('read',Selector(title='Source'),captures=[Capture('live',Selector(title='Source'))],safety=contract()),
            Step('set_value',Selector(title='Target'),params={'value':text},safety=contract(('write_ephemeral',)))])
        stored=json.loads(flow.to_json())
        self.assertEqual(4,stored['schema'])
        self.assertEqual('user-owned',stored['params']['_tacto_data_1'])
        self.assertEqual('prefix {{secret}} / ',stored['literal_params']['_tacto_data_2'])
        from tacto.flow import _render_step
        self.assert_roundtrip(flow,lambda bound:self.assertEqual('prefix {{secret}} / actual',
            _render_step(bound.steps[1],{'live':'actual'}).params['value']))

    def test_adjacent_literal_spans_and_unknown_reference_names_do_not_collide(self):
        text=_RuntimeText('{{unknown}}',('{','{unknown','}}'))
        flow=Flow('literal','app',steps=[Step('set_value',Selector(title='Target'),
            params={'value':text},safety=contract(('write_ephemeral',)))])
        self.assert_roundtrip(flow,lambda bound:self.assertEqual('{{unknown}}',bound.steps[0].params['value']))
        self.assertEqual({},flow.params)

    def test_promoted_ui_fields_params_and_bindings_are_literal(self):
        action={'do':'set_value','role':'TextField','attr':'Title','match':'{{secret}}',
            'container':'{{scope}}','label':'Set {{secret}}','value':'payload {{secret}}',
            'line':'1 TextField Title: {{secret}}'}
        c=Candidate(goal='copy text',target='app',actions=[action],verified=True,envelope=Envelope.minimal())
        flow=to_flow(c,'observed')
        def verify(bound):
            step=bound.steps[0]
            self.assertEqual('{{secret}}',step.selector.title)
            self.assertEqual('{{scope}}',step.selector.ancestor)
            self.assertEqual('payload {{secret}}',step.params['value'])
            self.assertEqual('{{secret}}',step.binding.title)
            self.assertEqual('{{scope}}',step.binding.ancestor)
            from tacto.flow import pin_params
            self.assertEqual(pin_params(step.params),step.binding.params_digest)
        self.assert_roundtrip(flow,verify)

    def test_promoted_read_really_resolves_literal_control_without_capture_substitution(self):
        action={'do':'read','role':'StaticText','attr':'Title','match':'{{secret}}','label':'{{secret}}',
                'container':'','line':'1 StaticText Title: {{secret}}','read_attribute':'Value'}
        c=Candidate(goal='read',target='app',actions=[action],verified=True,envelope=Envelope.minimal())
        flow=to_flow(c,'read literal')
        class Screen(App):
            @property
            def tree(self):return '0 Window Title: app\n  1 StaticText Title: {{secret}}, Value: visible-result'
        for current in (flow.bind(),Flow.from_json(flow.to_json()).bind()):
            result=replay(Screen(),current,_seed={'secret':'DO-NOT-SUBSTITUTE'})
            self.assertTrue(result.ok,result.log)
            self.assertEqual('visible-result',result.log[-1]['captured']['결과'])

    def test_menu_result_and_decision_basis_are_data(self):
        post=_menu_check({'effect':{'role':'Button','attr':'Title','match':'{{button}}',
            'attribute':'Value','equals':'{{result}}'}})
        basis={'role':'TextField','attr':'Title','match':'{{basis}}','container':'{{container}}','label':'{{label}}'}
        read=_basis_read(basis,'source')
        flow=Flow('read','app',steps=[read,Step('read',Selector(title='result'),postcondition=post,safety=contract())])
        def verify(bound):
            self.assertEqual('{{basis}}',bound.steps[0].selector.title)
            self.assertEqual('{{container}}',bound.steps[0].captures[0].field.ancestor)
            self.assertEqual('{{button}}',bound.steps[1].postcondition.field.title)
            self.assertEqual('{{result}}',bound.steps[1].postcondition.equals)
        self.assert_roundtrip(flow,verify)

    def test_offline_selector_patch_roundtrip_keeps_observed_label_literal(self):
        flow=Flow('patch','app',steps=[Step('click',Selector(title='old'),safety=contract(('write_ephemeral',)))])
        packet={'schema':1,'target':'app','step_index':0,'effect':'not_started','error':'target_missing',
                'action':'click','selector':json.loads(flow.to_json())['steps'][0]['selector'],
                'candidates':[{'role':'Button','title':'{{unknown}}'}]}
        with tempfile.TemporaryDirectory() as tmp:
            source,context,out=[Path(tmp)/name for name in ('flow.json','packet.json','out.json')]
            source.write_text(flow.to_json());context.write_text(json.dumps(packet))
            patch_selector(source,context,{'role':'Button','title':'{{unknown}}'},out)
            self.assertEqual('{{unknown}}',Flow.from_json(out.read_text()).bind().steps[0].selector.title)


    def test_decision_question_choices_basis_and_dynamic_selection_keep_boundaries(self):
        c=Candidate(goal='choose',target='app',verified=True,envelope=Envelope.minimal(),actions=[
            {'do':'click','role':'Button','attr':'Title','match':'{{secret}}','label':'{{secret}}','container':''}])
        flow=to_flow(c,'choice')
        decisions=[{'step':0,'question':'Question {{secret}}?', 'choices':['{{secret}}','Other'],
                    'basis':{'role':'TextField','attr':'Title','match':'{{basis}}','container':'','label':'{{basis}}'}}]
        decided=with_decisions(flow,decisions)
        from tacto.flow import _render_step
        def verify(bound):
            self.assertEqual('{{basis}}',bound.steps[0].selector.title)
            self.assertEqual('Question {{secret}}?',bound.steps[1].params['question'])
            self.assertEqual(['{{secret}}','Other'],bound.steps[1].params['choices'])
            effective=_render_step(bound.steps[2],{decisions[0]['into']:'{{secret}}','secret':'wrong'})
            self.assertEqual('{{secret}}',effective.selector.title)
            self.assertEqual('Question {{secret}}? → {{secret}}',effective.intent_text)
        self.assert_roundtrip(decided,verify)

    def test_parameterization_preserves_generated_data_parameters_and_prevents_collision(self):
        from tacto.reparam import parameterize
        flow=Flow('parameterize','app',steps=[Step('set_value',Selector(title=literal_text('{{field}}')),
                    params={'value':'25'},safety=contract(('write_ephemeral',)))])
        parameterized=parameterize(flow,{'25':'amount'})
        bound=parameterized.bind(amount='49')
        self.assertEqual('{{field}}',bound.steps[0].selector.title)
        self.assertEqual('49',bound.steps[0].params['value'])
        by_label=parameterize(flow,{'{{field}}':'field_name'})
        self.assertEqual('New field',by_label.bind(field_name='New field').steps[0].selector.title)
        with self.assertRaisesRegex(ValueError,'already used'):
            parameterize(flow,{'25':'_tacto_data_1'})

    def test_headless_browser_reads_a_promoted_literal_label_after_store_roundtrip(self):
        from tacto.browser import BrowserDriver
        from tacto.store import FlowStore
        driver=BrowserDriver()
        try:
            driver._page.set_content('<label>{{source}}<input value="verified-result" readonly></label>')
            c=Candidate(goal='read literal',target='page',target_identity=driver.identity('page'),
                actions=[{'do':'read','role':'textbox','attr':'Title','match':'{{source}}',
                          'label':'{{source}}','container':'','read_attribute':'Value'}],
                verified=True,envelope=Envelope.minimal())
            with tempfile.TemporaryDirectory() as tmp:
                store=FlowStore(tmp);store.save(to_flow(c,'literal-read'))
                result=replay(driver,store.load('literal-read').bind(),_seed={'source':'wrong-target'})
                self.assertTrue(result.ok,result.log)
                self.assertEqual('verified-result',result.log[-1]['captured']['결과'])
        finally:driver.close()


if __name__=='__main__':unittest.main()
