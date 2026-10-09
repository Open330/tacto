"""nativeRequireWindowReady, run for real against a fake AX tree.

2026-10-09 (Codex review of req 5c): strict full check stays the default;
budget/deadline is its own reason; non-window tops may lack AXModal; and the
one verified prune — Finder only, another window's file-list container — is
pinned case by case, including what it does NOT look at."""
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
    var windowReads = 0
    let read: AXRead = { e, name in
        let n = pidOf(e)
        if n == 42 && name == "AXWindows" {
            windowReads += 1
            let list = (windowReads > 1 ? windowsAfter : nil) ?? (tree[42]!["AXWindows"] as! [Int32])
            return (.success, list.map { el($0) } as CFArray)
        }
        guard let attrs = tree[n], let v = attrs[name] else { return (.attributeUnsupported, nil) }
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
let desktop: Attrs = ["AXRole": "AXScrollArea", "AXChildren": [Int32]()]
// target window 1 (small); other window 2 holding a big list 20; desktop 9
var base: [Int32: Attrs] = [42: ["AXWindows": [Int32(1), Int32(2), Int32(9)]],
    1: win([3]), 3: node("AXButton"), 2: win([20]), 9: desktop]
base = bigList(base)
func with(_ t: [Int32: Attrs], _ n: Int32, _ k: String, _ v: Any) -> [Int32: Attrs] { var t = t; t[n]![k] = v; return t }
var out: [String: String] = [:]
out["Finder: another window's big list is pruned"] = run(base)
out["not Finder: the same list exceeds the budget"] = run(base, finder: false)
var inTarget = base; inTarget[1] = win([20]); inTarget[2] = win([])
out["the target window's own list is walked"] = run(inTarget)
var deepSheet = base; deepSheet[2] = win([20, 30]); deepSheet[30] = node("AXGroup", [31]); deepSheet[31] = node("AXGroup", [32]); deepSheet[32] = node("AXSheet")
out["a deep sheet outside a list refuses"] = run(deepSheet)
out["a sheet listed on the list container refuses"] = run(with(base, 20, "AXSheets", [Int32(33)]))
var underList = base; underList[1000] = node("AXRow", [34]); underList[34] = node("AXSheet")
out["NOT looked at: a sheet below another window's list (the prune)"] = run(underList)
out["a top window that is modal refuses"] = run(with(base, 2, "AXModal", true))
out["a top window without AXModal refuses"] = run({ var t = base; t[2]!.removeValue(forKey: "AXModal"); return t }())
out["the desktop without AXModal passes"] = run(base)
out["the desktop saying modal refuses"] = run(with(base, 9, "AXModal", true))
out["a modal node deep in a window refuses"] = run(with(base, 3, "AXModal", true))
out["AXSheets unreadable refuses"] = run(with(base, 2, "AXSheets", AXError.cannotComplete))
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
        with tempfile.TemporaryDirectory(prefix='nlc-window-ready-') as directory:
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
            "Finder: another window's big list is pruned": "ok",
            "not Finder: the same list exceeds the budget": "readiness budget exceeded: nodes",
            "the target window's own list is walked": "readiness budget exceeded: nodes",
            "a deep sheet outside a list refuses": "attached sheet or malformed role",
            "a sheet listed on the list container refuses": "sheet or malformed sheet list",
            "NOT looked at: a sheet below another window's list (the prune)": "ok",
            "a top window that is modal refuses": "modal or unknown window",
            "a top window without AXModal refuses": "required AX attribute unavailable: AXModal",
            "the desktop without AXModal passes": "ok",
            "the desktop saying modal refuses": "modal or unknown window",
            "a modal node deep in a window refuses": "modal or unknown element",
            "AXSheets unreadable refuses": "window state unavailable: AXSheets",
            "children unreadable refuses": "window state unavailable: AXChildren",
            "deadline while checking is a budget reason": "readiness budget exceeded: deadline",
            "target missing refuses": "target window missing, ambiguous or minimized",
            "windows changing during the check refuse": "window state changed during validation",
        }, json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
