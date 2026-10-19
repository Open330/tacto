"""Real UI Automation on Windows against Notepad. Opt-in: TACTO_WINDOWS_LIVE=1.

Only an owned Notepad instance started by this test is observed and edited;
nothing is saved and the instance is terminated afterwards.
"""
import os
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
        from tacto.flow import Selector
        obs = self.driver.observe(self.target)
        self.assertEqual("observation", obs["type"], obs)
        self.assertIn("Window", obs["tree"])
        index = Selector(role="TextArea").resolve(obs["tree"])[0] if " TextArea" in obs["tree"] \
            else Selector(role="TextField").resolve(obs["tree"])[0]
        result = self.driver.act(obs["obs_id"], self.target,
                                 {"name": "set_value", "element_index": index, "value": "tacto windows"})
        self.assertEqual("acted", result["type"], result)
        after = self.driver.wait_until(self.target, lambda tree: "tacto windows" in tree, timeout_ms=5000)
        self.assertEqual("observation", after["type"], after)


if __name__ == "__main__":
    unittest.main()
