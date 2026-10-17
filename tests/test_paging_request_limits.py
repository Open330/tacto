"""Exercise actual ask retries, rather than mocking away request accounting."""
import unittest
from unittest.mock import patch

from tacto.ask import _here_question, ask_here_paged


class PagingRequestLimitsTests(unittest.TestCase):
    def setUp(self):
        self.candidate = {'label': 'owned value', 'can': ['read'], 'line': 'Value: 42'}
        self.goal = 'read owned value'
        self.initial = _here_question(self.goal, [self.candidate], (), 8,
                                      picks=(0,), total=1).prompt

    def test_retry_instruction_cannot_exceed_full_prompt_budget(self):
        sent = []

        def respond(prompt, *args, **kwargs):
            sent.append(prompt)
            return '{"actions":[{"pick":999,"do":"read"}]}'

        budget = len(self.initial) + 1
        with patch('tacto.ask.reach_model', side_effect=respond):
            result = ask_here_paged(self.goal, [self.candidate], budget=budget,
                                    token_counter=len, max_pages=8)
        self.assertEqual(1, len(sent))
        self.assertTrue(all(len(prompt) <= budget for prompt in sent))
        self.assertEqual('context_budget_overflow', result['stop_reason'])
        self.assertGreater(result['paging']['token_count'], budget)
        self.assertEqual(1, result['paging']['requests_used'])
        self.assertEqual('validation_retry', result['paging']['phase'])
        self.assertEqual([], result['actions'])

    def test_rejected_answer_does_not_get_an_extra_call_past_limit(self):
        with patch('tacto.ask.reach_model', return_value='{"actions":[{"pick":999,"do":"read"}]}') as call:
            result = ask_here_paged(self.goal, [self.candidate], max_pages=1)
        self.assertEqual(1, call.call_count)
        self.assertEqual('model_request_limit', result['stop_reason'])
        self.assertEqual(1, result['paging']['requests_used'])

    def test_fitting_retry_still_selects_the_original_candidate(self):
        with patch('tacto.ask.reach_model', side_effect=(
                '{"actions":[{"pick":999,"do":"read"}]}',
                '{"actions":[{"pick":0,"do":"read"}]}')) as call:
            result = ask_here_paged(self.goal, [self.candidate], max_pages=2)
        self.assertEqual(2, call.call_count)
        self.assertEqual(self.candidate, result['actions'][0]['candidate'])

    def test_retry_counter_failure_is_not_misclassified_as_model_failure(self):
        def counter(prompt):
            if '이전 답은 호스트 검증을 통과하지 못했고' in prompt:
                raise ValueError('PRIVATE counter error')
            return len(prompt)

        with patch('tacto.ask.reach_model', return_value='{"actions":[{"pick":999,"do":"read"}]}') as call:
            result = ask_here_paged(self.goal, [self.candidate], token_counter=counter)
        self.assertEqual(1, call.call_count)
        self.assertEqual('token_counter_failed', result['stop_reason'])
        self.assertIsNone(result['paging']['token_count'])
        self.assertEqual('validation_retry', result['paging']['phase'])
        self.assertNotIn('PRIVATE', str(result))

    def test_retry_consumes_a_request_before_asking_for_next_page(self):
        candidates = [dict(self.candidate, label=f'item {i} ' + 'x' * 80)
                      for i in range(100)]
        sent = []

        def respond(prompt, *args, **kwargs):
            sent.append(prompt)
            return ('{"actions":[{"pick":999,"do":"read"}]}'
                    if len(sent) == 1 else '{"actions":[],"next_page":true}')

        with patch('tacto.ask.reach_model', side_effect=respond):
            result = ask_here_paged(self.goal, candidates, ['x' * 20000],
                                    budget=16000, token_counter=len, max_pages=2)
        self.assertEqual(2, len(sent))
        self.assertEqual('candidate_page_limit', result['stop_reason'])
        self.assertEqual([], result['actions'])


if __name__ == '__main__':
    unittest.main()
