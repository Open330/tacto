"""The owned save probe never repeats preparation/Save or closes on handoff."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
owned_spec=importlib.util.spec_from_file_location('signed_owned_textedit',ROOT/'examples/probes/signed_owned_textedit.py')
owned=importlib.util.module_from_spec(owned_spec);owned_spec.loader.exec_module(owned)
spec=importlib.util.spec_from_file_location('save_probe',ROOT/'examples/probes/signed_textedit_save.py')
probe=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'signed_owned_textedit':owned}):spec.loader.exec_module(probe)


class Witness:
    initial='other-app'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def summary(self):return {'initial':self.initial,'activations':[]}


class ProbeTests(unittest.TestCase):
    def run_probe(self,*,menus=(True,True),preparation=None,cleanup_allowed=True):
        fixture=Mock(path=Path('/owned/note.txt'),directory=Path('/missing-owned-probe-directory'),
                     directory_identity=(1,2),token='owned-token',closed_observed=True)
        fixture.write.return_value=True;fixture.cleanup.return_value='closed'
        driver=Mock()
        driver.menu_state.side_effect=[{'type':'menu_state','enabled':enabled} for enabled in menus]
        driver.observe.return_value={'type':'observation','obs_id':'fresh','document_token':'owned-token',
                                     'truncated':False,'tree':'0 Window'}
        driver.act.side_effect=lambda obs,target,action,**kw:preparation if action['name']=='prepare_window' else {'type':'acted'}
        receipt=Mock()
        body='NLC 실제 저장 검증 aa\n두 번째 줄\n'.encode()
        with patch.object(probe,'SignedOwnedTextEdit',return_value=fixture),patch.object(probe.secrets,'token_hex',return_value='aa'),\
             patch.dict(sys.modules,{'focus_witness':types.SimpleNamespace(FocusWitness=Witness)}),\
             patch('nlc.existing_file_receipt.ExistingTextFileReceipt',return_value=receipt),\
             patch('nlc.existing_file_receipt._file_state',return_value=(None,None,None,None,None,body)),\
             patch('nlc.commit_evidence.evidence_error',return_value=None):
            report=probe.run(driver,can_cleanup=lambda:cleanup_allowed)
        return report,fixture,driver

    def test_enabled_save_uses_bound_menu_once_without_preparation(self):
        report,fixture,driver=self.run_probe()
        self.assertTrue(report['passed'])
        driver.act.assert_called_once()
        action=driver.act.call_args.args[2]
        self.assertEqual('menu',action['name'])
        self.assertEqual('/owned/note.txt',action['focused_document'])
        self.assertFalse(report['producer_attribution_verified'])
        fixture.cleanup.assert_called_once()

    def test_failed_preparation_never_attempts_save(self):
        report,fixture,driver=self.run_probe(menus=(False,),preparation={'type':'error','effect':'not_started'})
        self.assertFalse(report['passed'])
        driver.act.assert_called_once()
        self.assertEqual('prepare_window',driver.act.call_args.args[2]['name'])
        self.assertEqual('window_preparation_unverified',report['failure_code'])

    def test_preparation_must_reobserve_enabled_menu(self):
        report,fixture,driver=self.run_probe(menus=(False,False),preparation={'type':'acted'})
        self.assertFalse(report['passed'])
        driver.act.assert_called_once()
        self.assertEqual('save_menu_not_enabled_after_prepare',report['failure_code'])

    def test_changed_environment_leaves_owned_document_open(self):
        report,fixture,driver=self.run_probe(cleanup_allowed=False)
        self.assertFalse(report['passed'])
        fixture.cleanup.assert_not_called()
        self.assertEqual('environment_changed_left_as_is',report['cleanup_status'])
