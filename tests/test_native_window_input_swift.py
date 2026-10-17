"""Execute real Swift construction/readback without posting any UI input."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class SwiftWindowInputTests(unittest.TestCase):
    def test_process_identity_and_prepared_pair_without_dispatch(self):
        harness = '''
import ApplicationServices
import Foundation
import Darwin
let pid = getpid()
guard let start = nativeProcessStartIdentity(pid) else { fatalError("missing identity") }
precondition(start > 0 && nativeProcessStartIdentity(pid) == start)
precondition(nativeProcessStartIdentity(-1) == nil)
let element = AXUIElementCreateApplication(pid)
func binding(_ point: CGPoint) -> NativeWindowBinding {
    NativeWindowBinding(element: element, window: element, pid: pid, processStart: start,
        windowID: 17, windowBounds: CGRect(x: 100, y: 200, width: 300, height: 200),
        elementBounds: CGRect(x: 110, y: 210, width: 20, height: 20), point: point)
}
let first = try buildNativeWindowClick(binding(CGPoint(x: 120, y: 220)))
let second = try buildNativeWindowClick(binding(CGPoint(x: 120, y: 220)))
precondition(first.eventNumber != second.eventNumber)
precondition(first.down.type == .leftMouseDown && first.up.type == .leftMouseUp)
precondition(first.owners.count == 2 && first.down.flags.isEmpty)
for point in [CGPoint(x: 0, y: 0), CGPoint(x: 400, y: 220), CGPoint(x: CGFloat.nan, y: 220)] {
    do { _ = try buildNativeWindowClick(binding(point)); fatalError("invalid point accepted") }
    catch NativeWindowInputError.refused { }
}
do { _ = try captureNativeWindowBinding(element, pid, expectedStart: start - 1)
     fatalError("stale process accepted") }
catch NativeWindowInputError.refused { }
let titlebar = try nativeTitlebarPoints(CGRect(x: 10, y: 20, width: 300, height: 200),
    CGRect(x: 18, y: 26, width: 14, height: 14))
precondition(titlebar == [CGPoint(x: 160, y: 33), CGPoint(x: 235, y: 33), CGPoint(x: 85, y: 33)])
let negative = try nativeTitlebarPoints(CGRect(x: -400, y: -100, width: 300, height: 200),
    CGRect(x: -392, y: -94, width: 14, height: 14))
precondition(negative[0] == CGPoint(x: -250, y: -87))
for anchor in [CGRect(x: 18, y: 100, width: 14, height: 14),
               CGRect(x: 0, y: 26, width: 14, height: 14),
               CGRect(x: 18, y: 26, width: CGFloat.nan, height: 14)] {
    do { _ = try nativeTitlebarPoints(CGRect(x: 10, y: 20, width: 300, height: 200), anchor)
         fatalError("invalid titlebar anchor accepted") }
    catch NativeWindowInputError.refused { }
}
print("SWIFT_WINDOW_INPUT_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-swift-window-') as directory:
            source = Path(directory)/'main.swift'
            binary = Path(directory)/'check'
            source.write_text(harness)
            build = subprocess.run(['xcrun', 'swiftc', str(source),
                str(ROOT/'helper/WindowInput.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), 'SWIFT_WINDOW_INPUT_PASS')

    def test_window_preparation_refuses_before_writing_anything(self):
        """The guard this replaces protected a synthetic title-bar click.

        `windowBoundClick` posted a mouse down/up pair at a window's title bar
        and needed a final re-check after its AX reads, because those reads can
        block and a stop can arrive while they do. `prepareWindow` writes
        `AXMain`/`AXFocused` instead, and the same question applies to the
        write: a stop, a moved observation, an expired deadline or a background
        app must all be caught before it happens.

        The real function is extracted and run, so this cannot pass by testing
        a copy of the logic.
        """
        source = (ROOT/'helper/TactoHelper.swift').read_text()
        function = 'func prepareWindow(' + source.split('func prepareWindow(', 1)[1]
        function = function.split('\n}\n', 1)[0] + '\n}\n'
        function = function.replace('AXUIElementIsAttributeSettable(', 'fakeSettable(')
        function = function.replace('AXUIElementSetAttributeValue(', 'fakeWrite(')
        harness = '''
import Foundation
import ApplicationServices
final class Abort { var isStopped = false }
let abortState = Abort()
var latestObsID = "test"
var latestTargetPID: pid_t = 1
var frontmost = true
var minimized = false
var writes = 0
var changeDuringRead = false
var changeAfterWrite = false
var writeError = false
var backgroundCalls = 0
func prepareBackgroundWindow(_ obsID: String, _ target: String, _ element: AXUIElement,
                             _ pid: pid_t, deadline: Double) -> [String: Any] {
    backgroundCalls += 1
    return ["type":"acted", "input_route":"window_prepare", "unverified":"observe_effect"]
}
func processForWindowTarget(_ target: String) -> pid_t? { 1 }
func isFrontmost(pid p: pid_t) -> Bool { frontmost }
func attr(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    if name == "AXMinimized" { return (minimized ? kCFBooleanTrue : kCFBooleanFalse) }
    return kCFBooleanTrue
}
func fakeSettable(_ element: AXUIElement, _ name: CFString,
                  _ value: UnsafeMutablePointer<DarwinBoolean>) -> AXError {
    value.pointee = true
    if changeDuringRead { frontmost = false }
    return .success
}
func fakeWrite(_ element: AXUIElement, _ name: CFString, _ value: CFTypeRef) -> AXError {
    writes += 1
    if changeAfterWrite { frontmost = false }
    return writeError ? .cannotComplete : .success
}
''' + function + '''
let element = AXUIElementCreateApplication(1)
func refusalFor(_ setup: () -> Void) -> [String: Any] {
    abortState.isStopped = false; latestObsID = "test"; latestTargetPID = 1
    frontmost = true; minimized = false
    writes = 0; changeDuringRead = false; changeAfterWrite = false; writeError = false
    setup()
    return prepareWindow("test", "fixture", element, 1,
                         deadline: ProcessInfo.processInfo.systemUptime + 10)
}
let cases: [(String, () -> Void)] = [
    ("stopped", { abortState.isStopped = true }),
    ("stale observation", { latestObsID = "other" }),
    ("other target", { latestTargetPID = 2 }),
    ("minimised", { minimized = true }),
]
for (name, setup) in cases {
    let out = refusalFor(setup)
    guard (out["type"] as? String) == "error",
          (out["effect"] as? String) == "not_started" else {
        fatalError("not refused before writing: " + name + " -> " + String(describing: out))
    }
}
// Background is delegated; this test covers the foreground AX branch.
let background = refusalFor({ frontmost = false })
guard (background["type"] as? String) == "acted", backgroundCalls == 1, writes == 0 else {
    fatalError("background preparation was not delegated without AX writes")
}
let stopped = refusalFor({ abortState.isStopped = true })
guard stopped["needs_foreground"] == nil else {
    fatalError("a stop is not a foreground problem")
}
// The deadline is checked too, with nothing else wrong.
abortState.isStopped = false; latestObsID = "test"; latestTargetPID = 1
frontmost = true; minimized = false
let expired = prepareWindow("test", "fixture", element, 1,
                            deadline: ProcessInfo.processInfo.systemUptime - 1)
guard (expired["effect"] as? String) == "not_started" else {
    fatalError("an expired deadline must refuse")
}
let readChange = refusalFor({ changeDuringRead = true })
guard (readChange["effect"] as? String) == "not_started", writes == 0 else {
    fatalError("foreground changed during AX query but a write was dispatched")
}
let writeChange = refusalFor({ changeAfterWrite = true })
guard (writeChange["effect"] as? String) == "unknown", writes == 1 else {
    fatalError("partial preparation must stop with unknown effect")
}
let failure = refusalFor({ writeError = true })
guard (failure["effect"] as? String) == "unknown", writes == 1 else {
    fatalError("failed AX write must not claim no effect")
}
print("SWIFT_PREPARE_GUARD_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-swift-prepare-') as directory:
            script = Path(directory)/'main.swift'
            binary = Path(directory)/'check'
            script.write_text(harness)
            build = subprocess.run(['xcrun', 'swiftc', str(script), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), 'SWIFT_PREPARE_GUARD_PASS')
