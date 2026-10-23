"""A hand-written step that needs approval is bound to the action it names."""
import json
import unittest
from tacto.flow import Flow


def flow_file(step):
    return json.dumps({"schema": 2, "name": "create-app", "target": "com.apple.Safari", "steps": [step]})


SAFETY = {"effects": ["write_persistent"], "reversibility": "recoverable",
          "resource": {"object_type": "GitHub App", "scope": "item"}}


class HandwrittenBindingTests(unittest.TestCase):
    def test_risky_step_without_binding_is_pinned_from_its_selector_at_authorization(self):
        from tacto.agent import Agent
        flow = Flow.from_json(flow_file({"action": "click", "safety": SAFETY,
                                    "selector": {"role": "Button", "title": "Create GitHub App for jiunbae"}}))
        self.assertIsNone(flow.steps[0].binding)  # loading leaves the file as written
        approvals, blocked = Agent.authorize(object.__new__(Agent), flow, ask=lambda what: True)
        binding = flow.steps[0].binding
        self.assertEqual((None, "Button", "Create GitHub App for jiunbae", "click"),
                         (binding.bundle_id, binding.role, binding.title, binding.operation))
        self.assertIn(0, approvals)

    def test_explicit_binding_is_unchanged(self):
        flow = Flow.from_json(flow_file({"action": "click", "safety": SAFETY,
                                    "selector": {"role": "Button", "title": "Create"},
                                    "binding": {"bundle_id": "com.apple.Safari", "role": "Button",
                                                "title": "Create", "operation": "click"}}))
        self.assertEqual("Create", flow.steps[0].binding.title)

    def test_authorize_no_longer_blocks_a_named_button(self):
        from tacto.agent import Agent
        flow = Flow.from_json(flow_file({"action": "click", "safety": SAFETY,
                                    "selector": {"role": "Button", "title": "Create GitHub App for jiunbae"}}))
        approvals, blocked = Agent.authorize(object.__new__(Agent), flow, ask=lambda what: True)
        self.assertEqual([], [b for b in blocked if "고정된 설명" in b])


if __name__ == "__main__":
    unittest.main()


class ReplayAcceptsDerivedPinTests(unittest.TestCase):
    def test_the_named_button_is_clicked_once_after_authorization(self):
        from tacto.agent import Agent
        from tacto.flow import replay

        class Driver:
            capabilities = ("observe", "act")
            clicks = []
            def observe(self, target, mode="tree_full", require_window=True, **kw):
                tree = '0 Window  Title: "Create GitHub App"\n  1 Button  Title: Create GitHub App for jiunbae'
                if self.clicks:
                    tree += '\n  2 StaticText  Value: Created'
                return {"type": "observation", "obs_id": f"o{len(self.clicks)}", "tree": tree}
            def act(self, obs_id, target, action, **kw):
                self.clicks.append(action)
                return {"type": "acted", "action": action["name"]}

        flow = Flow.from_json(flow_file({"action": "click", "safety": SAFETY,
                                         "selector": {"role": "Button", "title": "Create GitHub App for jiunbae"},
                                         "postcondition": {"field": {"role": "StaticText", "value": "Created"}}}))
        driver = Driver()
        approvals, blocked = Agent.authorize(Agent(driver=driver), flow, ask=lambda what: True)
        self.assertEqual([], blocked)
        result = replay(driver, flow, approvals=approvals)
        # Approved and sent exactly once. A persistent write without a typed
        # host receipt is still reported unverified, never as done.
        self.assertEqual(1, len(driver.clicks))
        self.assertFalse(any(e.get("error") in ("contract_invalidated", "approval_required", "not_approved")
                             for e in result.log), result.log)
        self.assertEqual("commit_unverified", result.log[-1].get("error"))
