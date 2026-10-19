"""Participation and signed-channel gates; never launch a UI or send input."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = load('examples/probes/concurrent-typing.py', 'typing_probe')


@contextlib.contextmanager
def isolated_reviews():
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory).resolve();reviews=root/'reviews';reviews.mkdir()
        with patch.object(probe,'ROOT',root):
            yield reviews


class ConcurrentTypingTests(unittest.TestCase):
    def run_refused(self, **options):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            with patch.object(probe.subprocess, 'Popen') as launch, contextlib.redirect_stdout(io.StringIO()):
                result = probe.run(Path('/missing'), output, Path('/missing-report'), **options)
            launch.assert_not_called()
            self.assertEqual(1, result)
            self.assertEqual(0o600, output.stat().st_mode & 0o777)
            value = json.loads(output.read_text())
            self.assertFalse(value['passed'])
            self.assertFalse(value['foreground_input_generated'])
            self.assertEqual([], value['actions'])
            return value

    def test_participation_is_explicit_true_before_helper_or_manifest(self):
        for value in (False, None, 1, 'true'):
            with self.subTest(value=value), patch('tacto.helper.InheritedHelperDriver.available') as available, \
                    patch.object(probe, 'prepared_fixtures') as prepared:
                report = self.run_refused(participant_confirmed=value)
                self.assertEqual('human_participation_required', report['refusal'])
                available.assert_not_called()
                prepared.assert_not_called()

    def test_unsigned_driver_cannot_lookup_or_launch(self):
        with patch('tacto.helper.InheritedHelperDriver.available') as available, \
                patch.object(probe, 'prepared_fixtures') as prepared:
            report = self.run_refused(driver=object(), participant_confirmed=True)
            self.assertEqual('signed_helper_required', report['refusal'])
            available.assert_not_called()
            prepared.assert_not_called()

    def test_report_reservation_precedes_work_and_preserves_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'report'
            target.write_text('KEEP')
            link = Path(directory) / 'link'
            link.symlink_to(target)
            for path in (target, link):
                with patch.object(probe, 'digest') as digest, self.assertRaises(FileExistsError):
                    probe.run(Path('/missing'), path, Path('/missing'))
                digest.assert_not_called()
            self.assertEqual('KEEP', target.read_text())

    def test_source_read_failure_still_finalizes_sanitized_report(self):
        with patch.object(probe, 'digest', side_effect=OSError('PRIVATE_SECRET')):
            value = self.run_refused()
        self.assertEqual('OSError', value['error_class'])
        self.assertEqual('OSError', value['finalization_error_class'])
        self.assertNotIn('PRIVATE_SECRET', json.dumps(value))

    def test_interrupt_is_preserved_and_report_finalized(self):
        from tacto.helper import InheritedHelperDriver
        driver = object.__new__(InheritedHelperDriver)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report'
            with patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
                    patch.object(probe, 'prepared_fixtures', side_effect=KeyboardInterrupt), \
                    patch.object(probe.subprocess, 'Popen') as launch, self.assertRaises(KeyboardInterrupt):
                probe.run(Path('/missing'), output, Path('/missing'), driver=driver, participant_confirmed=True)
            launch.assert_not_called()
            self.assertEqual('interrupted_no_retry', json.loads(output.read_text())['classification'])

    def test_prepared_manifest_cannot_choose_arbitrary_executable(self):
        with isolated_reviews() as reviews, tempfile.TemporaryDirectory(dir=reviews) as directory:
            base = Path(directory)
            background = base / 'background'
            witness = base / 'witness'
            background.mkdir(); witness.mkdir()
            for path, value in ((background / 'build-report.json', {'binary': '/bin/echo'}),
                                (witness / 'build-report.json', {'app': '/Applications/Other.app'})):
                path.write_text(json.dumps(value)); path.chmod(0o600)
            with self.assertRaisesRegex(ValueError, 'prepared_fixture_identity_changed'):
                probe.prepared_fixtures(background, witness / 'build-report.json')


class TypingEvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load('examples/eval.py', 'typing_eval')

    def test_default_scenario_skips_without_native_driver(self):
        with patch('tacto.helper.InheritedHelperDriver.available') as available:
            self.assertEqual('human_participation_required', self.module.scenario_signed_concurrent_typing())
        available.assert_not_called()

    def test_options_cannot_arm_other_scenarios_or_partial_preparation(self):
        for args in (['signed_concurrent_typing', '--participant-confirmed'],
                     ['signed_concurrent_typing', '--typing-directory', '/missing', '--typing-witness-report', '/missing'],
                     ['signed_native_selection_parameters', '--participant-confirmed', '--typing-directory', '/missing', '--typing-witness-report', '/missing']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.module._parse_plan(args)

    def test_registered_isolated_and_gui_only(self):
        self.assertIn('signed_concurrent_typing', self.module.ISOLATED)
        self.assertEqual({'gui', 'native'}, self.module.SCENARIO_REQUIREMENTS['signed_concurrent_typing'])

    def test_prepared_run_injects_signed_driver_without_launching(self):
        with isolated_reviews() as reviews, tempfile.TemporaryDirectory(dir=reviews) as directory:
            base = Path(directory); witness = base / 'report'; witness.touch()
            calls = []
            def run(*args, **kwargs):
                calls.append((args, kwargs)); return 0
            with patch('tacto.helper.InheritedHelperDriver.available', return_value=True), \
                    patch('tacto.helper.InheritedHelperDriver') as signed, \
                    patch('runpy.run_path', return_value={'run': run}), \
                    patch.object(self.module, '__file__', str(reviews.parent/'examples/eval.py')):
                self.assertTrue(self.module.scenario_signed_concurrent_typing(base, witness, True))
            self.assertIs(calls[0][1]['driver'], signed.return_value)
            self.assertIs(calls[0][1]['participant_confirmed'], True)


if __name__ == '__main__':
    unittest.main()
