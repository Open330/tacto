"""Finder probe keeps reported effect/count and never arbitrary CLI details."""
from pathlib import Path
import runpy
import unittest

PROBE = runpy.run_path(str(Path(__file__).resolve().parents[1] /
                          'examples/probes/finder-preparation-diagnostic.py'))


class FinderPreparationReportTests(unittest.TestCase):
    def test_native_failure_metadata_retained_without_private_detail(self):
        response = {'type': 'error', 'effect': 'unknown', 'posted_or_attempted_events': 1,
                    'detail': 'PRIVATE_SENTINEL'}
        output = 'PRIVATE_SENTINEL\nfailed: ' + repr({'detail': response})
        self.assertEqual(PROBE['action_failure'](output),
                         {'effect': 'unknown', 'reported_posted_or_attempted_events': 1})

    def test_missing_count_is_null_not_zero(self):
        output = "failed: {'detail': {'type': 'error', 'effect': 'not_started'}}"
        self.assertEqual(PROBE['action_failure'](output),
                         {'effect': 'not_started', 'reported_posted_or_attempted_events': None})

    def test_invalid_counter_or_unclosed_effect_is_rejected(self):
        for count in (True, -1, 17, 'private'):
            record = {'detail': {'type': 'error', 'effect': 'unknown',
                                 'posted_or_attempted_events': count}}
            self.assertIsNone(PROBE['action_failure']('failed: ' + repr(record)))
        for output in ("failed: {'detail': {'type': 'error', 'effect': 'private'}}",
                       'failed: __import__("os").getcwd()', 'failed: []', 'no marker'):
            self.assertIsNone(PROBE['action_failure'](output))

    def test_output_and_final_record_are_bounded(self):
        self.assertIsNone(PROBE['action_failure']('x' * (1024 * 1024 + 1)))
        self.assertIsNone(PROBE['action_failure']('failed: ' + 'x' * 8192))


if __name__ == '__main__':
    unittest.main()
