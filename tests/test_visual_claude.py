import unittest
from unittest.mock import patch

from nlc.provider_result import ProviderResult
from nlc.visual import VisualCapture, VisualError, plan_visual, query_visual


class ClaudeVisual(unittest.TestCase):
    def setUp(self):
        self.capture = VisualCapture('v1', 'b1', 'page', 'digest', 'state',
            {'width': 80, 'height': 40}, 1, b'private image')

    def result(self, **changes):
        values = dict(request_id='request', requested_provider='claude', provider='claude',
            transport='cli', status='ok', text='{"answer":"blue"}', error=None,
            elapsed_seconds=1, model='observed-model', input_tokens=20, output_tokens=3)
        values.update(changes)
        return ProviderResult(**values)

    def test_image_is_sent_to_pinned_provider_and_metadata_is_observed(self):
        with patch('nlc.claude_result.claude_result', return_value=self.result()) as call, \
             patch('nlc.visual._run_visual') as codex:
            result = query_visual(self.capture, 'What color?', provider='claude', model='requested-model')
        self.assertEqual(b'private image', call.call_args.kwargs['image_bytes'])
        self.assertEqual('image/png', call.call_args.kwargs['image_media_type'])
        self.assertEqual('blue', result['answer'])
        self.assertEqual('observed-model', result['model'])
        self.assertEqual('requested-model', result['requested_model'])
        self.assertEqual(20, result['provider_result']['input_tokens'])
        self.assertNotIn('text', result['provider_result'])
        self.assertFalse(result['commit_witness'])
        self.assertFalse(result['authorizes_action'])
        codex.assert_not_called()

    def test_failure_or_invalid_answer_never_falls_back(self):
        for response in (self.result(status='error', text=None),
                         self.result(text='{"answer":"ok","authorize":true}'),
                         self.result(text='[]')):
            with self.subTest(response=response.status), \
                 patch('nlc.claude_result.claude_result', return_value=response), \
                 patch('nlc.visual._run_visual') as codex:
                with self.assertRaises(VisualError):
                    query_visual(self.capture, 'Read', provider='claude')
                codex.assert_not_called()

    def test_native_image_coordinates_are_not_browser_clicks(self):
        self.capture.viewport['coordinate_space'] = 'window_pixels'
        with patch('nlc.visual.query_visual') as query:
            with self.assertRaisesRegex(VisualError, 'coordinate_space'):
                plan_visual(self.capture, 'Click', provider='claude')
            query.assert_not_called()


if __name__ == '__main__':
    unittest.main()
