"""Session / capability layer — lock state, keep-awake, and honest capability flags.

Locked-screen conclusion (researched with the codex peer, sourced from Apple +
learn.chatgpt.com/docs/computer-use#locked-use):

  Operating a *locked* macOS GUI is NOT reachable with ordinary Accessibility +
  Screen Recording permission. ChatGPT's "Locked use" installs an Apple
  authorization plug-in (/Library/Security/SecurityAgentPlugins/…) that briefly
  unlocks for a trusted turn, blanks displays, blocks local input, and re-locks
  on any local activity. That is a separate login/unlock product, not a flag a
  normal app can set. So Tacto models it as a capability that is off by default.

Pragmatic path (option A): don't fight the lock — prevent an *idle* lock during a
task with `caffeinate`, and when the screen is genuinely locked, surface it as a
first-class state and hand off. `caffeinate` is best-effort and never defeats a
manual lock, a security policy, a closed lid, or the screen saver.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, asdict

import Quartz


# ---- capabilities (option D): name what is and isn't reachable, honestly ----

CAPABILITIES = {
    "ax_tree_read": True,          # AXUIElement tree read
    "ax_action_input": True,       # AXPress / AXValue (focus-free, parallel)
    "cg_event_input": True,        # typing / coordinate (global focus, serialized)
    "parallel_apps": True,         # concurrent flows across distinct apps
    "screen_capture": None,        # depends on Screen Recording grant (checked live)
    "locked_gui_access": False,    # NOT reachable without an authorization plug-in
    "trusted_unlock": False,       # provider-specific (ChatGPT Locked use); not us
    "headless_browser": False,     # backend-specific (Playwright), not yet wired
}


def lock_state() -> dict:
    """Best-effort current-session state, not an atomic input authorization.

    This platform can omit the lock key in a logged-in console session. Interpret
    absence as unlocked only with both console and login-complete indicators.
    Missing session/indicators and malformed flags remain unknown. The lock key
    is private; app visibility and input guards still need their own checks.
    """
    try:
        d = Quartz.CGSessionCopyCurrentDictionary()
        if d is None:
            return {"locked": None, "on_console": None, "login_done": None,
                    "source": "no_session_dict"}
        def flag(value):
            return value if type(value) is bool else None
        console = flag(d.get("kCGSSessionOnConsoleKey", d.get("OnConsole")))
        login = flag(d.get("kCGSessionLoginDoneKey"))
        locked = flag(d.get("CGSSessionScreenIsLocked"))
        if 'CGSSessionScreenIsLocked' not in d and console is True and login is True:
            locked = False
        return {
            "locked": locked, "on_console": console, "login_done": login,
            "source": "CGSessionCopyCurrentDictionary",
        }
    except Exception as e:
        return {"locked": None, "on_console": None, "login_done": None,
                "source": f"error:{type(e).__name__}"}


@dataclass
class KeepAwake:
    """Prevent idle display/system sleep (a common path to the lock) for a task's lifetime.

    Wraps `caffeinate -di`. Best-effort: does not stop a manual lock or policy.
    Use as a context manager around a flow run.
    """
    reason: str = "tacto automation"
    _proc: object = None

    def __enter__(self):
        try:
            # -d no display sleep, -i no idle sleep; dies with us via -w on our pid
            import os
            self._proc = subprocess.Popen(
                ["caffeinate", "-di", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            self._proc = None
        return self

    def __exit__(self, *exc):
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        return False


KEEP_AWAKE_MAX_MINUTES = 480
CAFFEINATE = '/usr/bin/caffeinate'


def _process_identity(pid, run=subprocess.run):
    """(start time, full command) of a live pid; False when ps says there is no
    such process; None when it could not be asked (unknown, not absent)."""
    try:
        started = run(['/bin/ps', '-p', str(int(pid)), '-o', 'lstart='], capture_output=True, text=True, timeout=5)
        command = run(['/bin/ps', '-p', str(int(pid)), '-o', 'command='], capture_output=True, text=True, timeout=5)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    if started.returncode == 1 and not started.stdout.strip():
        return False
    if started.returncode or command.returncode or not started.stdout.strip():
        return None
    return started.stdout.strip(), command.stdout.strip()


class _KeepAwakeLock:
    """One start/replace/stop at a time, across processes (concurrent agents)."""
    def __init__(self, state):
        self.path = state/'keep-awake.lock'

    def __enter__(self):
        import fcntl
        self.handle = open(self.path, 'a')
        fcntl.flock(self.handle, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        fcntl.flock(self.handle, fcntl.LOCK_UN)
        self.handle.close()
        return False


def _state_dir(state_dir):
    from pathlib import Path
    from .preferences import product_directory
    state = Path(state_dir) if state_dir else product_directory()
    state.mkdir(parents=True, exist_ok=True)
    return state


def keep_awake_for(minutes, *, state_dir=None, run=subprocess.Popen, now=None, identify=None, kill=None):
    """Prevent idle display and system sleep for `minutes`, beyond this command.

    A person's opt-in for long unattended or remote work: a display that goes
    to sleep between an agent's commands can lock the screen, and only the
    person can unlock. `caffeinate -di -t` ends by itself. It does not defeat
    a manual lock, a security policy, a closed lid, or a screen-saver lock.
    One holder at a time: a new request replaces the one this command started.
    """
    import json, os as _os, time
    if type(minutes) is not int or not 1 <= minutes <= KEEP_AWAKE_MAX_MINUTES:
        raise ValueError(f'minutes must be 1..{KEEP_AWAKE_MAX_MINUTES}')
    identify = identify or _process_identity
    state = _state_dir(state_dir)
    record = state/'keep-awake.json'
    with _KeepAwakeLock(state):
        if _stop_locked(state, kill=kill, now=now, identify=identify).get('unverified'):
            # A previous holder may still run and could not be checked: do not
            # add a second one nobody would know to stop.
            raise RuntimeError('previous keep-awake holder could not be verified; try --stop again')
        argv = [CAFFEINATE, '-di', '-t', str(minutes*60)]
        proc = run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        until = (now or time.time)() + minutes*60
        try:
            identity = identify(proc.pid)
            if identity is None:
                raise OSError('holder identity unreadable')
            temporary = record.with_suffix('.tmp')
            temporary.write_text(json.dumps({'pid': proc.pid, 'until': until, 'started': identity[0],
                                             'command': ' '.join(argv)}))
            _os.replace(temporary, record)
        except Exception:
            # Never leave a holder nobody can find to stop.
            try:
                proc.terminate()
            except Exception:
                pass
            raise
    return {'type': 'keep_awake', 'pid': proc.pid, 'minutes': minutes, 'until': until,
            'note': 'idle display/system sleep only; a manual, policy or screen-saver lock still applies'}


def stop_keep_awake(*, state_dir=None, kill=None, now=None, identify=None):
    """End the holder this command started, if that exact process still runs."""
    state = _state_dir(state_dir)
    with _KeepAwakeLock(state):
        return _stop_locked(state, kill=kill, now=now, identify=identify or _process_identity)


def _stop_locked(state, *, kill=None, now=None, identify=_process_identity):
    import json, signal, time
    record = state/'keep-awake.json'
    try:
        entry = json.loads(record.read_text())
        pid, until, started, command = entry['pid'], entry['until'], entry['started'], entry['command']
    except (OSError, ValueError, KeyError, TypeError):
        return {'type': 'keep_awake_stopped', 'stopped': False}
    if type(until) not in (int, float) or until <= (now or time.time)():
        record.unlink(missing_ok=True)
        return {'type': 'keep_awake_stopped', 'stopped': False}   # it has already ended by itself
    identity = identify(pid)
    if identity is None:
        # Could not ask: keep the only handle to a holder that may still run.
        return {'type': 'keep_awake_stopped', 'stopped': False, 'unverified': True}
    # Same process: start time and full command, so a recycled pid is never signalled.
    if identity != (started, command):
        record.unlink(missing_ok=True)
        return {'type': 'keep_awake_stopped', 'stopped': False}
    try:
        (kill or os.kill)(int(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    else:
        record.unlink(missing_ok=True)
        return {'type': 'keep_awake_stopped', 'stopped': True}
    record.unlink(missing_ok=True)
    return {'type': 'keep_awake_stopped', 'stopped': False}


def preflight(require_unlocked: bool = True) -> dict:
    """One call before a task: capabilities + live lock/screen state + a verdict.

    verdict: 'ready' | 'session_locked' (hand off) | 'unknown_lock' (proceed with
    care). Screen-capture capability is filled from the live grant.
    """
    caps = dict(CAPABILITIES)
    try:
        caps["screen_capture"] = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        caps["screen_capture"] = None
    ls = lock_state()
    if ls["locked"] is True:
        verdict = "session_locked"
    elif ls["locked"] is None:
        verdict = "unknown_lock"
    else:
        verdict = "ready"
    return {"capabilities": caps, "lock": ls, "verdict": verdict,
            "note": "locked GUI needs an authorization plug-in; not enabled"}


if __name__ == "__main__":
    import json
    print(json.dumps(preflight(), indent=2, ensure_ascii=False))


def sandboxed() -> bool:
    """Is this process inside a macOS (seatbelt) sandbox?

    A coding agent's sandbox — Codex's `workspace-write`, for one — blocks the
    GUI session and the accessibility API, and from inside it every symptom
    looks like a missing permission: `tacto apps` said
    accessibility_permission_missing and `tacto doctor` told the agent to grant
    Accessibility, which was already granted (2026-09-28). Asked of the system
    rather than inferred from an agent's environment variables, so it holds
    for any agent that sandboxes this way.
    """
    try:
        import ctypes
        check = ctypes.CDLL("/usr/lib/libSystem.dylib").sandbox_check
        check.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        check.restype = ctypes.c_int
        return check(os.getpid(), None, 0) == 1
    except Exception:
        return bool(os.environ.get("CODEX_SANDBOX"))


SANDBOXED = (
    "Tacto가 샌드박스 안에서 실행됐습니다 — 화면(GUI) 세션과 접근성 API가 막혀 있어 앱을\n"
    "조작할 수 없습니다. 권한 문제가 아니니 권한 설정은 바꾸지 마세요.\n"
    "  · Codex: 이 명령을 샌드박스 밖에서 실행하도록 권한 상승을 요청하거나\n"
    "    (sandbox_permissions: require_escalated), 사용자에게 --sandbox danger-full-access 로\n"
    "    다시 실행해 달라고 하세요.\n"
    "  · 그 밖의 에이전트: 샌드박스 밖에서 실행해 달라고 사용자에게 요청하세요.")
