"""Arrival timing preserves provider output and excludes command/UI content."""
import json
import sys
import unittest
from tacto.cli_event_timing import CLIEventTiming
from tacto.codex_result import _run_codex


class CLIEventTimingTests(unittest.TestCase):
    def test_fragmented_lines_store_only_event_metadata(self):
        now = [10.]
        observer = CLIEventTiming(clock=lambda: now[0])
        payload = (json.dumps({'type':'item.started','item':{'id':'item_1','type':'command_execution',
                   'command':'PRIVATE_UI_VALUE'}})+'\n').encode()
        observer.feed(payload[:20]); now[0] = 12.; observer.feed(payload[20:])
        snapshot = observer.snapshot()
        self.assertEqual(2., snapshot['events'][0]['received_seconds'])
        self.assertNotIn('PRIVATE_UI_VALUE', json.dumps(snapshot))
        self.assertEqual(0, snapshot['partial_line_bytes'])

    def test_oversized_record_recovers_at_newline(self):
        observer = CLIEventTiming()
        observer.MAX_LINE_BYTES = 100
        observer.feed(b'x' * 101)
        observer.feed(b'\n{"type":"turn.started"}\n')
        snapshot = observer.snapshot()
        self.assertEqual(1, snapshot['dropped_records'])
        self.assertEqual('turn.started', snapshot['events'][0]['event'])

    def test_partial_and_malformed_records_cannot_be_completed_events(self):
        observer = CLIEventTiming()
        observer.feed(b'not json\n{"type":"turn.completed"}')
        snapshot = observer.snapshot()
        self.assertEqual([], snapshot['events'])
        self.assertGreater(snapshot['partial_line_bytes'], 0)
        self.assertEqual(1, snapshot['dropped_records'])

    def test_real_owned_child_output_is_unchanged_and_timed(self):
        observations = []
        script = 'import sys,time;sys.stdin.read();print(\'{"type":"turn.started"}\',flush=True);time.sleep(.04);print(\'{"type":"turn.completed"}\',flush=True)'
        result = _run_codex([sys.executable,'-c',script], 'input', 3, observations)
        self.assertEqual(0, result.returncode)
        self.assertEqual(['turn.started', 'turn.completed'],
                         [json.loads(line)['type'] for line in result.stdout.splitlines()])
        events = observations[0]['cli_event_timing']['events']
        self.assertGreater(events[-1]['received_seconds'], events[0]['received_seconds'])
        self.assertEqual(['turn.started', 'turn.completed'], [e['event'] for e in events])
