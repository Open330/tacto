"""Saved-state reporting preserves scope and omits unrelated window titles."""
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
from examples.probes import signed_textedit_saved_state as probe
from tacto.helper import InheritedHelperDriver


class TextEditSavedStateTests(unittest.TestCase):
    def test_report_redacts_witness_titles_and_never_claims_writer_attribution(self):
        module=types.ModuleType('signed_textedit_save')
        module.__file__=str(Path(probe.__file__).with_name('signed_textedit_save.py'))
        module.run=Mock(return_value={'passed':True,'producer_attribution_verified':False,
            'exact_saved_text':True,'focus':{'initial':'other','activations':[],
                'windows':['PRIVATE_DOCUMENT_TITLE']}})
        driver=Mock(spec=InheritedHelperDriver)
        audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
        audit.report.return_value={'status':'UNCHANGED_SAMPLED'}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            with patch.object(InheritedHelperDriver,'available',return_value=True), \
                 patch.object(probe,'background_precondition',return_value={'ready':True,'reason':'background_target'}), \
                 patch.dict('sys.modules',{'signed_textedit_save':module}), \
                 patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit):
                result=probe.main(driver,output)
            self.assertTrue(result['passed'])
            self.assertTrue(result['source_subset_stable'])
            self.assertFalse(result['producer_attribution_verified'])
            self.assertFalse(result['proves_no_intermediate_input_change'])
            self.assertNotIn('PRIVATE_DOCUMENT_TITLE',output.read_text())
            self.assertEqual(result['saved_state_result']['focus']['window_sample_count'],1)
            self.assertEqual(0o600,output.stat().st_mode&0o777)
            self.assertTrue(module.run.call_args.kwargs['scoped_save'])

    def test_foreground_target_does_not_start_audit_or_owned_probe(self):
        module=types.ModuleType('signed_textedit_save');module.run=Mock()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            with patch.object(InheritedHelperDriver,'available',return_value=True), \
                 patch.object(probe,'background_precondition',return_value={'ready':False,'reason':'target_already_foreground'}), \
                 patch.dict('sys.modules',{'signed_textedit_save':module}), \
                 patch('tacto.input_audit_process.ProcessInputAudit') as audit:
                result=probe.main(Mock(spec=InheritedHelperDriver),output)
            self.assertEqual(result['run_status'],'precondition_unmet')
            self.assertFalse(result['passed'])
            module.run.assert_not_called();audit.assert_not_called()
