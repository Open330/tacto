"""Record-bound relocation does not adopt unknown broken symlinks."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from tacto.install_record import authorize,write_record

class InstallRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name).resolve();self.root=self.folder/'runtime';self.root.mkdir()
        (self.root/'launcher').mkdir();(self.root/'launcher/tacto').write_bytes(b'owned executable')
        (self.root/'skill').mkdir();(self.root/'skill/SKILL.md').write_text('name: nlc')
        self.command=self.folder/'command';self.skill=self.folder/'skill'
        self.command.symlink_to(self.root/'launcher/tacto');self.skill.symlink_to(self.root/'skill')
        self.record=self.folder/'record.json'
        write_record(self.record,self.root,self.command,[self.skill])

    def moved(self):
        root=self.folder/'moved';self.root.rename(root);return root

    def test_same_runtime_move_authorizes_only_recorded_links(self):
        root=self.moved()
        self.assertFalse(self.command.exists());self.assertFalse(self.skill.exists())
        self.assertTrue(authorize(self.record,root,self.command,'command'))
        self.assertTrue(authorize(self.record,root,self.skill,'skill'))
        unknown=self.folder/'foreign';unknown.symlink_to('/missing/launcher/tacto')
        self.assertFalse(authorize(self.record,root,unknown,'command'))

    def test_changed_target_or_runtime_bytes_cannot_reuse_record(self):
        root=self.moved();self.command.unlink();self.command.symlink_to('/missing/foreign')
        self.assertFalse(authorize(self.record,root,self.command,'command'))
        (root/'skill/SKILL.md').write_text('changed')
        self.assertFalse(authorize(self.record,root,self.skill,'skill'))

    def test_wrong_kind_corrupt_insecure_or_linked_record_refuses(self):
        root=self.moved()
        self.assertFalse(authorize(self.record,root,self.skill,'command'))
        self.record.chmod(0o644)
        self.assertFalse(authorize(self.record,root,self.command,'command'))
        self.record.chmod(0o600);self.record.write_text('{malformed')
        self.assertFalse(authorize(self.record,root,self.command,'command'))
        linked=self.folder/'linked';linked.symlink_to(self.record)
        self.assertFalse(authorize(linked,root,self.command,'command'))

    def test_copied_runtime_and_record_overwrite_are_refused(self):
        import shutil
        copied=self.folder/'copied';shutil.copytree(self.root,copied)
        self.assertFalse(authorize(self.record,copied,self.command,'command'))
        with self.assertRaises(FileExistsError):write_record(self.record,self.root,self.command,[self.skill])
        self.assertEqual(0o600,self.record.stat().st_mode&0o777)
