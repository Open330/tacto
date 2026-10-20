"""Tacto settings use one namespace without mutating unrelated user data."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from tacto.environment import value
from tacto import store


class NamespaceConfigurationTests(unittest.TestCase):
    def test_environment_lookup_uses_canonical_names_without_mutating_input(self):
        env = {'TACTO_NO_INDICATOR':'1', 'OTHER_NO_INDICATOR':'0'}
        original = dict(env)
        self.assertEqual('1', value('NO_INDICATOR', environ=env))
        self.assertEqual(original, env)
        for key in ('AUTHOR_DEBUG', 'EVENT_MARKER', 'HELPER_FD', 'HELPER_TOKEN', 'LAUNCHER', 'NO_INDICATOR'):
            with self.subTest(key=key):
                self.assertEqual('4', value(key, environ={'TACTO_'+key:'4'}))
                self.assertIsNone(value(key, environ={'OTHER_'+key:'3'}))

    def test_inherited_authority_is_not_forwarded_to_model_processes(self):
        from tacto.authoring import _provider_env
        env = {name:'3' for name in ['TACTO_HELPER_FD','TACTO_LAUNCHER','TACTO_HELPER_TOKEN']}
        with patch.dict(os.environ, env, clear=True):
            cleaned = _provider_env()
        self.assertTrue(set(env).isdisjoint(cleaned))

    def test_unrelated_flow_directory_is_not_selected_or_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); data=root/'data'; unrelated=data/'other-product/flows'
            unrelated.mkdir(parents=True)
            flow=unrelated/'owned.json';flow.write_text('original')
            with patch.object(store,'__file__',str(root/'site-packages/tacto/store.py')), patch.dict(os.environ,{'XDG_DATA_HOME':str(data)}):
                canonical=data/'tacto/flows'
                self.assertEqual(str(canonical),store.default_directory())
                self.assertFalse(canonical.exists())
                self.assertEqual(str(canonical),store.FlowStore().path)
                self.assertEqual('original',flow.read_text())

    def test_environment_lookup_cannot_supply_trust_anchors(self):
        for key in ['TEAM_ID','HELPER_REQUIREMENT']:
            with self.assertRaises(KeyError):value(key,environ={'TACTO_'+key:'untrusted'})

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
