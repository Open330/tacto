import json
import unittest
from tacto.flow import Flow, Postcondition, Selector, replay
from tacto.command_help import calculator_example
from tests.test_flow_safety_defaults import Calculator

class GroupedChecksTests(unittest.TestCase):
    def test_all_values_must_hold_in_same_snapshot(self):
        post=Postcondition(all_of=[Postcondition(field=Selector(identifier='amount'),equals='11',attribute='Value'),
                                   Postcondition(field=Selector(identifier='factor'),equals='3',attribute='Value')])
        self.assertTrue(post.check('0 TextField Identifier: amount, Value: 11\n1 TextField Identifier: factor, Value: 3'))
        self.assertFalse(post.check('0 TextField Identifier: amount, Value: 11\n1 TextField Identifier: factor, Value: 4'))
        self.assertFalse(post.check('0 TextField Identifier: amount, Value: 10\n1 TextField Identifier: factor, Value: 3'))
        self.assertFalse(post.check('0 TextField Identifier: amount, Value: 11'))
        self.assertFalse(Postcondition().check(''))
    def test_nested_parameter_binding_and_roundtrip_preserve_checks(self):
        doc=calculator_example();doc['params']={'expected':'408'}
        doc['steps'][-1]['postcondition']['all_of']=[{'all_of':[{'field':{'identifier':'StandardInputView'},'equals':'{{expected}}','attribute':'Value'}]}]
        flow=Flow.from_json(json.dumps(doc));bound=flow.bind()
        self.assertEqual('408',bound.steps[-1].postcondition.all_of[0].all_of[0].equals)
        self.assertEqual('{{expected}}',flow.steps[-1].postcondition.all_of[0].all_of[0].equals)
        self.assertEqual(bound,Flow.from_json(bound.to_json()))
        result=replay(Calculator(),bound);self.assertTrue(result.ok,result.log)
        self.assertEqual('408',result.log[-1]['result'])
    def test_primary_true_does_not_hide_failed_secondary_condition(self):
        app=Calculator();doc=calculator_example();doc['steps'][-1]['postcondition']['all_of']=[{'field':{'identifier':'NotPresent'},'equals':'yes','attribute':'Value'}]
        result=replay(app,Flow.from_json(json.dumps(doc)))
        self.assertFalse(result.ok);self.assertEqual('postcondition_timeout',result.log[-1]['error'])
        self.assertEqual(1,len([a for a in app.calls if a.get('element_index')==7]))
    def test_commit_guard_remains_true_even_when_extra_check_is_false(self):
        post=Postcondition(field=Selector(identifier='stage'),equals='done',attribute='Value',
                           all_of=[Postcondition(field=Selector(identifier='preview'),equals='38',attribute='Value')])
        tree='0 StaticText Identifier: stage, Value: done\n1 StaticText Identifier: preview, Value: wrong'
        self.assertFalse(post.check(tree));self.assertTrue(post.primary_check(tree))
    def test_return_is_not_dispatched_when_original_completion_already_holds(self):
        from tests.test_navigation_replay import App, NavigationReplayTests
        from tacto.flow import pin_params
        app=App();app.capabilities=set(app.capabilities)|{'press_key.return_bound'}
        flow=NavigationReplayTests().flow();step=flow.steps[0]
        step.params={'key':'Return'};step.is_submit=True
        step.binding.params_digest=pin_params(step.params)
        step.postcondition.equals='0'
        step.postcondition.all_of=[Postcondition(field=Selector(identifier='missing'),equals='yes')]
        result=replay(app,flow)
        self.assertFalse(result.ok);self.assertEqual([],app.calls)
        self.assertEqual('postcondition_already_true',result.log[-1]['error'])
    def test_known_fixture_variants_retain_review_and_finish_checks(self):
        from examples.benchmark_grouped_checks import variants
        actions=['set_value','set_value','click','click','read','read','read','click','click','read']
        ids=['bench-amount','bench-factor','bench-bonus','bench-review','bench-amount',
             'bench-factor','bench-bonus','bench-compute','bench-finish','bench-preview']
        checks={2:('bench-bonus','{{bonus}}'),3:('bench-stage','review'),
                4:('bench-amount','{{amount}}'),5:('bench-factor','{{factor}}'),
                6:('bench-bonus','{{bonus}}'),7:('bench-preview','{{result}}'),
                8:('bench-stage','done'),9:('bench-preview','{{result}}')}
        steps=[]
        for i,(action,identifier) in enumerate(zip(actions,ids)):
            step={'action':action,'selector':{'identifier':identifier}}
            if i<2:step['params']={'value':'{{amount}}' if i==0 else '{{factor}}'}
            if i in checks:
                field,value=checks[i];step['postcondition']={'field':{'identifier':field},'equals':value,'attribute':'Value'}
            if i==9:step['answers']=True
            steps.append(step)
        source=Flow.from_json(json.dumps({'name':'owned-fixture','target':'owned.target','steps':steps}))
        variants_by_name=variants(source,'owned.target')
        grouped=variants_by_name['grouped_checks'];self.assertEqual(6,len(grouped.steps))
        self.assertEqual(['bench-amount','bench-factor','bench-bonus'],
                         [p.field.identifier for p in grouped.steps[3].postcondition.all_of])
        self.assertEqual('bench-preview',grouped.steps[-1].postcondition.field.identifier)
        self.assertEqual('done',grouped.steps[-1].postcondition.all_of[0].equals)
        self.assertTrue(grouped.steps[-1].answers)
        for i in (0,1):self.assertIsNotNone(grouped.steps[i].postcondition)
    def test_saved_grouped_flow_uses_schema3_and_plain_flows_remain_schema2(self):
        doc=calculator_example();plain=Flow.from_json(json.dumps(doc))
        self.assertEqual(2,json.loads(plain.to_json())['schema'])
        doc['steps'][-1]['postcondition']['all_of']=[{'field':{'identifier':'StandardInputView'},'equals':'408','attribute':'Value'}]
        grouped=Flow.from_json(json.dumps(doc))
        self.assertEqual(3,grouped.schema)
        self.assertEqual(3,json.loads(grouped.to_json())['schema'])
        self.assertEqual(grouped,Flow.from_json(grouped.to_json()))
        result=replay(Calculator(),grouped);self.assertTrue(result.ok,result.log)
    def test_invalid_groups_are_refused_before_replay(self):
        for value in (None,{},'text',[None],[{}],[{'equals':False}],[{'equals':'x'}]*33):
            doc=calculator_example();doc['steps'][-1]['postcondition']['all_of']=value
            with self.subTest(value=value),self.assertRaises(ValueError):Flow.from_json(json.dumps(doc))
        child={'equals':'x'}
        for _ in range(10):child={'all_of':[child]}
        doc['steps'][-1]['postcondition']=child
        with self.assertRaises(ValueError):Flow.from_json(json.dumps(doc))

if __name__=='__main__':unittest.main()
