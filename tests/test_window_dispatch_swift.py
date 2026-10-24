"""Real event construction and injected posting; never posts UI input."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class WindowDispatchSwiftTests(unittest.TestCase):
    def test_popup_transition_only_after_complete_click_and_receiving_proof(self):
        harness = r'''import Foundation
import AppKit
import ApplicationServices
let element=AXUIElementCreateApplication(10001),window=AXUIElementCreateApplication(10002),focus=AXUIElementCreateApplication(10003)
let binding=NativeWindowBinding(element:element,window:window,pid:42,processStart:10,
 windowID:17,windowBounds:CGRect(x:100,y:200,width:300,height:200),
 elementBounds:CGRect(x:110,y:230,width:100,height:40),point:CGPoint(x:160,y:250))
for mode in ["valid","lost_after_down","unconfirmed","user_changed","double","key"] {
 var posted=0,proofs=0
 do {
  let result=try dispatchNativeWindowPreparation(binding,deadline:100,stopped:{false},context:{},now:{0},
   readInput:{_ in NativeUserInputState(foreground:77,pointer:mode=="user_changed" && posted==2 ? CGPoint(x:1,y:1):.zero,focused:focus)},
   validate:{_ in if posted>0 && (mode=="lost_after_down" || posted==2) {throw NativeWindowInputError.refused("popup destroyed")}},
   post:{event,pid in precondition(pid==42);precondition(event.type==(posted==0 ? .leftMouseDown:.leftMouseUp));posted+=1},
   key:mode=="key" ? "Down":nil,routing:{_,_ in},elementClicks:mode=="key" ? 0:mode=="double" ? 2:1,
   finalTransition:{checkpoint in try checkpoint();proofs+=1;precondition(posted==2);if mode=="unconfirmed" {throw NativeWindowInputError.refused("receiving state unconfirmed")}})
  precondition(mode=="valid" && result.posted==2 && proofs==1)
 } catch let failure as NativeWindowDispatchFailure {
  precondition(mode != "valid")
  let expected=mode=="double" || mode=="key" ? 0:mode=="lost_after_down" ? 1:2
  precondition(failure.attempted==expected && posted==expected)
  precondition(proofs==(mode=="unconfirmed" ? 1:0))
 } catch {fatalError("unexpected failure: \(error)")}
}
print("SWIFT_POPUP_TRANSITION_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-popup-transition-') as directory:
            source,binary=Path(directory)/'main.swift',Path(directory)/'check'
            source.write_text(harness)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(source),
                str(ROOT/'helper/WindowInput.swift'),str(ROOT/'helper/WindowDispatch.swift'),
                str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual('SWIFT_POPUP_TRANSITION_PASS',result.stdout.strip())

    def test_measured_sequence_headroom_refuses_slow_checks_before_first_post(self):
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
let element = AXUIElementCreateApplication(10001)
let window = AXUIElementCreateApplication(10002)
let focus = AXUIElementCreateApplication(10003)
let binding = NativeWindowBinding(element:element,window:window,pid:42,
    processStart:10,windowID:17,windowBounds:CGRect(x:100,y:200,width:300,height:200),
    elementBounds:CGRect(x:110,y:230,width:100,height:40),point:CGPoint(x:160,y:250))
for mode in ["fast", "slow_validation", "slow_routing", "slow_input", "slow_context", "latency_spike", "submit", "diagnostic_delay"] {
    var clock = 0.0, posted = 0
    var headroom:[String:Any]?
    do {
        let result = try dispatchNativeWindowPreparation(binding,deadline:5,
            stopped:{ false },context:{ clock += mode == "slow_context" ? 0.15 : 0.001 },
            now:{ clock },readInput:{ _ in
                clock += mode == "slow_input" ? 0.5 : 0.001
                return NativeUserInputState(foreground:77,pointer:.zero,focused:focus)
            },validate:{ _ in
                clock += mode == "slow_validation" ? 0.7 : 0.01
                if mode == "latency_spike" && posted > 0 { clock = 6 }
            },post:{ _, pid in precondition(pid == 42); posted += 1 },
            key:mode == "submit" ? "Return" : nil,
            routing:{ _, _ in clock += mode == "slow_routing" ? 0.7 : 0.01 },
            elementClicks:mode == "submit" ? 0 : 2,submitKey:mode == "submit",headroomDiagnostic:{headroom=$0;if mode=="diagnostic_delay" {clock=6}})
        precondition(mode == "fast" || mode == "submit", "slow sequence admitted: \(mode)")
        precondition(posted == (mode == "submit" ? 1 : 4) && result.posted == posted)
    } catch let failure as NativeWindowDispatchFailure {
        if mode == "latency_spike" {
            precondition(posted == 1 && failure.attempted == 1 && !failure.stopped)
        } else {
            precondition((mode.hasPrefix("slow_") || mode=="diagnostic_delay") && posted == 0 && failure.attempted == 0)
            precondition(failure.detail == "window validation headroom insufficient")
        }
    } catch { fatalError("unexpected error: \(error)") }
    precondition(headroom != nil)
    precondition(headroom!["saturated"] as? Bool == false)
    precondition(headroom!["event_count"] as? Int == (mode=="submit" ? 1:4))
    if mode.hasPrefix("slow_") {
        precondition(headroom!["required_ms"] as! Int > headroom!["remaining_ms"] as! Int)
    }
}
print("SWIFT_WINDOW_HEADROOM_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-window-headroom-') as directory:
            source, binary = Path(directory) / 'main.swift', Path(directory) / 'check'
            source.write_text(harness)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=90)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'SWIFT_WINDOW_HEADROOM_PASS')

    def test_key_construction_and_routing_stop_before_next_event(self):
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
let input = AXUIElementCreateApplication(10001)
let window = AXUIElementCreateApplication(10002)
let focus = AXUIElementCreateApplication(10003)
let binding = NativeWindowBinding(element: input, window: window, pid: 42,
    processStart: 10, windowID: 17, windowBounds: CGRect(x: 100, y: 200, width: 300, height: 200),
    elementBounds: CGRect(x: 110, y: 230, width: 100, height: 40), point: CGPoint(x: 160, y: 250))
for key in ["Tab", "shift+Tab", "Up", "Down", "Left", "Right"] {
    let pair = try buildNativeWindowKey(17, key: key)
    precondition(pair.down.type == .keyDown && pair.up.type == .keyUp && pair.owners.count == 2)
}
for key in ["Escape", "Return", "cmd+q", "a", ""] {
    precondition(nativeNavigationKey(key) == nil)
}
func run(_ mode: String, expected: Int, success: Bool = false) {
    var posted: [UInt32] = [], routes: [Bool] = []
    var stop = false, clock = 10.0, preflights = 0
    do {
        let result = try dispatchNativeWindowPreparation(binding, deadline: 1000,
            stopped: { stop }, context: {}, now: { clock }, readInput: { _ in
                NativeUserInputState(foreground: 77,
                    pointer: mode == "pointer" && !posted.isEmpty ? CGPoint(x: 1, y: 2) : .zero, focused: focus)
            }, validate: { _ in
                if mode == "binding" && !posted.isEmpty { throw NativeWindowInputError.refused("changed") }
            }, post: { event, pid in
                precondition(pid == 42); posted.append(event.type.rawValue)
                if mode == "stop" { stop = true }
                if mode == "deadline" { clock = 1001 }
                if mode == "post" { throw NativeWindowInputError.unavailable("unknown") }
            }, key: "Tab", routing: { _, element in
                routes.append(element)
                if mode == "target_before" || (mode == "target_after" && !posted.isEmpty) {
                    throw NativeWindowInputError.refused("target changed")
                }
            }, preflight: {
                precondition(posted.isEmpty); preflights += 1
                if mode == "preflight" { throw NativeWindowInputError.refused("expectation changed") }
            })
        precondition(success && result.posted == 2 && posted == [10,11])
        precondition(routes == [true,true,false,false,false] && preflights == 1)
    } catch let failure as NativeWindowDispatchFailure {
        precondition(!success && failure.attempted == expected && posted.count == expected)
        precondition(failure.stopped == stop)
    } catch { fatalError("unexpected failure: \(error)") }
}
run("success", expected: 2, success: true)
for mode in ["preflight", "target_before"] { run(mode, expected: 0) }
for mode in ["target_after", "stop", "deadline", "post", "pointer", "binding"] { run(mode, expected: 1) }
print("SWIFT_WINDOW_KEYS_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-window-keys-') as directory:
            source, binary = Path(directory) / 'main.swift', Path(directory) / 'check'
            source.write_text(harness)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'SWIFT_WINDOW_KEYS_PASS')

    def test_post_boundaries_partial_effect_deadline_stop_and_user_state(self):
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
let window = AXUIElementCreateApplication(10001)
let focus = AXUIElementCreateApplication(10002)
let otherFocus = AXUIElementCreateApplication(10003)
let binding = NativeWindowBinding(element: window, window: window, pid: 42,
    processStart: 10, windowID: 17, windowBounds: CGRect(x: 100, y: 200, width: 300, height: 200),
    elementBounds: CGRect(x: 100, y: 200, width: 300, height: 200), point: CGPoint(x: 250, y: 212),
    pointKind: .titlebar)
func run(_ mode: String, expected: Int, success: Bool = false, point: Bool = false) {
    let selected = point ? NativeWindowBinding(element: window, window: window, pid: 42,
        processStart: 10, windowID: 17, windowBounds: binding.windowBounds,
        elementBounds: binding.windowBounds, point: CGPoint(x: 160, y: 250),
        pointKind: .windowPoint, relativePoint: CGPoint(x: 60, y: 50)) : binding
    var posts: [UInt32] = []
    var stop = mode == "stopped_before"
    var clock = mode == "expired_before" ? 1001.0 : 10.0
    var validContext = true
    var reads = 0
    var validates = 0
    do {
        let result = try dispatchNativeWindowPreparation(selected, deadline: 1000,
            stopped: { stop }, context: {
                if !validContext { throw NativeWindowInputError.refused("stale observation") }
            }, now: { clock }, readInput: { _ in
                reads += 1
                if mode == "read_before" || (mode == "read_after" && !posts.isEmpty) {
                    throw NativeWindowInputError.unavailable("input read failed")
                }
                let changed = !posts.isEmpty
                let foreground: pid_t = mode == "already_foreground" ? 42 :
                    (mode == "foreground_changed" && changed ? 99 : 77)
                let point = ((mode == "pointer_changed" && changed) || (mode == "between_pointer" && reads >= 5)) ? CGPoint(x: 1, y: 2) : .zero
                let focused = ((mode == "focus_changed" && changed) || (mode == "between_focus" && reads >= 5)) ? otherFocus : focus
                return NativeUserInputState(foreground: foreground, pointer: point, focused: focused)
            }, validate: { _ in
                validates += 1
                if mode == "binding_before" || (mode == "binding_after" && !posts.isEmpty) {
                    throw NativeWindowInputError.refused("window changed")
                }
            }, post: { event, pid in
                precondition(pid == 42)
                posts.append(event.type.rawValue)
                if mode == "stopped_after" { stop = true }
                if mode == "expired_after" { clock = 1001 }
                if mode == "context_after" { validContext = false }
                if mode == "post_error" { throw NativeWindowInputError.unavailable("uncertain post") }
            }, routing: point ? { _, _ in
                if mode == "routing_before" || (mode == "routing_after" && !posts.isEmpty) {
                    throw NativeWindowInputError.refused("target changed")
                }
            } : nil, preflight: {
                if mode == "preflight" { throw NativeWindowInputError.refused("expectation changed") }
            }, pointClick: point)
        precondition(success && result.posted == (point ? 2 : 3) && posts == (point ? [1, 2] : [13, 1, 2]), "\(mode): unexpected success")
        precondition(result.foreground == 77 && result.windowID == 17)
        precondition(reads == (point ? 7 : 10) && validates == (point ? 5 : 7))
    } catch let failure as NativeWindowDispatchFailure {
        precondition(!success && failure.attempted == expected && posts.count == expected,
                     "\(mode): \(failure)")
        precondition(failure.stopped == stop)
    } catch { fatalError("unexpected error: \(error)") }
}
run("success", expected: 3, success: true)
for mode in ["stopped_before", "expired_before", "binding_before", "read_before", "already_foreground"] {
    run(mode, expected: 0)
}
for mode in ["stopped_after", "expired_after", "context_after", "post_error", "read_after",
             "binding_after", "foreground_changed", "pointer_changed", "focus_changed", "between_pointer", "between_focus"] {
    run(mode, expected: 1)
}
run("success", expected: 2, success: true, point: true)
for mode in ["stopped_before", "expired_before", "binding_before", "read_before",
             "already_foreground", "preflight", "routing_before"] {
    run(mode, expected: 0, point: true)
}
for mode in ["stopped_after", "expired_after", "context_after", "post_error", "read_after",
             "binding_after", "foreground_changed", "pointer_changed", "focus_changed",
             "between_pointer", "between_focus", "routing_after"] {
    run(mode, expected: 1, point: true)
}
var calls = 0
let expired = NativeWindowReadScope(deadline: ProcessInfo.processInfo.systemUptime - 1, check: { calls += 1 })
do { _ = try expired.read(window, "AXRole"); fatalError("expired AX read started") }
catch NativeWindowInputError.refused { }
precondition(calls == 1)
print("SWIFT_WINDOW_DISPATCH_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-window-dispatch-') as directory:
            source = Path(directory) / 'main.swift'
            binary = Path(directory) / 'check'
            source.write_text(harness)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'SWIFT_WINDOW_DISPATCH_PASS')


class ForegroundLateFocusTests(unittest.TestCase):
    def test_focus_that_moves_after_mouse_down_completes_the_click(self):
        harness = r'''import Foundation
import AppKit
import ApplicationServices
let element=AXUIElementCreateApplication(10001),window=AXUIElementCreateApplication(10002)
let before=AXUIElementCreateApplication(10003),clicked=AXUIElementCreateApplication(10004)
let binding=NativeWindowBinding(element:element,window:window,pid:42,processStart:10,
 windowID:17,windowBounds:CGRect(x:100,y:200,width:300,height:200),
 elementBounds:CGRect(x:110,y:230,width:100,height:40),point:CGPoint(x:160,y:250))
// The app moves focus to the clicked view one read after mouseDown, as an
// outline view in front does; the person changes nothing.
for (mode, lateReads) in [("late_focus", 1), ("person_moves_pointer", 0)] {
 var posted=0,readsAfterDown=0
 do {
  let result=try dispatchNativeWindowPreparation(binding,deadline:100,stopped:{false},context:{},now:{0},
   readInput:{_ in
    if posted==0 { return NativeUserInputState(foreground:42,pointer:.zero,focused:before) }
    readsAfterDown+=1
    let pointer = mode=="person_moves_pointer" ? CGPoint(x:9,y:9) : CGPoint.zero
    return NativeUserInputState(foreground:42,pointer:pointer,focused:readsAfterDown>lateReads ? clicked:before)},
   validate:{_ in},post:{event,pid in posted+=1},
   routing:{_,_ in},elementClicks:1,foregroundClickFocus:{_,_ in})
  precondition(mode=="late_focus" && result.posted==2, "\(mode) \(result)")
 } catch let failure as NativeWindowDispatchFailure {
  precondition(mode=="person_moves_pointer" && failure.attempted==1, "\(mode) \(failure)")
 } catch { fatalError("unexpected failure: \(error)") }
}
print("SWIFT_LATE_FOCUS_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-late-focus-') as directory:
            source,binary=Path(directory)/'main.swift',Path(directory)/'check'
            source.write_text(harness)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(source),
                str(ROOT/'helper/WindowInput.swift'),str(ROOT/'helper/WindowDispatch.swift'),
                str(ROOT/'helper/FrontmostProcess.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=90)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stderr)
            self.assertEqual('SWIFT_LATE_FOCUS_PASS',result.stdout.strip())
