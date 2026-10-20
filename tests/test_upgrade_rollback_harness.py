"""The upgrade/rollback harness understands current Tacto layouts.

Only pure helpers are exercised, on temporary homes; nothing installs, links
real commands or touches the person's stores.
"""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('validate_upgrade_rollback',
                                              ROOT/'examples/validate_upgrade_rollback.py')
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def runtime(base, launcher, label=None):
    root = Path(base)/f'{label or launcher}.app/Contents/Resources/runtime'
    (root/'launcher').mkdir(parents=True)
    (root/'launcher'/launcher).write_text('fixture')
    (root/'skill').mkdir()
    return root


class UpgradeRollbackHarnessTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.base = Path(self.folder.name)
        self.home = self.base/'home'
        self.home.mkdir()

    def tearDown(self):
        self.folder.cleanup()

    def install(self, root, environ=None):
        """What either version's installer leaves behind: the command and both skills."""
        b = harness.bin_dir(environ or {}, self.home)
        b.mkdir(parents=True, exist_ok=True)
        for name in harness.COMMANDS:
            link = b/name
            if link.is_symlink():
                link.unlink()
            link.symlink_to(harness.launcher_of(root))
        for agent in harness.AGENTS:
            skill = self.home/agent/'skills/tacto'
            skill.parent.mkdir(parents=True, exist_ok=True)
            if skill.is_symlink():
                skill.unlink()
            skill.symlink_to(root/'skill')

    def test_each_runtime_uses_its_own_launcher_name(self):
        old, new = runtime(self.base, 'tacto', 'old'), runtime(self.base, 'tacto', 'new')
        self.assertEqual('tacto', harness.launcher_of(old).name)
        self.assertEqual('tacto', harness.launcher_of(new).name)
        with self.assertRaises(ValueError):
            harness.launcher_of(self.base)

    def test_command_and_skills_must_follow_each_stage(self):
        old, new = runtime(self.base, 'tacto', 'old'), runtime(self.base, 'tacto', 'new')
        self.install(old)
        self.assertTrue(harness.links_target(old, {}, self.home))
        self.assertFalse(harness.links_target(new, {}, self.home))
        self.install(new)
        self.assertTrue(harness.links_target(new, {}, self.home))
        # A stale command is caught even when both skills are current.
        (self.home/'.local/bin/tacto').unlink()
        (self.home/'.local/bin/tacto').symlink_to(harness.launcher_of(old))
        self.assertFalse(harness.links_target(new, {}, self.home))
        watched = [str(p) for p in harness.links({}, self.home)]
        self.assertEqual(3, len(set(watched)))
        self.assertIn(str(self.home/'.local/bin/tacto'), watched)

    def test_bin_dir_follows_installer_precedence(self):
        self.assertEqual(Path('/a'), harness.bin_dir({'TACTO_BIN_DIR': '/a'}, self.home))
        self.assertEqual(Path('/b'), harness.bin_dir({'TACTO_BIN_DIR': '/b'}, self.home))
        self.assertEqual(self.home/'.local/bin', harness.bin_dir({'TACTO_BIN_DIR': ''}, self.home))

    def test_probe_goes_to_the_canonical_store(self):
        store, refusal = harness.probe_store({}, self.home)
        self.assertIsNone(refusal)
        self.assertEqual(self.home/'.local/share/tacto/flows', store)
        data = self.base/'xdg'
        store, _ = harness.probe_store({'XDG_DATA_HOME': str(data)}, self.home)
        self.assertEqual(data/'tacto/flows', store)
        # A relative XDG path is ignored, as the store itself does.
        store, _ = harness.probe_store({'XDG_DATA_HOME': 'relative'}, self.home)
        self.assertEqual(self.home/'.local/share/tacto/flows', store)

    def test_existing_canonical_store_remains_usable(self):
        canonical = self.home/'.local/share/tacto/flows'
        canonical.mkdir(parents=True)
        store, refusal = harness.probe_store({}, self.home)
        self.assertEqual((self.home/'.local/share/tacto/flows', None), (store, refusal))
        (canonical/'mine.json').write_text('{}')
        self.assertEqual((canonical, None), harness.probe_store({}, self.home))
        self.assertEqual('{}', (canonical/'mine.json').read_text())

    def test_manifest_watches_canonical_and_xdg_state(self):
        for relative in ('.local/share/tacto/flows/a.json', '.local/share/tacto/flows/b.json',
                         '.config/tacto/c', '.config/tacto/d'):
            path = self.home/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        data = self.base/'xdg'
        (data/'tacto/flows').mkdir(parents=True)
        (data/'tacto/flows/e.json').write_text('e')
        seen = harness.manifest({'XDG_DATA_HOME': str(data)}, self.home)
        self.assertEqual(5, len(seen))
        self.assertIn(str(data/'tacto/flows/e.json'), seen)

    def test_layout_check_requires_a_standalone_bundle(self):
        self.assertTrue(harness.standalone_runtime(runtime(self.base, 'tacto')))
        self.assertFalse(harness.standalone_runtime(self.base))

    def test_identity_records_actual_current_artifacts(self):
        from unittest.mock import patch
        import subprocess
        identity_spec=importlib.util.spec_from_file_location('release_identity', ROOT/'examples/release_identity.py')
        provenance=importlib.util.module_from_spec(identity_spec)
        identity_spec.loader.exec_module(provenance)
        for name, helper in [('tacto', 'Tacto Helper.app')]:
            root=runtime(self.base,name).resolve()
            package=root/'.venv/lib/python3.11/site-packages'/name
            package.mkdir(parents=True)
            (package/'__init__.py').write_text('')
            (package/'flow.py').write_text('owned implementation')
            (root/'helper'/helper).mkdir(parents=True)
            seen=[]
            def call(argv, **kwargs):
                seen.append(argv)
                return subprocess.CompletedProcess(argv,0,'','CDHash=fixture\n')
            with patch.object(provenance.subprocess, 'run', side_effect=call):
                result=provenance.identity(root/'launcher'/name)
            self.assertIn('flow.py',result['module_sha256'])
            targets=[str(argv[-1]) for argv in seen if '-dv' in argv]
            self.assertIn(str(root/'launcher'/name),targets)
            self.assertIn(str(root/'helper'/helper),targets)


if __name__ == '__main__':
    unittest.main()
