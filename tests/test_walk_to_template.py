"""An agent's walk becomes a recipe that takes new values and replays alone.

Measured live (2026-09-23): "계산기에서 25의 제곱근 구해줘" walked by the model,
promoted with `-p 25=n`, replayed with n=49 and no model call, showed 7. Each
test below is one thing that broke on that path before it worked.
"""
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

from nlc.contract import Envelope
from nlc.flow import Flow
from nlc.general import _place, menu_effect
from nlc.promote import Candidate, _menu_check, deployable, parameters, to_flow
from nlc.reparam import digits_table, parameterize

BEFORE = "\n".join([
    "0 Window  Title: Calculator [focused]",
    "  1 Button  Description: Mode, Identifier: Mode: basic; unitConversion: false",
    "  2 Group  Identifier: StandardInputView;value:25",
    "    3 StaticText  Description: Edit field, Value: 25",
])
AFTER = BEFORE.replace("Mode: basic", "Mode: scientific")


def key(label, ident):
    return {"do": "click", "role": "Button", "label": label, "match": label,
            "attr": "Description", "container": "CalculatorKeypadView",
            "line": f"  5 Button  Description: {label}, Identifier: {ident}"}


def walked():
    return Candidate(
        goal="계산기로 25의 제곱근 구하기", target="com.apple.calculator",
        verified=True, envelope=Envelope.minimal(),
        actions=[
            {"do": "menu", "role": "MenuPath", "label": "Scientific",
             "match": '["View", "Scientific"]', "attr": "Title",
             "menu_path": ["View", "Scientific"], "line": "MenuItem",
             "effect": menu_effect(BEFORE, AFTER, ["View", "Scientific"])},
            key("2", "Two"), key("5", "Five"), key("Square Root", "SquareRoot"),
            {"do": "read", "role": "StaticText", "label": "Edit field",
             "match": "Edit field", "attr": "Description",
             "container": "StandardInputView", "read_attribute": "Value",
             "line": "    3 StaticText  Description: Edit field, Value: 5"}])


class WalkToTemplateTests(unittest.TestCase):
    def test_menu_effect_is_the_one_element_that_changed_to_name_the_command(self):
        seen = menu_effect(BEFORE, AFTER, ["View", "Scientific"])
        self.assertEqual({"role": "Button", "attr": "Description", "match": "Mode",
                          "attribute": "Identifier",
                          "equals": "Mode: scientific; unitConversion: false"}, seen)
        post = _menu_check({"effect": seen})
        self.assertTrue(post.check(AFTER))
        self.assertFalse(post.check(BEFORE))

    def test_no_change_or_an_unrelated_change_is_not_an_effect(self):
        self.assertIsNone(menu_effect(BEFORE, BEFORE, ["View", "Scientific"]))
        other = BEFORE.replace("Value: 25", "Value: 26")
        self.assertIsNone(menu_effect(BEFORE, other, ["View", "Scientific"]))
        self.assertIsNone(_menu_check({"effect": {"role": "Button"}}))

    def test_two_candidate_effects_are_a_guess_and_refused(self):
        two = BEFORE + "\n  4 Button  Description: Other, Identifier: x"
        after = AFTER + "\n  4 Button  Description: Other, Identifier: scientific on"
        self.assertIsNone(menu_effect(two, after, ["View", "Scientific"]))

    def test_container_is_named_by_what_it_is_not_what_it_shows(self):
        lines = BEFORE.splitlines()
        self.assertEqual("StandardInputView", _place(lines, 3)[0])

    def test_digits_typed_one_at_a_time_are_one_value(self):
        self.assertEqual({"25": [1, 2]}, parameters(walked()))

    def test_a_walked_recipe_deploys_and_its_template_reproduces_it(self):
        flow, why = deployable(walked(), "sqrt", "계산기에서 25의 제곱근 구해줘")
        self.assertEqual([], why)
        template = parameterize(flow, {"25": "n"}, tables=digits_table())
        folded = [s for s in template.steps if s.for_each]
        self.assertEqual(1, len(folded))
        self.assertEqual("{{item}}", folded[0].selector.desc)
        again = template.bind(n="25")
        self.assertEqual([s.selector.desc if s.selector else None for s in flow.steps],
                         [s.selector.desc if s.selector else None for s in again.steps])
        other = template.bind(n="49")
        self.assertEqual(["4", "9"], [s.selector.desc for s in other.steps[1:3]])
        self.assertEqual(["4", "9"], [s.binding.desc for s in other.steps[1:3]])

    def test_promote_refuses_a_value_that_did_not_come_from_the_request(self):
        from nlc import cli
        rec = {"blocked_by": [], "parameters": {"25": [1, 2]},
               "flow": json.loads(to_flow(walked(), "sqrt").to_json())}
        with patch("nlc.promote.CandidateStore.get", return_value=rec), \
             patch("nlc.cli.FlowStore") as store:
            out = StringIO()
            with redirect_stdout(out):
                code = cli.main(["promote", "sqrt", "-p", "5=n"])
        self.assertEqual(1, code)
        store.return_value.save.assert_not_called()
        self.assertIn("부탁에서 온 값이 아닙니다", out.getvalue())

    def test_promote_saves_a_template_that_names_its_input(self):
        from nlc import cli
        rec = {"blocked_by": [], "parameters": {"25": [1, 2]},
               "flow": json.loads(to_flow(walked(), "sqrt").to_json())}
        with patch("nlc.promote.CandidateStore.get", return_value=rec), \
             patch("nlc.cli.FlowStore") as store:
            with redirect_stdout(StringIO()):
                code = cli.main(["promote", "sqrt", "-p", "25=n"])
        self.assertEqual(0, code)
        saved = store.return_value.save.call_args.args[0]
        self.assertEqual({"n": "25"}, saved.params)


    def test_a_replay_says_the_answer_it_read(self):
        from types import SimpleNamespace
        from nlc import cli
        done = SimpleNamespace(ok=True, log=[{"status": "ok", "captured": "12"},
                                             {"status": "done", "result": None,
                                              "captured": {"결과": "12"}}])
        agent = patch("nlc.cli.Agent").start()
        self.addCleanup(patch.stopall)
        agent.return_value.authorize.return_value = ({}, [])
        agent.return_value.commit_adapter_hooks.return_value = {}
        with patch("nlc.flow.replay", return_value=done), \
             patch("nlc.cli._make_driver"), patch("nlc.cli._indicator_for"):
            out = StringIO()
            with redirect_stdout(out):
                code = cli._run_flow(to_flow(walked(), "sqrt"), ["--yes"])
        self.assertEqual(0, code)
        self.assertIn("done — 결과: 12", out.getvalue())


class ExplorationTests(unittest.TestCase):
    """The walk opened Calculator's Mode popup, then used the menu bar instead."""
    POPUP = [["MenuItem", "Basic"], ["MenuItem", "Scientific"]]

    def walk(self, *, used=False, stayed=False, changed=False, last=False):
        mode = {**key("Mode", "Mode"), "screen_change": {
            "appeared": self.POPUP, "vanished": [], "changed": changed}}
        menu = {"do": "menu", "role": "MenuPath", "label": "Scientific",
                "match": "Scientific", "screen_change": {
                    "appeared": [], "vanished": [] if stayed else self.POPUP, "changed": True}}
        pick = {**key("Scientific", "x"), "role": "MenuItem"}
        return [mode] if last else [mode, pick if used else menu, key("2", "Two")]

    def test_a_popup_nobody_used_is_left_out_and_kept_on_record(self):
        from nlc.promote import without_exploration
        kept, explored = without_exploration(self.walk())
        self.assertEqual(["Mode"], [a["label"] for a in explored])
        self.assertEqual(["Scientific", "2"], [a["label"] for a in kept])

    def test_anything_less_than_all_the_evidence_keeps_the_step(self):
        from nlc.promote import without_exploration
        for case in ({"used": True}, {"stayed": True}, {"changed": True}, {"last": True}):
            kept, explored = without_exploration(self.walk(**case))
            self.assertEqual([], explored, case)

    def test_screen_change_is_by_name(self):
        from nlc.general import screen_change
        popup = BEFORE + "\n  9 MenuItem  Title: Scientific"
        self.assertEqual({"appeared": [["MenuItem", "Scientific"]], "vanished": [],
                          "changed": False}, screen_change(BEFORE, popup))
        self.assertTrue(screen_change(BEFORE, AFTER)["changed"])


class WalkedRecipeBudgetTests(unittest.TestCase):
    def run_turn(self, flow, think):
        from nlc.agent import Agent, Match
        seen = {}
        agent = Agent(driver=object(), store=type("S", (), {
            "has": lambda s, n: True, "load": lambda s, n: flow, "list": lambda s: []})())
        agent.resolve = lambda intent: Match("f", {})
        agent.authorize = lambda f, ask: ({}, [])
        agent.commit_adapter_for = lambda f: None
        agent.commit_adapter_hooks = lambda r: {}
        agent._report_what_it_asked = lambda f, r: None
        agent._to_turn = lambda f, r: r
        with patch("nlc.agent.replay", side_effect=lambda *a, **k: seen.update(k)), \
             patch("nlc.judge.model_judge", return_value=lambda why: None), \
             patch("nlc.library.builders", return_value={}):
            agent.handle("x", think=think)
        return seen["budget"]

    def test_a_walked_recipe_may_ask_once_and_a_written_one_may_not(self):
        walked = Flow("f", "t", intents=["x"])
        written = Flow("f", "t")
        self.assertEqual(1, self.run_turn(walked, None))
        self.assertEqual(0, self.run_turn(written, None))
        self.assertEqual(0, self.run_turn(walked, 0))
        self.assertEqual(3, self.run_turn(written, 3))

    def test_only_steps_that_change_nothing_lasting_may_be_reaimed(self):
        flow = to_flow(walked(), "sqrt")
        self.assertTrue(all(s.on_uncertain == ["target_missing", "target_ambiguous"]
                            for s in flow.steps if s.action == "click"))

if __name__ == "__main__":
    unittest.main()
