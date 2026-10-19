"""Authoring shorthand expands to identical scoped selectors and checks."""
import copy
import json
import unittest
from tacto.command_help import calculator_example
from tacto.flow import Flow


class CompactFlowFieldsTests(unittest.TestCase):
    def test_compact_and_long_examples_expand_identically(self):
        compact = calculator_example()
        long = calculator_example(compact=False)
        self.assertEqual(Flow.from_json(json.dumps(long)), Flow.from_json(json.dumps(compact)))
        self.assertLess(len(json.dumps(compact)), len(json.dumps(long)) * .7)

    def test_explicit_selector_and_field_override_shared_values(self):
        doc = calculator_example()
        doc['steps'][2]['selector']['role'] = 'TextField'
        doc['steps'][2]['postcondition']['field'] = {'identifier':'other'}
        flow = Flow.from_json(json.dumps(doc))
        self.assertEqual('TextField', flow.steps[2].selector.role)
        self.assertEqual('other', flow.steps[2].postcondition.field.identifier)
        self.assertIsNone(flow.steps[2].postcondition.field.role)
        self.assertEqual('Value', flow.steps[2].postcondition.attribute)

    def test_absent_and_empty_checks_and_selectors_do_not_gain_defaults(self):
        doc = calculator_example()
        doc['steps'][2].update(selector={}, postcondition={})
        flow = Flow.from_json(json.dumps(doc))
        self.assertIsNone(flow.steps[2].selector)
        self.assertIsNone(flow.steps[2].postcondition)
        self.assertIsNone(flow.steps[4].postcondition)
        self.assertIsNone(flow.steps[4].skip_if)

    def test_shared_fields_are_independent_and_roundtrip_expanded(self):
        flow = Flow.from_json(json.dumps(calculator_example()))
        self.assertEqual(flow, Flow.from_json(flow.to_json()))
        flow.steps[2].postcondition.field.identifier = 'changed'
        self.assertEqual('StandardInputView', flow.steps[3].postcondition.field.identifier)

    def test_invalid_shared_fields_and_shared_predicates_are_refused(self):
        cases = [('selector_defaults',None),('selector_defaults',[]),
                 ('selector_defaults',{'element_index':1}),('check_defaults',None),
                 ('check_defaults',{'equals':'408'}),('check_defaults',{'field':{}}),
                 ('check_defaults',{'field':'display'}),('check_defaults',{'attribute':False})]
        for key,value in cases:
            with self.subTest(key=key,value=value):
                doc=calculator_example();doc[key]=value
                with self.assertRaisesRegex(ValueError,'Flow.'+key):Flow.from_json(json.dumps(doc))

    def test_branch_actions_receive_same_expansion_without_touching_decision(self):
        doc=calculator_example();step=copy.deepcopy(doc['steps'][2])
        doc['steps']=[{'action':'decide','params':{'into':'choice','question':'Which?',
                      'choices':['yes','no'],'goal':'choose','branches':{'yes':[step]}}}]
        flow=Flow.from_json(json.dumps(doc))
        branch=flow.steps[0].params['branches']['yes'][0]
        self.assertIsNone(flow.steps[0].selector)
        self.assertEqual('Button',branch.selector.role)
        self.assertEqual('StandardInputView',branch.postcondition.field.identifier)


class CompactClickTests(unittest.TestCase):
    def compact(self):
        doc = calculator_example()
        for step in doc['steps']:
            step['click'] = step.pop('selector')['identifier']
            step.pop('action')
            for old, new in (('postcondition', 'equals'), ('skip_if', 'skip_equals')):
                if old in step:
                    step[new] = step.pop(old)['equals']
        return doc

    def test_compact_clicks_are_identical_to_full_flow(self):
        self.assertEqual(Flow.from_json(json.dumps(calculator_example())).to_json(),
                         Flow.from_json(json.dumps(self.compact())).to_json())

    def test_ambiguous_or_invalid_shorthand_is_refused(self):
        for update in ({'click': ''}, {'click': 0}, {'click': [1]},
                       {'action': 'click'}, {'selector': {'identifier': 'One'}},
                       {'equals': 1}, {'postcondition': {'equals': '1'}},
                       {'skip_if': {'equals': '0'}}):
            doc = self.compact();doc['steps'][1].update(update)
            with self.subTest(update=update), self.assertRaises(ValueError):
                Flow.from_json(json.dumps(doc))
        doc = self.compact();doc.pop('check_defaults')
        with self.assertRaises(ValueError):Flow.from_json(json.dumps(doc))

    def test_shorthand_does_not_create_safety_contracts(self):
        doc=self.compact();doc.pop('safety_defaults');doc['steps']=doc['steps'][2:]
        flow=Flow.from_json(json.dumps(doc))
        self.assertIsNone(flow.steps[0].safety)
