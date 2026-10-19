"""Optional helper telemetry; no process control or input authority."""
from dataclasses import dataclass
import math
import os
import subprocess

from .native_process_identity import ProcessIdentityError, process_snapshot


class HelperIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class HelperProcessIdentity:
    pid: int
    start_time: float
    peer_pid_version: int | None = None


def parse_helper_identity(value, peer=None):
    if peer is not None:
        if (type(peer) is not dict or set(peer) != {'pid','pid_version'}
                or type(peer['pid']) is not int or not 0 < peer['pid'] <= 0x7FFFFFFF
                or type(peer['pid_version']) is not int or not 0 <= peer['pid_version'] <= 0xFFFFFFFF):
            raise HelperIdentityError('invalid helper peer identity')
    if value is None:
        return None
    if (type(value) is not dict or set(value) != {'pid','start_time'}
            or type(value['pid']) is not int or not 0 < value['pid'] <= 0x7FFFFFFF
            or type(value['start_time']) not in (int,float)
            or (peer is not None and peer['pid'] != value['pid'])):
        raise HelperIdentityError('invalid helper process identity')
    try:
        start = float(value['start_time'])
    except (OverflowError, ValueError):
        raise HelperIdentityError('invalid helper process identity') from None
    if not math.isfinite(start) or start <= 0:
        raise HelperIdentityError('invalid helper process identity')
    return HelperProcessIdentity(value['pid'], start,
                                 peer['pid_version'] if peer is not None else None)


def helper_instance_status(identity):
    """Passive point-in-time observation; absence and read denial differ."""
    if type(identity) is not HelperProcessIdentity:
        return 'unobservable'
    # Revalidate even a manually constructed dataclass before any OS query.
    try:
        peer = None if identity.peer_pid_version is None else {'pid':identity.pid,'pid_version':identity.peer_pid_version}
        parse_helper_identity({'pid':identity.pid,'start_time':identity.start_time}, peer)
    except HelperIdentityError:
        return 'unobservable'
    try:
        current = process_snapshot(identity.pid)
    except (ProcessIdentityError, OSError):
        try:
            if type(identity.pid) is not int or not 0 < identity.pid <= 0x7FFFFFFF:
                return 'unobservable'
            os.kill(identity.pid, 0)  # existence only; never signals a process
        except ProcessLookupError:
            return 'exited'
        except OSError:
            return 'unobservable'
        # Some macOS kernels refuse PROC_PIDTBSDINFO for zombies. Existence
        # alone cannot distinguish them; query only PID/state, never arguments.
        try:
            reply = subprocess.run(['/bin/ps','-p',str(identity.pid),'-o','pid=,stat='],
                capture_output=True, text=True, timeout=.5)
            fields = reply.stdout.split() if len(reply.stdout) <= 128 else []
            if (reply.returncode == 0 and len(fields) == 2 and fields[0] == str(identity.pid)
                    and fields[1].startswith('Z')):
                return 'zombie'
        except (OSError, subprocess.TimeoutExpired):
            pass
        return 'unobservable'
    if current['start_time'] != identity.start_time:
        return 'replaced'
    return 'zombie' if current['zombie'] else 'same_instance'
