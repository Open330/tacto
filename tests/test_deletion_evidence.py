"""Approval permits a dispatch; it does not attest that stored data was deleted."""
import unittest
import importlib.util
from pathlib import Path
from unittest.mock import patch

from tacto.contract import ContextBinding, Resource, SafetyContract, issue_approval
from tacto.flow import Flow, Selector, Step, replay


class CalculatorFixture:
    def __init__(self):
        self.dispatched = []

    def identity(self, target):
        return target

    def observe(self, *args, **kwargs):
        return {'type': 'observation', 'obs_id': 'calc', 'tree':
                '0 Window Title: Calculator\n  1 Group Identifier: CalculatorKeypadView\n'
                '    2 Button Identifier: Delete, Title: Delete the last digit'}

    def act(self, obs_id, target, action, *, risk_class='none', approved=False, **kwargs):
        if risk_class in ('confirm', 'preapproved') and not approved:
            return {'type': 'confirm_request'}
        self.dispatched.append(dict(action))
        return {'type': 'acted'}


class DeletionEvidenceTests(unittest.TestCase):
    def flow(self, scope, reversibility):
        contract = SafetyContract(effects=['delete'],
                                  resource=Resource(object_type='항목', scope=scope),
                                  reversibility=reversibility)
        binding = ContextBinding(bundle_id='com.apple.calculator', role='Button',
                                 identifier='Delete', ancestor='CalculatorKeypadView', operation='click')
        step = Step('click', Selector(role='Button', identifier='Delete', ancestor='CalculatorKeypadView'),
                    safety=contract, binding=binding)
        return Flow('gate_probe', 'com.apple.calculator', [step]), contract, binding

    def test_synthetic_stored_delete_gate_allows_dispatch_but_not_false_completion(self):
        driver = CalculatorFixture()
        flow, contract, binding = self.flow('item', 'recoverable')
        for approvals in ({}, {0: True}):
            result = replay(driver, flow, approvals=approvals)
            self.assertFalse(result.ok)
            self.assertEqual(result.log[-1]['status'], 'gated')
            self.assertEqual(driver.dispatched, [])
        result = replay(driver, flow, approvals={0: issue_approval(binding, contract)})
        self.assertEqual(len(driver.dispatched), 1)
        self.assertEqual(result.events[0]['state'], 'dispatched')
        self.assertEqual(result.events[0]['effects'], ['delete'])
        self.assertEqual(result.log[-1]['status'], 'unverified')
        self.assertFalse(result.ok)

    def test_real_entry_clear_contract_remains_nonconsequential(self):
        driver = CalculatorFixture()
        flow, _, _ = self.flow('entry', 'reversible')
        result = replay(driver, flow)
        self.assertTrue(result.ok, result.log)
        self.assertEqual(len(driver.dispatched), 1)
        self.assertEqual(result.log[-1]['status'], 'done')

    def test_evaluator_safety_gate_checks_dispatch_without_claiming_deletion(self):
        path = Path(__file__).resolve().parents[1] / 'examples/eval.py'
        spec = importlib.util.spec_from_file_location('nlc_deletion_evaluator', path)
        evaluator = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(evaluator)
        driver = CalculatorFixture()
        with patch.object(evaluator, 'Driver', return_value=driver):
            self.assertIs(evaluator.scenario_safety_block(), True)
        self.assertEqual(len(driver.dispatched), 1)


if __name__ == '__main__':
    unittest.main()
