import hashlib
import importlib.util
from pathlib import Path
import unittest
import os
import plistlib
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('save_writer',Path(__file__).resolve().parents[1]/'examples/probes/signed_save_writer.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CallbackTests(unittest.TestCase):
    def setUp(self):
        self.row={'callback':'written','nonce':'fresh','path':'/owned/new.rtf',
                  'sha256':hashlib.sha256(b'body').hexdigest(),'response':1}

    def verified(self,rows):
        return module.callback_verified(rows,'fresh','/owned/new.rtf','body')

    def test_one_exact_completion(self):
        self.assertTrue(self.verified([{'callback':'ready'},self.row]))

    def test_other_destination_content_nonce_or_response(self):
        for field in ('nonce','path','sha256','response'):
            with self.subTest(field=field):
                self.assertFalse(self.verified([dict(self.row,**{field:'wrong'})]))

    def test_duplicate_or_conflicting_completion(self):
        self.assertFalse(self.verified([self.row,self.row]))
        self.assertFalse(self.verified([self.row,{'callback':'refused'}]))
        self.assertFalse(self.verified([{'callback':'ready'}]))

    def test_boolean_response_is_not_a_native_completion_code(self):
        self.assertFalse(self.verified([dict(self.row,response=True)]))

    def test_empty_owned_preferences_removed(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'owned.plist'
            path.write_bytes(plistlib.dumps({}))
            module.remove_empty_preferences(path)
            self.assertFalse(path.exists())

    def test_populated_preferences_preserved(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'owned.plist'
            path.write_bytes(plistlib.dumps({'keep':'value'}))
            with self.assertRaises(RuntimeError):module.remove_empty_preferences(path)
            self.assertEqual(plistlib.loads(path.read_bytes()),{'keep':'value'})

    def test_linked_preferences_preserved(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'owned.plist';alias=Path(work)/'alias.plist'
            path.write_bytes(plistlib.dumps({}));os.symlink(path,alias)
            with self.assertRaises(OSError):module.remove_empty_preferences(alias)
            alias.unlink();os.link(path,alias)
            with self.assertRaises(RuntimeError):module.remove_empty_preferences(path)
            self.assertTrue(path.exists())

    def test_preferences_status_distinguishes_unknown_errors(self):
        cases=[(0,b'{\n}',b'',False), (0,b'{ key = value; }',b'',True),
               (1,b'',f"Error: Domain '{module.TARGET}' not found.".encode(),False),
               (1,b'',b'does not exist',False)]
        for code,stdout,stderr,expected in cases:
            with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=code,stdout=stdout,stderr=stderr)):
                self.assertEqual(module.preferences_exist(),expected)
        with patch.object(module.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=b'',stderr=b'permission denied')):
            with self.assertRaises(RuntimeError):module.preferences_exist()
