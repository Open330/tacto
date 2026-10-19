import json
import unittest
from tacto.flow import Flow, Capture, Selector
from tacto.command_help import calculator_example
from tests.test_flow_safety_defaults import Calculator
from tacto.flow import replay


class CompactSerializationTests(unittest.TestCase):
    def check(self, flow):
        compact = flow.to_json(compact=True)
        self.assertEqual(flow, Flow.from_json(compact))
        self.assertLess(len(compact), len(flow.to_json()))
        return compact

    def test_expanded_defaults_and_contracts_roundtrip_and_replay(self):
        flow = Flow.from_json(json.dumps(calculator_example()))
        compact = self.check(flow)
        self.assertNotIn('"selector":null', compact)
        self.assertTrue(replay(Calculator(), Flow.from_json(compact)).ok)

    def test_nondefault_false_empty_checks_and_capture_flags_survive(self):
        flow = Flow.from_json(json.dumps(calculator_example()))
        step = flow.steps[-1]
        step.require_enabled = False
        step.answers = True
        step.postcondition.equals = ''
        step.captures = [Capture(name='answer', field=Selector(identifier='StandardInputView'), optional=True,
                                 empty_ok=True, many=True, tail=True, sensitive=True, limit=3)]
        compact = self.check(flow)
        self.assertIn('"require_enabled":false', compact)
        self.assertIn('"equals":""', compact)

    def test_nested_group_checks_and_opaque_parameter_values_are_preserved(self):
        doc = calculator_example()
        doc['steps'][-1]['postcondition']['all_of'] = [{'all_of':[{'field':{'identifier':'StandardInputView'},'equals':'408'}]}]
        flow = Flow.from_json(json.dumps(doc))
        self.check(flow)
        # Dict contents are not schema defaults and must not be stripped.
        flow.steps[0].action = 'set_value'
        flow.steps[0].params = {'value':None}
        self.assertIn('"value":null', self.check(flow))

    def test_binding_digest_and_authorization_meaning_are_identical(self):
        from tests.test_navigation_replay import NavigationReplayTests, App
        flow = NavigationReplayTests().flow()
        canonical = Flow.from_json(flow.to_json())
        restored = Flow.from_json(flow.to_json(compact=True))
        self.assertEqual(canonical, restored)
        self.assertEqual(flow.steps[0].binding.params_digest, restored.steps[0].binding.params_digest)
        app = App()
        self.assertTrue(replay(app, restored).ok)
        self.assertEqual(['press_key','read'],[action['name'] for action,kwargs in app.calls])

    def test_nested_decision_branches_preserve_step_contracts(self):
        doc = calculator_example()
        doc['steps'] = [{'action':'decide','params':{'question':'Which path?','choices':['a','b'],
            'into':'choice','goal':'Calculate','branches':{'a':doc['steps']}}}]
        self.check(Flow.from_json(json.dumps(doc)))

if __name__ == '__main__':unittest.main()
