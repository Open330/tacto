import unittest

from tacto.candidate_paging import (
    StaleCursor, page_candidates, utf8_token_upper_bound,
)


class CandidatePagingTests(unittest.TestCase):
    def test_all_candidates_accessible_with_original_picks(self):
        candidates = [{"label": f"항목 {i}", "can": ["read"]}
                      for i in range(103)]
        cursor = None
        seen = []
        while True:
            page = page_candidates(candidates, budget=200, cursor=cursor)
            self.assertFalse(page.overflow)
            self.assertLessEqual(page.token_count, 200)
            self.assertEqual(page.total, 103)
            seen.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual([pick for pick, _ in seen], list(range(103)))
        self.assertEqual([row for _, row in seen], candidates)

    def test_oversized_candidate_is_intact_and_next_still_accessible(self):
        secret_long_value = "가" * 1000
        candidates = [{"value": secret_long_value}, {"value": "small"}]
        page = page_candidates(candidates, budget=100)
        self.assertTrue(page.overflow)
        self.assertEqual(page.picks, (0,))
        self.assertIn(secret_long_value, page.text)
        self.assertEqual(page.candidates[0], candidates[0])
        following = page_candidates(candidates, budget=100,
                                    cursor=page.next_cursor)
        self.assertEqual(following.picks, (1,))
        self.assertFalse(following.overflow)
        self.assertIsNone(following.next_cursor)

    def test_full_prompt_overhead_and_injected_counter_are_budgeted(self):
        def render(items):
            return "instructions goal history " + " ".join(
                f"pick{pick}" for pick, _ in items)

        page = page_candidates([{}, {}, {}], budget=4, render=render,
                               token_counter=lambda text: len(text.split()))
        self.assertEqual(page.picks, (0,))
        self.assertEqual(page.token_count, 4)
        self.assertEqual(page.text, "instructions goal history pick0")
        following = page_candidates([{}, {}, {}], budget=5, render=render,
                                    token_counter=lambda text: len(text.split()),
                                    cursor=page.next_cursor)
        self.assertEqual(following.picks, (1, 2))

    def test_changed_candidate_fields_or_context_invalidate_cursor(self):
        rows = [{"label": "A", "value": ""}, {"label": "B"}]
        cursor = page_candidates(rows, budget=1, context="obs1").next_cursor
        for changed in (
            [{"label": "A", "value": "typed"}, rows[1]],
            [rows[0], {"label": "B", "disabled": True}],
            list(reversed(rows)),
        ):
            with self.assertRaises(StaleCursor):
                page_candidates(changed, budget=1, context="obs1", cursor=cursor)
        with self.assertRaises(StaleCursor):
            page_candidates(rows, budget=1, context="obs2", cursor=cursor)

    def test_invalid_cursor_and_budget_are_rejected(self):
        for budget in (True, 0, -1, 3.5):
            with self.assertRaises(ValueError):
                page_candidates([{}], budget=budget)
        fingerprint = page_candidates([{}], budget=100).fingerprint
        for cursor in (42, "", "v2:ignored:0", f"v1:{fingerprint}:-1",
                       f"v1:{fingerprint}:1", f"v1:{fingerprint}:١"):
            with self.assertRaises(ValueError):
                page_candidates([{}], budget=100, cursor=cursor)

    def test_empty_candidates_and_oversized_fixed_prompt(self):
        page = page_candidates([], budget=2)
        self.assertEqual(page.items, ())
        self.assertEqual(page.total, 0)
        self.assertIsNone(page.next_cursor)
        self.assertFalse(page.overflow)
        page = page_candidates([], budget=2, render=lambda _: "too long")
        self.assertTrue(page.overflow)

    def test_default_counter_counts_utf8_bytes_not_codepoints(self):
        self.assertEqual(utf8_token_upper_bound("가a"), 4)

    def test_malformed_counter_or_renderer_fails_explicitly(self):
        for result in (True, -1, 1.5):
            with self.assertRaises(ValueError):
                page_candidates([{}], budget=100,
                                token_counter=lambda _, result=result: result)
        with self.assertRaises(TypeError):
            page_candidates([{}], budget=100, render=lambda _: None)


if __name__ == "__main__":
    unittest.main()
