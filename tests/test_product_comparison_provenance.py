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


class OwnedDocumentTests(unittest.TestCase):
    """textedit_edit_owned reads and removes only the harness's own document."""
    P = comparison

    def setUp(self):
        self.P._OWNED.clear()
        self.addCleanup(self.P._OWNED.clear)

    def own(self, path='/private/tmp/x/NLC.txt', window=None):
        self.P._OWNED.update(path=path, directory='/private/tmp/x', inode=(1, 2),
                             process=('pid', 1), window=window or object())
        return self.P._OWNED['window']

    def env(self, windows, paths, process=('pid', 1), read=None):
        P = self.P
        read = read if read is not None else []
        return [patch.object(P, '_textedit_process', return_value=(object(), process)),
                patch.object(P, '_textedit_windows', return_value=windows),
                patch.object(P, '_window_path', side_effect=lambda w: paths.get(w)),
                patch('CoreFoundation.CFEqual', side_effect=lambda a, b: a is b),
                patch.object(P, '_text_of', side_effect=lambda w: read.append(w) or P.TEXT)]

    def run_env(self, patches, fn):
        import contextlib
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return fn()

    def test_only_the_owned_window_is_read_even_beside_the_same_text(self):
        mine = self.own(); persons = object(); read = []
        paths = {mine: self.P._owned_path(), persons: None}
        ok, _ = self.run_env(self.env([persons, mine], paths, read=read),
                             lambda: self.P.owned_oracle(self.P.TEXT))
        self.assertTrue(ok)
        self.assertEqual([mine], read)

    def test_restart_twin_or_swapped_window_reads_nothing(self):
        for case in ('restart', 'twin', 'swap'):
            self.P._OWNED.clear()
            mine = self.own(); other = object(); read = []
            windows, paths, process = [mine], {mine: self.P._owned_path()}, ('pid', 1)
            if case == 'restart':
                process = ('pid', 2)
            elif case == 'twin':
                windows, paths = [mine, other], {mine: self.P._owned_path(), other: self.P._owned_path()}
            else:
                windows, paths = [other], {other: self.P._owned_path()}
            ok, _ = self.run_env(self.env(windows, paths, process, read),
                                 lambda: self.P.owned_oracle(self.P.TEXT))
            self.assertEqual((False, []), (ok, read), case)

    def test_unproven_cleanup_removes_nothing(self):
        mine = self.own()
        with patch('shutil.rmtree', side_effect=lambda *a: self.fail('removed unproven')), \
             patch('ApplicationServices.AXUIElementPerformAction',
                   side_effect=lambda *a: self.fail('pressed unproven')):
            out = self.run_env(self.env([object()], {}), self.P.owned_cleanup)
        self.assertEqual('not_provable_left_as_is', out)

    def test_a_failed_close_or_a_window_still_open_keeps_the_folder(self):
        mine = self.own()
        paths = {mine: self.P._owned_path()}
        for press, label in ((-25200, 'close_failed_left_as_is'), (0, 'still_open_left_as_is')):
            with patch('shutil.rmtree', side_effect=lambda *a: self.fail('removed while open')), \
                 patch('nlc.driver._attr', side_effect=lambda el, k: 'close' if k == 'AXCloseButton' else []), \
                 patch('ApplicationServices.AXUIElementPerformAction', return_value=press), \
                 patch.object(self.P, '_observed_windows', return_value=(('pid', 1), [mine])), \
                 patch.object(self.P.time, 'sleep'):
                out = self.run_env(self.env([mine], paths), self.P.owned_cleanup)
            self.assertEqual(label, out)

    def test_setup_never_removes_an_existing_directory(self):
        import os, tempfile
        with patch('shutil.rmtree', side_effect=lambda *a: self.fail('setup removed a directory')), \
             patch.object(self.P.subprocess, 'run'), \
             patch.object(self.P, '_textedit_process', return_value=(None, None)), \
             patch.object(self.P, '_textedit_windows', return_value=[]), \
             patch.object(self.P.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                self.P.owned_setup()
        directory = self.P._OWNED['directory']
        self.assertEqual(0o700, os.stat(directory).st_mode & 0o777)
        self.assertTrue(os.path.basename(directory).startswith('nlc-compare-owned-'))
        import shutil; shutil.rmtree(directory)

    def test_only_local_file_urls_are_paths(self):
        P = self.P
        for url, ok in (('file:///tmp/a.txt', True), ('file://localhost/tmp/a.txt', True),
                        ('file://evil.example/tmp/a.txt', False), ('file:///tmp/a.txt?x=1', False),
                        ('https://x/tmp/a.txt', False)):
            with patch('nlc.driver._attr', return_value=url):
                self.assertEqual(ok, P._window_path(object()) is not None, url)

    def test_korean_names_compare_composed(self):
        import unicodedata
        self.own(path='/private/tmp/x/NLC 비교 문서.txt')
        nfd = unicodedata.normalize('NFD', str(self.P._owned_path()))
        self.assertEqual(self.P._owned_path(), self.P._nfc(nfd))

    def test_a_restart_or_failed_read_after_close_removes_nothing(self):
        mine = self.own()
        paths = {mine: self.P._owned_path()}
        for seen in (None, (('pid', 2), [])):          # read failed / restarted
            with patch('shutil.rmtree', side_effect=lambda *a: self.fail('removed after unknown close')), \
                 patch.object(self.P, '_observed_windows', return_value=seen), \
                 patch('nlc.driver._attr', side_effect=lambda el, k: 'close' if k == 'AXCloseButton' else []), \
                 patch('ApplicationServices.AXUIElementPerformAction', return_value=0), \
                 patch.object(self.P.time, 'sleep'):
                windows = iter([[mine], [mine]] + [[]] * 40)
                patches = self.env([mine], paths)
                patches[1] = patch.object(self.P, '_textedit_windows', side_effect=lambda: next(windows))
                out = self.run_env(patches, self.P.owned_cleanup)
            self.assertEqual('not_observable_after_close_left_as_is', out, seen)

    def closed_then(self, seen, known=lambda w: True):
        """Run cleanup where the close press works and the window then leaves
        owned_window(); `seen` is the final same-process read."""
        mine = self.P._OWNED['window']
        paths = {mine: self.P._owned_path()}
        with patch('shutil.rmtree', side_effect=lambda *a: self.fail('removed')), \
             patch.object(self.P, '_observed_windows', return_value=seen), \
             patch.object(self.P, '_document_known', side_effect=known), \
             patch.object(self.P, '_owned_file_is_ours', return_value=False), \
             patch('nlc.driver._attr', side_effect=lambda el, k: 'close' if k == 'AXCloseButton' else []), \
             patch('ApplicationServices.AXUIElementPerformAction', return_value=0), \
             patch.object(self.P.time, 'sleep'):
            windows = iter([[mine], [mine]] + [[]] * 40)
            patches = self.env([mine], paths)
            patches[1] = patch.object(self.P, '_textedit_windows', side_effect=lambda: next(windows))
            return self.run_env(patches, self.P.owned_cleanup)

    def test_the_same_window_still_listed_is_open_whatever_its_path_reads(self):
        mine = self.own()
        # its AXDocument now fails (_window_path -> None), but it is the same window
        self.assertEqual('still_open_left_as_is', self.closed_then((('pid', 1), [mine])))

    def test_a_window_whose_document_cannot_be_read_blocks_removal(self):
        self.own(); unknown = object()
        out = self.closed_then((('pid', 1), [unknown]), known=lambda w: w is not unknown)
        self.assertEqual('not_observable_after_close_left_as_is', out)

    def test_only_known_other_windows_reach_the_directory_check(self):
        self.own()
        # file_changed: every window check passed and the last check was reached
        self.assertEqual('file_changed_left_as_is', self.closed_then((('pid', 1), [object()])))

    def test_a_process_replaced_during_the_read_is_not_an_observation(self):
        app = types.SimpleNamespace(processIdentifier=lambda: 1)
        identities = iter([(app, ('pid', 1)), (app, ('pid', 2))])
        with patch.object(self.P, '_textedit_process', side_effect=lambda: next(identities)), \
             patch('ApplicationServices.AXUIElementCreateApplication', return_value='app'), \
             patch('ApplicationServices.AXUIElementCopyAttributeValue', return_value=(0, [])):
            self.assertIsNone(self.P._observed_windows())


class FinderFolderTaskTests(unittest.TestCase):
    """The folder task is judged by Finder, and starts from a Finder not there."""

    def state(self, shown=(), sheets=()):
        return {'shown': list(shown), 'sheets': list(sheets)}

    def test_setup_refuses_a_finder_already_at_the_folder(self):
        for state in (self.state(shown=['w']), self.state(sheets=['s'])):
            with self.subTest(state=state), \
                    patch.object(comparison, '_make_folder'), \
                    patch.object(comparison, 'finder_state', return_value=state):
                with self.assertRaisesRegex(RuntimeError, 'finder_not_clean'):
                    comparison.finder_setup()

    def test_setup_refuses_when_finder_cannot_be_read(self):
        with patch.object(comparison, '_make_folder'), \
                patch.object(comparison, 'finder_state', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'finder_unobservable'):
                comparison.finder_setup()

    def test_answer_alone_does_not_pass(self):
        with patch.object(comparison, 'finder_state', return_value=self.state()):
            passed, observed = comparison.finder_oracle('It contains 7 items.')
        self.assertFalse(passed)
        self.assertIn('finder_at_folder=no', observed)

    def test_answer_and_finder_showing_the_count_pass(self):
        with patch.object(comparison, 'finder_state', return_value=self.state(shown=['w'])), \
                patch.object(comparison, '_find_identified', return_value='bar'), \
                patch('nlc.driver._attr', return_value='7 items, 286 GB available'):
            passed, _ = comparison.finder_oracle('7 items')
        self.assertTrue(passed)

    def test_wrong_count_on_screen_fails(self):
        with patch.object(comparison, 'finder_state', return_value=self.state(shown=['w'])), \
                patch.object(comparison, '_find_identified', return_value='bar'), \
                patch('nlc.driver._attr', return_value='17 items'):
            passed, _ = comparison.finder_oracle('7 items')
        self.assertFalse(passed)

    def test_cleanup_reports_what_it_could_not_close(self):
        with patch.object(comparison, 'finder_state', return_value=None):
            self.assertEqual(comparison.finder_cleanup(), 'not_observable_left_as_is')
