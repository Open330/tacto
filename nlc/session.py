"""Session / capability layer — lock state, keep-awake, and honest capability flags.

Locked-screen conclusion (researched with the codex peer, sourced from Apple +
learn.chatgpt.com/docs/computer-use#locked-use):

  Operating a *locked* macOS GUI is NOT reachable with ordinary Accessibility +
  Screen Recording permission. ChatGPT's "Locked use" installs an Apple
  authorization plug-in (/Library/Security/SecurityAgentPlugins/…) that briefly
  unlocks for a trusted turn, blanks displays, blocks local input, and re-locks
  on any local activity. That is a separate login/unlock product, not a flag a
  normal app can set. So NLC models it as a capability that is off by default.

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
    """Prevent idle display/system sleep (hence idle lock) for a task's lifetime.

    Wraps `caffeinate -di`. Best-effort: does not stop a manual lock or policy.
    Use as a context manager around a flow run.
    """
    reason: str = "nlc automation"
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
    looks like a missing permission: `nlc apps` said
    accessibility_permission_missing and `nlc doctor` told the agent to grant
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
