"""Run the actual focus reader with injected AX state; no GUI input."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class NativeUserFocusDiagnosticTests(unittest.TestCase):
    def test_focus_unavailable_foreground_drift_and_expiry_are_distinct(self):
        source = (Path(__file__).resolve().parents[1] / 'helper/WindowDispatch.swift').read_text()
        parser = source[source.index('func nativeUserFocusedElement('):source.index('func readNativeUserInputState(')]
        function = source[source.index('func readNativeUserInputState('):]
        function = function[:function.index('\n}\n') + 3]
        script = '''import Foundation
import CoreFoundation
typealias AXUIElement = NSNumber
enum NativeWindowInputError: Error { case unavailable(String) }
enum AXError { case success, failed, noValue, attributeUnsupported }
struct NativeUserInputState { let foreground:pid_t; let pointer:CGPoint; let focused:AXUIElement? }
struct CGEvent {
 init?(source:Int?) {}
 var location:CGPoint { CGPoint(x:0,y:0) }
}
var mode = "ok", reads = 0, resets = 0
func nativeFrontmostProcessPID() -> pid_t? {
 reads += 1
 return mode == "foreground" && reads > 1 ? 8 : 7
}
func AXUIElementCreateApplication(_ pid:pid_t) -> AXUIElement { NSNumber(value:pid) }
func AXUIElementGetTypeID() -> CFTypeID { CFNumberGetTypeID() }
func AXUIElementSetMessagingTimeout(_ app:AXUIElement, _ time:Float) -> AXError {
 if time == 0 { resets += 1; if mode == "reset" { return .failed } }
 return .success
}
func AXUIElementCopyAttributeValue(_ app:AXUIElement, _ key:CFString, _ raw:inout AnyObject?) -> AXError {
 if mode == "read" { return .failed }
 if mode == "absent" { return .noValue }
 if mode == "unsupported" { return .attributeUnsupported }
 if mode == "nil" { return .success }
 raw = mode == "shape" ? "not an AX element" as NSString : NSNumber(value:9)
 if mode == "deadline" { Thread.sleep(forTimeInterval:0.03) }
 return .success
}
''' + parser + function + '''
for scenario in ["ok","absent","unsupported","read","nil","shape","foreground","deadline","reset"] {
 mode=scenario; reads=0; resets=0
 var reason="ok"
 do { let result = try readNativeUserInputState(deadline:ProcessInfo.processInfo.systemUptime +
                                      (mode == "deadline" ? 0.01 : 10))
      precondition((result.focused == nil) == (mode == "absent")) }
 catch NativeWindowInputError.unavailable(let detail) { reason=detail }
 catch { fatalError("unexpected failure") }
 print("\\(scenario)|\\(reason)|\\(resets)")
}
'''
        with tempfile.TemporaryDirectory(prefix='tacto-user-focus-reasons-') as work:
            path = Path(work) / 'main.swift'
            path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        rows = dict((parts[0], parts[1:]) for parts in
                    (line.split('|') for line in run.stdout.splitlines()))
        self.assertEqual(['user focus query failed', '1'], rows['read'])
        for mode in ('nil', 'shape'):
            self.assertEqual(['user focus malformed', '1'], rows[mode])
        self.assertEqual(['user focus unsupported', '1'], rows['unsupported'])
        self.assertEqual(['ok', '1'], rows['absent'])
        self.assertEqual(['user foreground changed', '1'], rows['foreground'])
        self.assertEqual(['user focus deadline expired', '1'], rows['deadline'])
        self.assertEqual(['user focus timeout reset failed', '1'], rows['reset'])
        self.assertEqual(['ok', '1'], rows['ok'])
