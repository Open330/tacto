"""The Return sheet path, run for real against fake AX reads and fake posting.

Readiness: exactly one sheet (the field's own), nothing modal, nothing
unreadable. Dispatch: at most one keyDown, only after the last check passes
and with the person's input unchanged; a failure says how many were tried.
Never posts UI input."""
from pathlib import Path
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
func reader(_ tree: [Int32: Attrs]) -> AXRead {
    return { e, name in
        guard let attrs = tree[pidOf(e)], let v = attrs[name] else { return (.attributeUnsupported, nil) }
        if let err = v as? AXError { return (err, nil) }
        if let n = v as? Int32 { return (.success, el(n)) }
        if let ns = v as? [Int32] { return (.success, ns.map { el($0) } as CFArray) }
        if let b = v as? Bool { return (.success, (b ? kCFBooleanTrue : kCFBooleanFalse)!) }
        return (.success, v as CFTypeRef)
    }
}
func ready(_ tops: [Int32], _ tree: [Int32: Attrs], sheet: Int32 = 2) -> String {
    do { try nativeSheetSubmitReady(tops: tops.map { el($0) }, sheet: el(sheet), read: reader(tree)); return "ok" }
    catch NativeWindowInputError.refused(let why) { return why }
    catch { return "other" }
}
// 1 window > 2 sheet > 3 field ; 9 desktop scroll area
let window: Attrs = ["AXRole": "AXWindow", "AXModal": false, "AXChildren": [Int32(2)]]
let sheet: Attrs = ["AXRole": "AXSheet", "AXChildren": [Int32(3)]]
let field: Attrs = ["AXRole": "AXTextField"]
let desktop: Attrs = ["AXRole": "AXScrollArea"]
func with(_ a: Attrs, _ k: String, _ v: Any) -> Attrs { var a = a; a[k] = v; return a }
let base: [Int32: Attrs] = [1: window, 2: sheet, 3: field, 9: desktop]
var out: [String: String] = [:]
out["one sheet, desktop without AXModal"] = ready([1, 9], base)
var t = base; t[1] = with(window, "AXChildren", [Int32(2), Int32(4)]); t[4] = sheet
out["two sheets"] = ready([1, 9], t)
t = base; t[5] = ["AXRole": "AXWindow", "AXModal": false, "AXChildren": [Int32(6)]]; t[6] = sheet
out["same-looking sheet on another window"] = ready([1, 5], t)
t = base; t[3] = with(field, "AXChildren", [Int32(7)]); t[7] = sheet
out["nested sheet"] = ready([1], t)
out["modal window"] = ready([1], with2(base, 1, "AXModal", true))
out["window modal unreadable"] = ready([1], with2(base, 1, "AXModal", AXError.cannotComplete))
out["window without AXModal"] = ready([1], { var b = base; b[1]!.removeValue(forKey: "AXModal"); return b }())
out["desktop says modal"] = ready([1, 9], with2(base, 9, "AXModal", true))
out["role unreadable"] = ready([1], with2(base, 3, "AXRole", AXError.cannotComplete))
out["children unreadable"] = ready([1], with2(base, 2, "AXChildren", AXError.cannotComplete))
out["only a different sheet than the field's"] = ready([1], base, sheet: 8)
out["sheet missing"] = ready([1], with2(base, 1, "AXChildren", [Int32]()))
// Codex review of 479acd0: AXSheets, and modal below the top
out["AXSheets lists only the sheet"] = ready([1], with2(base, 1, "AXSheets", [Int32(2)]))
t = with2(base, 1, "AXSheets", [Int32(2), Int32(4)]); t[4] = sheet
out["AXSheets lists another sheet"] = ready([1], t)
out["AXSheets malformed"] = ready([1], with2(base, 1, "AXSheets", "x"))
out["AXSheets unreadable"] = ready([1], with2(base, 1, "AXSheets", AXError.cannotComplete))
out["modal inside the sheet"] = ready([1], with2(base, 3, "AXModal", true))
out["the sheet itself modal"] = ready([1], with2(base, 2, "AXModal", true))
out["modal malformed"] = ready([1], with2(base, 3, "AXModal", "yes"))
out["listed in AXSheets only"] = ready([1], with2(with2(base, 1, "AXChildren", [Int32]()), 1, "AXSheets", [Int32(2)]))
out["same sheet seen twice"] = ready([1], with2(base, 1, "AXChildren", [Int32(2), Int32(2)]))
func with2(_ tree: [Int32: Attrs], _ n: Int32, _ k: String, _ v: Any) -> [Int32: Attrs] {
    var t = tree; t[n] = with(t[n]!, k, v); return t
}

// Dispatch
let focusA = el(20), focusB = el(21)
let pair = try! buildNativeWindowKey(17, key: "Return", submit: true)
func send(_ mode: String) -> String {
    var posts = 0, checks = 0, reads = 0, stop = false, clock = 1.0, alive = true
    do {
        let n = try withExtendedLifetime(pair.owners) {
            try dispatchSheetReturn(pair.down, pid: 42, deadline: 100, stopped: { stop }, context: {},
                check: {
                    checks += 1
                    if mode == "check fails" || (mode == "check fails last" && checks == 2) {
                        throw NativeWindowInputError.refused("focus moved")
                    }
                },
                sameProcess: { alive },
                now: { clock },
                readInput: { _ in
                    reads += 1
                    if mode == "stop" && reads == 2 { stop = true }
                    if mode == "deadline" && reads == 2 { clock = 200 }
                    let fg: pid_t = mode == "target in front" ? 42 : 77
                    let changedBefore = mode == "input changed before" && reads >= 2
                    let changedDuring = mode == "input changed during" && reads >= 3
                    return NativeUserInputState(foreground: fg, pointer: .zero,
                        focused: changedBefore || changedDuring ? focusB : focusA)
                },
                post: { event, pid in
                    posts += 1
                    precondition(pid == 42 && event.type == .keyDown)
                    if mode == "post fails" { throw NativeWindowInputError.unavailable("post") }
                    if mode == "process replaced after" { alive = false }
                    if mode == "stopped after" { stop = true }
                    if mode == "deadline after" { clock = 200 }
                })
        }
        return "posted=\(n) posts=\(posts)"
    } catch let f as NativeWindowDispatchFailure {
        return "failed attempted=\(f.attempted) posts=\(posts)"
    } catch { return "other" }
}
for mode in ["ok", "check fails", "check fails last", "target in front", "input changed before",
             "stop", "deadline", "post fails", "input changed during",
             "process replaced after", "stopped after", "deadline after"] {
    out["send: " + mode] = send(mode)
}
print(String(data: try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]), encoding: .utf8)!)
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class ReturnSheetSwiftTests(unittest.TestCase):
    maxDiff = None

    def test_readiness_and_dispatch(self):
        import json
        with tempfile.TemporaryDirectory(prefix='tacto-return-sheet-') as directory:
            source, binary = Path(directory) / 'main.swift', Path(directory) / 'check'
            source.write_text(HARNESS)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        got = json.loads(result.stdout)
        self.assertEqual({
            'one sheet, desktop without AXModal': 'ok',
            'two sheets': 'another sheet is open',
            'same-looking sheet on another window': 'another sheet is open',
            'nested sheet': 'another sheet is open',
            'modal window': 'modal or unknown window',
            'window modal unreadable': 'window state unavailable: AXModal',
            'window without AXModal': 'window modal state unavailable',
            'desktop says modal': 'modal or unknown window',
            'role unreadable': 'window state unavailable: AXRole',
            'children unreadable': 'window state unavailable: AXChildren',
            "only a different sheet than the field's": 'another sheet is open',
            'sheet missing': 'the sheet is missing or seen twice',
            'same sheet seen twice': 'the sheet is missing or seen twice',
            'send: ok': 'posted=1 posts=1',
            'send: check fails': 'failed attempted=0 posts=0',
            'send: check fails last': 'failed attempted=0 posts=0',
            'send: target in front': 'failed attempted=0 posts=0',
            'send: input changed before': 'failed attempted=0 posts=0',
            'send: stop': 'failed attempted=0 posts=0',
            'send: deadline': 'failed attempted=0 posts=0',
            'send: post fails': 'failed attempted=1 posts=1',
            'send: input changed during': 'failed attempted=1 posts=1',
            'send: process replaced after': 'failed attempted=1 posts=1',
            'send: stopped after': 'failed attempted=1 posts=1',
            'send: deadline after': 'failed attempted=1 posts=1',
            'AXSheets lists only the sheet': 'ok',
            'AXSheets lists another sheet': 'another sheet is open',
            'AXSheets malformed': 'sheet list malformed',
            'AXSheets unreadable': 'window state unavailable: AXSheets',
            'modal inside the sheet': 'modal or unknown window',
            'the sheet itself modal': 'ok',
            'modal malformed': 'modal state malformed',
            'listed in AXSheets only': 'the sheet is missing or seen twice',
        }, got)


if __name__ == '__main__':
    unittest.main()
