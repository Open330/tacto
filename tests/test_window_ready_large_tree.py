"""Full Swift readiness traversal over wide trees; no GUI events are posted."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from tests.test_window_ready_swift import HARNESS

ROOT = Path(__file__).resolve().parents[1]
PREFIX = HARNESS.split("let desktop: Attrs =", 1)[0].replace(
    "maxNodes: Int = 60", "maxNodes: Int = nativeWindowReadinessNodeLimit")
CASES = r'''
func wide(_ count: Int) -> [Int32: Attrs] {
    let leaves = (0..<count).map { Int32(100000 + $0) }
    var tree: [Int32: Attrs] = [42: ["AXWindows": [Int32(1)]], 1: win(leaves)]
    for leaf in leaves { tree[leaf] = node("AXCell") }
    return tree
}
var out: [String:String] = [:]
let large = wide(6000)
out["wide tree"] = run(large)
var modal = large; modal[105999]!["AXModal"] = true
out["last child modal"] = run(modal)
var sheet = large; sheet[105999]!["AXRole"] = "AXSheet"
out["last child sheet"] = run(sheet)
var deadline = large; deadline[105999]!["AXRole"] = "deadline"
out["last child deadline"] = run(deadline)
out["exact capacity"] = run(wide(nativeWindowReadinessNodeLimit - 1))
out["over capacity"] = run(wide(nativeWindowReadinessNodeLimit))
out["explicit small capacity"] = run(large, maxNodes:5000)
var cycle: [Int32: Attrs] = [42: ["AXWindows": [Int32(1)]],
    1: win([2]), 2:node("AXGroup",[2])]
out["cycle exceeds depth"] = run(cycle)
var distributed: [Int32: Attrs] = [42: ["AXWindows": [Int32(1)]],
    1:win([2,3]), 2:node("AXGroup",[4,5]), 3:node("AXCell"),
    4:node("AXCell"), 5:node("AXCell")]
out["pending siblings count toward capacity"] = run(distributed,maxNodes:4)
print(String(data:try! JSONSerialization.data(withJSONObject:out),encoding:.utf8)!)
'''


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("xcrun"), "macOS Swift required")
class WindowReadyLargeTreeTests(unittest.TestCase):
    def test_capacity_and_adversarial_tail_nodes_in_single_and_batch_modes(self):
        expected = {
            "wide tree": "ok",
            "last child modal": "modal or unknown element",
            "last child sheet": "attached sheet or malformed role",
            "last child deadline": "readiness budget exceeded: deadline",
            "exact capacity": "ok",
            "over capacity": "readiness budget exceeded: nodes",
            "explicit small capacity": "readiness budget exceeded: nodes",
            "cycle exceeds depth": "readiness budget exceeded: nodes",
            "pending siblings count toward capacity": "readiness budget exceeded: nodes",
        }
        batch = '''read: read, readBatch: { node, names in
            let values: [CFTypeRef] = try names.map { name in
                let (status, value) = try read(node, name)
                if status == .success { return value! }
                var error = status
                return AXValueCreate(.axError, &error)!
            }
            return (.success, values as CFArray)
        }, isFinder: finder)'''
        for mode, prefix in (("single", PREFIX), ("batch", PREFIX.replace(
                "read: read, isFinder: finder)", batch))):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                src, exe = Path(directory) / "main.swift", Path(directory) / "check"
                src.write_text(prefix + CASES)
                built = subprocess.run(["xcrun", "swiftc", "-warnings-as-errors", str(src),
                    str(ROOT / "helper/WindowInput.swift"), str(ROOT / "helper/WindowDispatch.swift"),
                    str(ROOT / "helper/FrontmostProcess.swift"), "-o", str(exe)],
                    capture_output=True, text=True, timeout=90)
                self.assertEqual(built.returncode, 0, built.stderr)
                result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
