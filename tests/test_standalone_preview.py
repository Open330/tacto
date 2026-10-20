"""Preview packaging contracts; no app launch, signing or permissions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
import shutil
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('standalone_builder',ROOT/'setup/build_standalone_preview.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class StandalonePreviewTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('uv'), 'cached offline wheel builder required')
    def test_packaging_rebuilds_metadata_and_removes_retired_owned_console_scripts(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source = folder/'project'; source.mkdir()
            (source/'tacto').mkdir()
            (source/'tacto/__init__.py').write_text('')
            (source/'pyproject.toml').write_text('''[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[project]
name = "tacto-runtime"
version = "0.1.0"
readme = "README.md"
[project.scripts]
tacto = "tacto.cli:main"
[tool.setuptools]
packages = ["tacto"]
''')
            (source/'README.md').write_text('Current Tacto documentation')
            runtime = folder/'runtime'
            site = runtime/'.venv/lib/python3.11/site-packages'; site.mkdir(parents=True)
            stale = site/'tacto_runtime-0.1.0.dist-info'; stale.mkdir()
            (stale/'METADATA').write_text('Name: tacto-runtime\n\nOutdated local documentation')
            (stale/'entry_points.txt').write_text('[console_scripts]\nretired-command = tacto.cli:main\ntacto = tacto.cli:main\n')
            retired = site/'nlc-0.1.0.dist-info'; retired.mkdir()
            (retired/'METADATA').write_text('Name: nlc\n\nRetired project distribution')
            (retired/'entry_points.txt').write_text('[console_scripts]\nnlc = nlc.cli:main\n')
            foreign = site/'foreign_pkg-1.0.dist-info'; foreign.mkdir()
            (foreign/'METADATA').write_text('Name: foreign-pkg\n\nForeign distribution')
            (foreign/'entry_points.txt').write_text('[console_scripts]\nthird-party = foreign.cli:main\n')
            bin_dir = runtime/'.venv/bin'; bin_dir.mkdir()
            for name in ('retired-command', 'nlc', 'tacto', 'third-party'):
                (bin_dir/name).write_text('wrapper')
            work = folder/'work'; work.mkdir()
            with patch.object(builder, 'ROOT', source):
                builder.refresh_distribution_metadata(runtime, work)
            self.assertIn('Current Tacto documentation', (stale/'METADATA').read_text())
            self.assertNotIn('retired-command', (stale/'entry_points.txt').read_text())
            self.assertEqual('tacto\n', (stale/'top_level.txt').read_text())
            self.assertFalse((bin_dir/'retired-command').exists())
            self.assertFalse(retired.exists())
            self.assertFalse((bin_dir/'nlc').exists())
            self.assertFalse((bin_dir/'tacto').exists())
            self.assertEqual('wrapper', (bin_dir/'third-party').read_text())
            self.assertEqual('Name: foreign-pkg\n\nForeign distribution', (foreign/'METADATA').read_text())

    def test_copy_excludes_editable_paths_caches_and_direct_url_metadata(self):
        names=['__editable__.tacto.pth','__editable___finder.py','x.pth','direct_url.json','__pycache__','module.py','license.txt']
        kept=set(names)-set(builder.ignored('/owned',names))
        self.assertEqual({'module.py','license.txt'},kept)

    def test_build_metadata_is_sanitized_before_bytecode_is_generated(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder)/'runtime';lib=runtime/'.venv/lib/python3.11';lib.mkdir(parents=True)
            metadata=lib/'_sysconfigdata__darwin_darwin.py'
            metadata.write_text("build_time_vars={'prefix':'/private-builder/python','flag':'keep'}")
            untouched=lib/'ordinary.py';untouched.write_text("value='/private-builder/python'")
            self.assertEqual(1,builder.normalize_python_metadata(runtime,Path('/private-builder/python')))
            data={};exec(metadata.read_text(),data)
            self.assertEqual({'prefix':'/opt/tacto-runtime','flag':'keep'},data['build_time_vars'])
            self.assertIn('/private-builder/python',untouched.read_text())
            with self.assertRaisesRegex(RuntimeError,'private_build_path_in_bundle'):
                builder.require_private_paths_absent(runtime,('/private-builder',))
            untouched.unlink()
            self.assertEqual(1,builder.require_private_paths_absent(runtime,('/private-builder',)))

    @unittest.skipUnless(sys.platform=='darwin','macOS Swift GUI compiler required')
    def test_actual_gui_binary_has_no_local_home_or_checkout_path(self):
        with tempfile.TemporaryDirectory() as folder:
            work=Path(folder);binary=work/'TactoSetup'
            builder.compile_setup(binary,work)
            data=binary.read_bytes()
            self.assertNotIn(str(Path.home()).encode(),data)
            self.assertNotIn(str(ROOT).encode(),data)

    def test_import_origin_requires_existing_files_inside_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'runtime';root.mkdir();module=root/'module.py';module.write_text('')
            helper=root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
            helper.parent.mkdir(parents=True);helper.write_text('native fixture')
            def receipt(tacto=module, native=helper):
                return {'tacto':str(tacto),'prefix':str(root),'helper':str(native)}
            self.assertTrue(builder.bundled_import_valid(receipt(),root))
            outside=Path(folder)/'outside.py';outside.write_text('')
            self.assertFalse(builder.bundled_import_valid(receipt(outside),root))
            self.assertFalse(builder.bundled_import_valid(receipt(native=outside),root))
            self.assertFalse(builder.bundled_import_valid({'tacto':str(module),'prefix':str(root)},root))
            module.unlink();module.symlink_to(outside)
            self.assertFalse(builder.bundled_import_valid(receipt(),root))
            self.assertFalse(builder.bundled_import_valid(receipt(root/'missing'),root))

    def test_existing_app_or_symlink_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'owned.app';target.mkdir();(target/'sentinel').write_text('keep')
            with patch.object(builder.platform,'system',return_value='Darwin'),self.assertRaises(FileExistsError):builder.build(target,'unused')
            self.assertEqual('keep',(target/'sentinel').read_text())
            target=Path(folder)/'link.app';target.symlink_to(Path(folder)/'absent')
            with patch.object(builder.platform,'system',return_value='Darwin'),self.assertRaises(FileExistsError):builder.build(target,'unused')
            self.assertTrue(target.is_symlink())

    @unittest.skipUnless(sys.platform=='darwin','macOS Swift Foundation required')
    def test_swift_root_contract_is_closed_and_rejects_bundle_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);resources=folder/'Resources';resources.mkdir()
            outside=folder/'outside';outside.mkdir();(resources/'runtime').symlink_to(outside,target_is_directory=True)
            script=folder/'main.swift'
            script.write_text('''import Foundation
let base=URL(fileURLWithPath:CommandLine.arguments[1])
assert(setupRuntimeRoot(["root":"/owned/checkout"], resources:base)?.path == "/owned/checkout")
assert(setupRuntimeRoot(["root":"relative"], resources:base) == nil)
assert(setupRuntimeRoot(["bundledRoot":"../../outside"], resources:base) == nil)
assert(setupRuntimeRoot(["bundledRoot":"runtime","root":"/other"], resources:base) == nil)
assert(setupRuntimeRoot(["bundledRoot":"runtime"], resources:base) == nil)
try FileManager.default.removeItem(at:base.appendingPathComponent("runtime"))
try FileManager.default.createDirectory(at:base.appendingPathComponent("runtime"),withIntermediateDirectories:false)
assert(setupRuntimeRoot(["bundledRoot":"runtime"],resources:base)?.path == base.appendingPathComponent("runtime").path)
''')
            executable=folder/'check'
            subprocess.run(['swiftc',str(ROOT/'setup/PermissionState.swift'),str(script),'-o',str(executable)],check=True,capture_output=True,timeout=60)
            subprocess.run([str(executable),str(resources)],check=True,capture_output=True,timeout=5)
