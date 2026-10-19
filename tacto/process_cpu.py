"""Read-only live-process CPU snapshots via the installed Darwin libproc ABI.

RUSAGE_INFO_V2 fields follow SDK sys/resource.h; raw CPU time follows the Mach
timebase on the validated host. This is local whole-process CPU, not AX/thread
attribution, remote serving CPU, billable cost or an atomic liveness lease.
"""
import ctypes
from dataclasses import dataclass
from functools import lru_cache
import sys
import time


class ProcessCpuError(ValueError):
    pass


@dataclass(frozen=True)
class ProcessCpuSample:
    pid: int
    start_raw: int
    user_raw: int
    system_raw: int
    timebase_numer: int
    timebase_denom: int

    def __post_init__(self):
        bounds = ((self.pid, 1, 0x7fffffff), (self.start_raw, 1, 2**64-1),
                  (self.user_raw, 0, 2**64-1), (self.system_raw, 0, 2**64-1),
                  (self.timebase_numer, 1, 2**32-1), (self.timebase_denom, 1, 2**32-1))
        if any(type(value) is not int or not low <= value <= high for value,low,high in bounds):
            raise ProcessCpuError('invalid_cpu_sample')


class _RusageV2(ctypes.Structure):
    _fields_ = [('ri_uuid', ctypes.c_uint8 * 16)] + [
        (name, ctypes.c_uint64) for name in (
            'ri_user_time','ri_system_time','ri_pkg_idle_wkups','ri_interrupt_wkups',
            'ri_pageins','ri_wired_size','ri_resident_size','ri_phys_footprint',
            'ri_proc_start_abstime','ri_proc_exit_abstime','ri_child_user_time',
            'ri_child_system_time','ri_child_pkg_idle_wkups','ri_child_interrupt_wkups',
            'ri_child_pageins','ri_child_elapsed_abstime','ri_diskio_bytesread','ri_diskio_byteswritten')]


class _Timebase(ctypes.Structure):
    _fields_ = [('numer',ctypes.c_uint32),('denom',ctypes.c_uint32)]


@lru_cache(maxsize=1)
def _bindings():
    if sys.platform != 'darwin' or ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(_RusageV2) != 160:
        raise ProcessCpuError('cpu_platform_unsupported')
    try:
        proc = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        system = ctypes.CDLL('/usr/lib/libSystem.B.dylib')
        usage = proc.proc_pid_rusage
        usage.argtypes = [ctypes.c_int,ctypes.c_int,ctypes.c_void_p]
        usage.restype = ctypes.c_int
        timebase = system.mach_timebase_info
        timebase.argtypes = [ctypes.POINTER(_Timebase)]
        timebase.restype = ctypes.c_int
        return proc,system,usage,timebase
    except (OSError,AttributeError):
        raise ProcessCpuError('cpu_reader_unavailable') from None


def sample_process_cpu(pid):
    if type(pid) is not int or not 1 <= pid <= 0x7fffffff:
        raise ProcessCpuError('invalid_cpu_pid')
    _,_,usage,timebase = _bindings()
    info,clock = _RusageV2(),_Timebase()
    if usage(pid,2,ctypes.byref(info)) != 0 or timebase(ctypes.byref(clock)) != 0:
        raise ProcessCpuError('cpu_sample_unavailable')
    if info.ri_proc_exit_abstime:
        raise ProcessCpuError('cpu_process_exited')
    return ProcessCpuSample(pid,info.ri_proc_start_abstime,info.ri_user_time,
                            info.ri_system_time,clock.numer,clock.denom)


def cpu_delta_ns(before, after):
    if type(before) is not ProcessCpuSample or type(after) is not ProcessCpuSample:
        raise ProcessCpuError('invalid_cpu_sample_pair')
    if (before.pid,before.start_raw,before.timebase_numer,before.timebase_denom) != (
            after.pid,after.start_raw,after.timebase_numer,after.timebase_denom):
        raise ProcessCpuError('cpu_process_or_clock_changed')
    user,system = after.user_raw-before.user_raw,after.system_raw-before.system_raw
    if user < 0 or system < 0:
        raise ProcessCpuError('cpu_counter_regressed')
    return (user+system)*before.timebase_numer//before.timebase_denom


def verify_cpu_clock():
    """Validate this host's conversion against own POSIX CPU; no GUI effects.

    A short bounded local calibration is required before using the optional
    profiler. Failure stays unverified rather than silently changing units.
    """
    import os
    import resource
    before=sample_process_cpu(os.getpid())
    usage=resource.getrusage(resource.RUSAGE_SELF)
    posix_before=usage.ru_utime+usage.ru_stime
    cpu_started=time.process_time();wall_started=time.monotonic()
    while time.process_time()-cpu_started<.05 and time.monotonic()-wall_started<.5:
        pass
    usage=resource.getrusage(resource.RUSAGE_SELF)
    posix_ns=round((usage.ru_utime+usage.ru_stime-posix_before)*1e9)
    after=sample_process_cpu(os.getpid());native_ns=cpu_delta_ns(before,after)
    if posix_ns<1000000 or abs(native_ns-posix_ns)/posix_ns>.10:
        raise ProcessCpuError('cpu_clock_unverified')
    return {'timebase_numer':before.timebase_numer,'timebase_denom':before.timebase_denom,
            'native_delta_ns':native_ns,'posix_delta_ns':posix_ns,
            'relative_error':abs(native_ns-posix_ns)/posix_ns,
            'scope':'own local process calibration only'}
