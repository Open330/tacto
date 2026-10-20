"""Pre-input failures retain source drift and audit evidence without UI/model calls."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import examples.probe_native_select as probe


class ModelProbeFinalizationTests(unittest.TestCase):
    def run_failure(self,mode):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            names=['examples/probe_native_select.py','examples/eval.py','tacto/drift.py',
                   'helper/TactoHelper.swift','helper/WindowInput.swift','helper/WindowDispatch.swift',
                   'helper/tacto-helper','helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
                   'launcher/tacto','tests/fixtures/NativeSelectFixture.swift']
            for name in names:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('before')
            if mode=='initial_missing':(root/'helper/WindowInput.swift').unlink()
            class Audit:
                def __enter__(self):
                    if mode=='drift':(root/'tacto/drift.py').write_text('after')
                    if mode=='deleted':(root/'helper/WindowInput.swift').unlink()
                    raise RuntimeError('collector_not_ready')
                def __exit__(self,*args):pass
                def report(self):
                    if mode=='audit_error':raise ValueError('audit_report_unavailable')
                    return {'status':'INCONCLUSIVE','errors':['collector_not_ready']}
            driver=Mock()
            driver.observe.return_value={'type':'observation','tree':
                '0 StaticText  Value: Alpha, Identifier: tacto-fixture-result\n'
                '1 StaticText  Value: 0, Identifier: tacto-fixture-count'}
            with patch.object(probe,'ROOT',root),patch.object(probe,'__file__',str(root/names[0])), \
                 patch('tacto.driver.Driver') as reader,patch('tacto.driver.AXUIElementCreateApplication'), \
                 patch('tacto.driver._attr',return_value=False), \
                 patch('tacto.input_audit_process.ProcessInputAudit',return_value=Audit(),
                       side_effect=RuntimeError('audit_init_failed') if mode=='init_error' else None), \
                 patch('tacto.general.run') as model_loop,patch('tacto.ask.ask_here_paged') as ask:
                reader.return_value._resolve.return_value=42
                result=probe.run_model_probe(driver,provider='codex')
            driver.act.assert_not_called();model_loop.assert_not_called();ask.assert_not_called()
            return result

    def test_startup_failure_retains_after_snapshot_and_original_error(self):
        result=self.run_failure('stable')
        self.assertFalse(result['passed']);self.assertTrue(result['source_stable'])
        self.assertEqual(result['source_before'],result['source_after'])
        self.assertEqual('INCONCLUSIVE',result['audit']['status'])
        self.assertEqual('RuntimeError:collector_not_ready',result['error'])
        self.assertEqual(['probe_exception'],result['failure_reasons'])
        self.assertEqual([],result['native_receipts'])

    def test_drift_and_missing_sources_do_not_overwrite_startup_error(self):
        for mode in ('drift','deleted'):
            with self.subTest(mode=mode):
                result=self.run_failure(mode)
                self.assertFalse(result['source_stable']);self.assertFalse(result['passed'])
                self.assertEqual('RuntimeError:collector_not_ready',result['error'])
                self.assertEqual(['probe_exception','source_changed_or_unverified'],result['failure_reasons'])
                if mode=='deleted':self.assertEqual('FileNotFoundError',result['source_after_error_class'])

    def test_audit_report_failure_does_not_hide_original_failure(self):
        result=self.run_failure('audit_error')
        self.assertFalse(result['passed']);self.assertTrue(result['source_stable'])
        self.assertEqual('ValueError',result['audit_report_error_class'])
        self.assertEqual('RuntimeError:collector_not_ready',result['error'])

    def test_constructor_and_initial_snapshot_errors_are_terminal_reports(self):
        result=self.run_failure('init_error')
        self.assertFalse(result['passed']);self.assertTrue(result['source_stable'])
        self.assertEqual('RuntimeError:audit_init_failed',result['error'])
        self.assertNotIn('audit',result)
        result=self.run_failure('initial_missing')
        self.assertFalse(result['passed']);self.assertFalse(result['source_stable'])
        self.assertEqual('FileNotFoundError',result['source_before_error_class'])
        self.assertEqual('FileNotFoundError',result['source_after_error_class'])
        self.assertNotIn('audit',result)
