"""'Being controlled' indicator — a menu-bar item for the duration of a run.

When software acts on someone's screen for them, the person should be able to
see it happening and stop it. macOS has no public API that asks the system to
mark a window as remotely controlled (verified with the advisor), so this is
ours to show.

It used to be an outline drawn around the target window, and that was wrong in
two ways. It covered part of the screen with our own drawing, and it carried
`canJoinAllSpaces`, so switching desktops brought the box along and drew it
over unrelated work — around a window that was not even on that screen (user
report, 2026-09-21).

A menu-bar item hides nothing and follows nobody. It is also only half of what
is shown: when NLC actually captures the screen, macOS lights its own recording
indicator, which we do not draw, cannot fake and cannot suppress. The two
signals separate exactly as the access does — the menu bar for "NLC is acting",
the system indicator for "NLC is looking".

Run-loop note: an NSStatusItem needs the main thread's run loop, while a flow
runs synchronously in ours. Rather than restructure the driver around an event
loop, the indicator runs as a small child process that owns its own loop and is
torn down with the run — which also means a crash in the flow cannot leave a
stuck item in the menu bar.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

LABEL = "● NLC"


def run_indicator(pid: int, heartbeat: str | None = None, ttl: float = 4.0):
    """Own the main run loop and keep a menu-bar item up while `pid` is driven.

    With a `heartbeat` file, the item lives only as long as somebody keeps
    touching it. A driver that can act at any moment is not the same as one
    that IS acting, and an indicator that never goes away stops being read — so
    the driver touches the file on every observe and every act, and this exits
    once the touches stop. Without a heartbeat it stays up until killed, which
    is what a single bounded run wants.
    """
    from AppKit import (NSApplication, NSStatusBar, NSRunningApplication,
                        NSApplicationActivationPolicyAccessory,
                        NSVariableStatusItemLength)
    from Foundation import NSTimer
    import objc

    app = NSApplication.sharedApplication()
    # Accessory, not Prohibited. Prohibited means "cannot be activated at all"
    # and is not a policy a menu-bar app runs under — measured here, the call
    # returns False and the policy does not change, so the Dock icon it was
    # meant to suppress was being suppressed by luck. Accessory is the
    # documented policy for exactly this: menu bar, no Dock, never takes focus.
    if not app.setActivationPolicy_(NSApplicationActivationPolicyAccessory):
        return          # no policy, no promise: say nothing rather than half of it

    item = NSStatusBar.systemStatusBar().statusItemWithLength_(
        NSVariableStatusItemLength)
    button = item.button()
    if button is None:
        # Nothing to show means nothing to hide behind: say so by leaving,
        # which is what `Indicator.alive()` reports to the driver.
        return

    def tip_for(target_pid):
        who = NSRunningApplication.runningApplicationWithProcessIdentifier_(target_pid)
        name = who.localizedName() if who is not None else None
        return (f"NLC가 {name} 을(를) 조작하고 있습니다" if name
                else "NLC가 이 화면을 조작하고 있습니다") + ". Esc로 즉시 멈춥니다."

    # Short on purpose: the menu bar is the person's, and the app's name and
    # the way to stop are in the tooltip rather than taking its width.
    button.setTitle_(LABEL)
    button.setToolTip_(tip_for(pid))

    class Ticker(objc.lookUpClass("NSObject")):
        def tick_(self, _timer):
            if heartbeat is not None:
                try:
                    idle = time.time() - os.stat(heartbeat).st_mtime
                except OSError:
                    idle = ttl + 1          # the file went away: so do we
                if idle > ttl:
                    NSStatusBar.systemStatusBar().removeStatusItem_(item)
                    NSApplication.sharedApplication().terminate_(None)
                    return
            # The app being driven can change mid-run, and a stale name is a
            # worse answer than no name.
            button.setToolTip_(tip_for(pid))

    ticker = Ticker.alloc().init()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.5, ticker, "tick:", None, True)
    app.run()


class Indicator:
    """Context manager: show the indicator for the duration of a run.

    `heartbeat` hands the lifetime to whoever keeps touching that file — see
    `Driver._showing_control`, which is where this now comes from for every
    entry point rather than only the two CLI commands that remembered to ask.

    Failing to show the indicator must never take down the task, but it is
    reported — silently controlling someone's screen is the thing we are trying
    not to do."""

    def __init__(self, pid: int | None, enabled: bool = True, notify=None,
                 heartbeat: str | None = None, ttl: float = 4.0):
        self.pid, self.enabled, self._proc = pid, enabled, None
        self.heartbeat, self.ttl = heartbeat, ttl
        self._notify = notify or (lambda m: None)

    def __enter__(self):
        if not (self.enabled and self.pid):
            return self
        try:
            command = [sys.executable, "-m", "nlc.overlay", str(self.pid)]
            if self.heartbeat is not None:
                command += [self.heartbeat, str(self.ttl)]
            self._proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception as e:
            self._notify(f"(조작 중 표시를 띄우지 못했습니다: {e})")
        return self

    def __exit__(self, *exc):
        if self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except Exception:
                self._proc.terminate()
        return False


    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None


if __name__ == "__main__":
    run_indicator(int(sys.argv[1]),
                  sys.argv[2] if len(sys.argv) > 2 else None,
                  float(sys.argv[3]) if len(sys.argv) > 3 else 4.0)
