"""'Being controlled' indicator — an outline on the window we are driving.

When software moves someone's cursor and types for them, the person should be
able to see it happening and stop it. macOS has no public API that asks the
system to mark a window as remotely controlled (verified with the advisor;
Codex's own service is an LSUIElement background app that draws its own
overlay), so we draw ours the same way: a transparent, click-through panel
outlining the target window, labelled and dismissable with Esc.

Run-loop note: an NSPanel needs the main thread's run loop, while a flow runs
synchronously in ours. Rather than restructure the driver around an event loop,
the indicator runs as a small child process that owns its own loop and is torn
down with the run — which also means a crash in the flow cannot leave a stuck
overlay on screen.

Geometry: AX reports a window's position from the TOP-left of the primary
screen; AppKit's screen coordinates run from the BOTTOM-left, so the y axis is
flipped against `NSScreen.screens[0]` (the menu-bar screen — not `main`, which
follows keyboard focus).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

LABEL = "NLC가 이 창을 조작 중입니다 · Esc로 중단"


def _target_frame(pid: int):
    """(x, y, w, h) of the app's focused window in AX (top-left origin) coords."""
    # AXValueGetValue is wrapped in ApplicationServices, NOT in Quartz
    # (a real AttributeError here, and the same latent bug in driver's
    # coordinate fallback).
    from ApplicationServices import (AXUIElementCreateApplication,
                                     AXUIElementCopyAttributeValue,
                                     AXValueGetValue, kAXValueCGPointType,
                                     kAXValueCGSizeType)

    def attr(el, name):
        err, val = AXUIElementCopyAttributeValue(el, name, None)
        return val if err == 0 else None

    app = AXUIElementCreateApplication(pid)
    win = attr(app, "AXFocusedWindow")
    if win is None:
        wins = [w for w in (attr(app, "AXWindows") or [])
                if attr(w, "AXRole") == "AXWindow"]
        win = wins[0] if wins else None
    if win is None:
        return None
    pos, size = attr(win, "AXPosition"), attr(win, "AXSize")
    if pos is None or size is None:
        return None
    ok_p, p = AXValueGetValue(pos, kAXValueCGPointType, None)
    ok_s, s = AXValueGetValue(size, kAXValueCGSizeType, None)
    if not (ok_p and ok_s):
        return None
    return (p.x, p.y, s.width, s.height)


def run_indicator(pid: int, heartbeat: str | None = None, ttl: float = 4.0):
    """Own the main run loop and keep an outline on `pid`'s window.

    With a `heartbeat` file, the outline lives only as long as somebody keeps
    touching it. A driver that can act at any moment is not the same as one
    that IS acting, and an outline that never goes away stops being read — so
    the driver touches the file on every observe and every act, and this exits
    once the touches stop. Without a heartbeat it stays up until killed, which
    is what a single bounded run wants.
    """
    from AppKit import (NSApplication, NSPanel, NSColor, NSView, NSScreen,
                        NSMakeRect, NSBezierPath, NSFont, NSString,
                        NSForegroundColorAttributeName, NSFontAttributeName,
                        NSApplicationActivationPolicyProhibited,
                        NSWindowStyleMaskBorderless, NSWindowStyleMaskNonactivatingPanel,
                        NSBackingStoreBuffered, NSFloatingWindowLevel,
                        NSWindowCollectionBehaviorCanJoinAllSpaces,
                        NSWindowCollectionBehaviorFullScreenAuxiliary,
                        NSWindowCollectionBehaviorIgnoresCycle)
    from Foundation import NSTimer
    import objc

    ACCENT = NSColor.colorWithSRGBRed_green_blue_alpha_(0.05, 0.62, 0.55, 1.0)

    class OutlineView(NSView):
        def drawRect_(self, rect):
            b = self.bounds()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(b)
            # outline
            inset = NSMakeRect(b.origin.x + 2, b.origin.y + 2,
                               b.size.width - 4, b.size.height - 4)
            path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(inset, 8, 8)
            path.setLineWidth_(4.0)
            ACCENT.set()
            path.stroke()
            # label chip along the top edge
            attrs = {NSFontAttributeName: NSFont.boldSystemFontOfSize_(13),
                     NSForegroundColorAttributeName: NSColor.whiteColor()}
            text = NSString.stringWithString_(LABEL)
            size = text.sizeWithAttributes_(attrs)
            pad = 10
            chip = NSMakeRect(inset.origin.x + 12,
                              inset.origin.y + inset.size.height - size.height - 2 * pad - 2,
                              size.width + 2 * pad, size.height + pad)
            ACCENT.set()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(chip, 6, 6).fill()
            text.drawAtPoint_withAttributes_(
                (chip.origin.x + pad, chip.origin.y + pad / 2), attrs)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)  # no Dock icon

    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 10, 10),
        NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel,
        NSBackingStoreBuffered, False)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(NSColor.clearColor())
    panel.setIgnoresMouseEvents_(True)        # never intercept the user's clicks
    panel.setLevel_(NSFloatingWindowLevel)
    panel.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorIgnoresCycle)
    panel.setHasShadow_(False)
    panel.setContentView_(OutlineView.alloc().init())

    primary_h = NSScreen.screens()[0].frame().size.height

    class Ticker(objc.lookUpClass("NSObject")):
        def tick_(self, _timer):
            if heartbeat is not None:
                try:
                    idle = time.time() - os.stat(heartbeat).st_mtime
                except OSError:
                    idle = ttl + 1          # the file went away: so do we
                if idle > ttl:
                    panel.orderOut_(None)
                    NSApplication.sharedApplication().terminate_(None)
                    return
            f = _target_frame(pid)
            if f is None:
                panel.orderOut_(None)
                return
            x, y, w, h = f
            # AX top-left origin -> AppKit bottom-left origin
            panel.setFrame_display_(NSMakeRect(x, primary_h - y - h, w, h), True)
            if not panel.isVisible():
                panel.orderFrontRegardless()
            panel.contentView().setNeedsDisplay_(True)

    ticker = Ticker.alloc().init()
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.1, ticker, "tick:", None, True)
    app.run()


class Indicator:
    """Context manager: show the outline for the duration of a run.

    `heartbeat` hands the lifetime to whoever keeps touching that file — see
    `Driver._showing_control`, which is where this now comes from for every
    entry point rather than only the two CLI commands that remembered to ask.

    Failing to draw the indicator must never take down the task, but it is
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
