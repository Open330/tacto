"""Immutable packaged recipes and writable user overrides, without UI access."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from tacto import store
from tacto.flow import Flow


class BuiltinFlowStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "installed/tacto"
        self.builtins = self.package / "builtin_flows"
        self.builtins.mkdir(parents=True)
        self.original = Flow("sample", "builtin-target").to_json()
        (self.builtins / "sample.json").write_text(self.original)
        self.user = self.root / "data/tacto/flows"
        self.file_patch = patch.object(store, "__file__", str(self.package / "store.py"))
        self.env_patch = patch.dict(os.environ, {"XDG_DATA_HOME": str(self.root / "data")})
        self.file_patch.start()
        self.env_patch.start()
        self.addCleanup(self.file_patch.stop)
        self.addCleanup(self.env_patch.stop)

    def test_relocated_assets_read_and_save_only_to_user_directory(self):
        relocated = self.root / "moved/tacto"
        shutil.move(self.package, relocated)
        with patch.object(store, "__file__", str(relocated / "store.py")):
            library = store.FlowStore()
            self.assertEqual(library.load("sample").target, "builtin-target")
            self.assertTrue(library.has("sample"))
            self.assertEqual(library.list(), ["sample"])
            saved = Path(library.save(Flow("sample", "user-target")))
            self.assertEqual(saved, self.user / "sample.json")
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertEqual(library.load("sample").target, "user-target")
            self.assertEqual(library.list(), ["sample"])
            self.assertEqual((relocated / "builtin_flows/sample.json").read_text(), self.original)

    def test_explicit_store_is_isolated_unless_builtin_path_is_supplied(self):
        isolated = store.FlowStore(self.root / "custom")
        self.assertFalse(isolated.has("sample"))
        self.assertEqual(isolated.list(), [])
        self.assertFalse(self.user.exists())
        opted_in = store.FlowStore(self.root / "custom", builtin_path=self.builtins)
        self.assertEqual(opted_in.load("sample").target, "builtin-target")
        self.assertEqual(opted_in.list(), ["sample"])

    def test_corrupt_user_override_does_not_fall_back_and_can_be_reauthored(self):
        library = store.FlowStore()
        override = self.user / "sample.json"
        override.write_text("broken")
        with self.assertRaises(store.FlowStore.Corrupt):
            library.load("sample")
        self.assertFalse(library.has("sample"))
        library.save(Flow("sample", "repaired-target"))
        self.assertEqual(library.load("sample").target, "repaired-target")
        self.assertEqual((self.builtins / "sample.json").read_text(), self.original)

    def test_legacy_user_override_requires_migration_and_is_not_overwritten(self):
        library = store.FlowStore()
        legacy = '{"schema": 1}'
        override = self.user / "sample.json"
        override.write_text(legacy)
        for action in (lambda: library.load("sample"), lambda: library.has("sample"),
                       lambda: library.save(Flow("sample", "replacement"))):
            with self.assertRaises(store.FlowStore.MigrationRequired):
                action()
        self.assertEqual(override.read_text(), legacy)

    def test_dangling_user_override_does_not_fall_back(self):
        library = store.FlowStore()
        (self.user / "sample.json").symlink_to(self.root / "missing.json")
        with self.assertRaises(FileNotFoundError):
            library.load("sample")
        self.assertFalse(library.has("sample"))

    def test_unusable_builtins_fail_without_writing_assets(self):
        library = store.FlowStore()
        asset = self.builtins / "sample.json"
        for content, error in (("broken", store.FlowStore.Corrupt),
                               ('{"schema": 1}', store.FlowStore.MigrationRequired)):
            with self.subTest(content=content):
                asset.write_text(content)
                with self.assertRaises(error):
                    library.load("sample")
                self.assertEqual(asset.read_text(), content)
                self.assertEqual(list(self.user.iterdir()), [])

    def test_missing_builtin_directory_is_not_created_and_names_stay_sanitized(self):
        missing = self.root / "not-packaged"
        library = store.FlowStore(self.root / "custom", builtin_path=missing)
        self.assertFalse(library.has("sample"))
        self.assertEqual(library.list(), [])
        library.save(Flow("../sample", "user-target"))
        self.assertEqual(library.load("../sample").target, "user-target")
        self.assertEqual(library.list(), ["sample"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
