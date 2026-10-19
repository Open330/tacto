"""Only bounded private owned worker records enter the native fixture path."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from examples.probes.signed_native_journal_worker import private_json,write_json,configuration


class WorkerRecords(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        fixture_home=Path(temp.name).resolve()
        self.caches=fixture_home/'Library/Caches';self.caches.mkdir(parents=True)
        self.work=self.caches/'nlc-save-writer-owned';self.work.mkdir(mode=0o700)
        self.path=self.work/'worker-config.json'
        self.cfg={'work':str(self.work),'nonce':'a'*32,'pid':42,'start':123.0,
                  'callback_dev':1,'callback_ino':2}
        self.enterContext(patch('examples.probes.signed_native_journal_worker.Path.home',return_value=fixture_home))

    def test_private_valid_configuration_and_exclusive_output(self):
        write_json(self.path,self.cfg)
        self.assertEqual((self.cfg,self.work),configuration(self.path))
        self.assertEqual(0o600,self.path.stat().st_mode&0o777)
        with self.assertRaises(FileExistsError):write_json(self.path,{'replacement':True})
        self.assertEqual(self.cfg,private_json(self.path))

    def test_alias_shared_file_nonprivate_and_oversized_records_refused(self):
        write_json(self.path,self.cfg)
        alias=self.work/'alias';alias.symlink_to(self.path)
        with self.assertRaises(OSError):private_json(alias)
        alias.unlink();alias.hardlink_to(self.path)
        with self.assertRaises(ValueError):private_json(self.path)
        alias.unlink();self.path.chmod(0o644)
        with self.assertRaises(ValueError):private_json(self.path)
        self.path.chmod(0o600);self.path.write_bytes(b' '*65537)
        with self.assertRaises(ValueError):private_json(self.path)

    def test_wrong_location_unknown_fields_and_invalid_identity_refused(self):
        for changes in ({'nonce':'bad'},{'pid':True},{'start':float('nan')},
                        {'callback_ino':False},{'extra':1},{'work':str(self.caches/'foreign')}):
            with self.subTest(changes=changes):
                write_json(self.path,{**self.cfg,**changes})
                with self.assertRaises((ValueError,FileNotFoundError)):configuration(self.path)
                self.path.unlink()
        write_json(self.path,self.cfg);other=self.work/'other.json';self.path.rename(other)
        with self.assertRaises(ValueError):configuration(other)
