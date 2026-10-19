"""Stop key and a small "Tacto is working" notice on Windows.

Neither asks the person for anything. Esc is polled (no hook, no permission)
and only ever stops a run; the notice never takes focus, so the person keeps
working in their own window. When the notice cannot be drawn the run goes on:
the person already authorized Tacto, and Esc still stops it.
"""

from __future__ import annotations

import sys
import threading
import time

VK_ESCAPE = 0x1B
_stop = threading.Event()


def stopped() -> bool:
    """Has the person pressed Esc during this run?"""
    return _stop.is_set()


def _escape_down(user32) -> bool:
    # High bit: the key is down now. Polling reads state; it hooks nothing.
    return bool(user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)


class control:
    """Watch for Esc and show the notice while one command drives an app."""

    def __init__(self, *, notice=True, poll=0.05):
        self._notice = notice
        self._poll = poll
        self._done = threading.Event()
        self._threads = []

    def __enter__(self):
        _stop.clear()
        if sys.platform != "win32":
            return self
        import ctypes
        user32 = ctypes.WinDLL("user32")
        user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
        user32.GetAsyncKeyState.restype = ctypes.c_short
        # Ignore an Esc that was already down when the run started.
        held = _escape_down(user32)

        def watch():
            nonlocal held
            while not self._done.wait(self._poll):
                down = _escape_down(user32)
                if down and not held:
                    _stop.set()
                    return
                held = down

        self._start(watch)
        if self._notice:
            self._start(self._show_notice)
        return self

    def _start(self, target):
        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _show_notice(self):
        try:
            import ctypes
            import tkinter
            from ctypes import wintypes
            root = tkinter.Tk()
            # Hidden until its window is marked non-activating, so it never
            # takes focus even for a moment.
            root.withdraw()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg="#1f1a17")
            tkinter.Label(root, text="● Tacto 조작 중 — Esc로 중지", fg="#fff4ec", bg="#1f1a17",
                          padx=10, pady=4).pack()
            root.update_idletasks()
            width = root.winfo_reqwidth()
            root.geometry(f"+{root.winfo_screenwidth() - width - 16}+16")
            user32 = ctypes.WinDLL("user32")
            # Explicit signatures: a 64-bit HWND must not be truncated.
            user32.GetParent.argtypes = (wintypes.HWND,)
            user32.GetParent.restype = wintypes.HWND
            user32.GetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.GetWindowLongW.restype = wintypes.LONG
            user32.SetWindowLongW.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.LONG)
            user32.SetWindowLongW.restype = wintypes.LONG
            user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
            hwnd = user32.GetParent(root.winfo_id())
            GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = -20, 0x08000000, 0x80, 0x8
            style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)
            SW_SHOWNOACTIVATE = 4
            user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            root.update()

            def tick():
                if self._done.is_set():
                    root.destroy()
                else:
                    root.after(100, tick)
            root.after(100, tick)
            root.mainloop()
        except Exception:
            return  # best effort: a missing notice never blocks an authorized run

    def __exit__(self, *exc):
        self._done.set()
        for thread in self._threads:
            thread.join(timeout=1)
        return False


def wait(seconds: float, *, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Sleep up to `seconds`; True when Esc ended the wait early."""
    deadline = clock() + seconds
    while clock() < deadline:
        if stopped():
            return True
        sleep(min(0.05, max(0.0, deadline - clock())))
    return stopped()
