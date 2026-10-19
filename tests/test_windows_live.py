"""Real UI Automation on Windows against Notepad and Calculator.

Opt-in: TACTO_WINDOWS_LIVE=1, run in the signed-in desktop session. Only
instances this test starts are observed and edited; nothing is saved, they
are terminated afterwards, and the unsaved-tab state Windows 11 Notepad keeps
for them is removed so the test text never reappears in the person's Notepad.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import unittest

LIVE = sys.platform == "win32" and os.environ.get("TACTO_WINDOWS_LIVE") == "1"


def _owned(test, driver, command, exe):
    """Start `command` and return the one new process named `exe`, or skip.

    Windows 11 Notepad and Calculator are packaged: the started process is a
    stub and the window belongs to a new one. An app the person already has
    open may take the new window into their own process, so it is never used.
    """
    def running():
        return {app["pid"] for app in driver._backend.processes() if app["exe"].lower() == exe}
    if running():
        test.skipTest(f"{exe} the person opened is running; it is not touched")
    stub = subprocess.Popen([command])
    test.addCleanup(lambda: (stub.kill(), stub.wait()))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and len(running()) != 1:
        time.sleep(0.25)
    pids = running()
    if len(pids) != 1:
        return None
    pid = pids.pop()
    test.addCleanup(subprocess.run, ["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    return f"pid:{pid}"


NOTEPAD_TABS = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Packages/Microsoft.WindowsNotepad_8wekyb3d8bbwe/LocalState/TabState"


def _forget_new_tabs(test):
    """Remove only the tab-state files that appear during this test."""
    before = set(NOTEPAD_TABS.glob("*")) if NOTEPAD_TABS.is_dir() else set()

    def forget():
        time.sleep(0.5)  # the killed process may still be flushing
        for path in set(NOTEPAD_TABS.glob("*")) - before if NOTEPAD_TABS.is_dir() else ():
            path.unlink(missing_ok=True)
    test.addCleanup(forget)


@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsLiveTests(unittest.TestCase):
    def setUp(self):
        from tacto.windows_driver import WindowsDriver
        self.driver = WindowsDriver()
        _forget_new_tabs(self)  # registered first, so it runs after the kill
        self.target = _owned(self, self.driver, "notepad.exe", "notepad.exe")
        if self.target is None:
            # In CI a skip would read as a pass; there it must fail loudly.
            if os.environ.get("TACTO_WINDOWS_LIVE_REQUIRED") == "1":
                self.fail("Notepad window did not appear")
            self.skipTest("Notepad window did not appear")

    def test_observe_set_value_and_read_back_without_input(self):
        obs = self.driver.observe(self.target)
        self.assertEqual("observation", obs["type"], obs)
        self.assertIn("Window", obs["tree"])
        # The app's own front window is marked even while another app has focus.
        self.assertEqual(1, obs["tree"].count("[focused]"), obs["tree"])
        # A role alone is not a selector for acting, so pick the editor by line.
        editors = re.findall(r"^\s*(\d+) (?:TextArea|TextField)\b(?!.*\[readonly\])", obs["tree"], re.M)
        self.assertTrue(editors, obs["tree"])
        index = int(editors[0])
        result = self.driver.act(obs["obs_id"], self.target,
                                 {"name": "set_value", "element_index": index, "value": "tacto windows"})
        self.assertEqual("acted", result["type"], result)
        after = self.driver.wait_until(self.target, lambda tree: "tacto windows" in tree, timeout_ms=5000)
        self.assertEqual("observation", after["type"], after)



@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsPackagedAppLiveTests(unittest.TestCase):
    """Calculator: a packaged app whose frame belongs to ApplicationFrameHost."""

    def test_content_is_observed_and_invoked_through_the_frame_host(self):
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        target = _owned(self, driver, "calc.exe", "calculatorapp.exe")
        if target is None:
            # Windows Server images (CI) ship no packaged Calculator.
            self.skipTest("packaged Calculator did not appear")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and "num1Button" not in driver.observe(target).get("tree", ""):
            time.sleep(0.25)
        obs = driver.observe(target)
        self.assertEqual("observation", obs["type"], obs)
        from tacto.flow import Selector
        index = Selector(identifier="num1Button").resolve(obs["tree"])[0]
        result = driver.act(obs["obs_id"], target, {"name": "click", "element_index": index})
        self.assertEqual("acted", result["type"], result)
        after = driver.wait_until(target, lambda tree: re.search(r"Display is 1\b", tree), timeout_ms=5000)
        self.assertEqual("observation", after["type"], after)



@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsModalAndNoticeLiveTests(unittest.TestCase):
    def test_owned_message_box_is_seen_as_modal(self):
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        code = ("import os,tkinter as t\nfrom tkinter import messagebox\nprint(os.getpid(),flush=True)\n"
                "r=t.Tk();r.title('tacto modal owner')\n"
                "r.after(1500,lambda:messagebox.showinfo('tacto modal','modal'));r.mainloop()")
        # The child's own pid: a venv launcher may stand between us and it.
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (child.kill(), child.wait(), child.stdout.close()))
        pid = int(child.stdout.readline())
        self.addCleanup(subprocess.run, ["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and driver._resolve(f"pid:{pid}") is None:
            time.sleep(0.2)
        self.assertIs(False, driver._backend.modal_present(pid))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not driver._backend.modal_present(pid):
            time.sleep(0.2)
        self.assertIs(True, driver._backend.modal_present(pid))
        tree = driver.observe(f"pid:{pid}")["tree"]
        self.assertIn('Window  Title: "tacto modal" [focused]', tree)
        self.assertIn('Window  Title: "tacto modal owner" [disabled]', tree)

    def test_notice_never_takes_the_foreground(self):
        import ctypes
        from ctypes import wintypes
        from tacto import windows_control
        from tacto.uia_backend import _class, _owner
        user32 = ctypes.WinDLL("user32")
        user32.GetForegroundWindow.restype = wintypes.HWND
        before = user32.GetForegroundWindow()
        with windows_control.control():
            time.sleep(0.5)
            shown = [_class(h) for h in __import__("tacto.uia_backend").uia_backend.UIABackend()
                     ._windows_by_pid().get(os.getpid(), [])]
            foreground = [user32.GetForegroundWindow() for _ in range(5) if not time.sleep(0.1)]
        self.assertEqual(["#32770"], shown)
        self.assertNotIn(os.getpid(), {_owner(h) for h in foreground if h})
        self.assertEqual({before}, set(foreground))


if __name__ == "__main__":
    unittest.main()
