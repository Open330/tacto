"""A decision inside a branch: promoted, replayed, grown and bound at any depth.

Measured live on 2026-09-25 with "짝수면 ×2, 홀수면 +1, 그다음 20보다 크면 −5,
아니면 +5": walked with 7 (13) and promoted into two nested branch points;
then 9 → 15 (known), 25 → 21 (walked the inner unseen branch, kept), 12 → 19
(walked the outer unseen branch; its review found a one-button choice inside),
and 4 → 13, 27 → 23 with decisions as the only model calls.
"""
import json
import unittest

from nlc.contract import Envelope, Resource, SafetyContract
from nlc.flow import Capture, Flow, Selector, Step, every_step, replay
from nlc.promote import Candidate, replays_the_walk, to_flow, with_decisions

PAD = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                     resource=Resource(object_type="입력", scope="entry"))
SEEN = [{"role": "StaticText", "attr": "Description", "match": "Edit field",
         "container": "", "label": "Edit field", "value": "7"}]


def key(label):
    return {"do": "click", "role": "Button", "label": label, "match": label,
            "attr": "Description", "container": "", "line": f"  1 Button  Description: {label}",
            "screen_options": ["Add", "Subtract", "Multiply", "1", "2", "5", "Equals"],
            "screen_reads": SEEN}


def walk():
    return Candidate(goal="두 단계 계산", target="c", verified=True, envelope=Envelope.minimal(),
                     actions=[key("Add"), key("1"), key("Add"), key("5"), key("Equals")])


def rest(step, into_q, choices, taken):
    return {"step": step, "scope": "rest", "question": into_q, "choices": choices,
            "taken": taken, "basis": SEEN[0]}


class PromoteNestedTests(unittest.TestCase):
    def test_a_later_branch_point_nests_inside_the_earlier_one(self):
        concrete = to_flow(walk(), "p")
        ds = [rest(0, "짝수?", ["짝수", "홀수"], "홀수"), rest(2, "20 초과?", ["큼", "이하"], "이하")]
        decided = with_decisions(concrete, ds, goal="g")
        outer = decided.steps[1]
        inner_branch = outer.params["branches"]["홀수"]
        self.assertEqual(["click", "click", "read", "decide"], [s.action for s in inner_branch])
        inner = inner_branch[3]
        self.assertEqual(("case", "case2"), (outer.params["into"], inner.params["into"]))
        self.assertEqual(["Add", "5", "Equals"],
                         [s.selector.desc for s in inner.params["branches"]["이하"]])
        self.assertTrue(replays_the_walk(decided, concrete, ds))
        again = Flow.from_json(decided.to_json())
        self.assertEqual(["read", "decide", "click", "click", "read", "decide",
                          "click", "click", "click"], [s.action for s in every_step(again.steps)])
        self.assertEqual({"case", "case2", "seen_case", "seen_case2"} & again.runtime_names(),
                         {"case", "case2", "seen_case", "seen_case2"})

    def test_names_already_in_use_are_not_reused(self):
        ds = [rest(0, "짝수?", ["짝수", "홀수"], "홀수")]
        decided = with_decisions(to_flow(walk(), "p"), ds, taken_names={"case", "case2"})
        self.assertEqual("case3", decided.steps[1].params["into"])

    def test_a_wrong_walked_answer_does_not_reproduce(self):
        concrete = to_flow(walk(), "p")
        ds = [rest(0, "짝수?", ["짝수", "홀수"], "홀수"), rest(2, "20 초과?", ["큼", "이하"], "이하")]
        decided = with_decisions(concrete, ds)
        ds[1]["taken"] = "큼"                  # a branch filed under the other answer
        self.assertFalse(replays_the_walk(decided, concrete, ds))

    def test_every_decision_in_a_recipe_has_its_own_name(self):
        decided = with_decisions(to_flow(walk(), "p"),
                                 [rest(0, "a?", ["x", "y"], "x"), rest(2, "b?", ["p", "q"], "p")])
        d = json.loads(decided.to_json())
        d["steps"][1]["params"]["branches"]["x"][3]["params"]["into"] = "case"
        with self.assertRaises(ValueError):
            Flow.from_json(json.dumps(d))


class Pad:
    capabilities = ()

    def __init__(self):
        self.pressed = []

    def identity(self, target):
        return target

    def observe(self, target, mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": "o", "tree":
                "0 Window  Title: C [focused]\n  1 Button  Description: A\n"
                "  2 Button  Description: B\n  3 Button  Description: 7"}

    def act(self, obs_id, target, action, **kw):
        self.pressed.append(action["element_index"])
        return {"type": "acted"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        return self.observe(target)


def press(label, **kw):
    return Step("click", Selector(role="Button", desc=label), intent_text=label,
                safety=PAD, unverified_ok=True, **kw)


def nested(inner_branches):
    inner = Step("decide", None, params={"question": "안?", "choices": ["p", "q"], "into": "inner",
                                         "goal": "g", "branches": inner_branches})
    return Flow("n", "c", params={"n": "7"}, steps=[Step("decide", None, params={
        "question": "밖?", "choices": ["x", "y"], "into": "outer", "goal": "g",
        "branches": {"x": [press("A"), inner]}})])


class ReplayNestedTests(unittest.TestCase):
    def test_a_known_inner_branch_replays_and_an_unseen_one_is_walked_there(self):
        answers = {"밖?": "x", "안?": "p"}
        decide = lambda q, c, i: answers[q]
        pad = Pad()
        r = replay(pad, nested({"p": [press("B")]}), decide=decide)
        self.assertTrue(r.ok, r.log)
        self.assertEqual([1, 2], pad.pressed)
        answers["안?"] = "q"
        r = replay(Pad(), nested({"p": [press("B")]}), decide=decide,
                   walk=lambda g, q, c: {"ok": True, "said": ["9"], "steps": [press("A")]})
        self.assertEqual({("inner", "q"): [press("A")]}, r.learned)

    def test_inputs_are_filled_inside_branches_too(self):
        flow = nested({"p": [press("{{n}}")]})
        bound = flow.bind(n="7")
        inner = bound.steps[0].params["branches"]["x"][1]
        self.assertEqual("7", inner.params["branches"]["p"][0].selector.desc)

    def test_a_learned_branch_is_kept_where_its_decision_lives(self):
        from nlc.agent import Agent
        saved = []
        store = type("S", (), {"save": lambda s, f: saved.append(f)})()
        template = nested({"p": [press("B")]})
        Agent(driver=object(), store=store)._learn_branches(
            template, {("inner", "q"): [press("A")]}, {})
        inner = saved[0].steps[0].params["branches"]["x"][1]
        self.assertEqual({"p", "q"}, set(inner.params["branches"]))


if __name__ == "__main__":
    unittest.main()
