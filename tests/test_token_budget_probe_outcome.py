"""Provider gates must not become task failure rates in the live probe."""
import ast
from pathlib import Path
import unittest
from unittest.mock import patch


class TokenBudgetProbeOutcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'examples/probes/token-budget-planning.py'
        tree = ast.parse(path.read_text())
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name in ('case_outcome','live_cli_result')]
        namespace = {}
        exec(compile(ast.Module(body=functions, type_ignores=[]), str(path), 'exec'), namespace)
        cls.classify = staticmethod(namespace['case_outcome'])
        cls.live = staticmethod(namespace['live_cli_result'])

    def report(self, *responses, **overrides):
        return {'calls':[{'provider_result':response} for response in responses],
                'planner_result':None, 'driver_result':None, 'passed':False, **overrides}

    def test_unavailable_has_no_task_execution_and_no_comparison_eligibility(self):
        for reason in ('organization_access_disabled', 'not_logged_in', 'usage_limit', 'authentication_failed'):
            outcome = self.classify(self.report({'status':'error','error':{'code':reason}}))
            self.assertEqual('provider_unavailable', outcome['status'])
            self.assertEqual('not_attempted', outcome['task_execution'])
            self.assertEqual([reason], outcome['provider_error_codes'])
            self.assertFalse(outcome['comparison_eligible'])

    def test_unavailable_must_not_hide_a_partial_or_attempted_task(self):
        blocked = {'status':'error','error':{'code':'organization_access_disabled'}}
        successful = {'status':'ok','error':None}
        for report in (self.report(), self.report({'status':'timeout'}),
                       self.report({'status':'error','error':{'code':'unexpected_protocol'}}),
                       self.report(successful, blocked),
                       self.report(blocked, planner_result={'actions':[]}),
                       self.report(blocked, driver_result={'type':'error'})):
            self.assertEqual('failed', self.classify(report)['status'])

    def test_passed_receiving_oracle_is_reported_without_comparison_claim(self):
        outcome = self.classify(self.report({'status':'ok','error':None}, passed=True,
                                            planner_result={'actions':[{}]},
                                            driver_result={'type':'acted'}))
        self.assertEqual('passed', outcome['status'])
        self.assertEqual('attempted', outcome['task_execution'])
        self.assertFalse(outcome['comparison_eligible'])

    def test_opencode_routes_to_structured_adapter_without_claude_fallback(self):
        with patch('tacto.opencode_result.opencode_result',return_value='owned-result') as adapter, \
             patch('tacto.claude_result.claude_result') as claude:
            self.assertEqual('owned-result',self.live('opencode','owned prompt',2))
        adapter.assert_called_once_with('owned prompt',timeout=2)
        claude.assert_not_called()
        with self.assertRaises(ValueError):self.live('unsupported','owned',2)
