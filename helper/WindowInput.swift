// Read-only native binding and event construction. NO dispatch or activation.
// Caller owns observation identity, policy, Esc/deadline and partial-effect handling.
// Validation is not an atomic OS transaction: revalidate immediately before input.
// Public ABI: SDK sys/proc_info.h + libproc.h (proc_bsdinfo/PROC_PIDTBSDINFO).
// Private AX ABI: https://github.com/asmvik/yabai/blob/master/src/misc/extern.h
// Private CG SPI: WebKit 7596ac02075f631c107be8d4e0b056d236288433,
// Tools/TestRunnerShared/spi/CoreGraphicsTestSPI.h.
import Foundation
import AppKit
import ApplicationServices
import Darwin

enum NativeWindowInputError: Error {
    case unavailable(String)
    case refused(String)
    /// The app has to be in front and is not. Separate from `refused` because
    /// there IS a way — it costs the person their front app, which is not a
    /// price this helper pays on its own.
    case needsForeground(String)
}

func nativeProcessStartIdentity(_ pid: pid_t) -> Double? {
    guard pid > 0 else { return nil }
    var info = proc_bsdinfo()
    let size = MemoryLayout<proc_bsdinfo>.size
    let count = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, &info, Int32(size))
    guard count == size, info.pbi_pid == UInt32(pid),
          info.pbi_start_tvsec > 0, info.pbi_start_tvusec < 1_000_000 else { return nil }
    return Double(info.pbi_start_tvsec) + Double(info.pbi_start_tvusec) / 1_000_000
}

enum NativeWindowPointKind { case elementCenter, titlebar }

struct NativeWindowBinding {
    let element: AXUIElement
    let window: AXUIElement
    let pid: pid_t
    let processStart: Double
    let windowID: CGWindowID
    let windowBounds: CGRect
    let elementBounds: CGRect
    let point: CGPoint
    let pointKind: NativeWindowPointKind

    init(element: AXUIElement, window: AXUIElement, pid: pid_t, processStart: Double,
         windowID: CGWindowID, windowBounds: CGRect, elementBounds: CGRect, point: CGPoint,
         pointKind: NativeWindowPointKind = .elementCenter) {
        self.element = element; self.window = window; self.pid = pid
        self.processStart = processStart; self.windowID = windowID
        self.windowBounds = windowBounds; self.elementBounds = elementBounds
        self.point = point; self.pointKind = pointKind
    }
}

private enum WindowInputSPI {
    typealias AXWindow = @convention(c) (AXUIElement, UnsafeMutablePointer<CGWindowID>) -> Int32
    typealias SetLocation = @convention(c) (CGEvent, CGPoint) -> Void
    typealias GetLocation = @convention(c) (CGEvent) -> CGPoint

    // Handles intentionally retained for the lifetime of their function pointers.
    static let axLibrary = dlopen("/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices", RTLD_LAZY | RTLD_LOCAL)
    static let cgLibrary = dlopen("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics", RTLD_LAZY | RTLD_LOCAL)

    static func windowFunction() throws -> AXWindow {
        guard let library = axLibrary, let symbol = dlsym(library, "_AXUIElementGetWindow") else {
            throw NativeWindowInputError.unavailable("_AXUIElementGetWindow")
        }
        return unsafeBitCast(symbol, to: AXWindow.self)
    }

    static func locationFunctions() throws -> (SetLocation, GetLocation) {
        guard let library = cgLibrary,
              let setter = dlsym(library, "CGEventSetWindowLocation"),
              let getter = dlsym(library, "CGEventGetWindowLocation") else {
            throw NativeWindowInputError.unavailable("CGEvent window location SPI")
        }
        return (unsafeBitCast(setter, to: SetLocation.self), unsafeBitCast(getter, to: GetLocation.self))
    }
}

private func windowInputAttribute(_ element: AXUIElement, _ name: String) throws -> CFTypeRef {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success,
          let result = value else {
        throw NativeWindowInputError.refused("required AX attribute unavailable: \(name)")
    }
    return result
}

private func windowInputElement(_ value: CFTypeRef) throws -> AXUIElement {
    guard CFGetTypeID(value) == AXUIElementGetTypeID() else {
        throw NativeWindowInputError.refused("invalid AX element type")
    }
    return unsafeBitCast(value, to: AXUIElement.self)
}

private func windowInputPID(_ element: AXUIElement) throws -> pid_t {
    var pid: pid_t = 0
    guard AXUIElementGetPid(element, &pid) == .success, pid > 0 else {
        throw NativeWindowInputError.refused("AX process unavailable")
    }
    return pid
}

private func windowInputRole(_ element: AXUIElement) throws -> String {
    guard let role = try windowInputAttribute(element, kAXRoleAttribute) as? String else {
        throw NativeWindowInputError.refused("invalid AX role")
    }
    return role
}

private func windowInputRectIsValid(_ r: CGRect) -> Bool {
    [r.origin.x, r.origin.y, r.width, r.height, r.origin.x + r.width, r.origin.y + r.height].allSatisfy { $0.isFinite }
        && r.width > 0 && r.height > 0
}

private func windowInputFrame(_ element: AXUIElement) throws -> CGRect {
    let position = try windowInputAttribute(element, kAXPositionAttribute)
    let size = try windowInputAttribute(element, kAXSizeAttribute)
    guard CFGetTypeID(position) == AXValueGetTypeID(), CFGetTypeID(size) == AXValueGetTypeID() else {
        throw NativeWindowInputError.refused("invalid AX geometry types")
    }
    let pValue = unsafeBitCast(position, to: AXValue.self)
    let sValue = unsafeBitCast(size, to: AXValue.self)
    var p = CGPoint.zero
    var s = CGSize.zero
    guard AXValueGetType(pValue) == .cgPoint, AXValueGetType(sValue) == .cgSize,
          AXValueGetValue(pValue, .cgPoint, &p), AXValueGetValue(sValue, .cgSize, &s) else {
        throw NativeWindowInputError.refused("AX geometry unavailable")
    }
    let result = CGRect(origin: p, size: s)
    guard windowInputRectIsValid(result) else { throw NativeWindowInputError.refused("invalid AX geometry") }
    return result
}

private func windowInputAncestor(_ element: AXUIElement, _ pid: pid_t) throws -> AXUIElement {
    var current = element
    var seen: [AXUIElement] = []
    for depth in 0...20 {
        guard !seen.contains(where: { CFEqual($0, current) }), try windowInputPID(current) == pid else {
            throw NativeWindowInputError.refused("cyclic or cross-process ancestry")
        }
        if try windowInputRole(current) == kAXWindowRole { return current }
        guard depth < 20 else { break }
        seen.append(current)
        current = try windowInputElement(windowInputAttribute(current, kAXParentAttribute))
    }
    throw NativeWindowInputError.refused("bounded window ancestry unavailable")
}

private func windowInputNumber(_ value: Any?) throws -> Double {
    guard let number = value as? NSNumber,
          CFGetTypeID(number) != CFBooleanGetTypeID(), number.doubleValue.isFinite else {
        throw NativeWindowInputError.refused("invalid CG numeric metadata")
    }
    return number.doubleValue
}

private func windowInputCGFrame(_ windowID: CGWindowID, _ pid: pid_t) throws -> CGRect {
    guard let windows = CGWindowListCopyWindowInfo(.optionAll, 0) as? [[String: Any]] else {
        throw NativeWindowInputError.refused("CG window metadata unavailable")
    }
    var matches: [[String: Any]] = []
    for entry in windows {
        if try windowInputNumber(entry[kCGWindowNumber as String]) == Double(windowID) { matches.append(entry) }
    }
    guard matches.count == 1 else { throw NativeWindowInputError.refused("CG window not unique") }
    let entry = matches[0]
    guard try windowInputNumber(entry[kCGWindowOwnerPID as String]) == Double(pid),
          try windowInputNumber(entry[kCGWindowLayer as String]) == 0,
          let onScreen = entry[kCGWindowIsOnscreen as String] as? NSNumber,
          CFGetTypeID(onScreen) == CFBooleanGetTypeID(), onScreen.boolValue,
          let bounds = entry[kCGWindowBounds as String] as? [String: Any] else {
        throw NativeWindowInputError.refused("CG window ownership or visibility mismatch")
    }
    let rect = try CGRect(x: windowInputNumber(bounds["X"]), y: windowInputNumber(bounds["Y"]),
                          width: windowInputNumber(bounds["Width"]), height: windowInputNumber(bounds["Height"]))
    guard windowInputRectIsValid(rect) else { throw NativeWindowInputError.refused("invalid CG geometry") }
    return rect
}

/// Full exposed-tree modal check; AXSheets unsupported is not an empty list.
func nativeRequireWindowReady(_ window: AXUIElement, _ pid: pid_t,
                              maxNodes: Int = 5000, maxDepth: Int = 64) throws {
    guard maxNodes > 0, maxDepth >= 0 else { throw NativeWindowInputError.refused("invalid readiness budget") }
    func optional(_ node: AXUIElement, _ name: String) throws -> CFTypeRef? {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(node, name as CFString, &value)
        if error == .success, let value = value { return value }
        if [-25205, -25212].contains(error.rawValue) { return nil }
        throw NativeWindowInputError.refused("window state unavailable: \(name)")
    }
    func explicitlyFalse(_ node: AXUIElement, _ name: String) throws -> Bool {
        let value = try windowInputAttribute(node, name)
        return CFGetTypeID(value) == CFBooleanGetTypeID() && (value as? Bool) == false
    }
    let app = AXUIElementCreateApplication(pid)
    func windows() throws -> [AXUIElement] {
        guard let result = try windowInputAttribute(app, kAXWindowsAttribute) as? [AXUIElement] else {
            throw NativeWindowInputError.refused("application windows malformed")
        }
        return result
    }
    let before = try windows()
    guard before.filter({ CFEqual($0, window) }).count == 1,
          try explicitlyFalse(window, kAXMinimizedAttribute) else {
        throw NativeWindowInputError.refused("target window missing, ambiguous or minimized")
    }
    var remaining = maxNodes
    for top in before {
        guard try explicitlyFalse(top, "AXModal") else { throw NativeWindowInputError.refused("modal or unknown window") }
        if let value = try optional(top, "AXSheets") {
            guard let sheets = value as? [AXUIElement], sheets.isEmpty else {
                throw NativeWindowInputError.refused("sheet or malformed sheet list")
            }
        }
        var stack: [(AXUIElement, Int)] = [(top, 0)]
        while let (node, depth) = stack.popLast() {
            remaining -= 1
            guard remaining >= 0, depth <= maxDepth else {
                throw NativeWindowInputError.refused("readiness traversal budget exceeded")
            }
            let role = try windowInputRole(node)
            guard !role.isEmpty, role != "AXSheet" else { throw NativeWindowInputError.refused("attached sheet or malformed role") }
            if let value = try optional(node, kAXChildrenAttribute) {
                guard let children = value as? [AXUIElement] else { throw NativeWindowInputError.refused("window children malformed") }
                guard children.count <= remaining else { throw NativeWindowInputError.refused("readiness traversal budget exceeded") }
                stack.append(contentsOf: children.reversed().map { ($0, depth + 1) })
            }
        }
    }
    let after = try windows()
    guard after.count == before.count, zip(before, after).allSatisfy({ CFEqual($0.0, $0.1) }),
          try explicitlyFalse(window, kAXMinimizedAttribute) else {
        throw NativeWindowInputError.refused("window state changed during validation")
    }
    for top in before {
        guard try explicitlyFalse(top, "AXModal") else { throw NativeWindowInputError.refused("window became modal during validation") }
    }
}

func nativeTitlebarPoints(_ bounds: CGRect, _ anchor: CGRect) throws -> [CGPoint] {
    guard windowInputRectIsValid(bounds), windowInputRectIsValid(anchor),
          bounds.minX <= anchor.minX, anchor.maxX <= bounds.maxX,
          bounds.minY <= anchor.minY, anchor.maxY <= bounds.minY + min(bounds.height / 4, 50) else {
        throw NativeWindowInputError.refused("titlebar anchor outside upper window band")
    }
    return [0.5, 0.75, 0.25].map { CGPoint(x: bounds.minX + bounds.width * $0, y: anchor.midY) }
}

func captureNativeWindowBinding(_ element: AXUIElement, _ pid: pid_t,
                                expectedStart: Double,
                                pointKind: NativeWindowPointKind = .elementCenter) throws -> NativeWindowBinding {
    guard pid > 0, expectedStart.isFinite, expectedStart > 0,
          nativeProcessStartIdentity(pid) == expectedStart else {
        throw NativeWindowInputError.refused("process start identity mismatch")
    }
    let window = try windowInputAncestor(element, pid)
    var windowID: CGWindowID = 0
    let getWindow = try WindowInputSPI.windowFunction()
    guard getWindow(window, &windowID) == 0, windowID > 0 else {
        throw NativeWindowInputError.refused("AX window ID unavailable")
    }
    let bounds = try windowInputFrame(window)
    let rect = try windowInputFrame(element)
    guard try windowInputCGFrame(windowID, pid) == bounds,
          rect.minX >= bounds.minX, rect.minY >= bounds.minY,
          rect.maxX <= bounds.maxX, rect.maxY <= bounds.maxY else {
        throw NativeWindowInputError.refused("window or element geometry mismatch")
    }
    if pointKind == .titlebar {
        try nativeRequireWindowReady(window, pid)
        guard CFEqual(element, window),
              try windowInputAttribute(window, kAXSubroleAttribute) as? String == "AXStandardWindow" else {
            throw NativeWindowInputError.refused("titlebar requires a standard window")
        }
        let close = try windowInputElement(windowInputAttribute(window, "AXCloseButton"))
        guard try windowInputPID(close) == pid,
              try windowInputAttribute(close, kAXSubroleAttribute) as? String == "AXCloseButton",
              try CFEqual(windowInputAncestor(close, pid), window) else {
            throw NativeWindowInputError.refused("native titlebar anchor unavailable")
        }
        let anchor = try windowInputFrame(close)
        for point in try nativeTitlebarPoints(bounds, anchor) {
            var hit: AXUIElement?
            guard AXUIElementCopyElementAtPosition(AXUIElementCreateApplication(pid), Float(point.x), Float(point.y), &hit) == .success,
                  let hit = hit else { throw NativeWindowInputError.refused("application hit-test unavailable") }
            if try windowInputPID(hit) == pid, CFEqual(hit, window) {
                guard nativeProcessStartIdentity(pid) == expectedStart else {
                    throw NativeWindowInputError.refused("process changed during capture")
                }
                return NativeWindowBinding(element: element, window: window, pid: pid, processStart: expectedStart,
                    windowID: windowID, windowBounds: bounds, elementBounds: rect, point: point, pointKind: .titlebar)
            }
        }
        throw NativeWindowInputError.refused("no blank native titlebar point")
    }
    let point = CGPoint(x: rect.midX, y: rect.midY)
    var hit: AXUIElement?
    guard AXUIElementCopyElementAtPosition(AXUIElementCreateApplication(pid), Float(point.x), Float(point.y), &hit) == .success,
          var current = hit else { throw NativeWindowInputError.refused("application hit-test unavailable") }
    var seen: [AXUIElement] = []
    for depth in 0...20 {
        guard !seen.contains(where: { CFEqual($0, current) }), try windowInputPID(current) == pid else { break }
        if CFEqual(current, element) {
            guard nativeProcessStartIdentity(pid) == expectedStart else {
                throw NativeWindowInputError.refused("process changed during capture")
            }
            return NativeWindowBinding(element: element, window: window, pid: pid, processStart: expectedStart,
                                       windowID: windowID, windowBounds: bounds, elementBounds: rect, point: point)
        }
        if try windowInputRole(current) == kAXWindowRole { break }
        guard depth < 20 else { break }
        seen.append(current)
        current = try windowInputElement(windowInputAttribute(current, kAXParentAttribute))
    }
    throw NativeWindowInputError.refused("hit-test does not identify element or descendant")
}

func validateNativeWindowBinding(_ binding: NativeWindowBinding) throws {
    let fresh = try captureNativeWindowBinding(binding.element, binding.pid, expectedStart: binding.processStart,
                                               pointKind: binding.pointKind)
    guard CFEqual(fresh.window, binding.window), fresh.windowID == binding.windowID,
          fresh.windowBounds == binding.windowBounds, fresh.elementBounds == binding.elementBounds,
          fresh.point == binding.point else { throw NativeWindowInputError.refused("window binding changed") }
}

struct NativeWindowClick {
    let down: CGEvent
    let up: CGEvent
    let owners: [NSEvent]
    let eventNumber: Int
}

private final class WindowInputCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var number = 0
    func next() -> Int {
        lock.lock(); defer { lock.unlock() }
        number = number % Int(Int32.max) + 1
        return number
    }
}
private let windowInputCounter = WindowInputCounter()

func buildNativeWindowClick(_ binding: NativeWindowBinding) throws -> NativeWindowClick {
    let bounds = binding.windowBounds
    let point = binding.point
    guard binding.windowID > 0, windowInputRectIsValid(bounds), point.x.isFinite, point.y.isFinite,
          point.x >= bounds.minX, point.x < bounds.maxX, point.y >= bounds.minY, point.y < bounds.maxY else {
        throw NativeWindowInputError.refused("invalid click geometry")
    }
    let (setWindowLocation, getWindowLocation) = try WindowInputSPI.locationFunctions()
    let relative = CGPoint(x: point.x - bounds.minX, y: point.y - bounds.minY)
    let local = CGPoint(x: relative.x, y: bounds.height - relative.y)
    let number = windowInputCounter.next()
    let timestamp = ProcessInfo.processInfo.systemUptime
    var owners: [NSEvent] = []
    var events: [CGEvent] = []
    for (type, pressure) in [(NSEvent.EventType.leftMouseDown, Float(1)), (.leftMouseUp, Float(0))] {
        guard let owner = NSEvent.mouseEvent(with: type, location: local, modifierFlags: [], timestamp: timestamp,
                                            windowNumber: Int(binding.windowID), context: nil, eventNumber: number,
                                            clickCount: 1, pressure: pressure), let event = owner.cgEvent else {
            throw NativeWindowInputError.refused("mouse event construction failed")
        }
        event.flags = []
        event.location = point // Clears private window location; setter MUST follow.
        event.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: Int64(binding.windowID))
        event.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent, value: Int64(binding.windowID))
        setWindowLocation(event, relative)
        owners.append(owner); events.append(event)
    }
    for (index, event) in events.enumerated() {
        guard event.type == (index == 0 ? .leftMouseDown : .leftMouseUp), event.location == point,
              getWindowLocation(event) == relative, event.flags.isEmpty,
              event.getIntegerValueField(.mouseEventWindowUnderMousePointer) == Int64(binding.windowID),
              event.getIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent) == Int64(binding.windowID),
              event.getIntegerValueField(.mouseEventNumber) == Int64(number),
              event.getIntegerValueField(.mouseEventClickState) == 1,
              event.getDoubleValueField(.mouseEventPressure) == (index == 0 ? 1 : 0) else {
            throw NativeWindowInputError.refused("mouse event readback mismatch")
        }
    }
    return NativeWindowClick(down: events[0], up: events[1], owners: owners, eventNumber: number)
}
