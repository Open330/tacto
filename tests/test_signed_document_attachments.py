import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from examples.probes.signed_document_attachments import main,write_owned_package,package_manifest
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build

class SignedAttachmentProbeTests(unittest.TestCase):
    def test_existing_report_refuses_before_native_or_package(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder).resolve()/'report.json';output.write_text('KEEP');driver=Mock()
            with patch('examples.probes.signed_document_attachments.owned_body') as body:
                with self.assertRaises(FileExistsError):main(driver,output)
                body.assert_not_called();driver.list_targets.assert_not_called()
            self.assertEqual('KEEP',output.read_text())

    @requires_signed_build

    def test_unsigned_driver_cannot_open_document(self):
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder).resolve()/'report.json';driver=Mock()
            with patch('examples.probes.signed_document_attachments.subprocess.run') as command:
                result=main(driver,output)
                command.assert_not_called();driver.list_targets.assert_not_called()
            self.assertFalse(result['passed']);self.assertEqual(0,result['owned_document_open_requests'])
            self.assertEqual('signed_channel_required',result['error'])
            self.assertEqual(['report.json'],[p.name for p in output.parent.iterdir()])

    def test_package_reservation_refuses_existing_and_unsafe_child_names(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();existing=base/'existing.rtfd';existing.mkdir()
            wrapper=Mock()
            with self.assertRaises(FileExistsError):write_owned_package(existing,wrapper)
            wrapper.isDirectory.assert_not_called()
            wrapper.isDirectory.return_value=True;wrapper.isSymbolicLink.return_value=False
            wrapper.fileWrappers.return_value={'../outside':Mock()}
            with self.assertRaises(ValueError):write_owned_package(base/'new.rtfd',wrapper)
            self.assertFalse((base/'outside').exists())

    def test_manifest_rejects_links_and_nonprivate_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();file=base/'owned';file.write_bytes(b'owned');file.chmod(0o600)
            self.assertEqual(1,len(package_manifest(base)['files']))
            file.chmod(0o644)
            with self.assertRaises(ValueError):package_manifest(base)
            file.unlink();file.symlink_to(base/'missing')
            with self.assertRaises(ValueError):package_manifest(base)

    def test_evaluator_probe_is_isolated_and_offline_skipped(self):
        from examples.eval import _parse_plan,ISOLATED
        _,selected,cases=_parse_plan(['signed_document_attachments','--offline'])
        self.assertEqual(1,len(selected));self.assertEqual('skipped',cases[0]['status'])
        self.assertIn('signed_document_attachments',ISOLATED)

    def test_package_root_replacement_and_privacy_cannot_match_original(self):
        with tempfile.TemporaryDirectory() as folder:
            base=Path(folder).resolve();package=base/'owned.rtfd';package.mkdir(mode=0o700)
            before=package_manifest(package)
            package.rename(base/'old');package.mkdir(mode=0o700)
            self.assertNotEqual(before,package_manifest(package))
            package.chmod(0o755)
            with self.assertRaises(ValueError):package_manifest(package)
            package.rmdir();package.symlink_to(base/'old',target_is_directory=True)
            with self.assertRaises(ValueError):package_manifest(package)

    @requires_signed_build

    def test_final_manifest_failure_preserves_original_failure_and_report(self):
        import sys
        if sys.platform!='darwin':self.skipTest('native metadata imports required')
        class FakeSigned:
            capabilities={'evidence.document_rtf_export'}
            @staticmethod
            def available():return True
            def list_targets(self):return {'apps':[{'id':'com.apple.TextEdit','pid':42}]}
            def document_text(self,*args,**kwargs):raise ValueError('original receiving failure')
        class FakeAudit:
            def __init__(self,**kwargs):pass
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def report(self):return {'status':'INCONCLUSIVE'}
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder).resolve()/'report.json'
            with patch('tacto.input_audit_process.ProcessInputAudit',FakeAudit),patch('tacto.helper.InheritedHelperDriver',FakeSigned),patch('tacto.native_frontmost.frontmost_pid',return_value=99), \
                 patch('examples.probes.signed_document_attachments.subprocess.run'), \
                 patch('examples.probes.signed_document_attachments.package_manifest',side_effect=[{'files':{}},ValueError('final source drift')]):
                result=main(FakeSigned(),output)
            self.assertFalse(result['passed']);self.assertFalse(result['owned_source_package_unchanged'])
            self.assertEqual('ValueError',result['source_check_error_class'])
            self.assertEqual('original receiving failure',result['error'])
            self.assertEqual(result,json.loads(output.read_text()))
