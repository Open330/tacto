"""Where a model helps turn a walk into a recipe, and where the recording decides.

Measured live on 2026-09-24. A walk of "계산기 화면에 있는 수가 짝수면 2를
곱하고 홀수면 3을 곱해서 결과를 알려줘" pressed 3 with 7 on screen; the review at
promotion found that step, and the recipe then asked once per run: 8 → 16,
13 → 39, 22 → 44. Separately, a rephrased square-root request found its recipe
through the reading step, was learned, and the next one cost no model call.
"""
import json
import unittest
from unittest.mock import patch

from nlc.contract import Envelope
from nlc.flow import Flow
from nlc.promote import (Candidate, checked_decisions, decision_prompt, to_flow,
                         replays_the_walk, with_decisions)

REQUEST = "계산기 화면에 있는 수가 짝수면 2를 곱하고 홀수면 3을 곱해서 결과를 알려줘"
SEEN = [{"role": "StaticText", "attr": "Description", "match": "Edit field",
         "container": "StandardInputView", "label": "Edit field", "value": "7"}]
KEYS = ["7", "8", "9", "Multiply", "2", "3", "Equals"]


def key(label):
    return {"do": "click", "role": "Button", "label": label, "match": label,
            "attr": "Description", "container": "CalculatorKeypadView",
            "line": f"  5 Button  Description: {label}",
            "screen_options": KEYS, "screen_reads": SEEN}


def walk():
    return Candidate(goal="계산기 화면의 수가 짝수면 2를, 홀수면 3을 곱한 결과 알려주기",
                     target="com.apple.calculator", quote=REQUEST, verified=True,
                     envelope=Envelope.minimal(),
                     actions=[key("Multiply"), key("3"), key("Equals")])


PROPOSAL = {"step": 1, "depends_on": "Edit field", "question": "짝수입니까?",
            "choices": ["2", "3"]}


class DecisionReviewTests(unittest.TestCase):
    def test_the_prompt_shows_what_was_visible_and_what_could_be_pressed(self):
        prompt = decision_prompt(walk())
        self.assertIn("Edit field='7'", prompt)
        self.assertIn("같은 화면의 선택지: 7, 8, 9, Multiply, 2, 3, Equals", prompt)

    def test_a_supported_proposal_becomes_a_read_a_question_and_the_answer(self):
        ok, why = checked_decisions(walk(), [PROPOSAL], REQUEST)
        self.assertEqual([], why)
        concrete = to_flow(walk(), "parity")
        decided = with_decisions(concrete, ok)
        self.assertEqual(["click", "read", "decide", "click", "click"],
                         [s.action for s in decided.steps])
        self.assertEqual("{{choice1}}", decided.steps[3].selector.desc)
        self.assertEqual("{{choice1}}", decided.steps[3].binding.desc)
        self.assertEqual(["seen1"], [c.name for c in decided.steps[1].captures])
        self.assertTrue(replays_the_walk(decided, concrete, ok))
        again = Flow.from_json(decided.to_json())
        self.assertEqual(["2", "3"], again.steps[2].params["choices"])

    def test_the_recording_refuses_what_it_does_not_support(self):
        bad = [
            ({**PROPOSAL, "step": 0}, "Multiply is always pressed — not in choices"),
            ({**PROPOSAL, "choices": ["2", "4"]}, "walked key missing"),
            ({**PROPOSAL, "choices": ["2", "3", "Delete"]}, "not on that screen"),
            ({**PROPOSAL, "depends_on": "Clock"}, "basis was not visible"),
            ({**PROPOSAL, "step": 9}, "no such step"),
            ({**PROPOSAL, "question": ""}, "no question"),
        ]
        for proposal, case in bad:
            ok, why = checked_decisions(walk(), [proposal], REQUEST)
            self.assertEqual([], ok, case)
            self.assertEqual(1, len(why), case)
        self.assertEqual(([], ["답이 목록이 아닙니다"]), checked_decisions(walk(), None, REQUEST))

    def test_a_value_the_request_fixed_is_not_a_decision(self):
        # "3을 곱해줘" names one value; which key to press is not in doubt.
        ok, why = checked_decisions(walk(), [{**PROPOSAL, "choices": ["3", "Multiply"]}],
                                    "계산기 화면의 수에 3을 곱해줘")
        self.assertEqual([], ok)
        self.assertIn("요청에서 정해진 값", why[0])

    def test_promote_asks_once_and_applies_only_what_passed(self):
        from nlc import cli
        rec = {"blocked_by": [], "parameters": {"3": [1]}, "request": REQUEST,
               "candidate": {**walk().__dict__, "envelope": None},
               "flow": json.loads(to_flow(walk(), "parity").to_json())}
        with patch("nlc.promote.CandidateStore.get", return_value=rec), \
             patch("nlc.cli.FlowStore") as store, \
             patch("nlc.authoring.reach_model",
                   return_value=json.dumps({"decisions": [PROPOSAL]})) as reach:
            from contextlib import redirect_stdout
            from io import StringIO
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, cli.main(["promote", "parity"]))
        reach.assert_called_once()
        saved = store.return_value.save.call_args.args[0]
        self.assertEqual(1, sum(s.action == "decide" for s in saved.steps))
        self.assertIn("판단 지점 1곳에서만", out.getvalue())
        self.assertNotIn("-p 3=", out.getvalue())

    def test_a_walk_with_nothing_visible_costs_no_review(self):
        from nlc import cli
        blind = walk()
        for a in blind.actions:
            a["screen_reads"] = []
        rec = {"candidate": {**blind.__dict__, "envelope": None}, "request": REQUEST}
        with patch("nlc.authoring.reach_model") as reach:
            flow = to_flow(blind, "x")
            self.assertIs(flow, cli._promote_decisions(rec, flow, "auto"))
        reach.assert_not_called()


class LearnedPhrasingTests(unittest.TestCase):
    def agent(self, flows):
        from nlc.agent import Agent
        saved = {}
        store = type("S", (), {"list": lambda s: list(flows),
                               "load": lambda s, n: flows[n],
                               "save": lambda s, f: (saved.__setitem__(f.name, f),
                                                     flows.__setitem__(f.name, f))})()
        return Agent(driver=object(), store=store), saved

    def test_a_phrasing_that_worked_is_kept_with_its_value_marked(self):
        sqrt = Flow("sqrt", "t", params={"n": "25"}, intents=["{{n}}의 제곱근 구해줘"])
        agent, saved = self.agent({"sqrt": sqrt})
        agent._learn_phrasing(sqrt, "계산기로  100의 제곱근을 계산해 줄래?", {"n": "100"})
        self.assertEqual("계산기로 {{n}}의 제곱근을 계산해 줄래?", saved["sqrt"].intents[-1])
        self.assertEqual({"n": "7"}, agent._match_stored("계산기로 7의 제곱근을 계산해 줄래?").params)

    def test_ambiguous_or_claimed_sentences_are_not_learned(self):
        sqrt = Flow("sqrt", "t", params={"n": "25"}, intents=["{{n}}의 제곱근"])
        cube = Flow("cube", "t", params={"n": "3"}, intents=["{{n}}을 계산해 줄래?"])
        agent, saved = self.agent({"sqrt": sqrt, "cube": cube})
        agent._learn_phrasing(sqrt, "100 곱하기 100의 제곱근", {"n": "100"})   # twice
        agent._learn_phrasing(sqrt, "제곱근 부탁해", {"n": "100"})             # absent
        agent._learn_phrasing(sqrt, "100을 계산해 줄래?", {"n": "100"})        # cube's
        self.assertEqual({}, saved)

    def test_only_a_run_found_by_reading_and_done_teaches(self):
        from nlc.agent import Agent, Match, Turn
        sqrt = Flow("sqrt", "t", params={"n": "25"}, intents=["x {{n}}"])
        for found_by_reading, status, learned in ((True, "done", 1), (False, "done", 0),
                                                  (True, "failed", 0)):
            agent, _ = self.agent({"sqrt": sqrt})
            agent.resolve = lambda i: None if found_by_reading else Match("sqrt", {"n": "4"})
            agent.read_request = lambda i: (Match("sqrt", {"n": "4"}), {"goal": "g"})
            agent.store.has = lambda n: True
            agent.authorize = lambda f, ask: ({}, [])
            agent.commit_adapter_for = lambda f: None
            agent.commit_adapter_hooks = lambda r: {}
            agent._report_what_it_asked = lambda f, r: None
            agent._to_turn = lambda f, r: Turn(status, "")
            calls = []
            agent._learn_phrasing = lambda *a: calls.append(a)
            with patch("nlc.agent.replay"), patch("nlc.library.builders", return_value={}), \
                 patch("nlc.judge.model_judge"):
                agent.handle("말 4", think=0)
            self.assertEqual(learned, len(calls), (found_by_reading, status))


if __name__ == "__main__":
    unittest.main()
