"""Report reservation must precede all native and model work."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build

ROOT=Path(__file__).resolve().parents[1]


def load():
    spec=importlib.util.spec_from_file_location('navigation_probe',ROOT/'examples/probes/native-navigation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class NavigationProbeReportTests(unittest.TestCase):
    def test_private_output_exists_before_work_and_is_flushed(self):
        module=load()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            def execute(driver,report,**kwargs):
                self.assertTrue(path.exists())
                self.assertEqual(path.stat().st_mode&0o777,0o600)
                report.update(passed=True,source_stable_during_run=True)
            previous=os.umask(0)
            try:
                with patch.object(module,'_run_navigation',side_effect=execute),patch.object(module.os,'fsync') as sync:
                    report=module.main(Mock(),path)
            finally:os.umask(previous)
            self.assertEqual(json.loads(path.read_text()),report)
            self.assertTrue(report['passed']);self.assertFalse(report['all_dependencies_frozen'])
            sync.assert_called_once()

    def test_existing_file_and_symlink_block_before_execution(self):
        module=load()
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'existing.json';path.write_text('ORIGINAL')
            link=Path(directory)/'link.json';link.symlink_to(path)
            for output in (path,link):
                with patch.object(module,'_run_navigation') as execute,self.assertRaises(FileExistsError):
                    module.main(Mock(),output)
                execute.assert_not_called()
                self.assertEqual(path.read_text(),'ORIGINAL')

    def test_interruption_and_initialization_failure_preserve_partial_state_and_raise(self):
        module=load()
        for error in (KeyboardInterrupt(),RuntimeError('PRIVATE_EXCEPTION_BODY')):
            with self.subTest(error=type(error).__name__),tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json'
                def execute(driver,report,**kwargs):
                    report['actions']=[{'result':{'type':'acted'}}]
                    report['passed']=True
                    raise error
                with patch.object(module,'_run_navigation',side_effect=execute),self.assertRaises(type(error)):
                    module.main(Mock(),path)
                report=json.loads(path.read_text())
                self.assertFalse(report['passed']);self.assertEqual(report['effect'],'unknown')
                self.assertEqual(report['error_class'],type(error).__name__)
                self.assertEqual(len(report['actions']),1)
                self.assertNotIn('PRIVATE_EXCEPTION_BODY',path.read_text())
                self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_serialization_failure_preserves_receipts_and_original_exception(self):
        module=load()
        class Invalid:
            def __str__(self):raise AssertionError('object stringification must not run')
        for original in (None,KeyboardInterrupt()):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json'
                def execute(driver,report,**kwargs):
                    report.update(passed=True,invalid=Invalid(),actions=[{'result':{'type':'acted'}}])
                    if original is not None:raise original
                expected=TypeError if original is None else KeyboardInterrupt
                with patch.object(module,'_run_navigation',side_effect=execute),self.assertRaises(expected):
                    module.main(Mock(),path)
                report=json.loads(path.read_text())
                self.assertFalse(report['passed']);self.assertTrue(report['serialization_failed'])
                self.assertEqual(report['error_class'],expected.__name__)
                self.assertEqual(report['serialization_error_class'],'TypeError')
                self.assertEqual(report['report_fields_omitted'],['invalid'])
                self.assertEqual(len(report['actions']),1)
                self.assertEqual(report['effect'],'unknown')

    def test_invalid_options_do_not_reserve_report_or_execute(self):
        module=load()
        for options in ({'require_selection':1},{'model_provider':'foreign'}):
            with tempfile.TemporaryDirectory() as directory:
                path=Path(directory)/'report.json'
                with patch.object(module,'_run_navigation') as execute,self.assertRaises(ValueError):
                    module.main(Mock(),path,**options)
                execute.assert_not_called();self.assertFalse(path.exists())

    @requires_signed_build

    def test_manifest_covers_runtime_artifacts_and_new_imported_sources(self):
        module=load();manifest=module.source_hashes(ROOT)
        for path in ('launcher/tacto','helper/tacto-helper',
                     'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
                     'tacto/preparation_diagnostic.py','tacto/observation_state.py',
                     'tacto/window_listing_diagnostic.py','examples/eval.py'):
            self.assertIn(path,manifest)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for path in manifest:
                target=root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'fixture')
            before=module.source_hashes(root)
            (root/'helper/tacto-helper').write_bytes(b'changed-runtime')
            (root/'tacto/preparation_diagnostic.py').write_bytes(b'changed-python-runtime')
            after=module.source_hashes(root)
            self.assertNotEqual(before['helper/tacto-helper'],after['helper/tacto-helper'])
            self.assertNotEqual(before['tacto/preparation_diagnostic.py'],
                                after['tacto/preparation_diagnostic.py'])
            self.assertEqual(set(before),set(after))
