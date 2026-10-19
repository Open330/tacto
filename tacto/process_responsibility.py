"""Best-effort macOS process responsibility diagnostics; grants no authority.

Uses a private libSystem SPI and public libproc path reads. Availability and
process lifetime can change. Neither PID nor executable path proves a TCC
permission decision, code identity, descendant responsibility, or model parity.
No launchctl privilege escalation, process control, argv or environment reads.
"""
import ctypes
import sys
import threading


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


class OwnedProcessSampler:
    """Bounded optional diagnostics of an owned Popen handle, never permission.

    Samples only its primary PID; a CLI wrapper's descendants are not observed.
    A private API read cannot be forcibly cancelled. The daemon observer stops
    at request completion or after 64 samples; shutdown waits at most .2s.
    snapshot() copies records so a late read cannot mutate returned diagnostics.
    """
    def __init__(self, process, *, is_live=None):
        self.process = process
        self.is_live = is_live if is_live is not None else lambda: self.process.poll() is None
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.samples = []
        self.error = None
        self.limit_reached = False
        self.thread = None

    def __enter__(self):
        try:
            self.thread = threading.Thread(target=self._observe, daemon=True)
            self.thread.start()
        except Exception:
            self.error = 'observer_start_failed'
            self.thread = None
        return self

    def _observe(self):
        try:
            for _ in range(64):
                if self.stop.is_set() or not self.is_live():
                    return
                sample = observe_responsibility(self.process.pid)
                if type(sample) is not dict:
                    raise ValueError('invalid process diagnostic')
                if self.stop.is_set():
                    return
                if not self.is_live():
                    self.error = 'owned_process_exited_during_read'
                    return
                with self.lock:
                    if self.stop.is_set():
                        return
                    self.samples.append(sample)
                if self.stop.wait(.2):
                    return
            self.limit_reached = True
        except Exception:
            self.error = 'process_observation_failed'

    def __exit__(self, *args):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=.2)

    def snapshot(self):
        with self.lock:
            samples = [dict(sample) for sample in self.samples]
        return {'scope':'owned_primary_cli_pid_only', 'samples':samples,
                'sample_limit_reached':self.limit_reached,
                'observer_stopped':self.thread is None or not self.thread.is_alive(),
                'error_code':self.error, 'descendants_observed':False,
                'tcc_permission_verified':False}


def summarize_primary_responsibility(diagnostics):
    """Summarize sampled primary identities without granting GUI/TCC parity."""
    samples=[];malformed=False;limit_reached=False;observer_states=[]
    for diagnostic in diagnostics:
        if not isinstance(diagnostic,dict) or not isinstance(diagnostic.get('samples'),list):
            malformed=True
            observer_states.append(None)
            continue
        samples.extend(diagnostic['samples'])
        limit_reached=limit_reached or diagnostic.get('sample_limit_reached') is True
        state=diagnostic.get('observer_stopped')
        observer_states.append(state if type(state) is bool else None)
    observed=0;identities=set()
    for sample in samples:
        if not isinstance(sample,dict):
            malformed=True
            continue
        if sample.get('status')!='observed':continue
        pid=sample.get('pid');responsible=sample.get('responsible_pid')
        process_path=sample.get('process_path');responsible_path=sample.get('responsible_path')
        if (type(pid) is not int or pid<=0 or type(responsible) is not int or responsible<=0
                or not isinstance(process_path,str) or not process_path.startswith('/')
                or not isinstance(responsible_path,str) or not responsible_path.startswith('/')):
            malformed=True
            continue
        observed+=1
        identities.add((pid,responsible,process_path,responsible_path))
    observer_stopped=(False if False in observer_states else
                      True if observer_states and all(state is True for state in observer_states) else None)
    return {'scope':'sampled_owned_primary_cli_only','samples':len(samples),'observed_samples':observed,
            'consistent_observed_samples':bool(samples) and observed==len(samples) and len(identities)==1 and not malformed,
            'malformed_diagnostics':malformed,'sample_limit_reached':limit_reached,
            'observer_stopped':observer_stopped,'descendant_responsibility_verified':False,
            'tcc_permission_verified':False,'entire_execution_verified':False}
