import unittest
from unittest.mock import patch

from nlc.visual import VisualCapture, VisualError, plan_visual, query_visual


class VisualPlanner(unittest.TestCase):
    def setUp(self):
        self.capture = VisualCapture('v1', 'b1', 'page', 'hash', 'state',
                                     {'width': 100, 'height': 60}, 1, b'png')

    def test_proposal_preserves_css_coordinates_and_provider(self):
        with patch('nlc.visual.query_visual', return_value={'answer':
                '{"action":"click","x":42.5,"y":30}'}) as query:
            proposal = plan_visual(self.capture, 'Choose blue', provider='codex',
                                   model='test-model', timeout=12)
        self.assertEqual({'action': 'click', 'x': 42.5, 'y': 30}, proposal)
        self.assertEqual('test-model', query.call_args.kwargs['model'])
        self.assertEqual(12, query.call_args.kwargs['timeout'])
        self.assertIn('CSS viewport pixels', query.call_args.args[1])

    def test_invalid_or_authority_bearing_proposals_are_refused(self):
        bad = ['[]', 'null', '{}', '{"action":"done","verified":true}',
               '{"action":"click","x":true,"y":3}',
               '{"action":"click","x":NaN,"y":3}',
               '{"action":"click","x":1,"y":Infinity}',
               '{"action":"click","x":100,"y":3}',
               '{"action":"click","x":1,"y":-1}',
               '{"action":"type_text","text":"injected"}',
               '```json\n{"action":"done"}\n```']
        for answer in bad:
            with self.subTest(answer=answer), patch('nlc.visual.query_visual',
                    return_value={'answer': answer}):
                with self.assertRaisesRegex(VisualError, 'invalid_visual_proposal'):
                    plan_visual(self.capture, 'Choose blue')

    def test_done_and_stop_remain_proposals(self):
        for action in ('done', 'stop'):
            with patch('nlc.visual.query_visual',
                       return_value={'answer': '{"action":"' + action + '"}'}):
                self.assertEqual({'action': action}, plan_visual(self.capture, 'Choose blue'))

    def test_invalid_timeouts_never_launch_provider(self):
        with patch('nlc.visual._run_visual') as run:
            for value in (True, 0, -1, float('nan'), float('inf'), '60'):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    query_visual(self.capture, 'Read', timeout=value)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
