"""A decision whose answer changes everything after it grows a branch per case.

Measured live on 2026-09-25: a walk of "화면의 수가 짝수면 2를 곱하고 홀수면 1을
더해서" with 7 on screen was promoted into a branching decision with only the
odd branch. With 8, the unseen even case was walked from the decision on
(16) and kept; then 10 → 20... 12 → 24 and 9 → 10 replayed with the decision
as the only model call.
"""
import json
import unittest
from unittest.mock import patch

from tacto.contract import Resource, SafetyContract
from tacto.flow import Capture, Flow, Selector, Step, replay

PAD = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                     resource=Resource(object_type="입력", scope="entry"))
READ = SafetyContract(effects=["read"], reversibility="reversible",
                      resource=Resource(object_type="표시", scope="entry"))


class Pad:
    capabilities = ()

    def __init__(self):
        self.pressed = []

    def identity(self, target):
        return target

    def observe(self, target, mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": "o", "tree":
                "0 Window  Title: C [focused]\n  1 Button  Description: Add\n"
                "  2 Button  Description: Multiply\n  3 StaticText  Description: Edit field, Value: 7"}

    def act(self, obs_id, target, action, **kw):
        self.pressed.append(action["element_index"])
        return {"type": "acted"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        return self.observe(target)


def press(label):
    return Step("click", Selector(role="Button", desc=label), intent_text=label,
                safety=PAD, unverified_ok=True)


def recipe(branches):
    return Flow("parity", "c", steps=[Step("decide", None, params={
        "question": "짝수?", "choices": ["짝수", "홀수"], "into": "case",
        "goal": "짝수면 곱하고 홀수면 더하기", "branches": branches})])


class BranchReplayTests(unittest.TestCase):
    def test_a_known_branch_replays_with_no_walk(self):
        pad, walked = Pad(), []
        r = replay(pad, recipe({"홀수": [press("Add")]}), decide=lambda *a: "홀수",
                   walk=lambda *a: walked.append(a))
        self.assertTrue(r.ok, r.log)
        self.assertEqual(([1], []), (pad.pressed, walked))
        self.assertEqual({}, r.learned)

    def test_an_unseen_answer_is_walked_and_reported_not_saved(self):
        pad = Pad()
        steps = [press("Multiply")]
        r = replay(pad, recipe({"홀수": [press("Add")]}), decide=lambda *a: "짝수",
                   walk=lambda goal, q, c: {"ok": True, "said": ["16"], "steps": steps})
        self.assertTrue(r.ok, r.log)
        self.assertEqual("16", r.log[-1]["result"])
        self.assertEqual({("case", "짝수"): steps}, r.learned)
        self.assertEqual([], pad.pressed)          # the walk did the pressing

    def test_no_walker_or_a_failed_walk_ends_the_run_honestly(self):
        r = replay(Pad(), recipe({"홀수": [press("Add")]}), decide=lambda *a: "짝수")
        self.assertEqual("branch_unwalked", r.log[-1]["error"])
        r = replay(Pad(), recipe({"홀수": [press("Add")]}), decide=lambda *a: "짝수",
                   walk=lambda *a: {"ok": False, "why": "막힘"})
        self.assertEqual("branch_walk_failed", r.log[-1]["error"])

    def test_branches_round_trip_and_are_checked(self):
        flow = recipe({"홀수": [press("Add")]})
        again = Flow.from_json(flow.to_json())
        self.assertEqual("click", again.steps[0].params["branches"]["홀수"][0].action)
        bad = json.loads(flow.to_json())
        for params in ({**bad["steps"][0]["params"], "branches": {"셋": bad["steps"][0]["params"]["branches"]["홀수"]}},
                       {**bad["steps"][0]["params"], "goal": ""},
                       {k: v for k, v in bad["steps"][0]["params"].items() if k != "branches"}):
            case = json.loads(json.dumps(bad))
            case["steps"][0]["params"] = params
            with self.assertRaises(ValueError):
                Flow.from_json(json.dumps(case))


class LearnBranchTests(unittest.TestCase):
    def agent(self, template):
        from tacto.agent import Agent
        saved = []
        store = type("S", (), {"save": lambda s, f: saved.append(f)})()
        return Agent(driver=object(), store=store), saved

    def test_the_walked_steps_are_kept_with_inputs_turned_back_into_parameters(self):
        template = Flow("t", "c", params={"n": "4"},
                        steps=recipe({"홀수": [press("Add")]}).steps)
        agent, saved = self.agent(template)
        walked = [press("Multiply"), Step("click", Selector(role="Button", identifier="Seven"),
                                          intent_text="7", safety=PAD, unverified_ok=True)]
        agent._learn_branches(template, {("case", "짝수"): walked}, {"n": "7"})
        branch = saved[0].steps[0].params["branches"]["짝수"]
        self.assertEqual("{{digit[item]}}", branch[1].selector.identifier)
        self.assertEqual("{{n}}", branch[1].for_each)
        self.assertIn("홀수", saved[0].steps[0].params["branches"])

    def test_a_walked_branch_needing_consent_is_not_kept(self):
        from tacto.agent import Agent
        send = SafetyContract(effects=["transmit"], reversibility="irreversible",
                              resource=Resource(object_type="메시지", scope="entry"))
        result = type("R", (), {"ok": True, "verified": True, "said": ["x"], "why": "",
                                "target_identity": "c"})()
        flow = Flow("b", "c", steps=[Step("click", Selector(role="Button", desc="Send"),
                                          intent_text="보내기", safety=send)])
        agent = Agent(driver=object(), general=lambda *a: result)
        with patch("tacto.promote.candidate_from", return_value=object()), \
             patch("tacto.promote.deployable", return_value=(flow, [])):
            out = agent._walk_branch("c", "g", "q", "짝수", None)
        self.assertEqual((True, None), (out["ok"], out["steps"]))


class PromoteBranchTests(unittest.TestCase):
    SEEN = [{"role": "StaticText", "attr": "Description", "match": "Edit field",
             "container": "", "label": "Edit field", "value": "7"}]

    def walk(self):
        from tacto.contract import Envelope
        from tacto.promote import Candidate
        def key(label):
            return {"do": "click", "role": "Button", "label": label, "match": label,
                    "attr": "Description", "container": "", "line": f"  1 Button  Description: {label}",
                    "screen_options": ["Add", "Multiply", "1", "2", "Equals"],
                    "screen_reads": self.SEEN}
        return Candidate(goal="짝수면 2를 곱하고 홀수면 1을 더하기", target="c", verified=True,
                         envelope=Envelope.minimal(), actions=[key("Add"), key("1"), key("Equals")])

    def test_rest_scope_makes_a_branch_point_that_reproduces_the_walk(self):
        from tacto.promote import checked_decisions, replays_the_walk, to_flow, with_decisions
        c = self.walk()
        ok, why = checked_decisions(c, [{"step": 0, "scope": "rest", "depends_on": "Edit field",
                                         "question": "짝수?", "choices": ["짝수", "홀수"],
                                         "taken": "홀수"}], c.goal)
        self.assertEqual([], why)
        concrete = to_flow(c, "p")
        decided = with_decisions(concrete, ok, goal=c.goal)
        self.assertEqual(["read", "decide"], [s.action for s in decided.steps])
        self.assertEqual(["홀수"], list(decided.steps[1].params["branches"]))
        self.assertTrue(replays_the_walk(decided, concrete, ok))
        Flow.from_json(decided.to_json())

    def test_a_branch_the_decider_would_file_elsewhere_is_refused(self):
        from tacto import cli
        c = self.walk()
        rec = {"candidate": {**c.__dict__, "envelope": None}, "request": c.goal}
        from tacto.promote import to_flow
        flow = to_flow(c, "p")
        proposal = {"decisions": [{"step": 0, "scope": "rest", "depends_on": "Edit field",
                                   "question": "짝수?", "choices": ["짝수", "홀수"], "taken": "홀수"}]}
        with patch("tacto.authoring.reach_model", return_value=json.dumps(proposal)), \
             patch("tacto.judge.model_decider", return_value=lambda *a: "짝수"):
            from contextlib import redirect_stdout
            from io import StringIO
            with redirect_stdout(StringIO()):
                self.assertIs(flow, cli._promote_decisions(rec, flow, "auto"))


if __name__ == "__main__":
    unittest.main()
