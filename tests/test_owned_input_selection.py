"""Required foreground selection readiness blocks owned fixture/model input."""
import importlib.util
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
class OwnedInputSelectionTests(unittest.TestCase):
    def test_visual_journal_requires_fixed_visual_flow_before_driver_or_output(self):
        probe=runpy.run_path(str(ROOT/'examples/probes/native-opaque-input.py'))['main']
        for changes in ({'journal_barrier':1},{'journal_barrier':True},
                        {'journal_barrier':True,'visual_flow':True,'vision_provider':'codex'}):
            with patch('tacto.driver.Driver') as driver,patch('os.open') as output:
                with self.assertRaises(ValueError):probe('unused',**changes)
                driver.assert_not_called();output.assert_not_called()

    def test_opaque_report_collision_prevents_fixture_and_early_failure_is_private(self):
        probe=runpy.run_path(str(ROOT/'examples/probes/native-opaque-input.py'))['main']
        with tempfile.TemporaryDirectory() as folder:
            output=Path(folder)/'report.json';output.write_text('existing evidence')
            with patch('tacto.driver.Driver') as driver,patch('subprocess.Popen') as child:
                with self.assertRaises(FileExistsError):probe(output)
                driver.assert_not_called();child.assert_not_called()
            self.assertEqual('existing evidence',output.read_text())
            output.unlink()
            with patch('tacto.driver.Driver',side_effect=RuntimeError('PRIVATE')):
                with self.assertRaises(RuntimeError):probe(output)
            self.assertEqual({'passed':False,'error_class':'RuntimeError'},json.loads(output.read_text()))
            self.assertEqual(0o600,output.stat().st_mode&0o777)

    def test_missing_selection_blocks_fixture_start_and_provider_work(self):
        from tacto.input_audit_process import InputAuditProcessError
        from tacto.native_window_payload import WINDOW_INPUT_CAPABILITIES
        for name in ('native-navigation.py', 'native-opaque-input.py'):
            probe = runpy.run_path(str(ROOT / 'examples/probes' / name))['main']
            driver = Mock(capabilities=WINDOW_INPUT_CAPABILITIES)
            driver._resolve.return_value = None
            audit = Mock()
            audit.__enter__ = Mock(side_effect=InputAuditProcessError('initial selection unavailable'))
            audit.__exit__ = Mock(return_value=False)
            audit.report.return_value = {'status': 'INCONCLUSIVE', 'require_selection': True}
            with tempfile.TemporaryDirectory() as work, \
                    patch('tacto.driver.Driver', return_value=driver), \
                    patch('tacto.input_audit_process.ProcessInputAudit', return_value=audit) as factory, \
                    patch('subprocess.run') as compile_only, \
                    patch('subprocess.Popen') as launch, \
                    patch('tacto.ask.ask_here_paged') as model, \
                    patch('tacto.visual.plan_native_input') as vision:
                output = Path(work) / 'report.json'
                if name == 'native-navigation.py':
                    report = probe(driver, output, model_provider='claude', require_selection=True)
                else:
                    report = probe(output, public_driver=driver, visual_flow=True,
                                   vision_provider='claude', require_selection=True)
                self.assertFalse(report['passed'])
                self.assertTrue(report['require_selection'])
                self.assertNotIn('fixture_pid', report)
                self.assertIn('initial selection unavailable', report['error'])
                self.assertTrue(factory.call_args.kwargs['require_selection'])
                self.assertEqual(json.loads(output.read_text())['input_audit']['status'], 'INCONCLUSIVE')
                launch.assert_not_called()
                model.assert_not_called()
                vision.assert_not_called()
                driver.act.assert_not_called()
                driver.observe.assert_not_called()

    def test_nonboolean_requirement_rejected_before_driver_creation(self):
        for name in ('native-navigation.py', 'native-opaque-input.py'):
            probe = runpy.run_path(str(ROOT / 'examples/probes' / name))['main']
            with patch('tacto.driver.Driver') as create:
                with self.assertRaisesRegex(ValueError, 'boolean'):
                    if name == 'native-navigation.py':
                        probe(Mock(), 'unused', require_selection=1)
                    else:
                        probe('unused', require_selection=1)
                create.assert_not_called()

    def test_foreign_process_with_same_bundle_id_never_reaches_observe_or_input(self):
        from tacto.native_window_payload import WINDOW_INPUT_CAPABILITIES
        probe = runpy.run_path(str(ROOT / 'examples/probes/native-opaque-input.py'))['main']
        driver = Mock(capabilities=WINDOW_INPUT_CAPABILITIES)
        driver._resolve.side_effect = [None, 777]
        audit = Mock()
        audit.__enter__ = Mock(return_value=audit)
        audit.__exit__ = Mock(return_value=False)
        audit.report.return_value = {'status': 'UNCHANGED_SAMPLED', 'require_selection': True}
        child = Mock(pid=123)
        child.poll.return_value = None
        child.wait.side_effect = lambda **kwargs: setattr(child.poll, 'return_value', 0)
        with tempfile.TemporaryDirectory() as work, \
                patch('tacto.driver.Driver', return_value=driver), \
                patch('tacto.input_audit_process.ProcessInputAudit', return_value=audit), \
                patch('subprocess.run'), patch('subprocess.Popen', return_value=child), \
                patch('time.monotonic', side_effect=[0, 11]), \
                patch('tacto.visual.plan_native_input') as vision:
            report = probe(Path(work)/'report.json', public_driver=driver,
                           visual_flow=True, vision_provider='claude', require_selection=True)
        self.assertFalse(report['passed'])
        self.assertIn('fixture did not start', report['error'])
        self.assertEqual(report['fixture_pid'], 123)
        self.assertTrue(report['owned_child_reaped'])
        driver.act.assert_not_called()
        driver.observe.assert_not_called()
        vision.assert_not_called()
        child.terminate.assert_called_once()
