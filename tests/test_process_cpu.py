"""Clock-domain and PID/start bindings for local CPU; no GUI or paid requests."""
from dataclasses import replace
import os
import resource
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tacto.process_cpu import ProcessCpuSample,ProcessCpuError,cpu_delta_ns,sample_process_cpu,verify_cpu_clock


class ProcessCpuTests(unittest.TestCase):
    def test_wrong_clock_domain_cannot_be_published_as_verified_cpu(self):
        a=ProcessCpuSample(7,100,0,0,125,3)
        with patch('tacto.process_cpu.sample_process_cpu',side_effect=[a,replace(a,user_raw=1)]), \
                patch('resource.getrusage',side_effect=[SimpleNamespace(ru_utime=0,ru_stime=0),
                                                      SimpleNamespace(ru_utime=.05,ru_stime=0)]), \
                patch('tacto.process_cpu.time.process_time',side_effect=[0,1]):
            with self.assertRaisesRegex(ProcessCpuError,'cpu_clock_unverified'):verify_cpu_clock()

    def test_arm_timebase_and_identity_timebase_convert_without_float_loss(self):
        a=ProcessCpuSample(7,100,10,20,125,3)
        self.assertEqual(250000000,cpu_delta_ns(a,replace(a,user_raw=6000010)))
        b=replace(a,timebase_numer=1,timebase_denom=1)
        self.assertEqual(6000000,cpu_delta_ns(b,replace(b,user_raw=6000010)))
        self.assertEqual(0,cpu_delta_ns(a,a))

    def test_changed_pid_start_clock_or_regressing_counter_cannot_be_zero_cpu(self):
        a=ProcessCpuSample(7,100,10,20,125,3)
        for b in (replace(a,pid=8),replace(a,start_raw=101),replace(a,timebase_denom=1),
                  replace(a,user_raw=9,system_raw=100),replace(a,system_raw=19,user_raw=100)):
            with self.assertRaises(ProcessCpuError):cpu_delta_ns(a,b)
        with self.assertRaises(ProcessCpuError):cpu_delta_ns(a,{})
        with self.assertRaises(ProcessCpuError):replace(a,user_raw=True)
        for pid in (True,0,-1,2**31,'7'):
            with patch('tacto.process_cpu._bindings') as bindings,self.assertRaises(ProcessCpuError):sample_process_cpu(pid)
            bindings.assert_not_called()

    @unittest.skipUnless(sys.platform=='darwin','Darwin libproc required')
    def test_live_cpu_matches_posix_cpu_time_in_own_busy_process(self):
        a=sample_process_cpu(os.getpid());r=resource.getrusage(resource.RUSAGE_SELF)
        start_cpu=r.ru_utime+r.ru_stime;deadline=time.monotonic()+.15
        while time.monotonic()<deadline:pass
        r=resource.getrusage(resource.RUSAGE_SELF);b=sample_process_cpu(os.getpid())
        posix=(r.ru_utime+r.ru_stime-start_cpu)*1e9
        native=cpu_delta_ns(a,b)
        self.assertGreater(posix,1000000)
        self.assertLess(abs(native-posix)/posix,.10)
        with self.assertRaises(ProcessCpuError):sample_process_cpu(0x7fffffff)
        validation=verify_cpu_clock()
        self.assertLess(validation['relative_error'],.10)
