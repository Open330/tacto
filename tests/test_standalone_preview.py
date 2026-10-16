"""Preview packaging contracts; no app launch, signing or permissions."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('standalone_builder',ROOT/'setup/build_standalone_preview.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class StandalonePreviewTests(unittest.TestCase):
    def test_copy_excludes_editable_paths_caches_and_direct_url_metadata(self):
        names=['__editable__.nlc.pth','__editable___finder.py','x.pth','direct_url.json','__pycache__','module.py','license.txt']
        kept=set(names)-set(builder.ignored('/owned',names))
        self.assertEqual({'module.py','license.txt'},kept)

    def test_import_origin_requires_existing_files_inside_bundle(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'runtime';root.mkdir();module=root/'module.py';module.write_text('')
            self.assertTrue(builder.bundled_import_valid({'nlc':str(module),'prefix':str(root)},root))
            outside=Path(folder)/'outside.py';outside.write_text('')
            self.assertFalse(builder.bundled_import_valid({'nlc':str(outside),'prefix':str(root)},root))
            module.unlink();module.symlink_to(outside)
            self.assertFalse(builder.bundled_import_valid({'nlc':str(module),'prefix':str(root)},root))
            self.assertFalse(builder.bundled_import_valid({'nlc':str(root/'missing'),'prefix':str(root)},root))

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
