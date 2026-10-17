"""Diagnostic privacy and refusal before any paid request."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('claude_stream_probe',
    ROOT / 'examples/probes/claude-stream-framing.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ClaudeStreamDiagnosticTests(unittest.TestCase):
    def test_headers_never_echo_arbitrary_keys_subtypes_sessions_or_body(self):
        events = [
            {'type': 'system', 'subtype': 'PRIVATE_SUBTYPE', 'session_id': 'PRIVATE_SESSION',
             'PRIVATE_FIELD': 'PRIVATE_BODY'},
            {'type': ['PRIVATE_KIND'], 'session_id': 'PRIVATE_SESSION'},
            {'type': 'assistant', 'session_id': 'expected', 'message': 'PRIVATE_BODY'},
            {'type': 'system', 'subtype': 'commands_changed', 'session_id': 'expected',
             'uuid': 'PRIVATE_UUID', 'commands': ['PRIVATE_COMMAND']},
        ]
        result = probe.stream_headers('\n'.join(json.dumps(e) for e in events), 'expected')
        self.assertNotIn('PRIVATE', json.dumps(result))
        self.assertFalse(result['headers'][0]['same_session'])
        self.assertEqual('unknown', result['headers'][1]['kind'])
        self.assertFalse(result['headers'][3]['commands_empty'])
        self.assertFalse(result['headers'][3]['canonical_uuid'])

    def test_malformed_and_oversized_streams_have_bounded_closed_output(self):
        self.assertEqual({'bounded': False, 'headers': []}, probe.stream_headers('x' * 2097153, 's'))
        result = probe.stream_headers('\n'.join(['PRIVATE', '[]', '{}'] * 30), 's')
        self.assertFalse(result['bounded'])
        self.assertEqual(64, len(result['headers']))
        self.assertNotIn('PRIVATE', json.dumps(result))

    def test_invalid_budget_and_existing_artifact_never_invoke_provider(self):
        with tempfile.TemporaryDirectory() as folder, patch('tacto.claude_result.claude_result') as invoke:
            path = Path(folder) / 'report.json'
            for kwargs in ({'requests': True}, {'requests': 5}, {'timeout': float('nan')},
                           {'timeout': float('inf')}, {'requests': 4, 'timeout': 40}):
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    probe.main(path, **kwargs)
            path.write_text('preserve')
            with self.assertRaises(FileExistsError):
                probe.main(path)
            self.assertEqual('preserve', path.read_text())
            invoke.assert_not_called()
