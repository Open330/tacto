"""Where a replayed recipe may ask, and what an answer may do.

Two kinds of question: one a recipe PLANS (a `decide` step, every run) and one
it did not expect (a declared surprise, re-aimed once). Both measured live on
2026-09-24 before these tests pinned them: a parity recipe decided 9 → ×3 = 27
and 14 → ×2 = 28 with one model call each, and a promoted cube recipe re-aimed
Calculator's `Clear` to `All Clear` and answered 12³ = 1,728.
"""
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

from nlc.contract import ContextBinding, Resource, SafetyContract
from nlc.flow import (Flow, Selector, Step, _as_labelled, intent_pattern,
                      replay)

PAD = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                     resource=Resource(object_type="입력", scope="entry"))
SEND = SafetyContract(effects=["transmit"], reversibility="irreversible",
                      resource=Resource(object_type="메시지", scope="entry"))


class Screen:
    """Two buttons, 2 and 3; records what was pressed."""
    capabilities = ()

    def __init__(self):
        self.pressed = []

    def identity(self, target):
        return target

    def observe(self, target, mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": "o", "tree":
                "0 Window  Title: T [focused]\n  1 Button  Description: 2\n"
                "  2 Button  Description: 3"}

    def act(self, obs_id, target, action, **kw):
        self.pressed.append(action["element_index"])
        return {"type": "acted"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        return self.observe(target)


def decide_then_press(safety=PAD):
    return Flow("f", "t", steps=[
        Step("decide", None, params={"question": "몇?", "choices": ["2", "3"], "into": "k"}),
        Step("click", Selector(role="Button", desc="{{k}}"), intent_text="누르기",
             safety=safety, unverified_ok=True)])


class DecideStepTests(unittest.TestCase):
    def test_the_answer_drives_the_next_step(self):
        screen, asked = Screen(), []
        r = replay(screen, decide_then_press(),
                   decide=lambda q, c, i: asked.append((q, c, i)) or "3")
        self.assertTrue(r.ok, r.log)
        self.assertEqual([2], screen.pressed)
        self.assertEqual([("몇?", ["2", "3"], {})], asked)

    def test_no_decider_stops_instead_of_guessing(self):
        screen = Screen()
        r = replay(screen, decide_then_press())
        self.assertFalse(r.ok)
        self.assertEqual("decision_unavailable", r.log[-1]["error"])
        self.assertEqual([], screen.pressed)

    def test_an_answer_outside_the_choices_is_refused(self):
        for answer in ("4", None, ""):
            screen = Screen()
            r = replay(screen, decide_then_press(), decide=lambda q, c, i: answer)
            self.assertEqual("decision_invalid", r.log[-1]["error"])
            self.assertEqual([], screen.pressed)

    def test_a_decided_value_never_reaches_a_step_that_needs_consent(self):
        screen, asked = Screen(), []
        r = replay(screen, decide_then_press(SEND), decide=lambda *a: asked.append(a) or "2")
        self.assertEqual("decision_drives_consent", r.log[-1]["error"])
        self.assertEqual(([], []), (asked, screen.pressed))

    def test_sensitive_captures_are_not_shown_to_the_decider(self):
        from nlc.flow import Capture
        flow = decide_then_press()
        read = Step("read", Selector(role="Button", desc="2"), intent_text="읽기",
                    safety=SafetyContract(effects=["read"], reversibility="reversible",
                                          resource=Resource(object_type="x", scope="entry")),
                    unverified_ok=True,
                    captures=[Capture(name="secret", field=Selector(role="Button", desc="2"),
                                      attribute="Description", sensitive=True),
                              Capture(name="plain", field=Selector(role="Button", desc="3"),
                                      attribute="Description")])
        flow = replace(flow, steps=[read, *flow.steps])
        seen = []
        replay(Screen(), flow, decide=lambda q, c, i: seen.append(i) or "2")
        self.assertEqual([{"plain": "3"}], seen)

    def test_ir_round_trips_and_rejects_a_malformed_decision(self):
        flow = decide_then_press()
        again = Flow.from_json(flow.to_json())
        self.assertEqual("decide", again.steps[0].action)
        bad = json.loads(flow.to_json())
        for params in ({"question": "q", "choices": ["only"], "into": "k"},
                       {"question": "q", "choices": ["a", "a"], "into": "k"},
                       {"question": "q", "choices": ["a", "b"], "into": "값"},
                       {"question": "", "choices": ["a", "b"], "into": "k"}):
            bad["steps"][0]["params"] = params
            with self.assertRaises(ValueError):
                Flow.from_json(json.dumps(bad))

    def test_bind_leaves_run_time_names_for_the_run(self):
        flow = replace(decide_then_press(), params={"x": "1"})
        self.assertEqual("{{k}}", flow.bind(x="5").steps[1].selector.desc)


class ReaimTests(unittest.TestCase):
    TREE = "0 Window  Title: T [focused]\n  1 Button  Description: All Clear"

    def test_a_label_named_in_the_wrong_slot_still_finds_its_element(self):
        picked = _as_labelled(Selector(role="Button", title="All Clear"), self.TREE)
        self.assertEqual(("All Clear", None), (picked.desc, picked.title))
        self.assertEqual(1, picked.resolve(self.TREE)[0])

    def test_reaim_repins_the_element_but_keeps_the_operation(self):
        class Here(Screen):
            def observe(s, *a, **k):
                return {"type": "observation", "obs_id": "o", "tree": ReaimTests.TREE}
        from nlc.flow import Resolution
        step = Step("click", Selector(role="Button", desc="Clear"), intent_text="지우기",
                    safety=PAD, unverified_ok=True, on_uncertain=["target_missing"],
                    binding=ContextBinding(bundle_id="t", role="Button", desc="Clear",
                                           operation="click"))
        screen = Here()
        r = replay(screen, Flow("f", "t", steps=[step]), budget=1,
                   escalate=lambda why: Resolution(
                       kind="use_selector", selector=Selector(role="Button", title="All Clear")))
        self.assertTrue(r.ok, r.log)
        self.assertEqual([1], screen.pressed)


class IntentTests(unittest.TestCase):
    def test_a_promoted_sentence_reads_its_input_back_out(self):
        rx = intent_pattern("계산기에서 {{n}}의 제곱근 구해줘", {"n": "25"})
        self.assertEqual({"n": "49"}, rx.fullmatch("계산기에서 49의 제곱근 구해줘").groupdict())
        self.assertEqual({"n": "144"}, rx.fullmatch(" 계산기에서  144의 제곱근 구해줘. ").groupdict())
        self.assertIsNone(rx.fullmatch("계산기에서 49의 제곱근 구해줘 그리고 저장해"))
        self.assertIsNone(rx.fullmatch("계산기에서 abc의 제곱근 구해줘"))

    def test_the_reading_may_fill_inputs_only_with_words_that_were_said(self):
        from nlc.ask import which_task
        q = which_task("계산기로 64의 제곱근 계산", [("sqrt", "계산기에서 {{n}}의 제곱근 구해줘")],
                       inputs={"sqrt": {"n": "25"}}, words="계산기로 64의 제곱근을 계산해 줄래?")
        self.assertEqual({"n": "64"}, q.check({"pick": "sqrt", "values": {"n": "64"}})["values"])
        self.assertIsNone(q.check({"pick": "sqrt", "values": {"n": "8"}}))      # worked out
        self.assertIsNone(q.check({"pick": "sqrt", "values": {"n": "육십사"}}))  # not a number
        self.assertIsNone(q.check({"pick": "sqrt"}))                             # missing

    def test_two_matching_recipes_are_a_guess_and_neither_runs(self):
        from nlc.agent import Agent
        a = Flow("a", "t", intents=["{{n}} 해줘"], params={"n": "1"})
        b = Flow("b", "t", intents=["{{m}} 해줘"], params={"m": "1"})
        store = type("S", (), {"list": lambda s: ["a", "b"],
                               "load": lambda s, n: {"a": a, "b": b}[n]})()
        agent = Agent(driver=object(), store=store)
        self.assertIsNone(agent._match_stored("5 해줘"))
        store.list = lambda: ["a"]
        self.assertEqual(("a", {"n": "5"}), tuple(vars(agent._match_stored("5 해줘")).values()))


class JudgeRouteTests(unittest.TestCase):
    def test_both_model_callbacks_go_through_the_launcher_relay(self):
        # Inside the signed launcher the provider CLIs are off PATH on purpose;
        # a direct call failed with FileNotFoundError on every escalation.
        from nlc.flow import Uncertainty
        from nlc.judge import model_decider, model_judge
        with patch("nlc.judge.reach_model", return_value='{"choice": "3"}') as reach:
            self.assertEqual("3", model_decider()("q", ["2", "3"], {}))
        reach.assert_called_once()
        with patch("nlc.judge.reach_model", return_value='{"kind": "give_up"}') as reach:
            model_judge()(Uncertainty(kind="target_missing", step=0, intent="x", detail="y"))
        reach.assert_called_once()


if __name__ == "__main__":
    unittest.main()
