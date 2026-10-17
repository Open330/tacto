"""Read process start time via public libproc; no AppKit launch dependency.

ABI checked against the installed macOS SDK usr/include/sys/proc_info.h
(proc_bsdinfo, PROC_PIDTBSDINFO=3), sys/param.h (MAXCOMLEN=16), and
usr/include/libproc.h (proc_pidinfo). This is a PID/start-time binding, not an
atomic guarantee that the process remains alive after the read.
"""
import ctypes
import sys


class ProcessIdentityError(RuntimeError):
    pass


class _ProcBSDInfo(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint32) for name in (
        'pbi_flags', 'pbi_status', 'pbi_xstatus', 'pbi_pid', 'pbi_ppid',
        'pbi_uid', 'pbi_gid', 'pbi_ruid', 'pbi_rgid', 'pbi_svuid',
        'pbi_svgid', 'rfu_1')] + [
        ('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32),
    ] + [(name, ctypes.c_uint32) for name in (
        'pbi_nfiles', 'pbi_pgid', 'pbi_pjobc', 'e_tdev', 'e_tpgid')
    ] + [('pbi_nice', ctypes.c_int32), ('pbi_start_tvsec', ctypes.c_uint64),
         ('pbi_start_tvusec', ctypes.c_uint64)]


def _load_pidinfo():
    if sys.platform != 'darwin':
        raise ProcessIdentityError('libproc process identity requires macOS')
    try:
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        function = library.proc_pidinfo
        function.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
                             ctypes.c_void_p, ctypes.c_int]
        function.restype = ctypes.c_int
        return function
    except (OSError, AttributeError) as exc:
        raise ProcessIdentityError('libproc process identity unavailable') from exc


def process_start_identity(pid):
    """Return observed epoch seconds + microseconds / 1e6 as a float.

    Callers must use this same source for capture and comparison; AppKit's
    launchDate is not interchangeable. Never return a guessed/default time.
    """
    if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
        raise ProcessIdentityError('invalid PID')
    info = _ProcBSDInfo()
    size = ctypes.sizeof(info)
    count = _load_pidinfo()(pid, 3, 0, ctypes.byref(info), size)
    if count != size or info.pbi_pid != pid:
        raise ProcessIdentityError('process identity read incomplete or mismatched')
    if info.pbi_start_tvsec == 0 or info.pbi_start_tvusec >= 1_000_000:
        raise ProcessIdentityError('invalid process start time')
    return info.pbi_start_tvsec + info.pbi_start_tvusec / 1_000_000
