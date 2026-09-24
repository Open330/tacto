"""Text a run types that it READ: copied afresh, or extracted and checked.

Measured live on 2026-09-24 in a TextEdit document of our own: the model
extracted 'A-48213' from "고객 문의: 주문번호 A-48213 의 배송이 …", the step that
wrote it asked once, at run time, with the value in the question, and the
document then held exactly that.
"""
import json
import unittest

from nlc.contract import ContextBinding, Resource, SafetyContract
from nlc.flow import Capture, Flow, Selector, Step, pin_params, replay

FIELD = Selector(role="TextArea", identifier="doc")
EDIT = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                      resource=Resource(object_type="입력", scope="entry"))
SAVE = SafetyContract(effects=["write_persistent"], reversibility="recoverable",
                      resource=Resource(object_type="문서 본문", scope="item"))
READ = SafetyContract(effects=["read"], reversibility="reversible",
                      resource=Resource(object_type="문서", scope="item"))
TEXT = "고객 문의: 접수번호 A-48213 의 답변이 늦어요"


class Doc:
    capabilities = ()

    def __init__(self):
        self.value, self.sets = TEXT, []

    def identity(self, target):
        return target

    def observe(self, target, mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": "o", "tree":
                f"0 Window  Title: D [focused]\n  1 TextArea  Identifier: doc, Value: {self.value}"}

    def act(self, obs_id, target, action, **kw):
        if action.get("name") == "set_value":
            self.sets.append(action["value"])
            self.value = action["value"]
        return {"type": "acted"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        return self.observe(target)


def extract_then(safety, binding=None):
    return Flow("x", "t", steps=[
        Step("read", FIELD, intent_text="읽기", safety=READ, unverified_ok=True,
             captures=[Capture(name="source", field=FIELD, attribute="Value")]),
        Step("decide", None, params={"question": "주문번호?", "extract_from": ["source"],
                                     "into": "order"}),
        Step("set_value", FIELD, params={"value": "{{order}}"}, intent_text="쓰기",
             safety=safety, unverified_ok=True, binding=binding)])


class ExtractTests(unittest.TestCase):
    def test_an_answer_found_in_what_was_read_is_used(self):
        doc, seen = Doc(), []
        r = replay(doc, extract_then(EDIT),
                   decide=lambda q, c, i: seen.append((c, i)) or "A-48213")
        self.assertTrue(r.ok, r.log)
        self.assertEqual(["A-48213"], doc.sets)
        self.assertEqual([(None, {"source": TEXT})], seen)   # None = extract

    def test_an_answer_not_literally_there_is_refused(self):
        for answer in ("A48213", "주문번호는 A-48213", "", None, "X" * 600):
            doc = Doc()
            r = replay(doc, extract_then(EDIT), decide=lambda *a: answer)
            self.assertEqual("decision_invalid", r.log[-1]["error"], answer)
            self.assertEqual([], doc.sets)

    def test_the_ir_checks_an_extraction(self):
        flow = extract_then(EDIT)
        self.assertEqual(["source"], Flow.from_json(flow.to_json()).steps[1].params["extract_from"])
        bad = json.loads(flow.to_json())
        for params in ({"question": "q", "extract_from": [], "into": "o"},
                       {"question": "q", "extract_from": ["source"], "into": "o", "choices": ["a", "b"]},
                       {"question": "q", "extract_from": ["값"], "into": "o"}):
            bad["steps"][1]["params"] = params
            with self.assertRaises(ValueError):
                Flow.from_json(json.dumps(bad))


class RuntimeConsentTests(unittest.TestCase):
    BIND = ContextBinding(bundle_id="t", role="TextArea", identifier="doc",
                          operation="set_value", params_digest=pin_params({"value": "{{order}}"}))

    def test_consent_is_asked_when_the_value_exists_and_shows_it(self):
        doc, asked = Doc(), []
        r = replay(doc, extract_then(SAVE, self.BIND), decide=lambda *a: "A-48213",
                   confirm=lambda text: asked.append(text) or True)
        self.assertEqual(["A-48213"], doc.sets, r.log)
        self.assertEqual(1, len(asked))
        self.assertIn("A-48213", asked[0])

    def test_declined_or_unaskable_consent_writes_nothing(self):
        doc = Doc()
        r = replay(doc, extract_then(SAVE, self.BIND), decide=lambda *a: "A-48213",
                   confirm=lambda text: False)
        self.assertEqual(("gated", []), (r.log[-1]["status"], doc.sets))
        doc = Doc()
        r = replay(doc, extract_then(SAVE, self.BIND), decide=lambda *a: "A-48213")
        self.assertEqual(("decision_drives_consent", []), (r.log[-1]["error"], doc.sets))

    def test_nothing_is_asked_before_the_run_for_such_a_step(self):
        from nlc.agent import Agent
        asked = []
        Agent(driver=object()).authorize(extract_then(SAVE, self.BIND),
                                         ask=lambda w: asked.append(w) or True)
        self.assertEqual([], asked)


class LiftTests(unittest.TestCase):
    def walk(self, typed, visible):
        from nlc.contract import Envelope
        from nlc.promote import Candidate
        seen = [{"role": "StaticText", "attr": "Description", "match": "Order",
                 "container": "", "label": "Order", "value": visible}]
        return Candidate(goal="주문번호를 입력칸에 옮겨 적기", target="t", verified=True,
                         envelope=Envelope.minimal(), actions=[
            {"do": "set_value", "role": "TextField", "label": "Search", "match": "Search",
             "attr": "Description", "container": "", "value": typed,
             "line": "  2 TextField  Description: Search", "screen_reads": seen},
            {"do": "read", "role": "TextField", "label": "Search", "match": "Search",
             "attr": "Description", "container": "", "read_attribute": "Value",
             "line": f"  2 TextField  Description: Search, Value: {typed}"}])

    def test_the_whole_value_is_read_again_and_needs_no_model(self):
        from nlc.promote import deployable
        flow, why = deployable(self.walk("A-48213", "A-48213"), "t", "주문번호를 입력칸에 옮겨 적어줘")
        self.assertEqual([], why)
        self.assertEqual(["read", "set_value", "read"], [s.action for s in flow.steps])
        self.assertEqual("{{copied1}}", flow.steps[1].params["value"])
        Flow.from_json(flow.to_json())

    def test_part_of_a_value_becomes_a_checked_extraction(self):
        from nlc.promote import deployable
        flow, why = deployable(self.walk("A-48213", TEXT), "t", "주문번호를 입력칸에 옮겨 적어줘")
        self.assertEqual([], why)
        self.assertEqual(["read", "decide", "set_value", "read"], [s.action for s in flow.steps])
        self.assertEqual(["source1"], flow.steps[1].params["extract_from"])
        self.assertEqual("{{text1}}", flow.steps[2].params["value"])

    def test_a_value_seen_nowhere_is_still_refused(self):
        from nlc.promote import deployable
        flow, why = deployable(self.walk("B-1", TEXT), "t", "주문번호를 입력칸에 옮겨 적어줘")
        self.assertIsNone(flow)
        self.assertTrue(any("읽은 값을 그대로" in w for w in why), why)

    def test_decisions_are_renumbered_past_lifted_steps(self):
        from nlc.promote import shifted
        self.assertEqual([{"step": 5}], shifted([{"step": 2}], {0: 1, 1: 2, 3: 2}))


if __name__ == "__main__":
    unittest.main()
