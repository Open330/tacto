import json
import unittest
from tacto.pin_diagnostic import MARKER,pin_diagnostic,format_pin_diagnostic,diagnostic_lines


class PinDiagnosticTests(unittest.TestCase):
    def test_closed_metadata_rejects_paths_payloads_and_effect_claims(self):
        base={'schema':'document-pin-v1','phase':'pin','reason':'read_deadline','effect':'not_started'}
        self.assertEqual(base,pin_diagnostic(base))
        for changed in ({'path':'PRIVATE'},{'reason':'PRIVATE'},{'phase':'dispatch'},
                        {'effect':'unknown'},{'effect':'confirmed'},{'schema':'other'}):
            self.assertIsNone(pin_diagnostic({**base,**changed}))
        self.assertIsNone(format_pin_diagnostic('PRIVATE'))

    def test_only_exact_marker_lines_are_collected_with_bounded_output(self):
        line=format_pin_diagnostic('selection_ambiguous_or_absent')
        self.assertEqual(64,len(diagnostic_lines((line+'\n')*70)))
        self.assertEqual([],diagnostic_lines('log '+line))
        self.assertEqual([],diagnostic_lines(MARKER+json.dumps({'path':'PRIVATE'})))
        self.assertEqual([],diagnostic_lines(MARKER+'[[]'))
        self.assertEqual([],diagnostic_lines('x'*(1024*1024+1)))

    def test_comparison_collects_completed_cli_metadata_without_accepting_agent_claim(self):
        from tests.test_product_comparison_provenance import Provenance
        fixture=Provenance();fixture.setUp()
        try:
            item={'id':'pin','type':'command_execution','command':'nlc observe owned --helper',
                  'aggregated_output':format_pin_diagnostic('text_range_unreadable'),'exit_code':1}
            events=[{'type':'item.started','item':item},{'type':'item.completed','item':item}]
            result=fixture.execute(stdout='\n'.join(json.dumps(e) for e in events))
            self.assertEqual(1,len(result['reported_document_pin_diagnostics']))
            self.assertEqual('text_range_unreadable',result['reported_document_pin_diagnostics'][0]['reason'])
            self.assertFalse(result['comparison_eligible'])
        finally:fixture.doCleanups()
