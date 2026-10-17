"""nativeRequireWindowReady, run for real against a fake AX tree.

2026-10-09 (Codex review of req 5c): strict full check stays the default;
budget/deadline is its own reason; non-window tops may lack AXModal; and the
only Finder's measured desktop may lack AXModal. A role-only file-list
prune was tried and withdrawn (Codex review of 97398cb): the whole tree is
walked, and a large window is reported as a budget overrun."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
import Foundation
import AppKit
import ApplicationServices
func el(_ n: Int32) -> AXUIElement { AXUIElementCreateApplication(n) }
func pidOf(_ e: AXUIElement) -> Int32 { var p: pid_t = 0; AXUIElementGetPid(e, &p); return p }
typealias Attrs = [String: Any]
struct Deadline: Error {}
// pid 42 is the application; others are nodes.
func run(_ tree: [Int32: Attrs], target: Int32 = 1, finder: Bool = true, maxNodes: Int = 60,
         windowsAfter: [Int32]? = nil) -> String {
    var windowReads = 0, sheetReads: [Int32: Int] = [:]
    let read: AXRead = { e, name in
        let n = pidOf(e)
        if n == 42 && name == "AXWindows" {
            windowReads += 1
            let list = (windowReads > 1 ? windowsAfter : nil) ?? (tree[42]!["AXWindows"] as! [Int32])
            return (.success, list.map { el($0) } as CFArray)
        }
        if name == "AXSheets" { sheetReads[n, default: 0] += 1 }
        if name == "AXSheets", let late = tree[n]?["lateSheets"] as? [Int32], sheetReads[n]! > 1 {
            return (.success, late.map { el($0) } as CFArray)
        }
        guard let attrs = tree[n], let v = attrs[name] else { return (.attributeUnsupported, nil) }
        if let one = v as? Int32 { return (.success, el(one)) }
        if let err = v as? AXError { return (err, nil) }
        if let s = v as? String, s == "deadline" { throw NativeWindowInputError.refused("window observation deadline expired") }
        if let b = v as? Bool { return (.success, (b ? kCFBooleanTrue : kCFBooleanFalse)!) }
        if let list = v as? [Int32] { return (.success, list.map { el($0) } as CFArray) }
        return (.success, v as! String as CFString)
    }
    do {
        try nativeRequireWindowReady(el(target), 42, maxNodes: maxNodes, read: read, isFinder: finder)
        return "ok"
    } catch NativeWindowInputError.refused(let why) { return why } catch { return "other" }
}
func win(_ kids: [Int32], modal: Any = false) -> Attrs {
    ["AXRole": "AXWindow", "AXModal": modal, "AXMinimized": false, "AXChildren": kids]
}
func node(_ role: String, _ kids: [Int32] = []) -> Attrs { ["AXRole": role, "AXChildren": kids] }
// A big file list: container 20 with 200 rows (each with a cell)
func bigList(_ base: [Int32: Attrs], container: Int32 = 20) -> [Int32: Attrs] {
    var t = base
    let rows = (0..<200).map { Int32(1000 + $0) }
    t[container] = node("AXOutline", rows)
    for r in rows { t[r] = node("AXRow", [r + 5000]); t[r + 5000] = node("AXCell") }
    return t
}
let desktop: Attrs = ["AXRole": "AXScrollArea", "AXChildren": [Int32](), "AXParent": Int32(42)]
// target window 1 (small); other window 2 (small); desktop 9
let base: [Int32: Attrs] = [42: ["AXWindows": [Int32(1), Int32(2), Int32(9)]],
    1: win([3]), 3: node("AXButton"), 2: win([4]), 4: node("AXGroup"), 9: desktop]
func with(_ t: [Int32: Attrs], _ n: Int32, _ k: String, _ v: Any) -> [Int32: Attrs] { var t = t; t[n]![k] = v; return t }
var out: [String: String] = [:]
out["small windows and the desktop pass"] = run(base)
var big = bigList(with(base, 2, "AXChildren", [Int32(20)]))
out["Finder: a big list in another window exceeds the budget"] = run(big)
out["not Finder: the same"] = run(big, finder: false)
var underList = big; underList[1000] = node("AXRow", [34]); underList[34] = node("AXSheet")
out["a sheet below another window's list is reached (no prune)"] = run(underList, maxNodes: 2000)
var deepSheet = base; deepSheet[4] = node("AXGroup", [30]); deepSheet[30] = node("AXGroup", [31]); deepSheet[31] = node("AXSheet")
out["a deep sheet refuses"] = run(deepSheet)
out["a top window that is modal refuses"] = run(with(base, 2, "AXModal", true))
out["a top window without AXModal refuses"] = run({ var t = base; t[2]!.removeValue(forKey: "AXModal"); return t }())
out["the desktop saying modal refuses"] = run(with(base, 9, "AXModal", true))
out["a dialog top without AXModal refuses"] = run(with(base, 9, "AXRole", "AXDialog"))
out["an unknown top without AXModal refuses"] = run(with(base, 9, "AXRole", "AXUnknown"))
out["a scroll area top with another parent refuses"] = run(with(base, 9, "AXParent", Int32(2)))
out["a scroll area top outside Finder refuses"] = run(base, finder: false)
out["a modal node deep in a window refuses"] = run(with(base, 3, "AXModal", true))
out["AXSheets unreadable refuses"] = run(with(base, 2, "AXSheets", AXError.cannotComplete))
out["a sheet attached after the walk refuses"] = run(with(base, 2, "lateSheets", [Int32(35)]))
out["children unreadable refuses"] = run(with(base, 1, "AXChildren", AXError.cannotComplete))
out["deadline while checking is a budget reason"] = run(with(base, 3, "AXRole", "deadline"))
out["target missing refuses"] = run(base, target: 77)
out["windows changing during the check refuse"] = run(base, windowsAfter: [1, 9])
print(String(data: try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]), encoding: .utf8)!)
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class WindowReadySwiftTests(unittest.TestCase):
    maxDiff = None

    def test_cases(self):
        with tempfile.TemporaryDirectory(prefix='tacto-window-ready-') as directory:
            source, binary = Path(directory) / 'main.swift', Path(directory) / 'check'
            source.write_text(HARNESS)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({
            "small windows and the desktop pass": "ok",
            "Finder: a big list in another window exceeds the budget": "readiness budget exceeded: nodes",
            "not Finder: the same": "readiness budget exceeded: nodes",
            "a sheet below another window's list is reached (no prune)": "attached sheet or malformed role",
            "a deep sheet refuses": "attached sheet or malformed role",
            "a top window that is modal refuses": "modal or unknown window",
            "a top window without AXModal refuses": "required AX attribute unavailable: AXModal",
            "the desktop saying modal refuses": "modal or unknown window",
            "a dialog top without AXModal refuses": "required AX attribute unavailable: AXModal",
            "an unknown top without AXModal refuses": "required AX attribute unavailable: AXModal",
            "a scroll area top with another parent refuses": "required AX attribute unavailable: AXModal",
            "a scroll area top outside Finder refuses": "required AX attribute unavailable: AXModal",
            "a modal node deep in a window refuses": "modal or unknown element",
            "AXSheets unreadable refuses": "window state unavailable: AXSheets",
            "a sheet attached after the walk refuses": "sheet attached during validation",
            "children unreadable refuses": "window state unavailable: AXChildren",
            "deadline while checking is a budget reason": "readiness budget exceeded: deadline",
            "target missing refuses": "target window missing, ambiguous or minimized",
            "windows changing during the check refuse": "window state changed during validation",
        }, json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
