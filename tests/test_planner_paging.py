import re
import unittest
from unittest.mock import patch

from tacto.ask import ask_here_paged, what_to_do_here


def row_counter(prompt):
    # Deterministic injected counter: one unit of overhead plus one per row.
    return 1 + len(re.findall(r"^\d+\. ", prompt, re.MULTILINE))


class PlannerPagingTests(unittest.TestCase):
    def setUp(self):
        self.candidates = [{"label": f"row {i}", "can": ["read"]}
                           for i in range(5)]

    def run_plan(self, responder, **kwargs):
        with patch("tacto.ask.ask", side_effect=lambda q, **_: responder(q)) as calls:
            result = ask_here_paged("read row 4", self.candidates, budget=2,
                                    token_counter=row_counter, **kwargs)
        return result, calls

    def test_later_page_retains_original_global_pick(self):
        def respond(q):
            pick = q.shown["picks"][0]
            if pick != 4:
                return q.check({"actions": [], "next_page": True})
            self.assertIn("4. row 4", q.prompt)
            return q.check({"actions": [{"pick": 4, "do": "read"}]})

        result, calls = self.run_plan(respond)
        self.assertEqual(calls.call_count, 5)
        self.assertEqual(result["actions"][0]["pick"], 4)
        self.assertEqual(result["actions"][0]["candidate"], self.candidates[4])

    def test_hidden_or_previously_shown_picks_are_rejected(self):
        for answer in ({"actions": [{"pick": 4, "do": "read"}]},
                       {"actions": [{"pick": True, "do": "read"}]}):
            result, calls = self.run_plan(lambda q: q.check(answer))
            self.assertIsNone(result)
            self.assertEqual(calls.call_count, 1)
        def respond(q):
            return q.check({"actions": [], "next_page": True} if q.shown["picks"] == [0]
                           else {"actions": [{"pick": 0, "do": "read"}]})
        result, calls = self.run_plan(respond)
        self.assertIsNone(result)
        self.assertEqual(calls.call_count, 2)

    def test_page_limit_is_explicit_and_does_not_make_extra_call(self):
        result, calls = self.run_plan(
            lambda q: q.check({"actions": [], "next_page": True}), max_pages=2)
        self.assertEqual(result["stop_reason"], "candidate_page_limit")
        self.assertEqual(calls.call_count, 2)
        self.assertEqual(result["actions"], [])

    def test_oversized_prompt_never_reaches_model(self):
        with patch("tacto.ask.ask") as ask:
            result = ask_here_paged("read", self.candidates, budget=1)
        ask.assert_not_called()
        self.assertEqual(result["stop_reason"], "candidate_budget_overflow")

    def test_next_page_cannot_mix_actions_or_done_or_exceed_last_page(self):
        for answer in ({"next_page": True, "actions": [], "done": True},
                       {"next_page": True, "actions": [{"pick": 0, "do": "read"}]},
                       {"next_page": "true", "actions": []},
                       {"done": True, "actions": [{"pick": 0, "do": "read"}]}):
            result, _ = self.run_plan(lambda q: q.check(answer))
            self.assertIsNone(result)
        self.candidates = self.candidates[:1]
        result, _ = self.run_plan(lambda q: q.check({"next_page": True, "actions": []}))
        self.assertIsNone(result)

    def test_paged_prompt_preserves_values_options_goal_and_history(self):
        long_value = "x" * 600
        candidate = {"label": "size", "can": ["select_option"],
                     "line": "0 combobox Title: size, Value: " + long_value,
                     "options": [{"value": "XL", "label": "Extra large", "disabled": False}]}
        def respond(q, **_):
            for text in (long_value, "g" * 350, "history zero", '"value": "XL"'):
                self.assertIn(text, q.prompt)
            return q.check({"actions": [{"pick": 0, "do": "select_option", "value": "XL"}]})
        with patch("tacto.ask.ask", side_effect=respond):
            result = ask_here_paged("g" * 350, [candidate],
                                    ["history zero"] + ["other"] * 8)
        self.assertEqual(result["actions"][0]["value"], "XL")

    def test_legacy_question_metadata_and_actions_remain_compatible(self):
        q = what_to_do_here("read", self.candidates)
        self.assertEqual(q.shown, {"goal": "read", "n": 5})
        self.assertEqual(q.check({"actions": [{"pick": 4, "do": "read"}]})["actions"][0]["pick"], 4)
        self.assertIsNone(q.check({"actions": [], "next_page": True}))

    def test_page_budget_is_not_silent_action_truncation(self):
        result, _ = self.run_plan(lambda q: q.check(
            {"actions": [{"pick": 0, "do": "read"}] * 9}))
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
