"""Best-effort macOS process responsibility diagnostics; grants no authority.

Uses a private libSystem SPI and public libproc path reads. Availability and
process lifetime can change. Neither PID nor executable path proves a TCC
permission decision, code identity, descendant responsibility, or model parity.
No launchctl privilege escalation, process control, argv or environment reads.
"""
import ctypes
import sys


def _path(reader, pid):
    buffer = ctypes.create_string_buffer(4096)
    size = reader(pid, buffer, len(buffer))
    if size <= 0 or size >= len(buffer):
        raise ValueError('path unavailable')
    value = buffer.value.decode('utf-8')
    if not value.startswith('/') or not value:
        raise ValueError('invalid executable path')
    return value


def observe_responsibility(pid):
    """Query one live PID twice, refusing inconsistent responsibility/path reads.

    Callers must additionally hold/check their owned process handle. Consistent
    PID/path samples cannot exclude PID reuse or a same-path executable change.
    Unknown results retain null identities and a closed error code.
    """
    result = {'pid':pid, 'status':'unavailable', 'responsible_pid':None,
              'process_path':None, 'responsible_path':None,
              'source':'libSystem_responsibility_SPI_and_libproc',
              'tcc_permission_verified':False, 'descendants_observed':False}
    if type(pid) is not int or not 0 < pid < 2**31:
        raise ValueError('pid must be a positive pid_t')
    if sys.platform != 'darwin':
        return {**result, 'error_code':'unsupported_platform'}
    try:
        system = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        responsible = system.responsibility_get_pid_responsible_for_pid
        responsible.argtypes, responsible.restype = [ctypes.c_int], ctypes.c_int
        path = library.proc_pidpath
        path.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        path.restype = ctypes.c_int
    except (OSError, AttributeError):
        return {**result, 'error_code':'responsibility_api_unavailable'}
    try:
        first = responsible(pid)
        if first <= 0:
            return {**result, 'error_code':'responsibility_pid_unavailable'}
        process_path, responsible_path = _path(path, pid), _path(path, first)
        second = responsible(pid)
        if (second != first or _path(path, pid) != process_path
                or _path(path, first) != responsible_path):
            return {**result, 'error_code':'responsibility_changed_during_read'}
    except (OSError, ValueError, UnicodeError):
        return {**result, 'error_code':'process_path_unavailable'}
    return {**result, 'status':'observed', 'responsible_pid':first,
            'process_path':process_path, 'responsible_path':responsible_path}
