"""A settings change the person asked for is approvable, not a handoff."""
import json
import unittest
from tacto.contract import Resource, SafetyContract, policy
from tacto.flow import Flow


class SystemConfigPolicyTests(unittest.TestCase):
    def test_system_config_needs_confirmation_not_a_handoff(self):
        contract = SafetyContract(effects=['system_config'], reversibility='reversible',
                                  resource=Resource(scope='item'))
        self.assertEqual('confirm', policy(contract).risk)

    def test_credentials_stay_with_the_person(self):
        contract = SafetyContract(effects=['credential'], reversibility='reversible',
                                  resource=Resource(scope='item'))
        self.assertEqual('handoff', policy(contract).risk)

    def test_granting_screen_recording_is_approved_with_the_persons_yes(self):
        from tacto.agent import Agent
        flow = Flow.from_json(json.dumps({"schema": 2, "name": "grant", "target": "com.apple.systempreferences",
            "steps": [{"action": "click", "selector": {"role": "CheckBox", "title": "Tacto"},
                       "safety": {"effects": ["system_config"], "reversibility": "reversible",
                                  "resource": {"object_type": "privacy permission", "scope": "item"}}}]}))
        approvals, blocked = Agent.authorize(object.__new__(Agent), flow, ask=lambda what: True)
        self.assertEqual([], blocked)
        self.assertIn(0, approvals)


if __name__ == '__main__':
    unittest.main()
