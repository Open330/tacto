import unittest
from unittest.mock import patch

from tacto.general import run


class FakeClock:
    now = 100.0

    def __call__(self):
        return self.now


class App:
    tree = "0 Window Title: Test\n  1 TextField Title: Entry, Value: hello"

    def __init__(self):
        self.observations = 0
        self.calls = []
        self.on_act = lambda: None
        self.on_observe = lambda: None
        self.on_probe = lambda: None
        self.on_wait = lambda: None
        self.result = {"type": "acted"}

    def observe(self, *args, **kwargs):
        self.observations += 1
        self.on_observe()
        return {"type": "observation", "obs_id": "obs", "tree": self.tree}

    def probe(self, *args, **kwargs):
        self.on_probe()
        return {"type": "probe", "line": self.tree.splitlines()[1]}

    def act(self, obs_id, target, action, **kwargs):
        self.calls.append(action)
        self.on_act()
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def wait_until(self, *args, **kwargs):
        self.on_wait()
        return {"type": "observation", "tree": self.tree}


def plan(goal, candidates, history):
    if history:
        return {"done": True}
    return {"actions": [{"do": "read", "candidate": candidates[0]},
                        {"do": "read", "candidate": candidates[0]}]}


class GeneralBudgetTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.app = App()
        self.cancelled = False
        self.patch = patch("tacto.general.time.monotonic", self.clock)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def execute(self, planner=plan, **kwargs):
        return run(self.app, "app", "read Entry", planner, **kwargs)

    def test_expired_or_cancelled_before_start_does_not_observe_or_ask(self):
        for kwargs in ({"timeout_seconds": 0}, {"cancelled": lambda: True}):
            with self.subTest(kwargs=kwargs):
                result = self.execute(lambda *a: self.fail("model called"), **kwargs)
                self.assertIn(result.stop_reason, ("deadline_exceeded", "cancelled"))
                self.assertEqual(self.app.observations, 0)
                self.assertFalse(result.ok)

    def test_deadline_during_observation_prevents_model(self):
        self.app.on_observe = lambda: setattr(self.clock, "now", 102)
        result = self.execute(lambda *a: self.fail("model called"), timeout_seconds=1)
        self.assertEqual(result.stop_reason, "deadline_exceeded")

    def test_model_return_after_deadline_never_dispatches_actions(self):
        def slow_plan(*args):
            self.clock.now += 2
            return plan(*args)
        result = self.execute(slow_plan, timeout_seconds=1)
        self.assertEqual(result.stop_reason, "deadline_exceeded")
        self.assertEqual(result.asked, 1)
        self.assertEqual(self.app.calls, [])

    def test_cancellation_during_model_cannot_claim_done(self):
        def cancelled_plan(*args):
            self.cancelled = True
            return {"done": True}
        result = self.execute(cancelled_plan, cancelled=lambda: self.cancelled)
        self.assertEqual(result.stop_reason, "cancelled")
        self.assertFalse(result.ok)

    def test_probe_deadline_prevents_action_dispatch(self):
        self.app.on_probe = lambda: setattr(self.clock, "now", 102)
        result = self.execute(timeout_seconds=1)
        self.assertEqual(result.stop_reason, "deadline_exceeded")
        self.assertEqual(self.app.calls, [])

    def test_deadline_after_dispatch_records_effect_and_stops_batch(self):
        self.app.on_act = lambda: setattr(self.clock, "now", 102)
        result = self.execute(timeout_seconds=1)
        self.assertEqual(result.stop_reason, "deadline_exceeded")
        self.assertEqual(len(self.app.calls), 1)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.effects, {"read"})
        self.assertEqual(len(result.visits), 1)
        self.assertEqual(len(result.visits[0].actions), 1)

    def test_unknown_dispatch_result_is_recorded_without_retry(self):
        self.app.result = RuntimeError("lost response after dispatch")
        self.app.on_act = lambda: setattr(self, "cancelled", True)
        result = self.execute(cancelled=lambda: self.cancelled)
        self.assertEqual(result.stop_reason, "cancelled")
        self.assertEqual(len(self.app.calls), 1)
        self.assertEqual(result.events[0]["state"], "unknown")
        self.assertFalse(result.ok)

    def test_expect_wait_return_after_deadline_is_not_verification(self):
        def expected_plan(*args):
            return {**plan(*args), "expect": "hello"}
        self.app.on_wait = lambda: setattr(self.clock, "now", 102)
        result = self.execute(expected_plan, timeout_seconds=1)
        self.assertEqual(result.stop_reason, "deadline_exceeded")
        self.assertFalse(result.ok)
        self.assertIsNone(result.visits[0].verified)

    def test_paging_stop_reason_and_budget_details_survive(self):
        result = self.execute(lambda *a: {
            "stop_reason": "candidate_page_limit", "actions": [],
            "paging": {"page": 3, "token_count": 120, "budget": 150}})
        self.assertEqual(result.stop_reason, "candidate_page_limit")
        self.assertIn("120/150", result.why)
        self.assertIn("페이지 3", result.why)
        self.assertEqual(self.app.calls, [])

    def test_default_behavior_and_screen_limit_remain_bounded(self):
        result = self.execute()
        self.assertTrue(result.ok)
        self.assertTrue(result.verified)
        self.assertEqual(result.stop_reason, "")
        result = self.execute(max_screens=0)
        self.assertEqual(result.stop_reason, "screen_limit")

    def test_invalid_timeout_and_broken_cancellation_fail_explicitly(self):
        for timeout in (True, -1, float("nan"), float("inf"), "one"):
            with self.assertRaises(ValueError):
                self.execute(timeout_seconds=timeout)
        def broken():
            raise RuntimeError("unavailable")
        result = self.execute(cancelled=broken)
        self.assertEqual(result.stop_reason, "cancellation_check_failed")
        self.assertEqual(self.app.observations, 0)


if __name__ == "__main__":
    unittest.main()
