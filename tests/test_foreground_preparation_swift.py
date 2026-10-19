"""The helper's frontmost-window preparation keeps the person's typing."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class ForegroundPreparationSwiftTests(unittest.TestCase):
    def test_editable_focus_elsewhere_refuses_and_unknown_focus_is_not_safe(self):
        helper = (ROOT/'helper/TactoHelper.swift').read_text()
        dispatch = (ROOT/'helper/WindowDispatch.swift').read_text()
        guard = helper[helper.index('/// Choosing another window of the frontmost app'):
                       helper.index('func prepareWindow(')]
        parser = dispatch[dispatch.index('func nativeUserFocusedElement('):
                          dispatch.index('func readNativeUserInputState(')]
        script = r'''import Foundation
typealias AXUIElement = NSNumber
enum AXError { case success, noValue, attributeUnsupported, cannotComplete }
enum NativeWindowInputError: Error { case unavailable(String), refused(String), needsForeground(String) }
func AXUIElementGetTypeID() -> CFTypeID { CFNumberGetTypeID() }
let app = NSNumber(value: 7), window = NSNumber(value: 100), other = NSNumber(value: 200)
let field = NSNumber(value: 300)
var attrs: [String: CFTypeRef] = [:], focusError = AXError.success
var focusValue: AnyObject? = field, settable = true
func key(_ el: NSNumber, _ name: String) -> String { "\(el.intValue).\(name)" }
func attr(_ el: AXUIElement, _ name: String) -> CFTypeRef? { attrs[key(el, name)] }
func AXUIElementCreateApplication(_ pid: pid_t) -> AXUIElement { NSNumber(value: pid) }
func AXUIElementCopyAttributeValue(_ el: AXUIElement, _ name: CFString, _ raw: inout AnyObject?) -> AXError {
    raw = focusValue; return focusError
}
func AXUIElementIsAttributeSettable(_ el: AXUIElement, _ name: CFString, _ out: inout DarwinBoolean) -> AXError {
    out = DarwinBoolean(settable); return .success
}
''' + parser + guard + r'''
func run(_ name: String, _ setup: () -> Void) -> String {
    attrs = [key(field, "AXWindow"): other, key(field, "AXRole"): "AXTextField" as NSString,
             key(field, "AXValue"): "draft" as NSString]
    focusError = .success; focusValue = field; settable = true
    setup()
    do { return try foregroundPreparationIsDone(7, window) ? "prepared" : "write" }
    catch NativeWindowInputError.needsForeground(_) { return "needs_foreground" }
    catch NativeWindowInputError.unavailable(let detail) { return detail }
    catch { return "other" }
}
var rows: [String: String] = [:]
rows["prepared"] = run("prepared") { attrs[key(window, "AXMain")] = kCFBooleanTrue; attrs[key(app, "AXFocusedWindow")] = window }
rows["main_elsewhere"] = run("main_elsewhere") { attrs[key(window, "AXMain")] = kCFBooleanTrue; attrs[key(app, "AXFocusedWindow")] = other }
rows["text_field"] = run("text_field") {}
rows["text_area"] = run("text_area") { attrs[key(field, "AXRole")] = "AXTextArea" as NSString }
rows["settable_text"] = run("settable_text") { attrs[key(field, "AXRole")] = "AXGroup" as NSString }
rows["inside_target"] = run("inside_target") { attrs[key(field, "AXWindow")] = window }
rows["button"] = run("button") { attrs[key(field, "AXRole")] = "AXButton" as NSString; attrs[key(field, "AXValue")] = nil; settable = false }
rows["slider"] = run("slider") { attrs[key(field, "AXRole")] = "AXSlider" as NSString; attrs[key(field, "AXValue")] = NSNumber(value: 3) }
rows["absent"] = run("absent") { focusError = .noValue; focusValue = nil }
rows["unsupported"] = run("unsupported") { focusError = .attributeUnsupported; focusValue = nil }
rows["failed"] = run("failed") { focusError = .cannotComplete; focusValue = nil }
rows["malformed"] = run("malformed") { focusValue = "x" as NSString }
print(String(data: try! JSONSerialization.data(withJSONObject: rows), encoding: .utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-foreground-prepare-') as work:
            path = Path(work)/'main.swift'
            path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertEqual({
            'prepared': 'prepared', 'main_elsewhere': 'needs_foreground',
            'text_field': 'needs_foreground', 'text_area': 'needs_foreground',
            'settable_text': 'needs_foreground', 'inside_target': 'write',
            'button': 'write', 'slider': 'write', 'absent': 'write',
            'unsupported': 'user focus unsupported', 'failed': 'user focus query failed',
            'malformed': 'user focus malformed'}, json.loads(run.stdout))

    def test_check_runs_before_the_first_foreground_write(self):
        helper = (ROOT/'helper/TactoHelper.swift').read_text()
        body = helper[helper.index('func prepareWindow('):]
        body = body[:body.index('\n}\n')]
        self.assertLess(body.index('foregroundPreparationIsDone(pid, element)'),
                        body.index('phase = "foreground_write"'))
        self.assertIn('return refuse("action_failed", detail, foreground: true)', body)


if __name__ == '__main__':
    unittest.main()
