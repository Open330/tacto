"""Ready hints shorten the settle after unverified steps without weakening it."""
import json
import unittest
from unittest.mock import patch

from tacto.contract import Envelope
from tacto.flow import Flow, ReadyHint, Selector, Step, replay
from tacto.helper import HelperDriver

SAFETY = Envelope.minimal().contract_for({"write_ephemeral"}, "entry")
DISPLAY = Selector(identifier="display")


class Clock:
    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class Pad:
    """A keypad app: each press shows its digit after `lag` seconds."""

    def __init__(self, clock, *, lag=0.0, error_after_press=None, loading=False):
        self.clock, self.lag, self.error_after_press = clock, lag, error_after_press
        self.loading = loading
        self.display, self.pending, self.presses = "0", None, []
        self.helper = object.__new__(HelperDriver)
        self.helper._last_action_at = 0
        self.helper._send = self.send

    def tree(self):
        if self.pending and self.clock.now >= self.pending[0]:
            self.display, self.pending = self.pending[1], None
        # While a press is landing, an app may show an intermediate value.
        shown = "loading" if self.loading and self.pending else self.display
        return ("0 Window  Title: Pad\n  1 Button  Identifier: one\n  2 Button  Identifier: two\n"
                f"  3 StaticText  Identifier: display, Value: {shown}")

    def send(self, message):
        if self.error_after_press and self.presses:
            return {"type": "error", "error": self.error_after_press}
        return {"type": "observation", "obs_id": f"o{self.clock.now}", "target": "pad", "tree": self.tree()}

    def identity(self, target): return target
    def selection(self, target): return {"type": "selection", "digest": "stable"}
    def probe(self, obs_id, target, element_index):
        return {"type": "probe", "line": self.tree().splitlines()[element_index]}
    def observe(self, *args, **kwargs): return self.helper.observe(*args, **kwargs)
    def wait_until(self, *args, **kwargs): return self.helper.wait_until(*args, **kwargs)

    def act(self, obs_id, target, action, **kwargs):
        line = self.tree().splitlines()[action["element_index"]]
        digit = "1" if "one" in line else "2"
        self.presses.append(self.clock.now)
        shown = digit if self.display == "0" else self.display + digit
        self.pending = (self.clock.now + self.lag, shown)
        self.helper._last_action_at = self.clock.now
        return {"type": "acted"}


def press(identifier, hint=None):
    return Step("click", Selector(identifier=identifier), safety=SAFETY, ready_when=hint)


def run(pad, clock, flow):
    with patch("tacto.helper.time.monotonic", side_effect=clock.monotonic), \
         patch("tacto.helper.time.sleep", side_effect=clock.sleep):
        return replay(pad, flow.bind())


class ReadyHintReplayTests(unittest.TestCase):
    def test_a_met_hint_moves_on_without_the_fixed_settle(self):
        clock = Clock(); pad = Pad(clock, lag=0.05)
        result = run(pad, clock, Flow("p", "pad", [press("one", ReadyHint(DISPLAY, "Value", "1")),
                                                   press("two", ReadyHint(DISPLAY, "Value", "12"))], schema=2))
        self.assertTrue(result.ok, result.log)
        self.assertEqual("12", pad.display)
        self.assertLess(pad.presses[1] - pad.presses[0], 0.5)
        self.assertEqual(["met", "met"], [e["ready"] for e in result.log if e.get("status") == "ready_hint"])
        self.assertFalse(any(e.get("verified") for e in result.log if e.get("status") == "ready_hint"))

    def test_an_unmet_hint_settles_exactly_as_without_one(self):
        for hint in (ReadyHint(DISPLAY, "Value", "never"), None):
            clock = Clock(); pad = Pad(clock)
            run(pad, clock, Flow("p", "pad", [press("one", hint), press("two")], schema=2))
            with self.subTest(hint=hint):
                self.assertGreaterEqual(pad.presses[1] - pad.presses[0], 1.0)

    def test_a_hint_already_true_before_the_action_is_not_used(self):
        clock = Clock(); pad = Pad(clock, lag=0.5)
        pad.display = "1"
        result = run(pad, clock, Flow("p", "pad", [press("one", ReadyHint(DISPLAY, "Value", "1")),
                                                   press("two")], schema=2))
        self.assertTrue(result.ok, result.log)
        self.assertGreaterEqual(pad.presses[1] - pad.presses[0], 1.0)

    def test_a_terminal_error_while_waiting_stops_the_run(self):
        clock = Clock(); pad = Pad(clock, error_after_press="aborted_by_user")
        result = run(pad, clock, Flow("p", "pad", [press("one", ReadyHint(DISPLAY, "Value", "1")), press("two")], schema=2))
        self.assertFalse(result.ok)
        self.assertEqual(("ready_wait", "aborted_by_user"), (result.log[-1].get("stage"), result.log[-1].get("error")))
        self.assertEqual(1, len(pad.presses))

    def test_an_intermediate_value_does_not_release_the_next_press(self):
        # The app shows "loading" while the first press lands (0.6 s). The
        # next press must still find it landed, with a hint as without one.
        for hint in (ReadyHint(DISPLAY, "Value", "1"), None):
            clock = Clock(); pad = Pad(clock, lag=0.6, loading=True)
            result = run(pad, clock, Flow("p", "pad", [press("one", hint), press("two")], schema=2))
            clock.now += 2; pad.tree()
            with self.subTest(hint=hint):
                self.assertTrue(result.ok, result.log)
                self.assertGreaterEqual(pad.presses[1] - pad.presses[0], 0.6)
                self.assertEqual("12", pad.display)


class ReadyHintFormatTests(unittest.TestCase):
    def test_hints_survive_saving(self):
        flow = Flow("p", "pad", [press("one", ReadyHint(DISPLAY, "Value", "1"))], schema=2)
        again = Flow.from_json(flow.to_json())
        self.assertEqual(flow.steps[0].ready_when, again.steps[0].ready_when)

    def test_allow_nth_zero_survives_saving(self):
        tree = ("0 Window  Title: Pad\n  1 StaticText  Identifier: display, Value: 1\n"
                "  2 StaticText  Identifier: display, Value: 2")
        hint = ReadyHint(Selector(role="StaticText", identifier="display", allow_nth=0), "Value", "1")
        flow = Flow("p", "pad", [press("one", hint)], schema=2)
        again = Flow.from_json(flow.to_json()).steps[0].ready_when
        self.assertEqual(0, again.field.allow_nth)
        self.assertTrue(hint.met(tree))
        self.assertTrue(again.met(tree))

    def test_the_element_holding_a_value_is_read_through_its_block(self):
        # Calculator keeps the displayed Value on a child of the named
        # ScrollArea, whose identifier carries the value itself.
        tree = ("0 Window  Title: \"Calculator\"\n  1 Group  Identifier: CalculatorKeypadView\n"
                "    2 ScrollArea  Identifier: StandardInputView;value:12\n"
                "      3 StaticText  Description: Edit field, Value: 12\n    4 Button  Identifier: One")
        hint = ReadyHint(Selector(role="ScrollArea", identifier="StandardInputView"), "Value", "12")
        self.assertEqual("12", hint.read(tree))
        self.assertTrue(hint.met(tree))

    def test_a_malformed_hint_is_refused_before_anything_is_sent(self):
        base = json.loads(Flow("p", "pad", [press("one")], schema=2).to_json())
        for bad in ({"attribute": "Value"},
                    {"field": {"identifier": "display"}, "attribute": "Value"},
                    {"field": {"identifier": 42}, "attribute": "Value", "equals": "1"},
                    {"field": {"role": ["Button"]}, "attribute": "Value", "equals": "1"},
                    {"field": {"identifier": "display", "allow_nth": "0"}, "attribute": "Value", "equals": "1"},
                    {"field": {"identifier": []}, "attribute": "Value", "equals": "1"},
                    {"field": {"nearby": "x"}, "attribute": "Value", "equals": "1"},
                    {"field": {"allow_nth": 0}, "attribute": "Value", "equals": "1"},
                    {"field": {"identifier": "display"}, "attribute": "Value", "equals": 1}):
            doc = json.loads(json.dumps(base))
            doc["steps"][0]["ready_when"] = bad
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Flow.from_json(json.dumps(doc))


class BuiltinHintTests(unittest.TestCase):
    def test_only_calculator_recipes_carry_hints(self):
        from tacto.library import builders
        built = {name: build() for name, build in builders().items()}
        with_hints = sorted(name for name, flow in built.items() if any(st.ready_when for st in flow.steps))
        self.assertEqual(["calc_12x34", "calc_arith", "calc_multiply"], with_hints)
        self.assertEqual(["0", "1", "12", "12 ×", "12 × 3", "12 × 34"],
                         [st.ready_when.equals for st in built["calc_12x34"].steps if st.ready_when])

    def test_arithmetic_hints_name_each_exact_display(self):
        # Read off Calculator on macOS 27; the minus is U+2212.
        from tacto.library import builders
        for op, shown in (("+", "+"), ("-", "\u2212"), ("*", "×"), ("/", "÷")):
            flow = builders()["calc_arith"]().bind(a="75", b="81", op=op)
            with self.subTest(op=op):
                self.assertEqual(["0", "7", "75", f"75 {shown}", f"75 {shown} 8", f"75 {shown} 81"],
                                 [st.ready_when.equals for st in flow.steps if st.ready_when])
        flow = builders()["calc_multiply"]().bind(a="9", b="40")
        self.assertEqual(["0", "9", "9 ×", "9 × 4", "9 × 40"],
                         [st.ready_when.equals for st in flow.steps if st.ready_when])

    def test_loops_without_such_a_hint_pay_nothing_for_it(self):
        import time
        step = Step("click", Selector(identifier="{{item}}"), safety=SAFETY, for_each="{{n}}")
        flow = Flow("p", "pad", [step], params={"n": "7" * 8000}, schema=2)
        started = time.monotonic()
        bound = flow.bind()
        self.assertEqual(8000, len(bound.steps))
        # Quadratic prefix building took about 1.1 s here; linear binding is far below.
        self.assertLess(time.monotonic() - started, 0.5)

    def test_items_so_far_is_what_the_loop_has_entered(self):
        step = Step("click", Selector(identifier="{{item}}"), safety=SAFETY, for_each="{{n}}",
                    ready_when=ReadyHint(DISPLAY, "Value", "{{items_so_far}}"))
        bound = Flow("p", "pad", [step], params={"n": "305"}, schema=2).bind()
        self.assertEqual(["3", "30", "305"], [st.ready_when.equals for st in bound.steps])


if __name__ == "__main__":
    unittest.main()
