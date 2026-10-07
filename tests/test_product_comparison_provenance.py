"""Comparison provenance is checked without model calls or GUI access."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nlc_product_provenance', ROOT / 'examples/product_comparison.py')
comparison = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = comparison
spec.loader.exec_module(comparison)


class Provenance(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        self.root_patch = patch.object(comparison, 'ROOT', self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_rebuild_and_missing_artifact_are_part_of_identity(self):
        old = comparison.source_manifest()
        key = 'helper/NLC Helper.app/Contents/MacOS/NLCHelper'
        self.assertIsNone(old['files'][key])
        self.write(key, 'signed-v1')
        built = comparison.source_manifest()
        self.assertNotEqual(old['sha256'], built['sha256'])
        self.write(key, 'signed-v2')
        self.assertNotEqual(built['sha256'], comparison.source_manifest()['sha256'])

    def test_helper_source_and_relative_paths_change_identity(self):
        self.write('helper/WindowDispatch.swift', 'guard')
        self.write('nlc/a.py', 'same')
        before = comparison.source_manifest()
        self.write('helper/WindowDispatch.swift', 'new-guard')
        self.assertNotEqual(before['sha256'], comparison.source_manifest()['sha256'])
        self.assertIn('nlc/a.py', before['files'])

    def execute(self, *, change=False, exit_code=0, setup=None, cleanup=None):
        class Witness:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def summary(self): return {'activations': []}
        def fake_run(argv, **kwargs):
            pathlib.Path(argv[argv.index('-o') + 1]).write_text('claimed success')
            if change:
                self.write('helper/nlc-helper', 'rebuilt-during-run')
            return subprocess.CompletedProcess(argv, exit_code, '', '')
        task = comparison.Task('owned-test', 'owned goal', lambda answer: (True, 'oracle satisfied'), setup=setup, cleanup=cleanup)
        with patch.dict(sys.modules, {'focus_witness': types.SimpleNamespace(FocusWitness=Witness)}), \
             patch.object(comparison, '_locked', return_value=False), \
             patch.object(comparison.subprocess, 'run', side_effect=fake_run):
            return comparison.run_one(task, 'nlc')

    def test_drifting_source_cannot_pass_even_when_oracle_passes(self):
        result = self.execute(change=True)
        self.assertEqual('invalid', result['status'])
        self.assertEqual('passed', result['observed_status'])
        self.assertEqual('source_changed', result['invalid_reason'])
        self.assertTrue(result['oracle_passed'])
        self.assertNotEqual(result['source'], result['source_end'])

    def test_setup_changes_are_included_and_unchanged_run_passes(self):
        self.assertEqual('passed', self.execute()['status'])
        result = self.execute(setup=lambda: self.write('nlc/setup.py', 'changed'))
        self.assertTrue(result['source_changed'])
        self.assertEqual('invalid', result['status'])

    def test_nonzero_agent_exit_cannot_pass_from_leftover_app_state(self):
        result = self.execute(exit_code=1)
        self.assertEqual('failed', result['status'])
        self.assertEqual(1, result['process_exit'])
        self.assertTrue(result['oracle_passed'])
        self.assertFalse(result['source_changed'])

    def test_cleanup_error_is_visible_and_cleanup_source_drift_invalidates(self):
        def fail():
            raise RuntimeError('private error must not be logged')
        result = self.execute(cleanup=fail)
        self.assertEqual('failed', result['cleanup_status'])
        self.assertEqual('RuntimeError', result['cleanup_error'])
        self.assertNotIn('private error', str(result))
        changed = self.execute(cleanup=lambda: self.write('helper/nlc-helper', 'new-binary'))
        self.assertEqual('completed', changed['cleanup_status'])
        self.assertEqual('invalid', changed['status'])


class TextEditScopeTests(unittest.TestCase):
    """The TextEdit oracle reads and closes only the one window the run created."""
    P = comparison

    def setUp(self):
        self.reads, self.closed = [], []
        self.process = ('pid', 1)

    def run_with(self, before, after, process_after=None, body=None):
        P = self.P
        bodies = body or {}
        windows = iter([before, after, after])
        with patch.object(P, '_textedit_process',
                          side_effect=[(object(), self.process)] + [(object(), process_after or self.process)] * 4), \
             patch.object(P, '_textedit_windows', side_effect=lambda: next(windows)), \
             patch('CoreFoundation.CFEqual', side_effect=lambda a, b: a is b), \
             patch.object(P, '_text_of', side_effect=lambda w: self.reads.append(w) or bodies.get(w, P.TEXT)):
            P.textedit_baseline()
            return P.textedit_texts()

    def test_windows_open_before_setup_are_never_read(self):
        old, new = object(), object()
        self.assertEqual([self.P.TEXT], self.run_with([old], [old, new]))
        self.assertEqual([new], self.reads)

    def test_several_new_windows_are_not_read_at_all(self):
        old, a, b = object(), object(), object()
        self.assertEqual([], self.run_with([old], [old, a, b]))
        self.assertEqual([], self.reads)

    def test_a_restarted_textedit_is_not_read(self):
        old, new = object(), object()
        self.assertEqual([], self.run_with([old], [old, new], process_after=('pid', 2)))
        self.assertEqual([], self.reads)

    def test_cleanup_never_closes_a_window_without_our_text(self):
        P = self.P
        other = object()
        with patch.object(P, '_new_textedit_windows', return_value=[other]), \
             patch.object(P, '_text_of', return_value='사람이 쓴 글'), \
             patch('ApplicationServices.AXUIElementPerformAction',
                   side_effect=lambda *a: self.fail('closed a window that was not ours')):
            self.assertEqual(1, P.textedit_close(P.TEXT))
