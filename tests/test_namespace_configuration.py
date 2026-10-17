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
        env = {'NLC_HELPER_FD':'3', 'TACTO_HELPER_FD':'4'}
        self.assertEqual('4',value('HELPER_FD',environ=env))
        del env['TACTO_HELPER_FD']
        self.assertEqual('3',value('HELPER_FD',environ=env))
        self.assertEqual({'NLC_HELPER_FD':'3'},env)

    def test_legacy_inherited_authority_is_not_forwarded_to_model_processes(self):
        from tacto.authoring import _provider_env
        env = {name:'3' for name in ['NLC_HELPER_FD','TACTO_HELPER_FD','NLC_LAUNCHER','TACTO_LAUNCHER','NLC_HELPER_TOKEN','TACTO_HELPER_TOKEN']}
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

    def test_coexisting_old_bundle_and_new_bare_are_diagnosed(self):
        from tacto import helper, onboard
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            old=root/'NLC Helper.app/Contents/MacOS/NLCHelper'
            bare=root/'tacto-helper'
            old.parent.mkdir(parents=True);old.touch();bare.touch()
            with patch.multiple(helper, _BUNDLED=str(root/'missing'), _LEGACY_BUNDLED=str(old), _BARE=str(bare), _LEGACY_BARE=str(root/'missing-old'), HELPER=str(old)), patch.object(helper, 'HelperDriver', side_effect=RuntimeError('test only')):
                status=onboard.helper_status()
            self.assertEqual(str(old), status['path'])
            self.assertEqual([str(old),str(bare)], status['coexisting_builds'])
            lines=[]
            with patch.object(onboard, 'helper_status', return_value=status):
                onboard.run_helper_onboarding(lines.append, interactive=False)
            self.assertIn(str(old), '\n'.join(lines))
            self.assertIn('./build.sh', '\n'.join(lines))
