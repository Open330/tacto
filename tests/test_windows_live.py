"""Real UI Automation on Windows against Notepad. Opt-in: TACTO_WINDOWS_LIVE=1.

Only an owned Notepad instance started by this test is observed and edited;
nothing is saved and the instance is terminated afterwards.
"""
import os
import re
import subprocess
import sys
import time
import unittest

LIVE = sys.platform == "win32" and os.environ.get("TACTO_WINDOWS_LIVE") == "1"


@unittest.skipUnless(LIVE, "real UI Automation runs only with TACTO_WINDOWS_LIVE=1 on Windows")
class WindowsLiveTests(unittest.TestCase):
    def setUp(self):
        from tacto.windows_driver import WindowsDriver
        self.process = subprocess.Popen(["notepad.exe"])
        self.addCleanup(self.process.kill)
        self.driver = WindowsDriver()
        self.target = f"pid:{self.process.pid}"
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and self.driver._resolve(self.target) is None:
            time.sleep(0.25)
        if self.driver._resolve(self.target) is None:
            # In CI a skip would read as a pass; there it must fail loudly.
            if os.environ.get("TACTO_WINDOWS_LIVE_REQUIRED") == "1":
                self.fail("Notepad window did not appear")
            self.skipTest("Notepad window did not appear (store Notepad may re-parent its process)")

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

    def running(self, driver):
        return [app["pid"] for app in driver._backend.processes() if app["exe"].lower() == "calculatorapp.exe"]

    def test_content_is_observed_and_invoked_through_the_frame_host(self):
        from tacto.windows_driver import WindowsDriver
        driver = WindowsDriver()
        if self.running(driver):
            self.skipTest("a Calculator the person opened is running; it is not touched")
        subprocess.Popen(["calc.exe"])
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and len(self.running(driver)) != 1:
            time.sleep(0.25)
        pids = self.running(driver)
        if len(pids) != 1:
            # Windows Server images (CI) ship no packaged Calculator.
            self.skipTest("packaged Calculator did not appear")
        self.addCleanup(subprocess.run, ["taskkill", "/PID", str(pids[0]), "/F"], capture_output=True)
        target = f"pid:{pids[0]}"
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


if __name__ == "__main__":
    unittest.main()
