"""Rejected plans may be repaired before dispatch, within one model budget."""
import unittest
from unittest.mock import patch

from tacto.ask import Q, ask


class PlanValidationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.question = Q('next_action', 'host goal and observed candidates',
                          lambda value: value if value == {'done': True} else None)

    def test_rejected_plan_can_be_repaired_without_reusing_rejected_output(self):
        with patch('tacto.ask.reach_model', side_effect=[
                '{"untrusted":"private rejected output"}', '{"done":true}']) as model:
            self.assertEqual({'done': True}, ask(self.question, 'codex', validation_retries=1))
        self.assertEqual(2, model.call_count)
        prompt = model.call_args.args[0]
        self.assertIn(self.question.prompt, prompt)
        self.assertNotIn('private rejected output', prompt)
        self.assertEqual('host goal and observed candidates', self.question.prompt)
        self.assertTrue(all(call.args[1] == 'codex' for call in model.call_args_list))

    def test_repair_stops_at_limit_and_provider_errors_are_not_retried(self):
        with patch('tacto.ask.reach_model', return_value='{}') as model:
            self.assertIsNone(ask(self.question, validation_retries=1))
        self.assertEqual(2, model.call_count)
        with patch('tacto.ask.reach_model', side_effect=RuntimeError('provider unavailable')) as model:
            self.assertIsNone(ask(self.question, validation_retries=2))
        model.assert_called_once()
        with patch('tacto.ask.reach_model', return_value='{}') as model:
            self.assertIsNone(ask(self.question))
        model.assert_called_once()

    def test_repair_cannot_extend_the_original_deadline(self):
        now = [0.0]
        def rejected(*args, **kwargs):
            now[0] = 6.0
            return '{}'
        with patch('tacto.ask.time.monotonic', side_effect=lambda: now[0]), \
             patch('tacto.ask.reach_model', side_effect=rejected) as model:
            with self.assertRaises(TimeoutError):
                ask(self.question, timeout_seconds=5, validation_retries=1)
        model.assert_called_once()

    def test_repair_budget_configuration_is_validated_before_model_call(self):
        for value in (True, -1, 3, '1', None):
            with self.subTest(value=value), patch('tacto.ask.reach_model') as model:
                with self.assertRaises(ValueError):
                    ask(self.question, validation_retries=value)
                model.assert_not_called()
