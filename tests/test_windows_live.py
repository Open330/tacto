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


def _processes(exe):
    """Every process running `exe`, windowed or not."""
    import ctypes
    from ctypes import wintypes
    from tacto.uia_backend import _executable
    pids = (wintypes.DWORD * 8192)()
    size = wintypes.DWORD()
    ctypes.WinDLL("psapi").EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(size))
    return {pid for pid in pids[:size.value // ctypes.sizeof(wintypes.DWORD)]
            if pid and (_executable(pid) or "").lower() == exe}


NOTEPAD_APP = "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
CALCULATOR_APP = "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"


def _activate(app_id):
    """Start a packaged app; the pid of the process that handled this start."""
    from ctypes import POINTER, c_int
    from ctypes.wintypes import DWORD, LPCWSTR
    import comtypes
    import comtypes.client
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IApplicationActivationManager(IUnknown):
        _iid_ = GUID("{2e941141-7f97-4756-ba1d-9decde894a3d}")
        _methods_ = [COMMETHOD([], HRESULT, "ActivateApplication",
                               (["in"], LPCWSTR, "appUserModelId"), (["in"], LPCWSTR, "arguments"),
                               (["in"], c_int, "options"), (["out"], POINTER(DWORD), "processId"))]
    try:
        manager = comtypes.client.CreateObject(GUID("{45BA127D-10A8-46EA-8AB7-56EA9078943C}"),
                                               interface=IApplicationActivationManager)
        return manager.ActivateApplication(app_id, None, 0)
    except (OSError, comtypes.COMError):
        return None  # not installed here (Windows Server images)


def _family(roots):
    """These processes and every process they started, by parent id."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = kernel32.Process32NextW.argtypes = (wintypes.HANDLE,
                                                                           ctypes.POINTER(PROCESSENTRY32W))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    children = {}
    try:
        entry = PROCESSENTRY32W(dwSize=ctypes.sizeof(PROCESSENTRY32W))
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, set()).add(entry.th32ProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    family, todo = set(roots), list(roots)
    while todo:
        for child in children.get(todo.pop(), set()) - family:
            family.add(child)
            todo.append(child)
    return family


def _owned(test, driver, exe, *, app_id, command):
    """Start the app and return its one windowed process this test started.

    Ownership is causal: the process the start returned (the packaged app's
    activation, else our own child process) and the processes it started.
    Only those are ended afterwards, never another instance that happens to
    share the name. Any instance running before the start could take the
    new window, so the test then touches nothing.
    """
    if _processes(exe):
        test.skipTest(f"{exe} is already running; it is not touched")
    root = _activate(app_id)
    if root is None:
        stub = subprocess.Popen([command])
        test.addCleanup(lambda: (stub.kill(), stub.wait()))
        root = stub.pid
    family = {root}

    def end_family():
        # What is already known to be ours first, so a failing lookup of
        # later children cannot leave those running.
        try:
            members = _family(family)
        except Exception:
            members = set()
        for pid in family | members:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    test.addCleanup(end_family)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        family |= _family(family)
        windowed = [pid for pid in family if driver._backend.top_windows(pid)]
        if len(windowed) == 1:
            return f"pid:{windowed[0]}"
        time.sleep(0.25)
    return None


NOTEPAD_TABS = Path(os.environ.get("LOCALAPPDATA", "")) / \
    "Packages/Microsoft.WindowsNotepad_8wekyb3d8bbwe/LocalState/TabState"


def _forget_marked_tabs(test, marker):
    """Remove the tab state Notepad kept for text this test typed, and only that.

    A new file is ours only if it holds the marker the test wrote (Notepad
    stores tab text as UTF-16); files sharing its id go with it. Anything
    else, even a file that appeared meanwhile, belongs to the person.
    """
    before = set(NOTEPAD_TABS.glob("*")) if NOTEPAD_TABS.is_dir() else set()
    needle = marker.encode("utf-16-le")

    def forget():
        time.sleep(0.5)  # the killed process may still be flushing
        new = set(NOTEPAD_TABS.glob("*")) - before if NOTEPAD_TABS.is_dir() else set()
        ours = {path.name.split(".")[0] for path in new if needle in path.read_bytes()}
        for path in new:
            if path.name.split(".")[0] in ours:
                path.unlink(missing_ok=True)
    test.addCleanup(forget)


class MarkedTabCleanupTests(unittest.TestCase):
    """Runs everywhere: the cleanup must never take the person's tab state."""

    def test_only_files_holding_this_tests_marker_are_removed(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as folder:
            tabs = Path(folder)
            (tabs / "existing.bin").write_bytes("person's old tab".encode("utf-16-le"))
            with patch(__name__ + ".NOTEPAD_TABS", tabs):
                _forget_marked_tabs(self, "tacto-live-x")
                (tabs / "ours.bin").write_bytes("tacto-live-x".encode("utf-16-le"))
                (tabs / "ours.0.bin").write_bytes(b"\x00\x01")
                (tabs / "meanwhile.bin").write_bytes("person typed this".encode("utf-16-le"))
                with patch(__name__ + ".time.sleep"):
                    self.doCleanups()
            self.assertEqual(["existing.bin", "meanwhile.bin"], sorted(p.name for p in tabs.iterdir()))


@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsLiveTests(unittest.TestCase):
    def setUp(self):
        import uuid
        from tacto.windows_driver import WindowsDriver
        self.driver = WindowsDriver()
        self.marker = "tacto-live-" + uuid.uuid4().hex
        # Registered before the process exists: cleanups run last-in first-out,
        # so this runs after the kill, once Notepad has written its state.
        _forget_marked_tabs(self, self.marker)
        self.target = _owned(self, self.driver, "notepad.exe", app_id=NOTEPAD_APP, command="notepad.exe")
        if self.target is None:
            # In CI a skip would read as a pass; there it must fail loudly.
            if os.environ.get("TACTO_WINDOWS_LIVE_REQUIRED") == "1":
                self.fail("Notepad window did not appear")
            self.skipTest("Notepad window did not appear")
        # A new process is not yet a new document: Notepad may reopen the
        # person's unsaved tabs. Only one empty tab is this test's to write.
        obs = self.driver.observe(self.target)
        self.assertEqual("observation", obs["type"], obs)
        tabs = re.findall(r"^\s*\d+ RadioButton\b", obs["tree"], re.M)
        editor = re.search(rf"^\s*{self.editor(obs)} .*$", obs["tree"], re.M).group()
        if len(tabs) > 1 or "Value:" in editor:
            self.skipTest("Notepad reopened the person's tabs; they are not touched")
        # Mark this tab first, so its saved state can be told from the person's.
        self.set_text(self.marker)

    def editor(self, obs):
        # A role alone is not a selector for acting, so pick the editor by line.
        editors = re.findall(r"^\s*(\d+) (?:TextArea|TextField)\b(?!.*\[readonly\])", obs["tree"], re.M)
        self.assertTrue(editors, obs["tree"])
        return int(editors[0])

    def set_text(self, text):
        obs = self.driver.observe(self.target)
        self.assertEqual("observation", obs["type"], obs)
        result = self.driver.act(obs["obs_id"], self.target,
                                 {"name": "set_value", "element_index": self.editor(obs), "value": text})
        self.assertEqual("acted", result["type"], result)

    def test_observe_set_value_and_read_back_without_input(self):
        obs = self.driver.observe(self.target)
        self.assertEqual("observation", obs["type"], obs)
        self.assertIn("Window", obs["tree"])
        # The app's own front window is marked even while another app has focus.
        self.assertEqual(1, obs["tree"].count("[focused]"), obs["tree"])
        self.set_text("tacto windows " + self.marker)
        after = self.driver.wait_until(self.target, lambda tree: "tacto windows" in tree, timeout_ms=5000)
        self.assertEqual("observation", after["type"], after)

    def test_menu_opens_save_as_which_is_seen_as_modal_then_cancelled(self):
        def step(pattern):
            found = self.driver.wait_until(self.target, lambda tree: re.search(pattern, tree, re.M), timeout_ms=5000)
            self.assertEqual("observation", found["type"], (pattern, found.get("last_obs", found).get("tree")))
            index = int(re.search(pattern, found["tree"], re.M).group(1))
            result = self.driver.act(found["obs_id"], self.target, {"name": "click", "element_index": index})
            self.assertEqual("acted", result["type"], result)
        pid = int(self.target[4:])
        self.assertIs(False, self.driver._backend.modal_present(pid))
        # Windows 11 Notepad names its menu items (Identifier: File, "Save as");
        # the classic Win32 Notepad on Windows Server images does not ("File",
        # "Save As...").
        step(r"^\s*(\d+) MenuItem\s+Title: (?:File|파일)(?:, Identifier: File)?$")
        step(r"^\s*(\d+) MenuItem\s+Title: (?:Save [Aa]s(?:\.\.\.)?|다른 이름으로 저장(?:\.\.\.)?)\b")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not self.driver._backend.modal_present(pid):
            time.sleep(0.2)
        self.assertIs(True, self.driver._backend.modal_present(pid), self.driver.observe(self.target).get("tree"))
        step(r"^\s*(\d+) Button\s+Title: (?:Cancel|취소)\b")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.driver._backend.modal_present(pid):
            time.sleep(0.2)
        self.assertIs(False, self.driver._backend.modal_present(pid))



@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsPackagedAppLiveTests(unittest.TestCase):
    """Calculator: a packaged app whose frame belongs to ApplicationFrameHost."""

    def test_content_is_observed_and_invoked_through_the_frame_host(self):
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        target = _owned(self, driver, "calculatorapp.exe", app_id=CALCULATOR_APP, command="calc.exe")
        if target is None:
            # Windows Server images (CI) ship no packaged Calculator.
            self.skipTest("packaged Calculator did not appear")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and "num1Button" not in driver.observe(target).get("tree", ""):
            time.sleep(0.25)
        obs = driver.observe(target)
        self.assertEqual("observation", obs["type"], obs)
        if "num1Button" not in obs["tree"]:
            # Another Calculator build (a server image's first launch, say)
            # exposes no keypad here; say what it showed instead.
            self.skipTest("Calculator keypad not exposed: " + " | ".join(obs["tree"].splitlines()[:8]))
        from tacto.flow import Selector
        index = Selector(identifier="num1Button").resolve(obs["tree"])[0]
        result = driver.act(obs["obs_id"], target, {"name": "click", "element_index": index})
        self.assertEqual("acted", result["type"], result)
        # "Display is 1" in English, "표시는 1" in Korean: match the result
        # element by its identifier, whatever the display language.
        after = driver.wait_until(target, lambda tree: re.search(
            r"Title: [^,\n]*\b1, Identifier: CalculatorResults\b", tree), timeout_ms=5000)
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
        # The guarantee is that the notice never takes the foreground. On the
        # person's desktop they may switch windows meanwhile, so the window in
        # front is not required to stay the same; what was there is reported.
        seen = [(_owner(h), _class(h)) if h else None for h in [before, *foreground]]
        self.assertNotIn(os.getpid(), {_owner(h) for h in foreground if h}, (os.getpid(), seen))


if __name__ == "__main__":
    unittest.main()
