"""Exact live values, unknown values and refused probes stay distinct."""
import unittest
from tacto.general import _witness


class GeneralWitnessTests(unittest.TestCase):
    def witness(self, response):
        class Driver:
            def probe(self, *args): return response
        candidate = {'element_index':0, 'can':['read'], 'line':'0 textbox Value: stale'}
        return _witness(Driver(), {'obs_id':'owned'}, 'owned', 'body', [candidate],
            lambda name, choices:{'pick':0,'candidate':choices[0]}, lambda message:None)

    def test_exact_live_value_preserves_newlines_and_empty_text(self):
        for value in ('한글\n🧪 본문', ''):
            with self.subTest(value=value):
                self.assertEqual(value, self.witness({'type':'probe',
                    'line':'0 textbox Value: flattened', 'node':{'value':value}}))

    def test_unknown_typed_value_cannot_fall_back_to_display_text(self):
        for value in (None, 0, False, ['untrusted']):
            with self.subTest(value=value):
                self.assertIsNone(self.witness({'type':'probe',
                    'line':'0 textbox Value: forged', 'node':{'value':value}}))

    def test_refused_or_malformed_probe_cannot_reuse_snapshot(self):
        for response in (None, [], 'raw', {}, {'type':'error'},
                         {'type':'probe','line':None}):
            with self.subTest(response=response):
                self.assertIsNone(self.witness(response))
