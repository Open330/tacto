"""Bound pipe capture while a local planning CLI runs (POSIX only).

This bounds local stdout/stderr, not remote generation, billing, or CLI files.
Output-limit errors expose no partial content. Timeouts retain bounded raw bytes
for the host's private trace; provider adapters must never accept them as answers.
"""
import os
import selectors
import subprocess
import time
import signal
import sys
import ctypes
from functools import lru_cache
from contextlib import contextmanager

MAX_PROVIDER_OUTPUT_BYTES = 8 * 1024 * 1024  # per stream


class ProviderOutputLimitExceeded(ValueError):
    pass


def bounded_output_text(output):
    """Decode captured failure bytes for metadata only, never as an answer."""
    if type(output) is bytes:
        if len(output)>MAX_PROVIDER_OUTPUT_BYTES:return None
        try:return output.decode('utf-8')
        except UnicodeDecodeError:return None
    if type(output) is str:
        try:return output if len(output.encode('utf-8'))<=MAX_PROVIDER_OUTPUT_BYTES else None
        except UnicodeEncodeError:return None
    return None


@lru_cache(maxsize=1)
def _darwin_waitid():
    # Installed Darwin SDK sys/signal.h siginfo_t; no waitpid/reap here.
    class SigInfo(ctypes.Structure):
        _fields_=[('signo',ctypes.c_int),('error',ctypes.c_int),('code',ctypes.c_int),
                  ('pid',ctypes.c_int),('uid',ctypes.c_uint),('status',ctypes.c_int),
                  ('address',ctypes.c_void_p),('value',ctypes.c_void_p),
                  ('band',ctypes.c_long),('padding',ctypes.c_ulong*7)]
    library=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
    call=library.waitid
    call.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.POINTER(SigInfo),ctypes.c_int]
    call.restype=ctypes.c_int
    return call,SigInfo


def provider_process_exited_unreaped(process):
    """Observe owned child exit while retaining its PID for group cleanup."""
    if process.returncode is not None:raise ChildProcessError('provider leader already reaped')
    options=os.WEXITED|os.WNOHANG|os.WNOWAIT
    if hasattr(os,'waitid'):
        status=os.waitid(os.P_PID,process.pid,options)
        return status is not None and status.si_pid==process.pid
    if sys.platform!='darwin':raise RuntimeError('non-reaping provider wait unavailable')
    call,Info=_darwin_waitid();info=Info()
    if call(os.P_PID,process.pid,ctypes.byref(info),options)!=0:
        error=ctypes.get_errno();raise OSError(error,os.strerror(error))
    if info.pid not in (0,process.pid):raise ChildProcessError('unexpected provider child status')
    return info.pid==process.pid


def _darwin_group_has_no_other_processes(pid):
    # A group containing only its unreaped zombie leader can report EPERM.
    # Do not generalize that error to groups with any other member.
    if sys.platform!='darwin':return False
    library=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
    call=library.proc_listpids
    call.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_int]
    call.restype=ctypes.c_int
    members=(ctypes.c_int*4)();ctypes.set_errno(0)
    count=call(2,pid,members,ctypes.sizeof(members))
    if count<0 or count>=ctypes.sizeof(members) or count%ctypes.sizeof(ctypes.c_int) or ctypes.get_errno():
        return False
    return all(member==pid for member in list(members)[:count//ctypes.sizeof(ctypes.c_int)])


def require_provider_child_ownership():
    """Check disposition without changing handlers; callers must exclude all
    competing child reapers and disposition changes throughout the lifetime.
    """
    if os.name != 'posix' or signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError('provider requires default SIGCHLD disposition')
    if sys.platform == 'darwin':
        # Installed SDK struct sigaction, not kernel __sigaction. Python's
        # cached handler does not expose the native SA_NOCLDWAIT flag.
        class SigAction(ctypes.Structure):
            _fields_ = [('handler', ctypes.c_void_p), ('mask', ctypes.c_uint),
                        ('flags', ctypes.c_int)]
        library = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        call = library.sigaction
        call.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(SigAction)]
        call.restype = ctypes.c_int
        current = SigAction()
        if call(signal.SIGCHLD, None, ctypes.byref(current)) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        if current.handler or current.flags & 0x0020:  # SA_NOCLDWAIT
            raise RuntimeError('provider requires waitable default SIGCHLD disposition')


@contextmanager
def owned_process_session(process):
    """Retain a fresh waitable child leader until its owned group is cleaned.

    Call require_provider_child_ownership before spawning. The caller must not
    poll/wait/reap this handle inside the context, must exclude competing child
    reapers and SIGCHLD changes, and must bound its own pipe/operation loop.
    Detached sessions remain outside the local cleanup boundary.
    """
    owned=False
    try:
        if (os.name!='posix' or process.returncode is not None
                or os.getpgid(process.pid)!=process.pid or os.getsid(process.pid)!=process.pid):
            raise RuntimeError('provider requires a fresh owned session')
        owned=True
        yield process
    finally:
        try:
            if owned and process.returncode is None:
                # ECHILD means this child no longer pins the numeric PID.
                # Refuse group signalling when ownership cannot be established.
                provider_process_exited_unreaped(process)
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                except PermissionError:
                    if not (provider_process_exited_unreaped(process) and
                            _darwin_group_has_no_other_processes(process.pid)):
                        process.kill()
                        raise
            else:
                # A mismatched/already-reaped handle never authorizes group input.
                process.kill()
        finally:process.wait()



def communicate_owned_provider(process, *, input=None, timeout, stdout_observer=None):
    """Bound provider pipes and clean the owned session before child reaping."""
    with owned_process_session(process):
        if stdout_observer is None:
            return bounded_communicate(process,input=input,timeout=timeout,_retain_child_status=True)
        return bounded_communicate(process,input=input,timeout=timeout,_retain_child_status=True,
                                   stdout_observer=stdout_observer)


def bounded_communicate(process, *, input=None, timeout, _retain_child_status=False, stdout_observer=None):
    if os.name != 'posix':
        raise RuntimeError('bounded provider pipes require POSIX')
    if type(timeout) not in (int,float) or not 0 < timeout < float('inf'):
        raise ValueError('invalid provider timeout')
    if input is not None and type(input) is not str:
        raise ValueError('provider input must be text')
    deadline=time.monotonic()+timeout
    payload=(input or '').encode(process.stdin.encoding or 'utf-8') if process.stdin else b''
    offset=0
    chunks={'stdout':bytearray(),'stderr':bytearray()}
    def timed_out():
        return subprocess.TimeoutExpired(process.args,timeout,
            output=bytes(chunks['stdout']),stderr=bytes(chunks['stderr']))
    with selectors.DefaultSelector() as selector:
        for name in chunks:
            pipe=getattr(process,name)
            os.set_blocking(pipe.fileno(),False)
            selector.register(pipe,selectors.EVENT_READ,name)
        if process.stdin:
            if payload:
                os.set_blocking(process.stdin.fileno(),False)
                selector.register(process.stdin,selectors.EVENT_WRITE,'stdin')
            else:process.stdin.close()
        while selector.get_map():
            remaining=deadline-time.monotonic()
            if remaining<=0:raise timed_out()
            for key,_ in selector.select(remaining):
                pipe=key.fileobj
                if key.data=='stdin':
                    try:offset+=os.write(pipe.fileno(),payload[offset:offset+65536])
                    except BrokenPipeError:offset=len(payload)
                    except BlockingIOError:continue
                    if offset==len(payload):
                        selector.unregister(pipe);pipe.close()
                else:
                    try:data=os.read(pipe.fileno(),65536)
                    except BlockingIOError:continue
                    if not data:selector.unregister(pipe)
                    else:
                        collected=chunks[key.data]
                        if len(collected)+len(data)>MAX_PROVIDER_OUTPUT_BYTES:
                            raise ProviderOutputLimitExceeded('provider output limit exceeded')
                        collected.extend(data)
                        if key.data == 'stdout' and stdout_observer is not None:
                            stdout_observer(data)
        remaining=deadline-time.monotonic()
        if remaining<=0:raise timed_out()
        if _retain_child_status:
            while not provider_process_exited_unreaped(process):
                remaining=deadline-time.monotonic()
                if remaining<=0:raise timed_out()
                time.sleep(min(.01,remaining))
        else:
            try:process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:raise timed_out() from None
    def decode(name):
        if time.monotonic()>=deadline:raise timed_out()
        pipe=getattr(process,name)
        # Preserve Popen(text=True)'s universal newline behavior.
        decoded=bytes(chunks[name]).decode(pipe.encoding or 'utf-8',pipe.errors or 'strict').replace('\r\n','\n').replace('\r','\n')
        if time.monotonic()>=deadline:raise timed_out()
        return decoded
    return decode('stdout'),decode('stderr')
