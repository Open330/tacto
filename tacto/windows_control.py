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
        """A plain Win32 popup that is never activated.

        Tk activates its window when it maps it, which took the person's
        foreground for as long as the notice stood (seen on Windows 11), so
        the notice is a system dialog-class popup holding a STATIC label: no
        window procedure of ours, no focus. (A bare top-level STATIC paints
        through its parent's DC, the desktop, so it never shows.)
        """
        try:
            import ctypes
            from ctypes import wintypes
            user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
            # Explicit signatures: 64-bit handles must not be truncated.
            user32.CreateWindowExW.argtypes = (
                wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID)
            user32.CreateWindowExW.restype = wintypes.HWND
            user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
            user32.SendMessageW.restype = wintypes.LPARAM
            user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
            user32.DestroyWindow.argtypes = (wintypes.HWND,)
            user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                            wintypes.UINT, wintypes.UINT, wintypes.UINT)
            user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
            user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
            gdi32.GetStockObject.argtypes = (ctypes.c_int,)
            gdi32.GetStockObject.restype = wintypes.HGDIOBJ
            WS_EX = 0x08000000 | 0x8 | 0x80          # NOACTIVATE | TOPMOST | TOOLWINDOW
            WS_POPUP, WS_BORDER, WS_CHILD, WS_VISIBLE = 0x80000000, 0x00800000, 0x40000000, 0x10000000
            SS_CENTER, SS_CENTERIMAGE = 0x1, 0x200
            width, height = 280, 28
            left = user32.GetSystemMetrics(0) - width - 16  # SM_CXSCREEN
            hwnd = user32.CreateWindowExW(WS_EX, "#32770", "Tacto", WS_POPUP | WS_BORDER,
                                          left, 16, width, height, None, None, None, None)
            if not hwnd:
                return
            try:
                label = user32.CreateWindowExW(0, "STATIC", "● Tacto 조작 중 — Esc로 중지",
                                               WS_CHILD | WS_VISIBLE | SS_CENTER | SS_CENTERIMAGE,
                                               0, 0, width - 2, height - 2, hwnd, None, None, None)
                user32.SendMessageW(label, 0x0030, gdi32.GetStockObject(17), 1)  # WM_SETFONT, DEFAULT_GUI_FONT
                user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
                msg = wintypes.MSG()
                while not self._done.wait(0.05):
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
            finally:
                user32.DestroyWindow(hwnd)  # on the thread that made it
        except Exception:
            return  # best effort: a missing notice never blocks an authorized run

    def __exit__(self, *exc):
        self._done.set()
        for thread in self._threads:
            thread.join(timeout=3)
        return False


def wait(seconds: float, *, sleep=time.sleep, clock=time.monotonic) -> bool:
    """Sleep up to `seconds`; True when Esc ended the wait early."""
    deadline = clock() + seconds
    while clock() < deadline:
        if stopped():
            return True
        sleep(min(0.05, max(0.0, deadline - clock())))
    return stopped()
