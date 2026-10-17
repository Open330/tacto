"""Fresh read-only foreground-process sampling without NSWorkspace's app cache.

The public HIServices Process Manager APIs are deprecated but exported on the
verified macOS host. SDK Processes.h defines GetFrontProcess's OSErr as signed
16-bit and GetProcessPID's OSStatus/pid_t as signed 32-bit; MacTypes.h defines
ProcessSerialNumber as two UInt32 fields. Unavailable APIs fail explicitly.

A second foreground sample rejects an observed switch during PID resolution.
This is evidence at observation time, not an atomic guarantee that focus cannot
change immediately afterward. This module never activates or raises an app.
"""
import ctypes
import sys


HISERVICES_PATH = (
    '/System/Library/Frameworks/ApplicationServices.framework/'
    'Frameworks/HIServices.framework/HIServices'
)


class FrontmostUnavailable(RuntimeError):
    pass


class ProcessSerialNumber(ctypes.Structure):
    _fields_ = [('highLongOfPSN', ctypes.c_uint32),
                ('lowLongOfPSN', ctypes.c_uint32)]


def _load_library():
    if sys.platform != 'darwin':
        raise FrontmostUnavailable('foreground process API requires macOS')
    try:
        return ctypes.CDLL(HISERVICES_PATH)
    except (OSError, AttributeError) as exc:
        raise FrontmostUnavailable('HIServices foreground process API unavailable') from exc


def frontmost_pid():
    """Return a positive native PID from repeated foreground-process samples.

    Errors, absent exports, unset output arguments, and observed foreground
    changes are unavailable evidence. No cached-workspace fallback is used.
    """
    try:
        library = _load_library()
        get_front = library.GetFrontProcess
        get_pid = library.GetProcessPID
        get_front.argtypes = [ctypes.POINTER(ProcessSerialNumber)]
        get_front.restype = ctypes.c_int16
        get_pid.argtypes = [ctypes.POINTER(ProcessSerialNumber),
                            ctypes.POINTER(ctypes.c_int32)]
        get_pid.restype = ctypes.c_int32

        def sample():
            serial = ProcessSerialNumber()
            status = get_front(ctypes.byref(serial))
            if type(status) is not int or status != 0:
                raise FrontmostUnavailable('GetFrontProcess failed')
            if serial.highLongOfPSN == 0 and serial.lowLongOfPSN == 0:
                raise FrontmostUnavailable('GetFrontProcess returned an empty serial number')
            return serial

        before = sample()
        pid = ctypes.c_int32(-1)
        status = get_pid(ctypes.byref(before), ctypes.byref(pid))
        if type(status) is not int or status != 0:
            raise FrontmostUnavailable('GetProcessPID failed')
        if type(pid.value) is not int or pid.value <= 0:
            raise FrontmostUnavailable('GetProcessPID returned an invalid PID')
        after = sample()
        if ((before.highLongOfPSN, before.lowLongOfPSN) !=
                (after.highLongOfPSN, after.lowLongOfPSN)):
            raise FrontmostUnavailable('foreground process changed during observation')
        return pid.value
    except FrontmostUnavailable:
        raise
    except (OSError, AttributeError, TypeError, ValueError, ctypes.ArgumentError) as exc:
        raise FrontmostUnavailable('foreground process observation failed') from exc
