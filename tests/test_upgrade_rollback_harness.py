"""The upgrade/rollback harness understands Preview 1 and current layouts.

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


def runtime(base, launcher):
    root = Path(base)/f'{launcher}.app/Contents/Resources/runtime'
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
        """What either version's installer leaves behind: both commands, both skills."""
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
        old, new = runtime(self.base, 'nlc'), runtime(self.base, 'tacto')
        self.assertEqual('nlc', harness.launcher_of(old).name)
        self.assertEqual('tacto', harness.launcher_of(new).name)
        with self.assertRaises(ValueError):
            harness.launcher_of(self.base)

    def test_both_commands_and_skills_must_follow_each_stage(self):
        old, new = runtime(self.base, 'nlc'), runtime(self.base, 'tacto')
        self.install(old)
        self.assertTrue(harness.links_target(old, {}, self.home))
        self.assertFalse(harness.links_target(new, {}, self.home))
        self.install(new)
        self.assertTrue(harness.links_target(new, {}, self.home))
        # A stale legacy command alone is caught, not hidden by the other link.
        (self.home/'.local/bin/nlc').unlink()
        (self.home/'.local/bin/nlc').symlink_to(harness.launcher_of(old))
        self.assertFalse(harness.links_target(new, {}, self.home))
        watched = [str(p) for p in harness.links({}, self.home)]
        self.assertEqual(4, len(set(watched)))
        self.assertIn(str(self.home/'.local/bin/nlc'), watched)

    def test_bin_dir_follows_installer_precedence(self):
        self.assertEqual(Path('/a'), harness.bin_dir({'TACTO_BIN_DIR': '/a', 'NLC_BIN_DIR': '/b'}, self.home))
        self.assertEqual(Path('/b'), harness.bin_dir({'NLC_BIN_DIR': '/b'}, self.home))
        self.assertEqual(self.home/'.local/bin', harness.bin_dir({'TACTO_BIN_DIR': ''}, self.home))

    def test_probe_goes_to_the_legacy_store_every_version_reads(self):
        store, refusal = harness.probe_store({}, self.home)
        self.assertIsNone(refusal)
        self.assertEqual(self.home/'.local/share/nlc/flows', store)
        data = self.base/'xdg'
        store, _ = harness.probe_store({'XDG_DATA_HOME': str(data)}, self.home)
        self.assertEqual(data/'nlc/flows', store)
        # A relative XDG path is ignored, as the store itself does.
        store, _ = harness.probe_store({'XDG_DATA_HOME': 'relative'}, self.home)
        self.assertEqual(self.home/'.local/share/nlc/flows', store)

    def test_canonical_store_in_use_refuses_instead_of_creating_two_stores(self):
        canonical = self.home/'.local/share/tacto/flows'
        canonical.mkdir(parents=True)
        store, refusal = harness.probe_store({}, self.home)
        self.assertEqual((self.home/'.local/share/nlc/flows', None), (store, refusal))
        (canonical/'mine.json').write_text('{}')
        self.assertEqual((None, 'canonical_store_in_use'), harness.probe_store({}, self.home))
        self.assertFalse((self.home/'.local/share/nlc').exists())

    def test_manifest_watches_legacy_and_canonical_state(self):
        for relative in ('.local/share/nlc/flows/a.json', '.local/share/tacto/flows/b.json',
                         '.config/nlc/c', '.config/tacto/d'):
            path = self.home/relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        data = self.base/'xdg'
        (data/'nlc/flows').mkdir(parents=True)
        (data/'nlc/flows/e.json').write_text('e')
        seen = harness.manifest({'XDG_DATA_HOME': str(data)}, self.home)
        self.assertEqual(5, len(seen))
        self.assertIn(str(data/'nlc/flows/e.json'), seen)

    def test_layout_check_requires_a_standalone_bundle(self):
        self.assertTrue(harness.standalone_runtime(runtime(self.base, 'tacto')))
        self.assertFalse(harness.standalone_runtime(self.base))

    def test_identity_records_actual_preview1_and_current_artifacts(self):
        from unittest.mock import patch
        import subprocess
        identity_spec=importlib.util.spec_from_file_location('release_identity', ROOT/'examples/release_identity.py')
        provenance=importlib.util.module_from_spec(identity_spec)
        identity_spec.loader.exec_module(provenance)
        for name, helper in [('nlc', 'NLC Helper.app'), ('tacto', 'Tacto Helper.app')]:
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
