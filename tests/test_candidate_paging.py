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

    def test_page_snapshot_does_not_share_mutable_candidate_fields(self):
        rows=[{'label':'A','can':['read']},{'label':'B','can':['click']}]
        page=page_candidates(rows,budget=1,context='obs')
        rows[0]['can'].append('click')
        self.assertEqual(page.candidates[0]['can'],['read'])
        with self.assertRaises(StaleCursor):page_candidates(rows,budget=1,context='obs',cursor=page.next_cursor)
        page.candidates[0]['can'].append('delete')
        self.assertNotIn('delete',rows[0]['can'])

    def test_renderer_mutation_is_rejected_without_changing_original_candidates(self):
        rows=[{'label':'A','can':['read']}]
        def render(items):
            if items:items[0][1]['can'].append('click')
            return 'prompt'
        with self.assertRaisesRegex(ValueError,'renderer modified candidates'):
            page_candidates(rows,budget=100,render=render)
        self.assertEqual(rows,[{'label':'A','can':['read']}])

    def test_non_json_value_is_rejected_before_its_copy_hook_runs(self):
        class NonData:
            def __deepcopy__(self,memo):
                self.fail_if_copied=True
                raise AssertionError('non-data copy hook should not run')
        value=NonData()
        with self.assertRaises(TypeError):page_candidates([{'callback':value}],budget=100)
        self.assertFalse(hasattr(value,'fail_if_copied'))

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

    def test_order_presents_every_candidate_once_with_original_picks(self):
        candidates = [{"label": f"항목 {i}", "can": ["read"]} for i in range(40)]
        order = [7, 39, *[i for i in range(40) if i not in (7, 39)]]
        cursor, seen = None, []
        while True:
            page = page_candidates(candidates, budget=200, cursor=cursor, order=order)
            self.assertFalse(page.overflow)
            seen.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        self.assertEqual(order, [pick for pick, _ in seen])
        self.assertEqual([candidates[pick] for pick in order], [row for _, row in seen])

    def test_order_must_be_a_permutation_and_binds_its_cursor(self):
        rows = [{"label": "A"}, {"label": "B"}, {"label": "C"}]
        for invalid in ([0, 1], [0, 1, 1], [0, 1, 3], [0, 1, True], [0, 1, "2"]):
            with self.subTest(order=invalid), self.assertRaises(ValueError):
                page_candidates(rows, budget=1, order=invalid)
        plain = page_candidates(rows, budget=1).next_cursor
        ordered = page_candidates(rows, budget=1, order=[2, 0, 1]).next_cursor
        self.assertEqual(plain, page_candidates(rows, budget=1, order=None).next_cursor)
        for cursor, order in ((plain, [2, 0, 1]), (ordered, None), (ordered, [2, 1, 0])):
            with self.subTest(order=order), self.assertRaises(StaleCursor):
                page_candidates(rows, budget=1, cursor=cursor, order=order)
        self.assertEqual((0,), page_candidates(rows, budget=1, cursor=ordered,
                                               order=[2, 0, 1]).picks)


if __name__ == "__main__":
    unittest.main()
