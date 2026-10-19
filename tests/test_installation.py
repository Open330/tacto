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
import tacto.document_attachments, tacto.document_packages
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
            (root / 'pyproject.toml').write_text('[project]\nname="nlc"\n')
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
        with tempfile.TemporaryDirectory(prefix='nlc-wheel-test-') as directory:
            work = Path(directory)
            source = work / 'source'
            source.mkdir()
            for name in ('pyproject.toml', 'README.md'):
                shutil.copy2(ROOT / name, source / name)
            for package in ('tacto','nlc'):
                shutil.copytree(ROOT / package, source / package,
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
                for name in ('provider_io.py','rtf_receipt.py','process_responsibility.py','authoring.py','codex_result.py','claude_result.py',
                             'document_attachments.py','document_packages.py','native_document.py'):
                    self.assertEqual((ROOT/'tacto'/name).read_bytes(),wheel.read('tacto/'+name))
                self.assertFalse(any(name.startswith(('helper/', 'launcher/', 'examples/', 'flows/'))
                                     or name.endswith(('.dylib', '.so')) for name in names))
            environment = work / 'environment'
            execute([uv, 'venv', '--python', sys.executable, str(environment)])
            python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            execute([uv, 'pip', 'install', '--python', str(python), '--no-deps', str(wheels[0])])
            execute([str(python), '-I', '-c', PORTABLE_IMPORTS])
            executable = environment / ('Scripts/tacto.exe' if os.name == 'nt' else 'bin/nlc')
            result = execute([str(executable), '--help'])
            self.assertIn('Usage:', result.stdout)
            result = execute([str(python), '-I', '-m', 'tacto.cli', '--help'])
            self.assertIn('Usage:', result.stdout)
            if os.name=='posix':
                # Exercise installed stdlib-only provider cleanup, without model,
                # network, native GUI, source-tree imports or caller profile edits.
                owned=work/'owned-provider';owned.mkdir(mode=0o700)
                script=r'''
import os,pathlib,sys,time
from tacto.authoring import _run_provider
import tacto.provider_io
assert pathlib.Path(tacto.provider_io.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
base=pathlib.Path(sys.argv[1]);marker=base/'survived'
child='import pathlib,time;time.sleep(.5);pathlib.Path('+repr(str(marker))+').write_text("survived")'
program='import os,pathlib,subprocess,sys;subprocess.Popen([sys.executable,"-c",'+repr(child)+'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);print("owned answer")'
result=_run_provider([sys.executable,'-c',program],3)
assert result.returncode==0 and result.stdout=='owned answer\n'
time.sleep(.7)
assert not marker.exists(),'installed wheel left an owned group worker running'
if sys.platform=='darwin':
    base=base.resolve()
    from tacto.rtf_receipt import FreshRTFReceipt
    import tacto.rtf_receipt
    assert pathlib.Path(tacto.rtf_receipt.__file__).resolve().is_relative_to(pathlib.Path(sys.prefix).resolve())
    receipt=FreshRTFReceipt(base,'proof.rtf','Hello',document_path=lambda:str(base/'proof.rtf'))
    receipt._decode_deadline=receipt._clock()+3
    assert receipt._decode(b'{\\rtf1 Hello}')=='Hello'
    # Change only this isolated test interpreter's disposition. It must refuse
    # the decoder before creating another child, without changing host grants.
    import signal
    from unittest.mock import patch
    signal.signal(signal.SIGCHLD,signal.SIG_IGN)
    receipt._decode_deadline=receipt._clock()+3
    with patch('tacto.rtf_receipt.subprocess.Popen') as spawn:
        try:receipt._decode(b'{\\rtf1 Hello}')
        except RuntimeError:pass
        else:raise AssertionError('installed decoder accepted unwaitable child disposition')
        spawn.assert_not_called()
'''
                execute([str(python),'-I','-c',script,str(owned)])
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
