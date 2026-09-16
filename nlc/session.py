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
    """Best-effort screen-lock read. `locked` may be None when undeterminable —
    absence of the key is not a guaranteed 'unlocked' (codex's caveat)."""
    try:
        d = Quartz.CGSessionCopyCurrentDictionary()
        if d is None:
            return {"locked": None, "on_console": None, "source": "no_session_dict"}
        locked = d.get("CGSSessionScreenIsLocked")
        return {
            "locked": bool(locked) if locked is not None else None,
            "on_console": bool(d.get("kCGSSessionOnConsoleKey", d.get("OnConsole"))),
            "source": "CGSessionCopyCurrentDictionary",
        }
    except Exception as e:
        return {"locked": None, "on_console": None, "source": f"error:{e}"}


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
        if self._proc is not None:
            self._proc.terminate()
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
