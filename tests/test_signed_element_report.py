"""Interrupted native probes must preserve a private failure record."""
import json
from pathlib import Path
import runpy
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build


class SignedElementReportTests(unittest.TestCase):
    @requires_signed_build
    def test_interrupt_before_fixture_is_reported_and_existing_record_is_preserved(self):
        root = Path(__file__).resolve().parents[1]
        driver = SimpleNamespace(capabilities=[])
        audit = SimpleNamespace(report=lambda: {'status':'NOT_STARTED'})
        for probe in ('signed-element-click.py', 'signed-commit-disconnect.py', 'signed-native-recovery.py', 'native-opaque-point.py'):
            with self.subTest(probe=probe), tempfile.TemporaryDirectory(prefix='tacto-private-report-') as work:
                main = runpy.run_path(str(root / 'examples/probes' / probe))['main']
                def invoke(path):
                    return main(driver, path) if probe != 'native-opaque-point.py' else main(path)
                path = Path(work) / 'report.json'
                with patch('tacto.driver.Driver._resolve', side_effect=KeyboardInterrupt), \
                     patch('tacto.input_audit_process.ProcessInputAudit', return_value=audit):
                    with self.assertRaises(KeyboardInterrupt):
                        invoke(path)
                    original = path.read_bytes()
                    report = json.loads(original)
                    self.assertFalse(report['passed'])
                    self.assertTrue(report['interrupted'])
                    self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
                    with self.assertRaises(FileExistsError):
                        invoke(path)
                    self.assertEqual(original, path.read_bytes())
