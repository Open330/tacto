"""Import portability and opt-in wheel installation, without any UI access."""
import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PORTABLE_IMPORTS = '''
import importlib.abc
import sys
class BlockNative(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'ApplicationServices','Quartz','AppKit','Foundation','objc'}:
            raise ImportError('native module intentionally unavailable')
sys.meta_path.insert(0, BlockNative())
import tacto.agent, tacto.ask, tacto.browser, tacto.cli, tacto.flow, tacto.general
import tacto.outcome, tacto.protocol, tacto.trace
import tacto.native_visual, tacto.native_visual_actions, tacto.provider_images, tacto.general_visual, tacto.visual_replay, tacto.native_window_payload
assert not any(name in sys.modules for name in ('ApplicationServices','Quartz','AppKit'))
'''


class InstallationTests(unittest.TestCase):
    def test_core_and_browser_import_without_native_dependencies(self):
        result = subprocess.run([sys.executable, '-c', PORTABLE_IMPORTS], cwd=ROOT,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_help_aliases_succeed_without_loading_native_driver(self):
        from tacto.cli import main
        for alias in ('--help', '-h', 'help'):
            with self.subTest(alias=alias), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(main([alias]), 0)
                self.assertIn('Usage:', output.getvalue())

    def test_package_metadata_keeps_native_dependencies_platform_scoped(self):
        data = tomllib.loads((ROOT / 'pyproject.toml').read_text())
        dependencies = data['project']['dependencies']
        self.assertTrue(all("sys_platform == 'darwin'" in item
                            for item in dependencies if item.startswith('pyobjc')))
        self.assertIn('web', data['project']['optional-dependencies'])
        self.assertFalse(data['tool']['setuptools']['include-package-data'])

    def test_store_uses_user_data_after_install_and_respects_explicit_path(self):
        from tacto import store
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            installed_file = work / 'site-packages/tacto/store.py'
            data = work / 'user-data'
            with patch.object(store, '__file__', str(installed_file)), \
                    patch.dict(os.environ, {'XDG_DATA_HOME': str(data)}):
                explicit = store.FlowStore(work / 'explicit')
                self.assertEqual(explicit.path, str(work / 'explicit'))
                self.assertFalse(data.exists())
                installed = store.FlowStore()
                self.assertEqual(installed.path, str(data / 'tacto/flows'))
                self.assertEqual(installed.list(), [])
                self.assertFalse((work / 'site-packages/flows').exists())

    def test_store_source_checkout_keeps_existing_recipe_directory(self):
        from tacto import store
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'pyproject.toml').write_text('[project]\nname="tacto"\n')
            (root / 'flows').mkdir()
            with patch.object(store, '__file__', str(root / 'tacto/store.py')), \
                    patch.dict(os.environ, {'XDG_DATA_HOME': str(root / 'data')}):
                self.assertEqual(store.FlowStore().path, str(root / 'flows'))
                self.assertFalse((root / 'data').exists())

    def test_store_missing_or_relative_xdg_falls_back_to_user_home(self):
        from tacto import store
        with tempfile.TemporaryDirectory() as directory:
            for xdg in ('', 'relative/data'):
                with self.subTest(xdg=xdg), \
                        patch.object(store, '__file__', str(Path(directory) / 'site/tacto/store.py')), \
                        patch.dict(os.environ, {'XDG_DATA_HOME': xdg}), \
                        patch.object(store.os.path, 'expanduser', return_value=directory):
                    self.assertEqual(store.FlowStore().path,
                                     str(Path(directory) / '.local/share/tacto/flows'))

    def test_installed_eval_reports_missing_fixture_without_traceback(self):
        from tacto import cli
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(cli, '__file__', str(Path(directory) / 'tacto/cli.py')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['eval', '--offline']), 2)
            self.assertIn('소스 checkout', output.getvalue())

    @unittest.skipUnless(os.environ.get('TACTO_TEST_WHEEL_INSTALL') == '1',
                         'opt-in: builds and installs an isolated wheel')
    def test_built_wheel_installs_outside_checkout_and_help_is_portable(self):
        uv = shutil.which('uv')
        if uv is None:
            self.skipTest('uv build frontend is unavailable')
        with tempfile.TemporaryDirectory(prefix='tacto-wheel-test-') as directory:
            work = Path(directory)
            source = work / 'source'
            source.mkdir()
            for name in ('pyproject.toml', 'README.md'):
                shutil.copy2(ROOT / name, source / name)
            shutil.copytree(ROOT / 'nlc', source / 'nlc', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            shutil.copytree(ROOT / 'tacto', source / 'tacto',
                            ignore=shutil.ignore_patterns('__pycache__'))

            def execute(argv, **kwargs):
                result = subprocess.run(argv, cwd=work, capture_output=True,
                                        text=True, timeout=120, **kwargs)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result

            execute([uv, 'build', '--wheel', '--out-dir', str(work / 'dist'), str(source)])
            wheels = list((work / 'dist').glob('*.whl'))
            self.assertEqual(len(wheels), 1)
            self.assertTrue(wheels[0].name.endswith('-py3-none-any.whl'))
            with zipfile.ZipFile(wheels[0]) as wheel:
                names = wheel.namelist()
                self.assertIn('tacto/browser.py', names)
                self.assertIn('nlc/__init__.py', names)
                self.assertFalse(any(name.startswith(('helper/', 'launcher/', 'examples/', 'flows/'))
                                     or name.endswith(('.dylib', '.so')) for name in names))
            environment = work / 'environment'
            execute([uv, 'venv', '--python', sys.executable, str(environment)])
            python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            execute([uv, 'pip', 'install', '--python', str(python), '--no-deps', str(wheels[0])])
            execute([str(python), '-I', '-c', PORTABLE_IMPORTS])
            executable = environment / ('Scripts/nlc.exe' if os.name == 'nt' else 'bin/nlc')
            result = execute([str(executable), '--help'])
            self.assertIn('Usage:', result.stdout)
            branded = environment / ('Scripts/tacto.exe' if os.name == 'nt' else 'bin/tacto')
            branded_result = execute([str(branded), '--help'])
            self.assertEqual(result.stdout, branded_result.stdout)
            self.assertIn('Tacto', branded_result.stdout)
            result = execute([str(python), '-I', '-m', 'tacto.cli', '--help'])
            self.assertIn('Usage:', result.stdout)
            legacy_module = execute([str(python), '-I', '-m', 'nlc.cli', '--help'])
            self.assertEqual(result.stdout, legacy_module.stdout)
            execute([str(python), '-I', '-c',
                     'import tacto.flow,nlc.flow; assert tacto.flow is nlc.flow; assert tacto.flow.Flow is nlc.flow.Flow'])
            data = work / 'user-data'
            env = {**os.environ, 'XDG_DATA_HOME': str(data)}
            execute([str(python), '-I', '-c',
                     'from tacto.store import FlowStore; '
                     'import os; s=FlowStore(); '
                     'assert s.path == os.path.join(os.environ["XDG_DATA_HOME"], "tacto", "flows"); '
                     'assert s.list() == []'], env=env)
            result = subprocess.run([str(executable), 'eval', '--offline'], cwd=work,
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn('소스 checkout', result.stdout)
            self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
