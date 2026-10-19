import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from examples.focus_diagnostics import FocusDiagnosticReader
from tacto.input_audit import InputSnapshot, MacInputReader


class FocusDiagnosticTests(unittest.TestCase):
    def reader(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve(); root.chmod(0o700)
        return FocusDiagnosticReader(root/'focus.jsonl', max_records=2)

    def test_private_exclusive_output_snapshot_and_no_text_reads(self):
        reader = self.reader(); snapshot = InputSnapshot(10, (1, 2), 'focus-1')
        element = object(); reader._focuses = [(10, element, 'focus-1')]
        ax = Mock(); ax.AXUIElementGetPid.return_value = (0, 10)
        def attribute(node, name, out):
            if name == 'AXRole': return 0, 'AXTextField'
            if name == 'AXFocusedUIElement': return 0, element
            self.fail('unexpected attribute: ' + name)
        ax.AXUIElementCopyAttributeValue.side_effect = attribute
        cf = Mock(); cf.CFEqual.return_value = True
        with patch.object(MacInputReader, '__call__', return_value=snapshot), \
             patch.object(reader, '_load', return_value=(None, None, ax, cf)):
            self.assertIs(snapshot, reader())
        row = json.loads(Path(reader.output).read_text())
        self.assertEqual('complete', row['status']); self.assertEqual('AXTextField', row['role'])
        self.assertEqual(10, row['owner_pid'])
        self.assertEqual(0o600, Path(reader.output).stat().st_mode & 0o777)
        with self.assertRaises(FileExistsError): FocusDiagnosticReader(reader.output)

    def test_unknown_role_replacement_and_record_budget(self):
        reader = self.reader(); snapshot = InputSnapshot(10, (1, 2), 'focus-1')
        reader._focuses = [(10, object(), 'focus-1')]
        ax = Mock(); ax.AXUIElementGetPid.return_value = (0, 20)
        ax.AXUIElementCopyAttributeValue.side_effect = [(0, 'private label'), (0, object())]
        cf = Mock(); cf.CFEqual.return_value = False
        with patch.object(reader, '_load', return_value=(None, None, ax, cf)):
            row = reader.descriptor(snapshot)
        self.assertIsNone(row['role']); self.assertFalse(row['focus_recheck_matches'])
        self.assertNotIn('private label', json.dumps(row))
        with patch.object(MacInputReader, '__call__', return_value=snapshot), \
             patch.object(reader, 'descriptor', return_value=row):
            reader(); reader()
            with self.assertRaisesRegex(ValueError, 'budget'): reader()
        path = Path(reader.output); path.unlink(); path.write_text('foreign'); path.chmod(0o600)
        with patch.object(MacInputReader, '__call__', return_value=snapshot), \
             patch.object(reader, 'descriptor', return_value=row):
            reader.records = 0
            with self.assertRaisesRegex(ValueError, 'identity'): reader()
        self.assertEqual('foreign', path.read_text())

    def test_nonprivate_directory_refused_before_native_or_model_work(self):
        from examples import probe_native_select as probe
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve(); path.chmod(0o755)
            driver = Mock()
            with self.assertRaisesRegex(ValueError, 'private_focus_diagnostics'):
                probe.run_probe(driver, focus_diagnostics_dir=path)
            driver.act.assert_not_called()
            output = path/'report.json'
            with patch.object(probe, 'run_model_probe') as model, \
                 self.assertRaisesRegex(ValueError, 'nonmodel'):
                probe.run_and_save(driver, output=output, transport='owned', provider='codex',
                                   focus_diagnostics_dir=path)
            model.assert_not_called(); self.assertFalse(output.exists())
