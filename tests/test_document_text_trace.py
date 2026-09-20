"""Live document evidence must never be represented as replayable trace data."""
import unittest
from nlc.trace import Trace, TracingDriver, TraceDriver


class DocumentTextTraceTests(unittest.TestCase):
    def test_live_reader_forwards_identity_and_budget_without_recording_body(self):
        class Live:
            capabilities = {'evidence.document_text'}
            def document_text(self, target, document_token=None, *, max_utf16_units):
                self.request = target, document_token, max_utf16_units
                return {'type': 'document_text', 'text': 'private document body',
                        'document_token': document_token, 'text_complete': True}
        live = Live(); trace = Trace(); wrapper = TracingDriver(live, trace)
        result = wrapper.document_text('com.apple.TextEdit', 'live-token', max_utf16_units=42)
        self.assertEqual(('com.apple.TextEdit', 'live-token', 42), live.request)
        self.assertTrue(result['text_complete'])
        self.assertEqual([], trace.events)
        self.assertNotIn('private document body', trace.to_json())
        self.assertIn('evidence.document_text', wrapper.capabilities)

    def test_reloaded_trace_cannot_claim_live_document_evidence(self):
        trace = Trace(capabilities=['observe', 'evidence.document_text',
                                    'evidence.document_snapshot'])
        replay = TraceDriver(trace)
        self.assertEqual(frozenset({'observe'}), replay.capabilities)
