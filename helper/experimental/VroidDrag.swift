// Explicit foreground-only diagnostic for Unity hair editing. Existing
// background input routes keep their no-pointer/no-activation contract.
import Foundation
import AppKit
import ApplicationServices

func vroidDragTitleMatches(_ observed: String?, expected: String) -> Bool {
    guard let observed else { return false }
    return observed == expected || (!expected.hasSuffix("*") && observed == expected + "*")
}

// Activation and AX hit ownership can settle at different times. Reobserve
// the SAME guard for a bounded interval; never accept a different owner.
func vroidWaitForTargetHit(target: pid_t, deadline: Double,
                           now: () -> Double, wait: (Double) -> Void,
                           check: () throws -> Void,
                           owner: () throws -> pid_t) throws -> (pid_t, Int) {
    var attempts = 0
    while true {
        try check()
        let hit = try owner()
        attempts += 1
        if hit == target || now() >= deadline { return (hit, attempts) }
        wait(min(0.05, max(0, deadline - now())))
    }
}

// Separate explicit foreground preparation. Posts no pointer/key events and
// accepts only an exact freshly observed VRoid startup/document window.
func dispatchVroidFocus(_ binding: NativeWindowBinding, title: String,
                        deadline: Double, stopped: () -> Bool,
                        context: () throws -> Void) -> [String: Any] {
    var attempted = false
    do {
        guard title == "VRoid Studio" || title == "VRoid Studio 2.14.0"
                || title.hasPrefix("VRoid Studio 2.14.0 - ") && title.contains(".vroid"),
              !stopped(), ProcessInfo.processInfo.systemUptime + 0.3 < deadline else {
            throw NativeWindowInputError.refused("invalid focus title or interrupted deadline")
        }
        try context()
        try validateNativeWindowBinding(binding)
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(binding.window, kAXTitleAttribute as CFString, &raw) == .success,
              raw as? String == title,
              let app = NSRunningApplication(processIdentifier: binding.pid),
              app.bundleIdentifier == "net.pixiv.vroid.macosx" else {
            throw NativeWindowInputError.refused("VRoid focus identity changed")
        }
        attempted = true
        app.activate(options: [])
        guard AXUIElementPerformAction(binding.window, kAXRaiseAction as CFString) == .success else {
            throw NativeWindowInputError.refused("VRoid focus raise failed")
        }
        Thread.sleep(forTimeInterval: 0.15)
        try context()
        try validateNativeWindowBinding(binding)
        guard !stopped(), ProcessInfo.processInfo.systemUptime < deadline,
              nativeFrontmostProcessPID() == binding.pid,
              AXUIElementCopyAttributeValue(binding.window, kAXTitleAttribute as CFString, &raw) == .success,
              raw as? String == title else {
            throw NativeWindowInputError.refused("VRoid focus receiving state changed")
        }
        return ["type":"acted", "action":"vroid_focus", "effect":"unverified",
                "foreground_verified":true, "posted_events":0, "pid":binding.pid,
                "requires_reobservation":true]
    } catch {
        return ["type":"error", "error":"vroid_focus_failed",
                "effect":attempted ? "unknown" : "not_started", "posted_events":0,
                "detail":String(describing:error)]
    }
}

// Arc-length sampling retains the ordered, observed canvas path without
// releasing the button at intermediate points.
func vroidStrokePoint(_ path: [CGPoint], fraction: Double) -> CGPoint {
    let lengths = zip(path, path.dropFirst()).map { hypot($0.1.x - $0.0.x, $0.1.y - $0.0.y) }
    let total = lengths.reduce(0, +)
    guard total > 0 else { return path[0] }
    var distance = max(0, min(1, fraction)) * total
    for (index, length) in lengths.enumerated() {
        if length > 0 && distance <= length {
            let t = distance / length
            return CGPoint(x: path[index].x + (path[index + 1].x - path[index].x) * t,
                           y: path[index].y + (path[index + 1].y - path[index].y) * t)
        }
        distance -= length
    }
    return path.last!
}

// Unity may consume relative motion instead of cursor locations. Quantize
// endpoints before subtracting so sub-point steps retain their total motion.
func vroidDragDelta(previous: CGPoint, current: CGPoint) -> (Int64, Int64) {
    (Int64(current.x.rounded() - previous.x.rounded()),
     Int64(current.y.rounded() - previous.y.rounded()))
}

// Graph drags are separately scoped to one freshly observed rectangle in the
// right parameter panel. Canvas drags retain their original boundary.
func vroidDragRegion(windowSize: CGSize, graphBounds: CGRect?) -> CGRect? {
    let canvas=CGRect(x:240,y:120,width:windowSize.width-480,height:windowSize.height-180)
    guard let graph=graphBounds else {return canvas.width>0 && canvas.height>0 ? canvas : nil}
    guard graph.size.width>0,graph.size.width<=210,graph.size.height>0,graph.size.height<=120,
          graph.minX>=windowSize.width-240,graph.maxX<=windowSize.width-8,
          graph.minY>=120,graph.maxY<=windowSize.height-30 else {return nil}
    return graph
}

func dispatchVroidDrag(_ binding: NativeWindowBinding, title: String,
                       from: CGPoint, to: CGPoint, duration: Double,
                       via: [CGPoint] = [], graphBounds: CGRect? = nil,
                       deadline: Double, stopped: () -> Bool,
                       context: () throws -> Void) -> [String: Any] {
    var posted = 0
    var pointerAttempted = false
    var held = false
    var last = CGPoint.zero
    func refuse(_ detail: String) throws {
        throw NativeWindowInputError.refused(detail)
    }
    func check() throws {
        try context()
        guard !stopped(), ProcessInfo.processInfo.systemUptime < deadline else {
            try refuse("drag stopped or deadline expired"); return
        }
        guard nativeFrontmostProcessPID() == binding.pid else {
            try refuse("VRoid is no longer foreground"); return
        }
        try validateNativeWindowBinding(binding)
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(binding.window, kAXTitleAttribute as CFString, &raw) == .success,
              vroidDragTitleMatches(raw as? String, expected: title) else { try refuse("VRoid document changed"); return }
    }
    func event(_ kind: CGEventType, _ point: CGPoint) throws {
        // Posting a HID event alone can leave Unity polling an unchanged
        // global pointer. This route explicitly owns foreground pointer input.
        if kind == .mouseMoved || kind == .leftMouseDragged {
            pointerAttempted = true
            guard CGWarpMouseCursorPosition(point) == .success else {
                try refuse("foreground pointer positioning failed"); return
            }
        }
        guard let value = CGEvent(mouseEventSource: nil, mouseType: kind,
                                  mouseCursorPosition: point, mouseButton: .left) else {
            try refuse("mouse event construction failed"); return
        }
        value.flags = []
        let delta = kind == .leftMouseDragged
            ? vroidDragDelta(previous: last, current: point) : (0, 0)
        value.setIntegerValueField(.mouseEventDeltaX, value: delta.0)
        value.setIntegerValueField(.mouseEventDeltaY, value: delta.1)
        value.setIntegerValueField(.mouseEventClickState, value: 1)
        value.setDoubleValueField(.mouseEventPressure, value: kind == .leftMouseUp ? 0 : 1)
        value.post(tap: .cghidEventTap)
        posted += 1
        last = point
    }
    do {
        guard duration.isFinite, duration >= 0.5, duration <= 2,
              title.contains(".vroid"), from.x.isFinite, from.y.isFinite,
              to.x.isFinite, to.y.isFinite, via.count <= 12,
              via.allSatisfy({ $0.x.isFinite && $0.y.isFinite }) else { try refuse("invalid VRoid drag"); return [:] }
        guard let region=vroidDragRegion(windowSize:binding.windowBounds.size,graphBounds:graphBounds),
              region.contains(from),region.contains(to),via.allSatisfy({region.contains($0)}),
              ProcessInfo.processInfo.systemUptime + duration + 0.5 < deadline else {
            try refuse("drag outside declared hair canvas/curve graph or insufficient deadline"); return [:]
        }
        try context()
        try validateNativeWindowBinding(binding)
        // Foreground activation is part of this explicitly authorized action.
        guard let app = NSRunningApplication(processIdentifier: binding.pid),
              app.bundleIdentifier == "net.pixiv.vroid.macosx" else {
            try refuse("VRoid process identity mismatch"); return [:]
        }
        app.activate(options: [])
        guard AXUIElementPerformAction(binding.window, kAXRaiseAction as CFString) == .success else {
            try refuse("VRoid window could not be raised"); return [:]
        }
        Thread.sleep(forTimeInterval: 0.15)
        try check()
        let origin = binding.windowBounds.origin
        let start = CGPoint(x: origin.x + from.x, y: origin.y + from.y)
        let end = CGPoint(x: origin.x + to.x, y: origin.y + to.y)
        let path = [start] + via.map { CGPoint(x: origin.x + $0.x, y: origin.y + $0.y) } + [end]
        // Refuse if another app covers the start; do not click through it.
        let hitDeadline = min(deadline - duration - 0.5, ProcessInfo.processInfo.systemUptime + 1.2)
        let (hitPID, hitAttempts) = try vroidWaitForTargetHit(target:binding.pid, deadline:hitDeadline,
            now:{ProcessInfo.processInfo.systemUptime}, wait:{Thread.sleep(forTimeInterval:$0)}, check:check,
            owner:{
                var hit: AXUIElement?
                guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(start.x), Float(start.y), &hit) == .success,
                      let hit else { throw NativeWindowInputError.refused("drag target hit test unavailable") }
                var pid: pid_t = 0
                guard AXUIElementGetPid(hit, &pid) == .success else {
                    throw NativeWindowInputError.refused("drag target owner unavailable")
                }
                return pid
            })
        guard hitPID == binding.pid else {
            let owner = NSRunningApplication(processIdentifier: hitPID)?.bundleIdentifier ?? "unknown"
            try refuse("AX hit owner mismatch after \(hitAttempts) observations; visual occlusion unconfirmed: pid=\(hitPID), bundle=\(owner), point=\(start), window=\(binding.windowBounds)"); return [:]
        }
        try event(.mouseMoved, start)
        Thread.sleep(forTimeInterval: 0.1)
        try check()
        try event(.leftMouseDown, start)
        held = true
        Thread.sleep(forTimeInterval: 0.2)
        let steps = Int(ceil(duration * 60))
        let began = ProcessInfo.processInfo.systemUptime
        for i in 1...steps {
            try check()
            let fraction = Double(i) / Double(steps)
            let point = vroidStrokePoint(path, fraction: fraction)
            try event(.leftMouseDragged, point)
            let remaining = began + duration * fraction - ProcessInfo.processInfo.systemUptime
            if remaining > 0 { Thread.sleep(forTimeInterval: remaining) }
        }
        try check()
        try event(.leftMouseUp, end)
        held = false
        return ["type":"acted", "action":"vroid_drag", "effect":"unverified",
                "input_route":"foreground_hid_drag", "posted_events":posted,
                "window_id":binding.windowID, "pid":binding.pid,
                "hit_test_observations":hitAttempts,
                "relative_motion_explicit":true,
                "pointer_positioning":"foreground_warp",
                "requires_reobservation":true, "receiving_effect_verified":false]
    } catch {
        // Release only our own held button. A HID down must have a HID up,
        // otherwise Unity/global button state can remain stuck after abort.
        if held, let release = CGEvent(mouseEventSource:nil, mouseType:.leftMouseUp,
                                       mouseCursorPosition:last, mouseButton:.left) {
            release.flags = []
            release.setDoubleValueField(.mouseEventPressure, value: 0)
            release.post(tap: .cghidEventTap)
            posted += 1
        }
        return ["type":"error", "error":"vroid_drag_failed",
                "effect":posted > 0 || pointerAttempted ? "unknown" : "not_started",
                "posted_or_attempted_events":posted, "detail":String(describing:error)]
    }
}
