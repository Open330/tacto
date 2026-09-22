"""Watch for synthetic input, and say where it came from.

Settles a question that binary inspection could not: does another tool click by
posting events, or by asking the accessibility API? An event tap sees every
mouse and key event that reaches the system, and each one carries the pid of
the process that created it — so "did a click happen, and who made it" is one
measurement rather than an argument about symbol tables.

A click performed through `AXUIElementPerformAction` produces NO event here.
One posted with CGEvent does, and names its source.

    .venv/bin/python examples/probes/watch-input-events.py 60

Run it, then make the other tool click something. Events whose source is this
machine's own user input are labelled too, so a person moving the mouse is not
mistaken for a robot.
"""
import sys
import time

import Quartz
from AppKit import NSRunningApplication

WATCHED = {
    Quartz.kCGEventLeftMouseDown: "leftMouseDown",
    Quartz.kCGEventLeftMouseUp: "leftMouseUp",
    Quartz.kCGEventRightMouseDown: "rightMouseDown",
    Quartz.kCGEventRightMouseUp: "rightMouseUp",
    Quartz.kCGEventMouseMoved: "mouseMoved",
    Quartz.kCGEventScrollWheel: "scrollWheel",
    Quartz.kCGEventKeyDown: "keyDown",
}
# Reported but not printed one by one: a person moving a mouse makes hundreds.
NOISY = {Quartz.kCGEventMouseMoved}

seen = []
moved = 0


def _name(pid):
    if not pid:
        return "?"
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return f"pid {pid}"
    return app.bundleIdentifier() or app.localizedName() or f"pid {pid}"


def callback(proxy, event_type, event, refcon):
    global moved
    if event_type not in WATCHED:
        return event
    source = Quartz.CGEventGetIntegerValueField(
        event, Quartz.kCGEventSourceUnixProcessID)
    if event_type in NOISY:
        moved += 1
        return event
    where = Quartz.CGEventGetLocation(event)
    # 91/92 name the window an event is addressed to, when anything set them.
    window = Quartz.CGEventGetIntegerValueField(event, 92) or \
        Quartz.CGEventGetIntegerValueField(event, 91)
    line = (f"{time.strftime('%H:%M:%S')}  {WATCHED[event_type]:<16}"
            f"at ({where.x:7.1f},{where.y:7.1f})  "
            f"from {_name(source):<34} window={window or '-'}")
    seen.append(line)
    print(line, flush=True)
    return event


def main(seconds):
    mask = 0
    for kind in WATCHED:
        mask |= Quartz.CGEventMaskBit(kind)
    tap = Quartz.CGEventTapCreate(
        Quartz.kCGSessionEventTap, Quartz.kCGHeadInsertEventTap,
        Quartz.kCGEventTapOptionListenOnly, mask, callback, None)
    if tap is None:
        print("이벤트 탭을 만들지 못했습니다 — 이 프로세스에 손쉬운 사용 권한이 "
              "필요합니다", file=sys.stderr)
        return 2
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    Quartz.CFRunLoopAddSource(Quartz.CFRunLoopGetCurrent(), source,
                              Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    print(f"{seconds}초 동안 입력 이벤트를 지켜봅니다. 지금 클릭을 시켜보세요.\n")
    Quartz.CFRunLoopRunInMode(Quartz.kCFRunLoopDefaultMode, seconds, False)
    print(f"\n합계: 클릭·키 이벤트 {len(seen)}건, 마우스 이동 {moved}건")
    if not seen:
        print("이벤트가 하나도 없었습니다 — 그 동안 일어난 조작은 "
              "이벤트를 쏘지 않았다는 뜻입니다 (접근성 API 경로).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(float(sys.argv[1]) if len(sys.argv) > 1 else 60))
