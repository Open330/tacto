import ctypes
import os
import sys
import time
import unittest
from unittest.mock import patch

from nlc import native_process_identity as identity


class ProcessIdentityTests(unittest.TestCase):
    def test_invalid_pid_never_loads_library(self):
        with patch.object(identity, '_load_pidinfo') as load:
            for pid in (True, 0, -1, '1', 1.0, 2**31):
                with self.assertRaises(identity.ProcessIdentityError):
                    identity.process_start_identity(pid)
            load.assert_not_called()

    def test_full_read_pid_and_microsecond_validation(self):
        def fake(pid, flavor, arg, pointer, size):
            self.assertEqual((flavor,arg,size), (3,0,136))
            info=ctypes.cast(pointer,ctypes.POINTER(identity._ProcBSDInfo)).contents
            info.pbi_pid=values['pid']; info.pbi_start_tvsec=values['sec']
            info.pbi_start_tvusec=values['usec']
            return values['count']
        values=dict(pid=9,sec=123,usec=456789,count=136)
        with patch.object(identity,'_load_pidinfo',return_value=fake):
            self.assertEqual(identity.process_start_identity(9),123.456789)
            for change in (dict(count=0),dict(count=135),dict(pid=10),dict(sec=0),dict(usec=1000000)):
                original=values.copy(); values.update(change)
                with self.subTest(change=change),self.assertRaises(identity.ProcessIdentityError):
                    identity.process_start_identity(9)
                values=original

    @unittest.skipUnless(sys.platform=='darwin','libproc requires macOS')
    def test_actual_own_process_stable_and_nonexistent_pid_refused(self):
        first=identity.process_start_identity(os.getpid())
        self.assertEqual(first,identity.process_start_identity(os.getpid()))
        self.assertGreater(first,0)
        self.assertLessEqual(first,time.time())
        with self.assertRaises(identity.ProcessIdentityError):
            identity.process_start_identity(0x7FFFFFFF)


if __name__=='__main__': unittest.main()
