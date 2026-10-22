// Global pointer hover: the one route that moves the person's cursor. It never
// presses a button or activates an app, and puts the cursor back where it was.
// Apps that react to where the real cursor is (hover effects, a character that
// avoids the cursor) cannot be exercised by window-bound events, which leave
// the global pointer alone by design.
import Foundation
import AppKit
import ApplicationServices

struct PointerHoverIO {
    var location: () -> CGPoint?
    var move: (CGPoint) -> Bool
    var frame: () throws -> CGRect
    var owner: (CGPoint) -> pid_t?
    var now: () -> Double
    var sleep: (Double) -> Void
}

let pointerHoverTolerance: CGFloat = 2

func pointerHoverSystemIO(window: AXUIElement) -> PointerHoverIO {
    let source = CGEventSource(stateID: .hidSystemState)
    source?.localEventsSuppressionInterval = 0
    return PointerHoverIO(
        location: { CGEvent(source: nil)?.location },
        move: { point in
            // A warp alone can leave apps that poll motion unchanged; the
            // moved event is what tracking areas and global monitors see.
            guard CGWarpMouseCursorPosition(point) == .success,
                  let event = CGEvent(mouseEventSource: source, mouseType: .mouseMoved,
                                      mouseCursorPosition: point, mouseButton: .left) else { return false }
            event.flags = []
            event.post(tap: .cghidEventTap)
            return true
        },
        frame: { try windowInputFrame(window) },
        owner: { point in
            var hit: AXUIElement?
            var pid: pid_t = 0
            guard AXUIElementCopyElementAtPosition(AXUIElementCreateSystemWide(), Float(point.x), Float(point.y), &hit) == .success,
                  let hit, AXUIElementGetPid(hit, &pid) == .success else { return nil }
            return pid
        },
        now: { ProcessInfo.processInfo.systemUptime },
        sleep: { Thread.sleep(forTimeInterval: $0) })
}

func pointerHoverPoint(_ raw: Any?) -> CGPoint? {
    guard let point = raw as? [String: Any], Set(point.keys) == Set(["x", "y"]),
          let x = point["x"] as? NSNumber, let y = point["y"] as? NSNumber,
          CFGetTypeID(x) != CFBooleanGetTypeID(), CFGetTypeID(y) != CFBooleanGetTypeID(),
          x.doubleValue.isFinite, y.doubleValue.isFinite, x.doubleValue >= 0, y.doubleValue >= 0 else { return nil }
    return CGPoint(x: x.doubleValue, y: y.doubleValue)
}

private func pointerHoverJSON(_ point: CGPoint) -> [String: Double] {
    ["x": Double(point.x), "y": Double(point.y)]
}

private func pointerHoverJSON(_ rect: CGRect) -> [String: Double] {
    ["x": Double(rect.minX), "y": Double(rect.minY), "width": Double(rect.width), "height": Double(rect.height)]
}

// `path` is in points relative to the observed window's top-left. The window
// frame is sampled during the hold, so a window that moves away from the
// cursor is reported, not inferred.
func dispatchPointerHover(pid: pid_t, path: [CGPoint], duration: Double, hold: Double,
                          deadline: Double, stopped: () -> Bool,
                          context: () throws -> Void, io: PointerHoverIO,
                          atHoldEnd: (() -> [String: Any])? = nil) -> [String: Any] {
    var moved = 0
    var original: CGPoint?
    var last: CGPoint?
    var personMoved = false
    var frames: [CGRect] = []
    var owners: [Bool] = []
    func refuse(_ detail: String) -> NativeWindowInputError { .refused(detail) }
    func note(_ frame: CGRect) { if frames.last != frame { frames.append(frame) } }
    // The person moving the mouse ends the hover at once, and the cursor stays
    // where they put it: taking it back from them is not restoring anything.
    func personTookOver() -> Bool {
        guard let last, let here = io.location() else { return false }
        if hypot(here.x - last.x, here.y - last.y) > pointerHoverTolerance { personMoved = true }
        return personMoved
    }
    func step(_ point: CGPoint) throws {
        guard !personTookOver() else { throw refuse("person moved the pointer") }
        guard !stopped(), io.now() < deadline else { throw refuse("hover stopped or deadline expired") }
        try context()
        guard io.move(point) else { throw refuse("pointer positioning failed") }
        moved += 1
        last = point
    }
    func restore() -> Bool {
        guard let original, moved > 0, !personMoved, !personTookOver() else { return false }
        guard io.move(original) else { return false }
        moved += 1
        guard let here = io.location() else { return false }
        return hypot(here.x - original.x, here.y - original.y) <= pointerHoverTolerance
    }
    do {
        guard !path.isEmpty, path.count <= 13, duration.isFinite, duration >= 0, duration <= 2,
              hold.isFinite, hold >= 0, hold <= 2 else { throw refuse("invalid hover") }
        guard io.now() + duration + hold + (atHoldEnd == nil ? 0.3 : 1.3) < deadline else { throw refuse("insufficient deadline") }
        try context()
        let first = try io.frame()
        guard path.allSatisfy({ $0.x < first.width && $0.y < first.height }) else {
            throw refuse("hover point outside the observed window")
        }
        guard let start = io.location() else { throw refuse("pointer location unavailable") }
        original = start
        note(first)
        let screen = path.map { CGPoint(x: first.minX + $0.x, y: first.minY + $0.y) }
        owners = screen.map { io.owner($0) == pid }
        let steps = duration > 0 ? max(1, Int(ceil(duration * 60))) : 1
        let began = io.now()
        for i in 1...steps {
            let fraction = Double(i) / Double(steps)
            try step(screen.count == 1 ? screen[0] : pointerHoverPathPoint(screen, fraction: fraction))
            let remaining = began + duration * fraction - io.now()
            if remaining > 0 { io.sleep(remaining) }
        }
        let holdEnd = io.now() + hold
        repeat {
            if let frame = try? io.frame() { note(frame) }
            guard !personTookOver() else { throw refuse("person moved the pointer") }
            guard !stopped() else { throw refuse("hover stopped") }
            let remaining = holdEnd - io.now()
            if remaining > 0 { io.sleep(min(0.05, remaining)) }
        } while io.now() < holdEnd
        if let frame = try? io.frame() { note(frame) }
        // A picture while the cursor is still there: once it is restored, an
        // app that reacted to it may already have gone back.
        let capture = atHoldEnd?()
        let restored = restore()
        var reply: [String: Any] = ["type": "acted", "action": "hover", "input_route": "global_pointer",
                "pid": pid, "pointer_events": moved, "buttons": 0,
                "original_pointer": pointerHoverJSON(start),
                "pointer_restored": restored, "person_moved_pointer": personMoved,
                "hit_owner_matches": owners,
                "window_frames": frames.map { pointerHoverJSON($0) },
                "window_moved": frames.count > 1,
                "unverified": "observe_effect", "reobserve_required": true]
        if let capture { reply["hold_capture"] = capture }
        return reply
    } catch {
        let restored = restore()
        var reply: [String: Any] = ["type": "error",
            "error": personMoved ? "person_moved_pointer" : "hover_failed",
            "effect": moved > 0 ? "unknown" : "not_started",
            "pointer_events": moved, "buttons": 0,
            "pointer_restored": restored, "person_moved_pointer": personMoved,
            "detail": String(describing: error)]
        if let original { reply["original_pointer"] = pointerHoverJSON(original) }
        if !frames.isEmpty { reply["window_frames"] = frames.map { pointerHoverJSON($0) } }
        return reply
    }
}

// Arc-length sampling along the requested path (the foreground drag has its own).
func pointerHoverPathPoint(_ path: [CGPoint], fraction: Double) -> CGPoint {
    let lengths = zip(path, path.dropFirst()).map { hypot($0.1.x - $0.0.x, $0.1.y - $0.0.y) }
    let total = lengths.reduce(0, +)
    guard total > 0 else { return path.last! }
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
