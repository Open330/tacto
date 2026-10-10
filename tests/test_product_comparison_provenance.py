"""Comparison provenance is checked without model calls or GUI access."""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from shutil import rmtree as _real_rmtree

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('nlc_product_provenance', ROOT / 'examples/product_comparison.py')
comparison = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = comparison
spec.loader.exec_module(comparison)


class SessionAvailabilityTests(unittest.TestCase):
    def test_missing_lock_key_is_unknown_even_on_console(self):
        with patch('Quartz.CGSessionCopyCurrentDictionary', return_value={
                'kCGSSessionOnConsoleKey': True}):
            self.assertIsNone(comparison._locked())

    def test_only_an_explicit_unlocked_console_session_is_available(self):
        for locked in (True, False):
            with patch('Quartz.CGSessionCopyCurrentDictionary', return_value={
                    'CGSSessionScreenIsLocked': locked, 'kCGSSessionOnConsoleKey': True,
                    'kCGSessionLoginDoneKey': True}):
                self.assertIs(locked, comparison._locked())
        for state in (None, {}, {'CGSSessionScreenIsLocked': False},
                      {'CGSSessionScreenIsLocked': False, 'kCGSSessionOnConsoleKey': False}):
            with self.subTest(state=state), \
                 patch('Quartz.CGSessionCopyCurrentDictionary', return_value=state):
                self.assertIsNone(comparison._locked())

    def test_missing_lock_key_with_logged_in_console_is_not_a_false_block(self):
        from nlc.session import lock_state
        with patch('Quartz.CGSessionCopyCurrentDictionary', return_value={
                'kCGSSessionOnConsoleKey': True, 'kCGSessionLoginDoneKey': True}):
            self.assertIs(False, comparison._locked())
            self.assertEqual({'locked':False, 'on_console':True, 'login_done':True,
                             'source':'CGSessionCopyCurrentDictionary'}, lock_state())

    def test_malformed_flags_are_unknown_and_reader_details_are_not_exposed(self):
        from nlc.session import lock_state
        for value in (None, 0, 'false', [], {}):
            with self.subTest(value=value), \
                 patch('Quartz.CGSessionCopyCurrentDictionary', return_value={
                    'CGSSessionScreenIsLocked': value, 'kCGSSessionOnConsoleKey': True,
                    'kCGSessionLoginDoneKey': True}):
                self.assertIsNone(comparison._locked())
        with patch('Quartz.CGSessionCopyCurrentDictionary', side_effect=RuntimeError('PRIVATE')):
            self.assertNotIn('PRIVATE', str(lock_state()))

    def test_session_reader_error_cannot_allow_comparison(self):
        with patch('Quartz.CGSessionCopyCurrentDictionary', side_effect=RuntimeError('PRIVATE')):
            self.assertIsNone(comparison._locked())

    def test_unknown_session_does_not_start_fixture_agent_or_cleanup(self):
        from unittest.mock import Mock
        setup, cleanup, oracle = Mock(), Mock(), Mock()
        task = comparison.Task('owned-test', 'owned goal', oracle, setup=setup, cleanup=cleanup)
        manifest = {'version':2, 'sha256':'a'*64, 'files':{}}
        with patch('Quartz.CGSessionCopyCurrentDictionary', return_value={
                'kCGSSessionOnConsoleKey': True}), \
             patch.object(comparison, 'source_manifest', return_value=manifest), \
             patch.object(comparison.subprocess, 'run') as run:
            result = comparison.run_one(task, 'nlc')
        self.assertEqual('not_run', result['status'])
        self.assertEqual('session_unobservable', result['observed'])
        self.assertFalse(result['comparison_eligible'])
        self.assertIsNone(result['oracle_passed'])
        self.assertIsNone(result['process_exit'])
        setup.assert_not_called()
        cleanup.assert_not_called()
        oracle.assert_not_called()
        run.assert_not_called()


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
            def summary(self): return {'initial': 'com.example.foreground-sentinel', 'activations': []}
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
        unchanged = self.execute()
        self.assertEqual('passed', unchanged['status'])
        self.assertEqual('com.example.foreground-sentinel', unchanged['initial_frontmost_bundle'])
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

    def closed_then(self, seen, read=lambda w: ('path', None)):
        """Run cleanup where the close press works and the window then leaves
        owned_window(); `seen` is the final same-process read."""
        mine = self.P._OWNED['window']
        paths = {mine: self.P._owned_path()}
        with patch('shutil.rmtree', side_effect=lambda *a: self.fail('removed')), \
             patch.object(self.P, '_observed_windows', return_value=seen), \
             patch.object(self.P, '_document_read', side_effect=read), \
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
        out = self.closed_then((('pid', 1), [unknown]),
                               read=lambda w: ('error', None) if w is unknown else ('none', None))
        self.assertEqual('not_observable_after_close_left_as_is', out)

    def test_only_known_other_windows_reach_the_directory_check(self):
        self.own()
        # file_changed: every window check passed and the last check was reached
        self.assertEqual('file_changed_left_as_is', self.closed_then((('pid', 1), [object()])))

    def test_a_path_that_recovers_on_the_final_read_is_still_open(self):
        # Codex review of 6c2c6a9: the wait loop's read failed (None, so the
        # window left owned_window()), then a separate later read succeeded
        # with OUR path. The decision must use the read it looks at.
        self.own(); wrapper = object()
        out = self.closed_then((('pid', 1), [wrapper]),
                               read=lambda w: ('path', self.P._owned_path()))
        self.assertEqual('still_open_left_as_is', out)

    def test_one_read_classifies_a_window(self):
        P = self.P
        for result, want in (((-25212, None), ('none', None)),
                             ((-25205, None), ('error', None)),
                             ((0, 42), ('error', None)),
                             ((0, 'https://x/a'), ('path', None)),
                             ((0, 'file:///tmp/a.txt'), ('path', P._nfc(pathlib.Path('/tmp/a.txt').resolve())))):
            with patch('ApplicationServices.AXUIElementCopyAttributeValue', return_value=result) as read:
                self.assertEqual(want, P._document_read(object()), result)
                self.assertEqual(1, read.call_count)

    def test_a_process_replaced_during_the_read_is_not_an_observation(self):
        app = types.SimpleNamespace(processIdentifier=lambda: 1)
        identities = iter([(app, ('pid', 1)), (app, ('pid', 2))])
        with patch.object(self.P, '_textedit_process', side_effect=lambda: next(identities)), \
             patch('ApplicationServices.AXUIElementCreateApplication', return_value='app'), \
             patch('ApplicationServices.AXUIElementCopyAttributeValue', return_value=(0, [])):
            self.assertIsNone(self.P._observed_windows())


class FinderFolderTaskTests(unittest.TestCase):
    """The folder task: a fresh folder per run, judged by Finder, and nothing
    in Finder is closed by the harness."""
    P = comparison

    def setUp(self):
        self.P._FOLDER.clear()
        self.addCleanup(self.P._FOLDER.clear)

    def state(self, shown=(), sheets=()):
        return {'shown': list(shown), 'sheets': list(sheets)}

    def made(self):
        self.P._make_folder()
        path = self.P._FOLDER['path']
        self.addCleanup(_real_rmtree, path, True)
        return path

    def test_each_run_gets_its_own_folder_and_no_fixed_path_is_removed(self):
        import os
        with patch('shutil.rmtree', side_effect=lambda *a, **k: self.fail('setup removed')):
            first = self.made()
            second = self.made()
        self.assertNotEqual(first, second)
        for path in (first, second):
            self.assertTrue(os.path.basename(path).startswith(self.P.FOLDER_PREFIX))
            self.assertEqual(self.P._folder_names(), set(os.listdir(path)))
        self.assertIn(second, self.P._folder_ask())

    def test_setup_refuses_a_window_already_named_like_the_folder(self):
        with patch.object(self.P, 'finder_state', return_value=self.state(shown=['w'])), \
                patch.object(self.P, '_make_folder'):
            with self.assertRaisesRegex(RuntimeError, 'finder_not_clean'):
                self.P.finder_setup()

    def test_a_refused_setup_removes_the_folder_it_just_made(self):
        import os
        made, real = [], self.P._make_folder

        def track():
            real()
            made.append(self.P._FOLDER['path'])
            self.addCleanup(_real_rmtree, self.P._FOLDER['path'], True)
        for state in (None, self.state(shown=['w'])):
            with self.subTest(state=state), \
                    patch.object(self.P, '_make_folder', side_effect=track), \
                    patch.object(self.P, 'finder_state', return_value=state):
                with self.assertRaisesRegex(RuntimeError, 'own folder removed'):
                    self.P.finder_setup()
            self.assertEqual({}, self.P._FOLDER)
        # Only the paths this test made are checked; other folders in /tmp
        # are none of its business (Codex review of bd5c9e2).
        self.assertEqual(2, len(made))
        self.assertEqual([], [p for p in made if os.path.exists(p)])

    def test_setup_records_open_sheets_instead_of_refusing_or_closing(self):
        with patch.object(self.P, 'finder_state', return_value=self.state(sheets=['s'])), \
                patch.object(self.P, '_make_folder'):
            self.P.finder_setup()
        self.assertEqual(1, self.P._FOLDER['sheets_before'])

    def test_setup_refuses_when_finder_cannot_be_read(self):
        with patch.object(self.P, '_make_folder'), \
                patch.object(self.P, 'finder_state', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'finder_unobservable'):
                self.P.finder_setup()

    def test_a_failed_read_is_not_an_empty_finder(self):
        self.P._FOLDER['path'] = '/tmp/nlc-compare-folder-x'
        for failing in ('AXTitle', 'AXChildren', 'AXIdentifier'):
            def ax(el, name, failing=failing):
                if name == failing:
                    return False, None
                return True, ({'AXTitle': 'Documents', 'AXChildren': ['sheet'],
                               'AXIdentifier': 'GoToWindow'}[name])
            with self.subTest(failing=failing), \
                    patch.object(self.P, '_finder_windows', return_value=['w']), \
                    patch.object(self.P, '_ax', side_effect=ax):
                self.assertIsNone(self.P.finder_state())

    def test_answer_alone_does_not_pass(self):
        with patch.object(self.P, 'finder_state', return_value=self.state()):
            passed, observed = self.P.finder_oracle('It contains 7 items.')
        self.assertFalse(passed)
        self.assertIn('finder_window_titled_with_run_folder=no', observed)

    def test_answer_and_finder_showing_the_count_pass(self):
        with patch.object(self.P, 'finder_state', return_value=self.state(shown=['w'])), \
                patch.object(self.P, '_find_identified', return_value='bar'), \
                patch('nlc.driver._attr', return_value='7 items, 286 GB available'):
            passed, _ = self.P.finder_oracle('7 items')
        self.assertTrue(passed)

    def test_wrong_count_on_screen_fails(self):
        with patch.object(self.P, 'finder_state', return_value=self.state(shown=['w'])), \
                patch.object(self.P, '_find_identified', return_value='bar'), \
                patch('nlc.driver._attr', return_value='17 items'):
            passed, _ = self.P.finder_oracle('7 items')
        self.assertFalse(passed)

    def test_cleanup_presses_nothing_and_removes_only_its_own_folder(self):
        import os
        path = self.made()
        with patch('ApplicationServices.AXUIElementPerformAction',
                   side_effect=lambda *a: self.fail('pressed in Finder')), \
                patch.object(self.P, 'finder_state',
                             return_value=self.state(shown=['w'], sheets=['s'])):
            out = self.P.finder_cleanup()
        self.assertTrue(out.startswith('folder_removed'), out)
        self.assertIn('windows=1 sheets=1', out)
        self.assertFalse(os.path.exists(path))

    def test_a_changed_folder_is_left(self):
        import os
        path = self.made()
        pathlib.Path(path, 'someone-else.txt').write_text('x')
        with patch.object(self.P, 'finder_state', return_value=None):
            out = self.P.finder_cleanup()
        self.assertTrue(out.startswith('folder_changed_left_as_is'), out)
        self.assertIn('finder_unobservable', out)
        self.assertTrue(os.path.exists(path))


class NewTaskOracleTests(unittest.TestCase):
    """2026-10-09 tasks: saved-on-disk, a new folder, appearance."""
    P = comparison

    def test_saved_means_on_disk_and_shown(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            d = os.path.realpath(d)
            path = os.path.join(d, self.P.OWNED_NAME)
            self.P._OWNED.clear(); self.addCleanup(self.P._OWNED.clear)
            st = os.stat(d)
            self.P._OWNED.update(path=path, directory=d, inode=(st.st_dev, st.st_ino))
            for disk, shown, want in (('NLC 저장 확인 4417', True, True),
                                      ('', True, False),          # typed but not saved
                                      ('NLC 저장 확인 4417', False, False),
                                      (None, True, False)):
                if disk is None:
                    if os.path.exists(path): os.unlink(path)
                else:
                    pathlib.Path(path).write_text(disk, encoding='utf-8')
                with self.subTest(disk=disk, shown=shown), \
                        patch.object(self.P, 'owned_oracle', return_value=(shown, 'w')):
                    self.assertEqual(want, self.P.owned_saved_oracle('NLC 저장 확인 4417')[0])

    def test_new_folder_needs_exactly_one_named_folder(self):
        import os
        self.P.newdir_setup()
        base = self.P._NEWDIR['path']
        self.addCleanup(lambda: _real_rmtree(base, True))
        self.assertFalse(self.P.newdir_oracle('untitled folder')[0])
        os.mkdir(os.path.join(base, 'untitled folder'))
        self.assertTrue(self.P.newdir_oracle('Created "untitled folder".')[0])
        self.assertFalse(self.P.newdir_oracle('Created a folder.')[0])
        pathlib.Path(base, 'stray.txt').write_text('x')
        self.assertFalse(self.P.newdir_oracle('untitled folder')[0])
        self.assertEqual('unexpected_contents_left_as_is', self.P.newdir_cleanup())
        os.unlink(os.path.join(base, 'stray.txt'))
        self.assertEqual('folder_removed', self.P.newdir_cleanup())
        self.assertFalse(os.path.exists(base))

    def test_a_link_to_a_folder_is_not_a_new_folder(self):
        import os, tempfile
        self.P.newdir_setup()
        base = self.P._NEWDIR['path']
        self.addCleanup(lambda: _real_rmtree(base, True))
        outside = tempfile.mkdtemp(); self.addCleanup(lambda: _real_rmtree(outside, True))
        os.symlink(outside, os.path.join(base, 'linked'))
        self.assertFalse(self.P.newdir_oracle('linked')[0])
        self.assertEqual('unexpected_contents_left_as_is', self.P.newdir_cleanup())
        self.assertTrue(os.path.isdir(outside))

    def test_a_failed_defaults_read_is_unobservable_not_light(self):
        def run(argv, **k):
            return types.SimpleNamespace(returncode=1, stdout='', stderr='permission denied')
        with patch.object(self.P.subprocess, 'run', side_effect=run):
            self.assertIsNone(self.P._appearance())
            self.assertEqual((False, 'appearance unobservable'), self.P.appearance_oracle('Light'))
        def missing(argv, **k):
            return types.SimpleNamespace(returncode=1, stdout='', stderr='The domain/default pair does not exist')
        with patch.object(self.P.subprocess, 'run', side_effect=missing):
            self.assertEqual('Light', self.P._appearance())
        def missing27(argv, **k):   # the wording macOS 27 actually prints
            return types.SimpleNamespace(returncode=1, stdout='',
                stderr="Error: Could not find key 'AppleInterfaceStyle' in domain 'kCFPreferencesAnyApplication'.")
        with patch.object(self.P.subprocess, 'run', side_effect=missing27):
            self.assertEqual('Light', self.P._appearance())

    def test_appearance_must_name_only_the_right_mode(self):
        with patch.object(self.P, '_appearance', return_value='Light'):
            self.assertTrue(self.P.appearance_oracle('Appearance is set to Light.')[0])
            self.assertFalse(self.P.appearance_oracle('It is Dark.')[0])
            self.assertFalse(self.P.appearance_oracle('Light or Dark, not sure')[0])
