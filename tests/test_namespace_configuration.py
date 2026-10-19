"""Canonical settings and legacy lookup must not split authority or user data."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tacto.environment import value
from tacto import store


class NamespaceConfigurationTests(unittest.TestCase):
    def test_canonical_environment_wins_without_mutating_input(self):
        env = {'NLC_NO_INDICATOR':'0', 'TACTO_NO_INDICATOR':'1'}
        self.assertEqual('1',value('NO_INDICATOR',environ=env))
        del env['TACTO_NO_INDICATOR']
        self.assertEqual('0',value('NO_INDICATOR',environ=env))
        self.assertEqual({'NLC_NO_INDICATOR':'0'},env)

    def test_helper_authority_is_read_only_from_canonical_names(self):
        for key in ('HELPER_FD', 'HELPER_TOKEN', 'LAUNCHER'):
            with self.subTest(key):
                self.assertIsNone(value(key, environ={'NLC_'+key: '3'}))
                self.assertEqual('4', value(key, environ={'NLC_'+key: '3', 'TACTO_'+key: '4'}))

    def test_inherited_authority_is_not_forwarded_to_model_processes(self):
        from tacto.authoring import _provider_env
        env = {name:'3' for name in ['TACTO_HELPER_FD','TACTO_LAUNCHER','TACTO_HELPER_TOKEN']}
        with patch.dict(os.environ, env, clear=True):
            cleaned = _provider_env()
        self.assertTrue(set(env).isdisjoint(cleaned))

    def test_existing_legacy_flows_are_not_moved_or_lost_on_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'data';old=data/'nlc/flows';old.mkdir(parents=True)
            flow=old/'owned.json';flow.write_text('original')
            with patch.object(store,'__file__',str(root/'site-packages/tacto/store.py')), patch.dict(os.environ,{'XDG_DATA_HOME':str(data)}):
                self.assertEqual(str(old),store.default_directory())
                self.assertEqual('original',flow.read_text())
                self.assertFalse((data/'tacto').exists())
                canonical=data/'tacto/flows';canonical.mkdir(parents=True)
                self.assertEqual(str(old),store.default_directory())
                (canonical/'new.json').write_text('new data')
                with self.assertRaises(store.FlowStore.MigrationRequired):store.FlowStore()
                self.assertEqual('original',flow.read_text())
                self.assertEqual(str(canonical),store.FlowStore(canonical).path)
                self.assertEqual('new data',(canonical/'new.json').read_text())

    def test_environment_lookup_cannot_supply_trust_anchors(self):
        for key in ['TEAM_ID','HELPER_REQUIREMENT']:
            with self.assertRaises(KeyError):value(key,environ={'NLC_'+key:'untrusted'})

    def test_empty_legacy_directory_does_not_select_old_brand(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'nlc/flows').mkdir(parents=True)
            with patch.object(store, '__file__', str(root/'site/tacto/store.py')), patch.dict(os.environ, {'XDG_DATA_HOME':str(root)}):
                self.assertEqual(str(root/'tacto/flows'), store.default_directory())

    def test_cli_explains_conflicting_stores_without_traceback_or_writes(self):
        import contextlib
        import io
        from tacto.cli import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('nlc', 'tacto'):
                folder=root/name/'flows';folder.mkdir(parents=True)
                (folder/'owned.json').write_text(name)
            error = io.StringIO()
            with patch.object(store, '__file__', str(root/'site/tacto/store.py')), patch.dict(os.environ, {'XDG_DATA_HOME':str(root)}), contextlib.redirect_stderr(error):
                self.assertEqual(2, main(['list']))
            self.assertIn('migration_required:', error.getvalue())
            self.assertNotIn('Traceback', error.getvalue())
            for name in ('nlc', 'tacto'):
                self.assertIn(str(root/name/'flows'), error.getvalue())
                self.assertEqual(name,(root/name/'flows/owned.json').read_text())

    def test_cli_refuses_foreign_namespace_before_runtime_imports(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            package=Path(directory)/'tacto';package.mkdir()
            (package/'__init__.py').write_text('')
            (package/'cli.py').write_text((Path(store.__file__).parent/'cli.py').read_text())
            result=subprocess.run([sys.executable,'-B','-m','tacto.cli','--help'],cwd=directory,capture_output=True,text=True)
            self.assertNotEqual(0,result.returncode)
            self.assertIn('namespace conflict',result.stderr)
            self.assertNotIn('No module named',result.stderr)

    def test_only_the_tacto_helper_build_is_selected(self):
        from tacto import helper
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            bundled=root/'Tacto Helper.app/Contents/MacOS/TactoHelper'
            bare=root/'tacto-helper'
            bundled.parent.mkdir(parents=True);bundled.touch();bare.touch()
            with patch.multiple(helper, _BUNDLED=str(bundled), _BARE=str(bare)):
                self.assertEqual([str(bundled),str(bare)], helper.helper_candidates())
            with patch.multiple(helper, _BUNDLED=str(root/'missing'), _BARE=str(bare)):
                self.assertEqual([str(bare)], helper.helper_candidates())

    def test_no_tracked_file_names_the_former_helper(self):
        import re, subprocess
        root=Path(__file__).resolve().parents[1]
        listed=subprocess.run(['git','ls-files','-z'],cwd=root,capture_output=True)
        if listed.returncode:
            self.skipTest('not a git checkout')
        former=re.compile('nl'+'c'+r'[ _-]?helper', re.IGNORECASE)
        hits=[]
        for name in listed.stdout.decode().split('\0'):
            path=root/name
            if not name or not path.is_file():
                continue
            try:
                text=path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            hits.extend(f'{name}:{n}' for n,line in enumerate(text.splitlines(),1) if former.search(line))
        self.assertEqual([], hits)
