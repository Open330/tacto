"""Owned callback identity and private report boundary; no GUI calls."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from examples.probes import signed_modality_inventory as probe

class ModalityInventoryProbeTests(unittest.TestCase):
    def record(self):
        return {'nonce':'n'*32,'seq':1,'mode':'deep_sheet','appkit_sheet_attached':False,
                'appkit_modal_window':False,'custom_sheet_is_synthetic':True,
                'panel_visible':False,'popover_shown':False,'nested_sheet_attached':False}

    def test_callback_keeps_synthetic_representation_separate_from_real_sheet(self):
        value=self.record();data=json.dumps(value).encode()+b'\n'
        self.assertEqual(probe.callback(data+b'{"partial":','n'*32,'deep_sheet'),value)
        self.assertIsNone(probe.callback(b'{"partial":','n'*32,'deep_sheet'))
        for updates in ({'nonce':'foreign'},{'seq':True},{'seq':0},{'mode':'window_sheet'},
                        {'appkit_modal_window':1},{'private_title':'PRIVATE'}):
            with self.assertRaises(ValueError):
                probe.callback(json.dumps({**value,**updates}).encode()+b'\n','n'*32,'deep_sheet')
        with self.assertRaises(ValueError):probe.callback(b' '*131073,'n'*32,'deep_sheet')

    def test_requested_state_requires_actual_appkit_callback(self):
        for mode in probe.MODES:
            value={**self.record(),'mode':mode}
            if mode in ('clear','deep_sheet'):
                self.assertTrue(probe.requested_state(value,mode))
            else:
                self.assertFalse(probe.requested_state(value,mode))
        self.assertFalse(probe.requested_state(None,'app_modal'))
        self.assertFalse(probe.requested_state(self.record(),'foreign'))

    def test_existing_and_symlink_reports_refuse_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing';path.write_text('KEEP')
            link=Path(directory)/'link';link.symlink_to(path)
            for output in (path,link):
                with patch.object(probe,'_run') as run,self.assertRaises(FileExistsError):probe.main(None,output)
                run.assert_not_called();self.assertEqual(path.read_text(),'KEEP')

    def test_interruption_preserves_partial_refusal_without_input_safety_claim(self):
        def interrupt(driver,report):
            report['cases'].append({'mode':'deep_sheet','passed':False,'boundary':'clear'})
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report'
            with patch.object(probe,'_run',side_effect=interrupt),self.assertRaises(KeyboardInterrupt):probe.main(None,output)
            report=json.loads(output.read_text());self.assertFalse(report['passed'])
            self.assertFalse(report['input_interception_verified']);self.assertFalse(report['all_dependencies_frozen'])
            self.assertEqual(report['cases'][0]['boundary'],'clear');self.assertEqual(output.stat().st_mode&0o777,0o600)
