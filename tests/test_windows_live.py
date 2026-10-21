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

    def test_in_window_save_prompt_is_seen_as_modal(self):
        """Windows 11 Notepad asks "save changes?" inside its own window (a
        content dialog), so its owner is never disabled; UI Automation must
        still report it modal (jiun-home, Korean UI, 2026-10-21)."""
        pid = int(self.target[4:])
        self.set_text("tacto prompt " + self.marker)
        obs = self.driver.observe(self.target)
        menu = re.search(r"^\s*(\d+) MenuItem\s+.*Identifier: File\b", obs["tree"], re.M)
        if menu is None:
            self.skipTest("classic Notepad: no tab to close")
        # Before the menu: its open popup is itself reported modal.
        self.assertIs(False, self.driver._backend.modal_present(pid))
        self.assertEqual("acted", self.driver.act(obs["obs_id"], self.target,
                         {"name": "click", "element_index": int(menu.group(1))})["type"])
        close = self.driver.wait_until(self.target, lambda tree: re.search(
            r"^\s*\d+ MenuItem\s+Title: (?:Close tab|탭 닫기)$", tree, re.M), timeout_ms=5000)
        if close.get("type") != "observation":
            self.skipTest("this Notepad has no Close tab command")
        index = int(re.search(r"^\s*(\d+) MenuItem\s+Title: (?:Close tab|탭 닫기)$", close["tree"], re.M).group(1))
        self.assertEqual("acted", self.driver.act(close["obs_id"], self.target,
                         {"name": "click", "element_index": index})["type"])
        prompt = self.driver.wait_until(self.target, lambda tree: "Identifier: SecondaryButton" in tree,
                                        timeout_ms=5000)
        self.assertEqual("observation", prompt["type"], prompt.get("last_obs", prompt).get("tree"))
        self.assertIs(True, self.driver._backend.modal_present(pid), prompt["tree"])
        from tacto.uia_backend import _disables_its_owner
        # Not the owner rule: nothing is disabled; UI Automation sees the dialog.
        self.assertFalse(any(_disables_its_owner(h, pid)
                             for h in self.driver._backend._windows_by_pid().get(pid, [])))
        dont = int(re.search(r"^\s*(\d+) Button\s+.*Identifier: SecondaryButton\b", prompt["tree"], re.M).group(1))
        self.assertEqual("acted", self.driver.act(prompt["obs_id"], self.target,
                         {"name": "click", "element_index": dont})["type"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and self.driver._backend.modal_present(pid):
            time.sleep(0.2)
        self.assertIs(False, self.driver._backend.modal_present(pid))

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
    """Calculator, through whichever build the machine has.

    Windows 11 ships the packaged app, whose frame belongs to
    ApplicationFrameHost; Windows Server images (CI) start the classic Win32
    calculator instead, whose controls carry numeric identifiers. Both are
    driven the same way: invoke the "1" key, then read the result back.
    """
    # (key line, result line) per build. The result is matched by identifier,
    # not by its words: "Display is 1" in English, "표시는 1" in Korean.
    BUILDS = {
        "packaged": (r"^\s*(\d+) Button\s+Title: [^,\n]*, Identifier: num1Button\b",
                     r"Title: [^,\n]*\b1, Identifier: CalculatorResults\b"),
        "classic": (r"^\s*(\d+) Button\s+Title: 1(?:, Identifier: \d+)?$",
                    r"Title: Result, Value: 1, Identifier: 150\b"),
    }

    def build(self, tree):
        return next((name for name, (key, _) in self.BUILDS.items() if re.search(key, tree, re.M)), None)

    def test_content_is_observed_and_invoked(self):
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        target = _owned(self, driver, "calculatorapp.exe", app_id=CALCULATOR_APP, command="calc.exe")
        if target is None:
            self.skipTest("no Calculator appeared")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.build(driver.observe(target).get("tree", "")) is None:
            time.sleep(0.25)
        obs = driver.observe(target)
        self.assertEqual("observation", obs["type"], obs)
        build = self.build(obs["tree"])
        if build is None:
            self.skipTest("Calculator keypad not exposed: " + " | ".join(obs["tree"].splitlines()[:40]))
        key, shown = self.BUILDS[build]
        index = int(re.search(key, obs["tree"], re.M).group(1))
        result = driver.act(obs["obs_id"], target, {"name": "click", "element_index": index})
        self.assertEqual("acted", result["type"], (build, result))
        after = driver.wait_until(target, lambda tree: re.search(shown, tree), timeout_ms=5000)
        self.assertEqual("observation", after["type"],
                         (build, "\n".join(after.get("last_obs", after).get("tree", "").splitlines()[:40])))
        print(f"\n  calculator build: {build}", file=sys.stderr)


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

    def _fixture(self, code):
        """An owned Tk process running `code`; returns (driver, pid)."""
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (child.kill(), child.wait(), child.stdout.close()))
        pid = int(child.stdout.readline())
        self.addCleanup(subprocess.run, ["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and driver._resolve(f"pid:{pid}") is None:
            time.sleep(0.2)
        return driver, pid, child

    def _windows(self, driver, pid):
        from tacto.uia_backend import _class, _disables_its_owner
        return [(_class(h), _disables_its_owner(h, pid)) for h in driver._backend._windows_by_pid().get(pid, [])]

    def test_a_common_save_dialog_is_modal_by_the_owner_it_disables(self):
        """The CI 39564444225 shape: the file dialog is listed at the top level."""
        from unittest import mock
        from tacto import uia_backend
        driver, pid, _ = self._fixture(
            "import os,tkinter as t\nfrom tkinter import filedialog\nprint(os.getpid(),flush=True)\n"
            "r=t.Tk();r.title('tacto save owner')\n"
            "r.after(1500,lambda:filedialog.asksaveasfilename(parent=r,title='tacto save'));r.mainloop()")
        self.assertIs(False, driver._backend.modal_present(pid))
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not any(e for _, e in self._windows(driver, pid)):
            time.sleep(0.2)
        windows = self._windows(driver, pid)
        self.assertIn(("#32770", True), windows, windows)
        self.assertIs(True, driver._backend.modal_present(pid))
        # Diagnostic only: what UI Automation alone said at this moment.
        with mock.patch.object(uia_backend, "_disables_its_owner", lambda *a, **k: False):
            uia_only = driver._backend.modal_present(pid)
        print(f"\n  save dialog: windows={windows} uia_only={uia_only}", file=sys.stderr)

    def test_a_modeless_owned_find_dialog_is_not_modal(self):
        driver, pid, child = self._fixture(
            "import os,ctypes,tkinter as t\nfrom ctypes import wintypes as w\nprint(os.getpid(),flush=True)\n"
            "class FR(ctypes.Structure):_fields_=[('size',w.DWORD),('owner',w.HWND),('inst',w.HINSTANCE),"
            "('flags',w.DWORD),('find',w.LPWSTR),('repl',w.LPWSTR),('findlen',w.WORD),('repllen',w.WORD),"
            "('data',w.LPARAM),('hook',ctypes.c_void_p),('templ',w.LPCWSTR)]\n"
            "buf=ctypes.create_unicode_buffer(80);r=t.Tk();r.title('tacto modeless owner')\n"
            "u=ctypes.WinDLL('user32');u.GetParent.restype=w.HWND;u.GetParent.argtypes=(w.HWND,)\n"
            "c=ctypes.WinDLL('comdlg32');c.FindTextW.restype=w.HWND;c.FindTextW.argtypes=(ctypes.POINTER(FR),)\n"
            "def show():\n global fr\n fr=FR(ctypes.sizeof(FR),u.GetParent(r.winfo_id()),None,0,"
            "ctypes.cast(buf,w.LPWSTR),None,80,0,0,None,None)\n print('find',int(c.FindTextW(ctypes.byref(fr)) or 0),flush=True)\n"
            "r.after(1500,show);r.mainloop()")
        self.assertNotEqual("find 0", child.stdout.readline().strip(), "FindTextW did not open")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and [c for c, _ in self._windows(driver, pid)].count("#32770") < 1:
            time.sleep(0.2)
        windows = self._windows(driver, pid)
        self.assertIn("#32770", [c for c, _ in windows], windows)
        self.assertEqual([], [w for w in windows if w[1]], windows)
        self.assertIs(False, driver._backend.modal_present(pid), windows)

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
