"""Read-only native foreground observation and exact public C ABI contracts."""
import ctypes
import os
import sys
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tacto.native_frontmost import (
    FrontmostUnavailable, ProcessSerialNumber, frontmost_pid,
)


class Function:
    def __init__(self, callback):
        self.callback = callback

    def __call__(self, *args):
        return self.callback(*args)


class Library:
    def __init__(self, *, front_status=0, pid_status=0, pid=1234,
                 serial=(0, 55), after_serial=None, write_pid=True):
        self.calls = []
        def front(pointer):
            self.calls.append('front')
            value = ctypes.cast(pointer, ctypes.POINTER(ProcessSerialNumber)).contents
            current = after_serial if after_serial is not None and self.calls.count('front') > 1 else serial
            value.highLongOfPSN, value.lowLongOfPSN = current
            return front_status
        def get_pid(serial_pointer, pid_pointer):
            self.calls.append('pid')
            value = ctypes.cast(serial_pointer, ctypes.POINTER(ProcessSerialNumber)).contents
            self.pid_input = (value.highLongOfPSN, value.lowLongOfPSN)
            if write_pid:
                ctypes.cast(pid_pointer, ctypes.POINTER(ctypes.c_int32)).contents.value = pid
            return pid_status
        self.GetFrontProcess = Function(front)
        self.GetProcessPID = Function(get_pid)


class FrontmostTests(unittest.TestCase):
    def run_native(self, library):
        with patch('tacto.native_frontmost._load_library', return_value=library):
            return frontmost_pid()

    def test_exact_abi_and_repeated_foreground_read(self):
        library = Library()
        self.assertEqual(self.run_native(library), 1234)
        self.assertEqual(library.calls, ['front', 'pid', 'front'])
        self.assertEqual(library.pid_input, (0, 55))
        self.assertEqual(ctypes.sizeof(ProcessSerialNumber), 8)
        self.assertEqual(ProcessSerialNumber.highLongOfPSN.offset, 0)
        self.assertEqual(ProcessSerialNumber.lowLongOfPSN.offset, 4)
        self.assertEqual(library.GetFrontProcess.argtypes,
                         [ctypes.POINTER(ProcessSerialNumber)])
        self.assertIs(library.GetFrontProcess.restype, ctypes.c_int16)
        self.assertEqual(library.GetProcessPID.argtypes,
                         [ctypes.POINTER(ProcessSerialNumber), ctypes.POINTER(ctypes.c_int32)])
        self.assertIs(library.GetProcessPID.restype, ctypes.c_int32)

    def test_api_failure_and_malformed_status_rejected(self):
        for status in (-600, 1, None, False, '0'):
            for which in ('front_status', 'pid_status'):
                with self.subTest(which=which, status=status), self.assertRaises(FrontmostUnavailable):
                    self.run_native(Library(**{which: status}))

    def test_empty_serial_invalid_or_unset_pid_rejected(self):
        cases = ({'serial': (0, 0)}, {'pid': 0}, {'pid': -1},
                 {'pid': -(2**31)}, {'write_pid': False})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(FrontmostUnavailable):
                self.run_native(Library(**kwargs))

    def test_observed_switch_rejected(self):
        with self.assertRaisesRegex(FrontmostUnavailable, 'changed during observation'):
            self.run_native(Library(after_serial=(0, 99)))

    def test_missing_api_and_loader_failure_are_explicit(self):
        with self.assertRaises(FrontmostUnavailable):
            self.run_native(object())
        with patch('tacto.native_frontmost.ctypes.CDLL', side_effect=OSError('absent')):
            with self.assertRaises(FrontmostUnavailable):
                frontmost_pid()
        with patch('tacto.native_frontmost.sys.platform', 'linux'):
            with self.assertRaises(FrontmostUnavailable):
                frontmost_pid()

    @unittest.skipUnless(sys.platform == 'darwin', 'requires macOS GUI host')
    def test_platform_native_read_only(self):
        # No activation, focus changes, UI events, or NSWorkspace consultation.
        pid = frontmost_pid()
        self.assertIs(type(pid), int)
        self.assertGreater(pid, 0)
        try:
            os.kill(pid, 0)
        except PermissionError:
            # EPERM still proves the PID exists; a foreground system process
            # need not permit this user to signal it. No signal is sent.
            pass


@unittest.skipUnless(sys.platform == 'darwin', 'native driver requires macOS')
class DriverForegroundTests(unittest.TestCase):
    def test_preparation_rechecks_foreground_after_settable_read_and_each_write(self):
        from types import SimpleNamespace
        from tacto.driver import Driver, NotStarted
        driver = Driver.__new__(Driver)
        obs = SimpleNamespace(pid=123)
        for results, count in (([True, False], 0), ([True, True, False], 1)):
            with self.subTest(results=results), \
                 patch.object(Driver, '_frontmost', side_effect=results), \
                 patch('tacto.driver._attr', return_value=False), \
                 patch('tacto.driver.AXUIElementIsAttributeSettable', return_value=(0, True)), \
                 patch('tacto.driver.AXUIElementSetAttributeValue', return_value=0) as write, \
                 patch('tacto.driver._aborted_now', return_value=False):
                with self.assertRaises(RuntimeError) as error:
                    driver._prepare_window(obs, object())
                self.assertEqual(isinstance(error.exception, NotStarted), count == 0)
                self.assertEqual(write.call_count, count)

    def test_failed_window_write_never_claims_not_started(self):
        from types import SimpleNamespace
        from tacto.driver import Driver, NotStarted
        driver = Driver.__new__(Driver)
        with patch.object(Driver, '_frontmost', return_value=True), \
             patch('tacto.driver._attr', return_value=False), \
             patch('tacto.driver.AXUIElementIsAttributeSettable', return_value=(0, True)), \
             patch('tacto.driver.AXUIElementSetAttributeValue', return_value=-25204) as write, \
             patch('tacto.driver._aborted_now', return_value=False):
            with self.assertRaises(RuntimeError) as error:
                driver._prepare_window(SimpleNamespace(pid=123), object())
            self.assertNotIsInstance(error.exception, NotStarted)
            self.assertEqual(write.call_count, 1)

    def test_cached_workspace_cannot_authorize_a_write(self):
        from tacto.driver import Driver
        with patch('tacto.native_frontmost.frontmost_pid', return_value=123):
            self.assertTrue(Driver._frontmost(123))
            self.assertFalse(Driver._frontmost(456))
        with patch('tacto.native_frontmost.frontmost_pid', side_effect=FrontmostUnavailable('missing')):
            self.assertFalse(Driver._frontmost(123))
        for pid in (True, None, 0, -1, '123'):
            self.assertFalse(Driver._frontmost(pid))

    @unittest.skipUnless(shutil.which('xcrun'), 'Swift compiler required')
    def test_swift_public_abi_observer_without_input(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'main.swift'
            binary = Path(directory)/'observe'
            source.write_text('import Darwin\n'
                'guard let pid = nativeFrontmostProcessPID(), pid > 0 else { fatalError("no foreground") }\n'
                'print(pid)\n')
            build = subprocess.run(['xcrun','swiftc',str(source),
                    str(root/'helper/FrontmostProcess.swift'),'-o',str(binary)],
                    capture_output=True,text=True,timeout=30)
            self.assertEqual(build.returncode,0,build.stderr)
            run = subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
            self.assertGreater(int(run.stdout.strip()),0)


if __name__ == '__main__':
    unittest.main()
