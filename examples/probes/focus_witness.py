"""Record every change of the frontmost app while something runs.

Looking at the frontmost app at the start and at the end says nothing about
the middle: an app pulled forward and handed back in between leaves both ends
identical, and the person still lost their window for that moment. A peer
review said exactly that about the background-menu check, and it was right.

macOS posts `NSWorkspaceDidActivateApplicationNotification` for every
activation. Notifications need a run loop, and a probe running inside the
evaluator has none, so the witness is its own process: it subscribes, writes
each activation as one JSON line the moment it arrives, and stops on SIGTERM.

    with FocusWitness() as witness:
        ...                      # the thing that must not take focus
    assert witness.activations == []

Polling was considered and rejected: a poll every N ms cannot see a change
that lasts less than N ms, and "we sampled and saw nothing" is not the same
claim as "nothing happened".
"""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


class FocusWitness:
    """Context manager around the witness process."""

    def __init__(self, ready_timeout: float = 10.0):
        self.ready_timeout = ready_timeout
        self.path = None
        self.process = None
        self.initial = None
        self.activations = []
        self.windows = []

    def __enter__(self):
        fd, name = tempfile.mkstemp(prefix="nlc-focus-", suffix=".jsonl")
        os.close(fd)
        self.path = Path(name)
        self.process = subprocess.Popen(
            [sys.executable, __file__, str(self.path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Not armed until it says so. An activation before the subscription
        # existed would be missed, and a witness that starts late is one that
        # can only report the part it happened to see.
        deadline = time.monotonic() + self.ready_timeout
        while time.monotonic() < deadline:
            first = self._lines()
            if first and first[0].get("event") == "ready":
                self.initial = first[0].get("frontmost")
                return self
            if self.process.poll() is not None:
                break
            time.sleep(0.05)
        self._stop()
        raise RuntimeError("focus witness did not become ready")

    def __exit__(self, *exc):
        # Let anything already posted reach the run loop before stopping.
        time.sleep(0.3)
        self._stop()
        lines = self._lines()
        self.activations = [line for line in lines if line.get("event") == "activated"]
        self.windows = [line for line in lines if line.get("event") == "window"]
        try:
            self.path.unlink()
        except OSError:
            pass
        return False

    def _stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()

    def _lines(self):
        try:
            text = self.path.read_text()
        except OSError:
            return []
        out = []
        for raw in text.splitlines():
            try:
                out.append(json.loads(raw))
            except ValueError:
                pass            # a line still being written
        return out

    def summary(self):
        return {"initial": self.initial,
                "activations": [a.get("bundle") for a in self.activations],
                "windows": [f'{w.get("owner")}: {w.get("title") or "(untitled)"}'
                            for w in self.windows]}


def _run(path):
    from AppKit import NSWorkspace
    from Foundation import NSObject, NSRunLoop, NSDate
    import objc

    out = open(path, "a", buffering=1)

    def write(record):
        record["t"] = time.monotonic()
        out.write(json.dumps(record) + "\n")

    workspace = NSWorkspace.sharedWorkspace()

    class Observer(NSObject):
        def activated_(self, note):
            app = note.userInfo().get("NSWorkspaceApplicationKey")
            # Named even without a bundle id: "None" hid which app it was.
            write({"event": "activated",
                   "bundle": (app.bundleIdentifier() or f"{app.localizedName()}#{app.processIdentifier()}")
                             if app else None,
                   "pid": int(app.processIdentifier()) if app else None})

    observer = Observer.alloc().init()
    workspace.notificationCenter().addObserver_selector_name_object_(
        observer, "activated:", "NSWorkspaceDidActivateApplicationNotification", None)

    stop = {"now": False}
    signal.signal(signal.SIGTERM, lambda *_: stop.update(now=True))
    front = workspace.frontmostApplication()
    write({"event": "ready",
           "frontmost": (front.bundleIdentifier() or f"{front.localizedName()}#{front.processIdentifier()}")
                        if front else None})
    # Windows are the other way to land on someone's screen. A dialog can
    # appear over their work without any app becoming active, which the
    # activation notification cannot see. Windows persist for at least a frame
    # and usually far longer, so a short poll of the on-screen list is enough
    # here where it was not for activations.
    import Quartz

    def on_screen():
        seen = {}
        for w in Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionOnScreenOnly
                | Quartz.kCGWindowListExcludeDesktopElements,
                Quartz.kCGNullWindowID) or []:
            if w.get("kCGWindowLayer") != 0:
                continue
            seen[w["kCGWindowNumber"]] = (w.get("kCGWindowOwnerName"),
                                         w.get("kCGWindowName"))
        return seen

    known = on_screen()
    loop = NSRunLoop.currentRunLoop()
    while not stop["now"]:
        loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
        now = on_screen()
        for number, (owner, title) in now.items():
            if number not in known:
                write({"event": "window", "owner": owner, "title": title,
                       "number": number})
        known = now
    out.close()


if __name__ == "__main__":
    _run(sys.argv[1])
