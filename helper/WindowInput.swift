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

enum NativeWindowPointKind { case elementCenter, titlebar, windowPoint, windowInput }

struct NativeWindowKeyPair {
    let down: CGEvent
    let up: CGEvent
    let owners: [NSEvent]
}

func nativeNavigationKey(_ key: String) -> (UInt16, String, String, NSEvent.ModifierFlags)? {
    switch key {
    case "Tab": return (48, "\t", "\t", [])
    case "shift+Tab": return (48, "\u{19}", "\t", .shift)
    case "Up": return (126, "\u{F700}", "\u{F700}", .function)
    case "Down": return (125, "\u{F701}", "\u{F701}", .function)
    case "Left": return (123, "\u{F702}", "\u{F702}", .function)
    case "Right": return (124, "\u{F703}", "\u{F703}", .function)
    default: return nil
    }
}

/// Return, the one commit key. Kept out of `nativeNavigationKey` so nothing
/// that accepts "navigation" (window_input, the opaque key path) accepts it;
/// only `press_key` asks for it, after its own target checks.
func nativeSubmitKey(_ key: String) -> (UInt16, String, String, NSEvent.ModifierFlags)? {
    key == "Return" ? (36, "\r", "\r", []) : nil
}

func buildNativeWindowKey(_ windowID: CGWindowID, key: String,
                          submit: Bool = false) throws -> NativeWindowKeyPair {
    guard windowID > 0,
          let (code, chars, plain, flags) = nativeNavigationKey(key) ?? (submit ? nativeSubmitKey(key) : nil) else {
        throw NativeWindowInputError.refused("unsupported navigation key or window ID")
    }
    var owners: [NSEvent] = [], events: [CGEvent] = []
    let timestamp = ProcessInfo.processInfo.systemUptime
    for kind in [NSEvent.EventType.keyDown, .keyUp] {
        guard let owner = NSEvent.keyEvent(with: kind, location: .zero, modifierFlags: flags,
            timestamp: timestamp, windowNumber: Int(windowID), context: nil, characters: chars,
            charactersIgnoringModifiers: plain, isARepeat: false, keyCode: code), let event = owner.cgEvent else {
            throw NativeWindowInputError.unavailable("key event construction failed")
        }
        owners.append(owner); events.append(event)
    }
    for (number, event) in events.enumerated() {
        let kind: NSEvent.EventType = number == 0 ? .keyDown : .keyUp
        guard event.type == (number == 0 ? .keyDown : .keyUp),
              let reflected = NSEvent(cgEvent: event), reflected.type == kind,
              reflected.windowNumber == Int(windowID), reflected.keyCode == code,
              event.getIntegerValueField(.keyboardEventKeycode) == Int64(code),
              reflected.characters == chars, reflected.charactersIgnoringModifiers == plain,
              event.flags.rawValue == UInt64(flags.rawValue), reflected.modifierFlags == flags,
              !reflected.isARepeat, event.getIntegerValueField(.keyboardEventAutorepeat) == 0 else {
            throw NativeWindowInputError.unavailable("key event readback mismatch")
        }
    }
    return NativeWindowKeyPair(down: events[0], up: events[1], owners: owners)
}

func requireNativeWindowTarget(_ binding: NativeWindowBinding, scope: NativeWindowReadScope) throws {
    try nativeRequireWindowReady(binding.window, binding.pid, scope: scope)
    let app = AXUIElementCreateApplication(binding.pid)
    let window = try windowInputElement(windowInputAttribute(app, kAXFocusedWindowAttribute, scope: scope))
    guard CFEqual(window, binding.window) else {
        throw NativeWindowInputError.refused("target window is not the app focused window")
    }
}

func requireNativeKeyTarget(_ binding: NativeWindowBinding, element: Bool,
                            scope: NativeWindowReadScope) throws {
    try nativeRequireWindowReady(binding.window, binding.pid, scope: scope)
    let app = AXUIElementCreateApplication(binding.pid)
    let window = try windowInputElement(windowInputAttribute(app, kAXFocusedWindowAttribute, scope: scope))
    guard CFEqual(window, binding.window) else {
        throw NativeWindowInputError.refused("target window is not the app focused window")
    }
    let focused = try windowInputElement(windowInputAttribute(app, kAXFocusedUIElementAttribute, scope: scope))
    guard CFEqual(try windowInputAncestor(focused, binding.pid, scope: scope), binding.window),
          !element || CFEqual(focused, binding.element) else {
        throw NativeWindowInputError.refused("target input element or window changed")
    }
}

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
    let relativePoint: CGPoint?

    init(element: AXUIElement, window: AXUIElement, pid: pid_t, processStart: Double,
         windowID: CGWindowID, windowBounds: CGRect, elementBounds: CGRect, point: CGPoint,
         pointKind: NativeWindowPointKind = .elementCenter, relativePoint: CGPoint? = nil) {
        self.element = element; self.window = window; self.pid = pid
        self.processStart = processStart; self.windowID = windowID
        self.windowBounds = windowBounds; self.elementBounds = elementBounds
        self.point = point; self.pointKind = pointKind; self.relativePoint = relativePoint
    }
}

enum WindowInputSPI {
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

final class NativeWindowReadScope {
    let deadline: Double
    let check: () throws -> Void
    init(deadline: Double, check: @escaping () throws -> Void) { self.deadline = deadline; self.check = check }
    func ready() throws {
        try check()
        guard deadline.isFinite, ProcessInfo.processInfo.systemUptime < deadline else {
            throw NativeWindowInputError.refused("window observation deadline expired")
        }
    }
    func withTimeout<T>(_ element: AXUIElement, _ operation: () -> T) throws -> T {
        try ready()
        let remaining = deadline - ProcessInfo.processInfo.systemUptime
        guard remaining > 0, AXUIElementSetMessagingTimeout(element, Float(min(0.5, remaining))) == .success else {
            throw NativeWindowInputError.unavailable("window AX timeout unavailable")
        }
        let result = operation()
        guard AXUIElementSetMessagingTimeout(element, 0) == .success else {
            throw NativeWindowInputError.unavailable("window AX timeout reset failed")
        }
        try ready()
        return result
    }
    func read(_ element: AXUIElement, _ name: String) throws -> (AXError, CFTypeRef?) {
        try withTimeout(element) {
            var value: CFTypeRef?
            let error = AXUIElementCopyAttributeValue(element, name as CFString, &value)
            return (error, value)
        }
    }
}

private func windowInputHit(_ point: CGPoint, _ pid: pid_t,
                            scope: NativeWindowReadScope?) throws -> AXUIElement {
    let app = AXUIElementCreateApplication(pid)
    var hit: AXUIElement?
    let error: AXError
    if let scope {
        error = try scope.withTimeout(app) {
            AXUIElementCopyElementAtPosition(app, Float(point.x), Float(point.y), &hit)
        }
    } else {
        error = AXUIElementCopyElementAtPosition(app, Float(point.x), Float(point.y), &hit)
    }
    guard error == .success, let hit else { throw NativeWindowInputError.refused("application hit-test unavailable") }
    return hit
}

func windowInputAttribute(_ element: AXUIElement, _ name: String,
                                  scope: NativeWindowReadScope? = nil) throws -> CFTypeRef {
    var value: CFTypeRef?
    let error: AXError
    if let scope { (error, value) = try scope.read(element, name) }
    else { error = AXUIElementCopyAttributeValue(element, name as CFString, &value) }
    guard error == .success,
          let result = value else {
        throw NativeWindowInputError.refused("required AX attribute unavailable: \(name)")
    }
    return result
}

func windowInputElement(_ value: CFTypeRef) throws -> AXUIElement {
    guard CFGetTypeID(value) == AXUIElementGetTypeID() else {
        throw NativeWindowInputError.refused("invalid AX element type")
    }
    return unsafeBitCast(value, to: AXUIElement.self)
}

func windowInputPID(_ element: AXUIElement) throws -> pid_t {
    var pid: pid_t = 0
    guard AXUIElementGetPid(element, &pid) == .success, pid > 0 else {
        throw NativeWindowInputError.refused("AX process unavailable")
    }
    return pid
}

func windowInputRole(_ element: AXUIElement, scope: NativeWindowReadScope? = nil) throws -> String {
    guard let role = try windowInputAttribute(element, kAXRoleAttribute, scope: scope) as? String else {
        throw NativeWindowInputError.refused("invalid AX role")
    }
    return role
}

private func windowInputRectIsValid(_ r: CGRect) -> Bool {
    [r.origin.x, r.origin.y, r.width, r.height, r.origin.x + r.width, r.origin.y + r.height].allSatisfy { $0.isFinite }
        && r.width > 0 && r.height > 0
}

func windowInputFrame(_ element: AXUIElement, scope: NativeWindowReadScope? = nil) throws -> CGRect {
    let position = try windowInputAttribute(element, kAXPositionAttribute, scope: scope)
    let size = try windowInputAttribute(element, kAXSizeAttribute, scope: scope)
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

private func windowInputAncestor(_ element: AXUIElement, _ pid: pid_t,
                                 scope: NativeWindowReadScope? = nil) throws -> AXUIElement {
    var current = element
    var seen: [AXUIElement] = []
    for depth in 0...20 {
        guard !seen.contains(where: { CFEqual($0, current) }), try windowInputPID(current) == pid else {
            throw NativeWindowInputError.refused("cyclic or cross-process ancestry")
        }
        if try windowInputRole(current, scope: scope) == kAXWindowRole { return current }
        guard depth < 20 else { break }
        seen.append(current)
        current = try windowInputElement(windowInputAttribute(current, kAXParentAttribute, scope: scope))
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

func windowInputCGFrame(_ windowID: CGWindowID, _ pid: pid_t,
                                scope: NativeWindowReadScope? = nil) throws -> CGRect {
    try scope?.ready()
    guard let windows = CGWindowListCopyWindowInfo(.optionAll, 0) as? [[String: Any]] else {
        throw NativeWindowInputError.refused("CG window metadata unavailable")
    }
    var matches: [[String: Any]] = []
    try scope?.ready()
    for entry in windows {
        try scope?.ready()
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
                              maxNodes: Int = 5000, maxDepth: Int = 64,
                              scope: NativeWindowReadScope? = nil) throws {
    guard maxNodes > 0, maxDepth >= 0 else { throw NativeWindowInputError.refused("invalid readiness budget") }
    func optional(_ node: AXUIElement, _ name: String) throws -> CFTypeRef? {
        var value: CFTypeRef?
        let error: AXError
        if let scope { (error, value) = try scope.read(node, name) }
        else { error = AXUIElementCopyAttributeValue(node, name as CFString, &value) }
        if error == .success, let value = value { return value }
        if [-25205, -25212].contains(error.rawValue) { return nil }
        throw NativeWindowInputError.refused("window state unavailable: \(name)")
    }
    func explicitlyFalse(_ node: AXUIElement, _ name: String) throws -> Bool {
        let value = try windowInputAttribute(node, name, scope: scope)
        return CFGetTypeID(value) == CFBooleanGetTypeID() && (value as? Bool) == false
    }
    let app = AXUIElementCreateApplication(pid)
    func windows() throws -> [AXUIElement] {
        guard let result = try windowInputAttribute(app, kAXWindowsAttribute, scope: scope) as? [AXUIElement] else {
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
            let role = try windowInputRole(node, scope: scope)
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
                                pointKind: NativeWindowPointKind = .elementCenter,
                                scope: NativeWindowReadScope? = nil,
                                relativePoint: CGPoint? = nil) throws -> NativeWindowBinding {
    try scope?.ready()
    guard pid > 0, expectedStart.isFinite, expectedStart > 0,
          nativeProcessStartIdentity(pid) == expectedStart else {
        throw NativeWindowInputError.refused("process start identity mismatch")
    }
    let window = try windowInputAncestor(element, pid, scope: scope)
    var windowID: CGWindowID = 0
    let getWindow = try WindowInputSPI.windowFunction()
    let windowError: Int32
    if let scope { windowError = try scope.withTimeout(window) { getWindow(window, &windowID) } }
    else { windowError = getWindow(window, &windowID) }
    guard windowError == 0, windowID > 0 else {
        throw NativeWindowInputError.refused("AX window ID unavailable")
    }
    let bounds = try windowInputFrame(window, scope: scope)
    let rect = try windowInputFrame(element, scope: scope)
    guard try windowInputCGFrame(windowID, pid, scope: scope) == bounds,
          rect.minX >= bounds.minX, rect.minY >= bounds.minY,
          rect.maxX <= bounds.maxX, rect.maxY <= bounds.maxY else {
        throw NativeWindowInputError.refused("window or element geometry mismatch")
    }
    if pointKind == .windowInput {
        try nativeRequireWindowReady(window, pid, scope: scope)
        guard relativePoint == nil, CFEqual(element, window),
              try windowInputAttribute(window, kAXSubroleAttribute, scope: scope) as? String == "AXStandardWindow",
              nativeProcessStartIdentity(pid) == expectedStart else {
            throw NativeWindowInputError.refused("opaque input requires an observed standard window")
        }
        return NativeWindowBinding(element: element, window: window, pid: pid, processStart: expectedStart,
            windowID: windowID, windowBounds: bounds, elementBounds: rect,
            point: CGPoint(x: bounds.midX, y: bounds.midY), pointKind: .windowInput)
    }
    if pointKind == .windowPoint {
        try nativeRequireWindowReady(window, pid, scope: scope)
        guard CFEqual(element, window),
              try windowInputAttribute(window, kAXSubroleAttribute, scope: scope) as? String == "AXStandardWindow",
              let relativePoint, relativePoint.x.isFinite, relativePoint.y.isFinite,
              relativePoint.x >= 0, relativePoint.x < bounds.width,
              relativePoint.y >= 0, relativePoint.y < bounds.height,
              nativeProcessStartIdentity(pid) == expectedStart else {
            throw NativeWindowInputError.refused("invalid observed window point")
        }
        return NativeWindowBinding(element: element, window: window, pid: pid, processStart: expectedStart,
            windowID: windowID, windowBounds: bounds, elementBounds: rect,
            point: CGPoint(x: bounds.minX+relativePoint.x, y: bounds.minY+relativePoint.y),
            pointKind: .windowPoint, relativePoint: relativePoint)
    }
    if relativePoint != nil { throw NativeWindowInputError.refused("point is only valid for a window-point binding") }
    if pointKind == .titlebar {
        try nativeRequireWindowReady(window, pid, scope: scope)
        guard CFEqual(element, window),
              try windowInputAttribute(window, kAXSubroleAttribute, scope: scope) as? String == "AXStandardWindow" else {
            throw NativeWindowInputError.refused("titlebar requires a standard window")
        }
        let close = try windowInputElement(windowInputAttribute(window, "AXCloseButton", scope: scope))
        guard try windowInputPID(close) == pid,
              try windowInputAttribute(close, kAXSubroleAttribute, scope: scope) as? String == "AXCloseButton",
              try CFEqual(windowInputAncestor(close, pid, scope: scope), window) else {
            throw NativeWindowInputError.refused("native titlebar anchor unavailable")
        }
        let anchor = try windowInputFrame(close, scope: scope)
        for point in try nativeTitlebarPoints(bounds, anchor) {
            try scope?.ready()
            let hit = try windowInputHit(point, pid, scope: scope)
            try scope?.ready()
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
    var current = try windowInputHit(point, pid, scope: scope)
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
        if try windowInputRole(current, scope: scope) == kAXWindowRole { break }
        guard depth < 20 else { break }
        seen.append(current)
        current = try windowInputElement(windowInputAttribute(current, kAXParentAttribute, scope: scope))
    }
    throw NativeWindowInputError.refused("hit-test does not identify element or descendant")
}

func validateNativeWindowBinding(_ binding: NativeWindowBinding, scope: NativeWindowReadScope? = nil) throws {
    let fresh = try captureNativeWindowBinding(binding.element, binding.pid, expectedStart: binding.processStart,
                                               pointKind: binding.pointKind, scope: scope, relativePoint: binding.relativePoint)
    guard CFEqual(fresh.window, binding.window), fresh.windowID == binding.windowID,
          fresh.windowBounds == binding.windowBounds, fresh.elementBounds == binding.elementBounds,
          fresh.point == binding.point else { throw NativeWindowInputError.refused("window binding changed") }
}

/// The window server's id for an accessibility window, or nil.
///
/// The SPI that answers this is private to this file, and a capture needs the
/// same answer a click does — the window the OBSERVATION described, not one
/// that merely shares its title.
func nativeWindowID(_ element: AXUIElement) -> CGWindowID? {
    guard let ask = try? WindowInputSPI.windowFunction() else { return nil }
    var id = CGWindowID(0)
    return ask(element, &id) == 0 && id != 0 ? id : nil   // kAXErrorSuccess
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


/// These inputs still require explicit host semantics and receiving proof.
/// Text carries no command modifiers and may not encode navigation characters.
enum NativeOpaqueWindowInput {
    case key(String)
    case text(String)
    var valid: Bool {
        switch self {
        case .key(let key): return nativeNavigationKey(key) != nil
        case .text(let text): return nativeWindowTextValid(text)
        }
    }
}

func nativeWindowTextValid(_ text: String) -> Bool {
    !text.isEmpty && text.utf16.count <= 64 && !text.unicodeScalars.contains {
        CharacterSet.controlCharacters.contains($0) && $0.properties.generalCategory == .control
            || $0.value == 0x2028 || $0.value == 0x2029
            || (0xF700...0xF8FF).contains($0.value)
    }
}

func buildNativeWindowText(_ windowID: CGWindowID, text: String) throws -> NativeWindowKeyPair {
    guard windowID > 0, nativeWindowTextValid(text) else {
        throw NativeWindowInputError.refused("invalid opaque window text or window ID")
    }
    var owners: [NSEvent] = [], events: [CGEvent] = []
    var keyUpChars: String?, keyUpPlain: String?
    let timestamp = ProcessInfo.processInfo.systemUptime
    var units = Array(text.utf16)
    for kind in [NSEvent.EventType.keyDown, .keyUp] {
        guard let owner = NSEvent.keyEvent(with: kind, location: .zero, modifierFlags: [],
            timestamp: timestamp, windowNumber: Int(windowID), context: nil, characters: "",
            charactersIgnoringModifiers: "", isARepeat: false, keyCode: 0), let event = owner.cgEvent else {
            throw NativeWindowInputError.unavailable("Unicode event construction failed")
        }
        if kind == .keyUp {
            guard let baseline = NSEvent(cgEvent: event) else {
                throw NativeWindowInputError.unavailable("Unicode key-up baseline unavailable")
            }
            keyUpChars = baseline.characters; keyUpPlain = baseline.charactersIgnoringModifiers
        }
        event.keyboardSetUnicodeString(stringLength: units.count, unicodeString: &units)
        owners.append(owner); events.append(event)
    }
    for (number, event) in events.enumerated() {
        let kind: NSEvent.EventType = number == 0 ? .keyDown : .keyUp
        var readUnits = [UniChar](repeating: 0, count: 64), readLength = 0
        event.keyboardGetUnicodeString(maxStringLength: 64, actualStringLength: &readLength, unicodeString: &readUnits)
        guard readLength == units.count, Array(readUnits.prefix(readLength)) == units,
              event.type == (number == 0 ? .keyDown : .keyUp),
              let reflected = NSEvent(cgEvent: event), reflected.type == kind,
              reflected.windowNumber == Int(windowID), reflected.keyCode == 0,
              event.getIntegerValueField(.keyboardEventKeycode) == 0,
              reflected.characters == (number == 0 ? text : keyUpChars),
              reflected.charactersIgnoringModifiers == (number == 0 ? text : keyUpPlain),
              event.flags.rawValue == 0, reflected.modifierFlags.isEmpty,
              !reflected.isARepeat, event.getIntegerValueField(.keyboardEventAutorepeat) == 0 else {
            throw NativeWindowInputError.unavailable("Unicode window event readback mismatch")
        }
    }
    return NativeWindowKeyPair(down: events[0], up: events[1], owners: owners)
}

// MARK: - Return into the Go to Folder sheet (press_key Return only)

/// An AX attribute read: the caller decides how it is bounded.
typealias AXRead = (AXUIElement, String) throws -> (AXError, CFTypeRef?)
//
// The one exception to "no sheet anywhere": a keyDown(Return) into the
// path field of the app's single Go to Folder sheet. Its own readiness and
// dispatch, so every other input keeps the no-sheet rule. Measured on an
// owned fixture (reviews/2026-10-08/return-key/sheet-cg-measurement.md): a
// keyDown alone confirms the field, and AppKit delivers it to the app's key
// window — the sheet — whatever window number the event names. So what
// keeps it from landing elsewhere is the check just before posting: the
// app's focused window IS that sheet and its focused element IS that field.
// Only the keyDown is sent: a normal Return closes the sheet, and a keyUp
// would then have no target left to be checked against.

/// The app's whole exposed tree holds exactly one AXSheet, `sheet` (CFEqual),
/// and nothing is modal. Window tops must say AXModal = false; a top that is
/// not a window (Finder's desktop AXScrollArea) may lack AXModal but may not
/// say true. Any unreadable role, children or modal state refuses; so does
/// another sheet, the same sheet twice, or none.
func nativeSheetSubmitReady(tops: [AXUIElement], sheet: AXUIElement, read: AXRead,
                            maxNodes: Int = 5000, maxDepth: Int = 64) throws {
    func value(_ node: AXUIElement, _ name: String) throws -> CFTypeRef? {
        let (error, result) = try read(node, name)
        if error == .success, let result { return result }
        if error == .noValue || error == .attributeUnsupported { return nil }
        throw NativeWindowInputError.refused("window state unavailable: \(name)")
    }
    func role(_ node: AXUIElement) throws -> String {
        guard let raw = try value(node, kAXRoleAttribute as String), CFGetTypeID(raw) == CFStringGetTypeID(),
              let text = raw as? String, !text.isEmpty else {
            throw NativeWindowInputError.refused("role unreadable")
        }
        return text
    }
    var found = 0, remaining = maxNodes
    for top in tops {
        let topRole = try role(top)
        let modal = try value(top, "AXModal")
        if let modal {
            guard CFGetTypeID(modal) == CFBooleanGetTypeID(), (modal as? Bool) == false else {
                throw NativeWindowInputError.refused("modal or unknown window")
            }
        } else if topRole == kAXWindowRole as String {
            throw NativeWindowInputError.refused("window modal state unavailable")
        }
        var stack: [(AXUIElement, Int)] = [(top, 0)]
        while let (node, depth) = stack.popLast() {
            remaining -= 1
            guard remaining >= 0, depth <= maxDepth else {
                throw NativeWindowInputError.refused("readiness traversal budget exceeded")
            }
            if try role(node) == "AXSheet" {
                guard CFEqual(node, sheet) else { throw NativeWindowInputError.refused("another sheet is open") }
                found += 1
            }
            if let raw = try value(node, kAXChildrenAttribute as String) {
                guard let children = raw as? [AXUIElement] else {
                    throw NativeWindowInputError.refused("window children malformed")
                }
                stack.append(contentsOf: children.reversed().map { ($0, depth + 1) })
            }
        }
    }
    guard found == 1 else { throw NativeWindowInputError.refused("the sheet is missing or seen twice") }
}
