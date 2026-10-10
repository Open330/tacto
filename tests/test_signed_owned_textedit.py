"""Owned fixture cleanup never falls back to direct AX or repeats a close."""
import importlib.util
from pathlib import Path
import os
import sys
import tempfile
import types
import unicodedata
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('owned_signed',ROOT/'examples/probes/signed_owned_textedit.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name).resolve()/'owned';self.directory.mkdir(mode=0o700)
        self.driver=Mock(capabilities=m.CAPS)
        self.fixture=m.SignedOwnedTextEdit(self.driver,'한글.txt')
        self.fixture.directory=self.directory
        st=self.directory.lstat();self.fixture.directory_identity=(st.st_dev,st.st_ino)
        self.fixture.path=self.directory/self.fixture.name;self.fixture.path.write_text('')
        self.fixture.token='owned-token'
        self.driver.document_window_status.side_effect=lambda *a:{'type':'document_window_status','document_token':'owned-token','state':'open','same_process_verified':True}
        self.driver.observe.return_value={'type':'observation','obs_id':'owned-obs','document_token':'owned-token','truncated':False,
            'tree':'0 Window\n  1 Button  Subrole: AXMinimizeButton\n  2 Button  Subrole: AXCloseButton'}
        self.driver.act.return_value={'type':'acted'}

    def closed(self):
        self.driver.document_window_status.side_effect=None
        self.driver.document_window_status.return_value={'type':'document_window_status','document_token':'owned-token','state':'closed','same_process_verified':True}

    def test_write_is_bound_to_only_the_pinned_body_and_requires_receiving_text(self):
        self.driver.capabilities=set(m.CAPS)|{'action.set_value'}
        self.driver.observe.return_value={'type':'observation','obs_id':'owned-obs','document_token':'owned-token','truncated':False,
                                         'tree':'0 Window\n  1 TextArea  Identifier: First Text View'}
        with patch.object(self.fixture,'read',return_value='owned text'):
            self.assertTrue(self.fixture.write('owned text'))
        self.assertEqual({'name':'set_value','element_index':1,'value':'owned text'},self.driver.act.call_args.args[2])
        self.driver.observe.return_value['document_token']='other'
        self.driver.act.reset_mock()
        with self.assertRaises(RuntimeError):self.fixture.write('owned text')
        self.driver.act.assert_not_called()

    def test_status_diagnostics_never_store_raw_failure_text(self):
        self.driver.document_window_status.side_effect=None
        self.driver.document_window_status.return_value={'type':'error','reason_code':'PRIVATE'}
        self.assertIsNone(self.fixture.status())
        self.assertNotIn('PRIVATE',str(self.fixture.status_observations))
        self.driver.document_window_status.return_value={'type':'error','reason_code':'window_snapshot_changed'}
        self.assertIsNone(self.fixture.status())
        self.assertEqual('window_snapshot_changed',self.fixture.status_observations[-1]['reason_code'])
        self.driver.document_window_status.return_value={'type':'document_window_status','document_token':'other','same_process_verified':True,'state':'open'}
        self.assertIsNone(self.fixture.status())
        self.assertIsNone(self.fixture.status_observations[-1]['state'])

    def test_bounded_status_diagnostics_mark_truncation(self):
        for _ in range(65):self.fixture.status()
        self.assertEqual(64,len(self.fixture.status_observations))
        self.assertTrue(self.fixture.status_observations_truncated)

    def test_unknown_lifecycle_preserves_file_and_sends_no_close(self):
        self.driver.document_window_status.side_effect=None
        self.driver.document_window_status.return_value={'type':'error'}
        self.assertEqual('document_status_unavailable_left_as_is',self.fixture.cleanup())
        self.assertTrue(self.fixture.path.exists());self.driver.act.assert_not_called()

    def test_transient_post_close_unknown_is_only_reobserved_never_clicked_twice(self):
        def reply(state):
            return {'type':'document_window_status','document_token':'owned-token','state':state,'same_process_verified':True}
        self.driver.document_window_status.side_effect=[reply('open'),{'type':'error'},reply('closed')]
        self.assertEqual('closed',self.fixture.cleanup())
        self.assertEqual(1,self.driver.act.call_count)
        self.assertFalse(self.directory.exists())

    def test_unknown_effect_is_not_retried_or_removed(self):
        self.driver.act.return_value={'type':'error','effect':'unknown'}
        self.assertEqual('close_unverified_left_as_is',self.fixture.cleanup())
        self.assertEqual('close_pending_left_as_is',self.fixture.cleanup())
        self.assertEqual(1,self.driver.act.call_count)
        self.assertTrue(self.fixture.path.exists())
        action=self.driver.act.call_args
        self.assertEqual({'name':'click','element_index':2},action.args[2])
        self.assertEqual({'role':'Button','attribute':'Subrole','label':'AXCloseButton','enabled':True},action.kwargs['expect'])

    def test_duplicate_close_wrong_scope_or_truncated_observation_never_acts(self):
        for patch_value in ({'tree':'0 Window\n  1 Button  Subrole: AXCloseButton\n  2 Button  Subrole: AXCloseButton'},
                            {'document_token':'other'},{'truncated':True}):
            self.driver.observe.return_value.update(patch_value)
            self.fixture.cleanup();self.driver.act.assert_not_called()

    def test_verified_closed_removes_only_original_owned_artifact(self):
        self.closed()
        self.assertEqual('closed',self.fixture.cleanup())
        self.assertFalse(self.directory.exists())
        self.assertTrue(self.fixture.closed_observed)
        self.driver.act.assert_not_called()

    def test_symlink_or_unexpected_file_cannot_be_removed(self):
        self.closed()
        sentinel=Path(self.temp.name)/'sentinel';sentinel.write_text('keep')
        self.fixture.path.unlink();self.fixture.path.symlink_to(sentinel)
        self.assertEqual('artifact_changed_left_as_is',self.fixture.cleanup())
        self.assertEqual('keep',sentinel.read_text())
        self.fixture.path.unlink();self.fixture.path.write_text('')
        (self.directory/'unowned').write_text('keep')
        self.assertEqual('artifact_changed_left_as_is',self.fixture.cleanup())
        self.assertTrue(self.fixture.path.exists())

    def test_directory_replacement_cannot_be_removed(self):
        self.closed()
        self.directory.rename(self.directory.with_name('prior'))
        self.directory.mkdir(mode=0o700);self.fixture.path.write_text('other')
        self.assertEqual('artifact_changed_left_as_is',self.fixture.cleanup())
        self.assertEqual('other',self.fixture.path.read_text())

    @unittest.skipUnless(sys.platform=='darwin','macOS filesystem canonical Unicode aliases required')
    def test_actual_nfd_path_is_the_same_owned_file_but_different_name_is_refused(self):
        nfd=unicodedata.normalize('NFD',str(self.fixture.path))
        self.assertNotEqual(nfd,str(self.fixture.path))
        self.assertTrue(self.fixture._matches_path(nfd))
        self.assertFalse(self.fixture._matches_path(str(self.fixture.path)+'other'))

    def test_capability_refusal_precedes_creating_any_fixture(self):
        with patch.object(m.tempfile,'mkdtemp') as create,self.assertRaises(RuntimeError):
            m.SignedOwnedTextEdit(Mock(capabilities=set()),'own.txt')
        create.assert_not_called()

    def test_resume_requires_explicit_host_identity_and_does_not_open_another_window(self):
        fixture=m.SignedOwnedTextEdit(self.driver,'한글.txt')
        with patch.object(fixture,'_pin') as pin,patch.object(m.subprocess,'run') as run:
            fixture.resume(self.directory,self.fixture.directory_identity)
        pin.assert_called_once();run.assert_not_called()
        with self.assertRaises(RuntimeError):
            m.SignedOwnedTextEdit(self.driver,'한글.txt').resume(self.directory,(0,0))

class EvaluationIntegrationTests(unittest.TestCase):
    def test_textedit_edit_uses_signed_setup_oracle_cleanup_and_no_live_setup_for_listing(self):
        from tests.test_product_comparison_provenance import comparison
        from contextlib import redirect_stdout
        from io import StringIO
        task=next(t for t in comparison.TASKS if t.name=='textedit_edit_owned')
        self.assertIs(comparison.signed_owned_setup,task.setup)
        self.assertIs(comparison.signed_owned_cleanup,task.cleanup)
        self.assertEqual('signed-owned-document-exact-text-v2',task.oracle_contract)
        with patch.object(sys,'argv',['comparison','--list','--only','textedit_edit_owned']), \
             patch.object(comparison,'_driver') as driver, \
             patch.object(comparison,'run_one') as run,redirect_stdout(StringIO()) as output:
            self.assertEqual(0,comparison.main())
        self.assertIn('exact path',output.getvalue())
        driver.assert_not_called();run.assert_not_called()

    def test_fixture_report_copies_bounded_status_records_and_contains_no_token(self):
        from tests.test_product_comparison_provenance import comparison
        fixture=types.SimpleNamespace(path=Path('/private/tmp/own/file.txt'),directory_identity=(1,2),
            token='PRIVATE-TOKEN',directory=Path('/private/tmp/own'),close_requested=True,closed_observed=True,
            status_observations=[{'state':'closed','reason_code':None}],status_observations_truncated=False)
        report=comparison.signed_fixture_evidence(fixture)
        self.assertNotIn('PRIVATE-TOKEN',str(report))
        self.assertEqual([{'state':'closed','reason_code':None}],report['status_observations'])
        report['status_observations'][0]['state']='changed'
        self.assertEqual('closed',fixture.status_observations[0]['state'])
        self.assertFalse(report['status_observations_truncated'])

    def test_owned_text_oracle_rejects_whitespace_changes(self):
        from tests.test_product_comparison_provenance import comparison
        fixture=Mock()
        with patch.dict(comparison._SIGNED_OWNED,{'fixture':fixture},clear=True):
            for body,want in [('owned text',True),('owned  text',False),('owned text\n',False),('owned\ntext',False)]:
                fixture.read.return_value=body
                self.assertEqual(want,comparison.signed_owned_oracle('owned text')[0])

    def test_failed_cleanup_remains_reachable_and_next_setup_cannot_overwrite_it(self):
        from tests.test_product_comparison_provenance import comparison
        fixture=Mock();fixture.cleanup.return_value='document_status_unavailable_left_as_is'
        with patch.dict(comparison._SIGNED_OWNED,{'fixture':fixture},clear=True):
            self.assertEqual('document_status_unavailable_left_as_is',comparison.signed_owned_cleanup())
            self.assertIs(fixture,comparison._SIGNED_OWNED['fixture'])
            with self.assertRaisesRegex(RuntimeError,'previous_owned_fixture_not_cleaned'):
                comparison.signed_owned_setup()

if __name__=='__main__':unittest.main()
