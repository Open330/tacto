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

/// Prove absence/replacement, never equate an unreadable start time with exit.
/// kill(pid, 0) sends no signal. EPERM and other errors remain unproven.
func nativeProcessInstanceEnded(_ pid:pid_t,expectedStart:Double?,
    probe:((pid_t)->(Int32,Int32))?=nil,
    readStart:((pid_t)->Double?)=nativeProcessStartIdentity)->Bool {
    guard pid>0 else {return false}
    let result:(Int32,Int32)
    if let probe {result=probe(pid)}
    else {errno=0;let status=Darwin.kill(pid,0);result=(status,errno)}
    if result.0 == -1 {return result.1==ESRCH}
    guard result.0==0,let expectedStart,expectedStart.isFinite,expectedStart>0,
          let current=readStart(pid),current.isFinite,current>0 else {return false}
    return current != expectedStart
}

/// Auxiliary bundle metadata for an already observed process. This is NOT a
/// liveness/PID-reuse check: callers must still match the OS start identity.
/// Read a fresh per-PID wrapper each time; never retain NSRunningApplication.
func nativeObservedBundleMatches(_ pid: pid_t, _ bundle: String,
    lookup: ((pid_t) -> (pid_t, String?, Bool)?)? = nil) -> Bool {
    guard pid > 0, !bundle.isEmpty else { return false }
    let metadata: (pid_t, String?, Bool)?
    if let lookup { metadata = lookup(pid) }
    else if let app = NSRunningApplication(processIdentifier: pid) {
        metadata = (app.processIdentifier, app.bundleIdentifier, app.isTerminated)
    } else { metadata = nil }
    guard let (actualPID, actualBundle, terminated) = metadata,
          actualPID == pid, !terminated else { return false }
    return actualBundle?.lowercased() == bundle.lowercased()
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

/// Where a bound Return may land: the app's focused window is the bound one,
/// and the keyboard focus is the bound input itself — or, for a list row, the
/// row is selected and the focus is the table or outline that holds it (a
/// row is never the focused element itself; its list is).
func requireNativeSubmitTarget(_ binding: NativeWindowBinding, scope: NativeWindowReadScope) throws {
    try nativeRequireWindowReady(binding.window, binding.pid, scope: scope)
    let app = AXUIElementCreateApplication(binding.pid)
    let window = try windowInputElement(windowInputAttribute(app, kAXFocusedWindowAttribute, scope: scope))
    guard CFEqual(window, binding.window) else {
        throw NativeWindowInputError.refused("target window is not the app focused window")
    }
    let focused = try windowInputElement(windowInputAttribute(app, kAXFocusedUIElementAttribute, scope: scope))
    guard CFEqual(try windowInputAncestor(focused, binding.pid, scope: scope), binding.window) else {
        throw NativeWindowInputError.refused("focus is outside the bound window")
    }
    if CFEqual(focused, binding.element) { return }
    guard try windowInputRole(binding.element, scope: scope) == kAXRowRole as String,
          try windowInputAttribute(binding.element, kAXSelectedAttribute, scope: scope) as? Bool == true,
          ["AXTable", "AXOutline"].contains(try windowInputRole(focused, scope: scope)) else {
        throw NativeWindowInputError.refused("the bound input is not the keyboard focus")
    }
    var node = binding.element
    for _ in 0..<8 {
        node = try windowInputElement(windowInputAttribute(node, kAXParentAttribute, scope: scope))
        if CFEqual(node, focused) { return }
    }
    throw NativeWindowInputError.refused("the selected row is not in the focused list")
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
    private var temporaryReadDeadline: Double?
    /// Read-only limit for loops nested inside a shorter receiving scope.
    var effectiveReadDeadline: Double { min(deadline,temporaryReadDeadline ?? deadline) }
    private(set) var operationCount = 0
    private var contextSeconds = 0.0
    private var operationSeconds = 0.0
    private let collectReadinessRoleCosts: Bool
    private var readinessIdentities:[CFHashCode:[AXUIElement]]=[:]
    private var readinessUniqueNodes=0,readinessRepeatedNodes=0
    var readinessIdentityCounts:[String:Int]? {
        guard collectReadinessRoleCosts else {return nil}
        return ["unique_nodes":readinessUniqueNodes,"repeated_nodes":readinessRepeatedNodes,
                "visits":readinessUniqueNodes+readinessRepeatedNodes]
    }
    func recordReadinessIdentity(_ node:AXUIElement) {
        guard collectReadinessRoleCosts else {return}
        let hash=CFHash(node as AnyObject)
        if readinessIdentities[hash,default:[]].contains(where:{CFEqual($0,node)}) {
            readinessRepeatedNodes+=1
        } else {
            readinessIdentities[hash,default:[]].append(node);readinessUniqueNodes+=1
        }
    }
    private struct RoleCost {
        var nodes = 0, operations = 0
        var ax = 0.0, context = 0.0, wall = 0.0
    }
    private var roleCosts: [String: RoleCost] = [:]
    private var attributeCosts: [String: RoleCost] = [:]
    var readinessAttributeCosts: [[String: Any]] {
        attributeCosts.keys.sorted().map { name in
            let cost = attributeCosts[name]!
            return ["attribute":name,"scoped_ax_operations":cost.operations,
                    "ax_operation_us":Int(max(0,min(600000000,cost.ax*1000000)))]
        }
    }
    var readinessRoleCosts: [[String: Any]] {
        roleCosts.keys.sorted().map { role in
            let cost = roleCosts[role]!
            func micros(_ seconds: Double) -> Int { Int(max(0,min(600000000,seconds*1000000))) }
            return ["role":role,"nodes":cost.nodes,"scoped_ax_operations":cost.operations,
                    "ax_operation_us":micros(cost.ax),"context_us":micros(cost.context),"wall_us":micros(cost.wall)]
        }
    }
    func readinessNodeTiming() -> (Int, Double, Double, Double)? {
        collectReadinessRoleCosts ? (operationCount, operationSeconds, contextSeconds,
                                    ProcessInfo.processInfo.systemUptime) : nil
    }
    func recordReadinessRole(_ role: String?, since start: (Int, Double, Double, Double)) {
        let allowed = ["AXWindow","AXScrollArea","AXGroup","AXRow","AXCell","AXSheet",
                       "AXStaticText","AXImage","AXButton","AXTextField","AXOutline",
                       "AXSplitGroup","AXToolbar","AXPopUpButton","AXDisclosureTriangle"]
        let key = role == nil ? "unreadable" : allowed.contains(role!) ? role! : "other"
        var cost = roleCosts[key] ?? RoleCost()
        cost.nodes += 1; cost.operations += max(0,operationCount-start.0)
        cost.ax += max(0,operationSeconds-start.1); cost.context += max(0,contextSeconds-start.2)
        cost.wall += max(0,ProcessInfo.processInfo.systemUptime-start.3)
        roleCosts[key] = cost
    }
    var contextElapsedMS: Int { Int(max(0,min(600000,contextSeconds*1000))) }
    var operationElapsedMS: Int { Int(max(0,min(600000,operationSeconds*1000))) }
    let check: () throws -> Void
    let incrementalCheck: (() throws -> Void)?
    private var readinessContext = false
    private var lastFullOperation = 0
    private(set) var fullContextChecks = 0
    private(set) var incrementalContextChecks = 0
    private(set) var readinessAXError: Int?
    private struct ReadinessProgress {
        let pass: Int
        var windows: Int
        var completed = 0
        var index = -1
        var target = false
        var nodes = 0
        var totalNodes = 0
        var stage = "entry"
        var metadata: [String: Any] {
            ["pass":pass,"windows":windows,"completed":completed,"index":index,
             "target":target,"nodes":nodes,"total_nodes":totalNodes,"stage":stage]
        }
    }
    private var progress: ReadinessProgress?
    var readinessProgress: [String: Any]? { progress?.metadata }
    private var readinessPass = 0
    func beginReadiness(_ windows: Int) {
        readinessPass += 1
        roleCosts.removeAll(keepingCapacity: true)
        readinessIdentities.removeAll(keepingCapacity:true)
        readinessUniqueNodes=0;readinessRepeatedNodes=0
        progress = ReadinessProgress(pass: readinessPass, windows: windows)
    }
    func setReadinessWindows(_ windows: Int) { progress?.windows = windows }
    func beginReadinessTop(_ index: Int, target: Bool, stage: String = "top") {
        progress?.index = index; progress?.target = target
        progress?.nodes = 0; progress?.stage = stage
    }
    func visitReadinessNode() {
        progress?.stage = "tree"; progress?.nodes += 1; progress?.totalNodes += 1
    }
    func finishReadinessTop() { progress?.completed += 1 }
    func markReadinessStage(_ stage: String) { progress?.stage = stage }
    func recordReadinessAXFailure(_ error: AXError) { readinessAXError = Int(error.rawValue) }
    /// The first readiness read that could not be used, as closed codes and
    /// numbers only: never a value, label, title, pid or path. Recording it
    /// cannot change the verdict; the caller still throws exactly as before.
    private(set) var readinessFailure: [String: Any]?
    func recordReadinessFailure(attribute: String, source: String, kind: String,
                                error: AXError?, depth: Int) {
        guard readinessFailure == nil else { return }
        let attributes = ["AXRole", "AXModal", "AXChildren", "AXSheets", "AXWindows",
                          "AXMinimized", "AXParent", "batch"]
        var failure: [String: Any] = [
            "stage": progress?.stage ?? "entry",
            "attribute": attributes.contains(attribute) ? attribute : "other",
            "source": source, "kind": kind,
            "depth": max(0, min(64, depth)), "visit": max(0, min(20000, progress?.totalNodes ?? 0))]
        if let error, error != .success { failure["ax_error"] = Int(error.rawValue) }
        readinessFailure = failure
    }
    init(deadline: Double, incrementalCheck: (() throws -> Void)? = nil,
         collectReadinessRoleCosts: Bool = false,
         check: @escaping () throws -> Void) {
        self.deadline = deadline; self.check = check; self.incrementalCheck = incrementalCheck
        self.collectReadinessRoleCosts = collectReadinessRoleCosts
    }
    /// Read-only readiness and complete physical binding captures may amortize
    /// auxiliary metadata. Identity/abort/deadline remain per RPC; full live
    /// context checkpoints bracket every phase, including nested phases.
    func withReadinessContext<T>(_ body: () throws -> T) throws -> T {
        try withReadOnlyContext(body)
    }
    func withBindingContext<T>(_ body: () throws -> T) throws -> T {
        try withReadOnlyContext(body)
    }
    private func withReadOnlyContext<T>(_ body: () throws -> T) throws -> T {
        guard incrementalCheck != nil else { return try body() }
        try ready(forceFull: true)
        let previous = readinessContext
        readinessContext = true
        defer { readinessContext = previous }
        let result = try body()
        try ready(forceFull: true)
        return result
    }
    /// A receiving phase can narrow the shared read budget, never extend it.
    /// Counters/context checks stay on this scope; nested/throwing phases restore
    /// the preceding limit before their caller can perform another read.
    func withReadDeadline<T>(_ limit:Double,_ body:() throws -> T) throws -> T {
        guard limit.isFinite,deadline.isFinite,limit<=deadline else {
            throw NativeWindowInputError.refused("invalid window read deadline")
        }
        let previous=temporaryReadDeadline
        temporaryReadDeadline=min(effectiveReadDeadline,limit)
        defer {temporaryReadDeadline=previous}
        try ready(forceFull:true)
        let result=try body()
        try ready(forceFull:true)
        return result
    }
    func ready(forceFull: Bool = false) throws {
        let start = ProcessInfo.processInfo.systemUptime
        defer { contextSeconds += max(0,ProcessInfo.processInfo.systemUptime-start) }
        if readinessContext, !forceFull, operationCount-lastFullOperation < 16,
           let incrementalCheck {
            incrementalContextChecks += 1
            try incrementalCheck()
        } else {
            fullContextChecks += 1
            try check()
            lastFullOperation = operationCount
        }
        guard effectiveReadDeadline.isFinite, ProcessInfo.processInfo.systemUptime < effectiveReadDeadline else {
            throw NativeWindowInputError.refused("window observation deadline expired")
        }
    }
    func withTimeout<T>(_ element: AXUIElement, _ operation: () -> T) throws -> T {
        try ready()
        let remaining = effectiveReadDeadline - ProcessInfo.processInfo.systemUptime
        guard remaining > 0, AXUIElementSetMessagingTimeout(element, Float(min(0.5, remaining))) == .success else {
            throw NativeWindowInputError.unavailable("window AX timeout unavailable")
        }
        operationCount += 1
        let operationStart = ProcessInfo.processInfo.systemUptime
        let result = operation()
        operationSeconds += max(0,ProcessInfo.processInfo.systemUptime-operationStart)
        guard AXUIElementSetMessagingTimeout(element, 0) == .success else {
            throw NativeWindowInputError.unavailable("window AX timeout reset failed")
        }
        try ready()
        return result
    }
    func read(_ element: AXUIElement, _ name: String) throws -> (AXError, CFTypeRef?) {
        let calls = operationCount, seconds = operationSeconds
        defer {
            if collectReadinessRoleCosts, operationCount > calls {
                let allowed = ["AXRole","AXModal","AXChildren","AXWindows","AXMinimized","AXParent","AXSheets"]
                let key = allowed.contains(name) ? name : "other"
                var cost = attributeCosts[key] ?? RoleCost()
                cost.operations += operationCount-calls
                cost.ax += max(0,operationSeconds-seconds)
                attributeCosts[key] = cost
            }
        }
        return try withTimeout(element) {
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
        let owner = entry[kCGWindowOwnerPID as String] as? NSNumber
        let layer = entry[kCGWindowLayer as String] as? NSNumber
        let visible = entry[kCGWindowIsOnscreen as String] as? NSNumber
        throw NativeWindowInputError.refused("CG window ownership or visibility mismatch (window=\(windowID), expected_pid=\(pid), owner_pid=\(owner?.stringValue ?? "missing"), layer=\(layer?.stringValue ?? "missing"), onscreen=\(visible?.stringValue ?? "missing"))")
    }
    let rect = try CGRect(x: windowInputNumber(bounds["X"]), y: windowInputNumber(bounds["Y"]),
                          width: windowInputNumber(bounds["Width"]), height: windowInputNumber(bounds["Height"]))
    guard windowInputRectIsValid(rect) else { throw NativeWindowInputError.refused("invalid CG geometry") }
    return rect
}

/// AX can retain a popup subtree after its physical window disappears.
/// Absence is evidence only in a complete, successfully read on-screen list.
func nativePopupVisibility(_ windowID:CGWindowID,_ pid:pid_t,
    scope:NativeWindowReadScope?=nil,
    read:(()->[[String:Any]]?)?=nil) throws -> Bool {
    guard windowID>0,pid>0 else {throw NativeWindowInputError.refused("popup identity unavailable")}
    try scope?.ready()
    let entries=read != nil ? read!():CGWindowListCopyWindowInfo(.optionOnScreenOnly,0) as? [[String:Any]]
    guard let entries else {throw NativeWindowInputError.refused("popup visibility unavailable")}
    var matches:[[String:Any]]=[]
    for entry in entries {
        try scope?.ready()
        if try windowInputNumber(entry[kCGWindowNumber as String])==Double(windowID) {matches.append(entry)}
    }
    guard matches.count<=1 else {throw NativeWindowInputError.refused("popup window not unique")}
    guard let entry=matches.first else {return false}
    guard try windowInputNumber(entry[kCGWindowOwnerPID as String])==Double(pid),
          try windowInputNumber(entry[kCGWindowLayer as String])==0,
          let visible=entry[kCGWindowIsOnscreen as String] as? NSNumber,
          CFGetTypeID(visible)==CFBooleanGetTypeID(),visible.boolValue else {
        throw NativeWindowInputError.refused("popup ownership or visibility mismatch")
    }
    return true
}

/// Full exposed-tree modal check; AXSheets unsupported is not an empty list.
///
/// Three refinements (2026-10-09, Codex review of req 5c):
/// - A window top must say AXModal = false. The one top that may lack it is
///   Finder's desktop as measured: in Finder, an AXScrollArea whose parent is
///   the application itself (it has no AXModal; before this every Finder
///   window preparation was refused by it). Any other top — a dialog, an
///   unknown role, a scroll area elsewhere — must say AXModal = false.
/// - Running out of nodes or time while checking is reported as "readiness
///   budget exceeded", distinct from a sheet or modal actually found.
/// - The whole tree is still walked: a role-only prune of Finder's file lists
///   was proposed and withdrawn (Codex review of 97398cb — no proof the
///   container is a file list; it bought nothing either, preparation then
///   failed for want of a visible titlebar point). Large Finder windows can
///   therefore exceed the budget; that is reported as such.
typealias AXBatchRead = (AXUIElement, [String]) throws -> (AXError, CFArray?)

/// One packet per node. Individual unsupported/no-value attributes retain
/// their AX error, rather than becoming false/empty state. No cross-node or
/// cross-validation cache is permitted.
func nativeReadinessBatch(_ element: AXUIElement, names: [String],
                          scope: NativeWindowReadScope? = nil,
                          read injected: AXBatchRead? = nil) throws -> [(AXError, CFTypeRef?)]? {
    let reply: (AXError, CFArray?)
    if let injected { reply = try injected(element, names) }
    else {
        func copy() -> (AXError, CFArray?) {
            var values: CFArray?
            let error = AXUIElementCopyMultipleAttributeValues(element, names as CFArray,
                AXCopyMultipleAttributeOptions(rawValue: 0), &values)
            return (error, values)
        }
        reply = try scope?.withTimeout(element, copy) ?? copy()
    }
    // Unsupported batch transport can fall back to the existing strict reads;
    // timeout/other failures cannot be hidden by trying a different API.
    if reply.0 == .attributeUnsupported || reply.0 == .notImplemented { return nil }
    guard reply.0 == .success else {
        scope?.recordReadinessAXFailure(reply.0)
        throw NativeWindowInputError.refused("readiness batch failed")
    }
    guard let values = reply.1 as? [AnyObject], values.count == names.count else {
        throw NativeWindowInputError.refused("readiness batch malformed")
    }
    return try values.map { value in
        if CFGetTypeID(value) == AXValueGetTypeID(), AXValueGetType(value as! AXValue) == .axError {
            var error = AXError.success
            guard AXValueGetValue(value as! AXValue, .axError, &error), error != .success else {
                throw NativeWindowInputError.refused("readiness batch error malformed")
            }
            return (error, nil)
        }
        return (.success, value as CFTypeRef)
    }
}

// Resource ceiling, separate from the shared input deadline. Full modal/sheet
// validation must finish within both bounds; a larger tree gets no exemption.
let nativeWindowReadinessNodeLimit = 20000

func nativeRequireWindowReady(_ window: AXUIElement, _ pid: pid_t,
                              maxNodes: Int = nativeWindowReadinessNodeLimit, maxDepth: Int = 64,
                              scope: NativeWindowReadScope? = nil,
                              read injected: AXRead? = nil,
                              readBatch injectedBatch: AXBatchRead? = nil,
                              isFinder knownFinder: Bool? = nil) throws {
    scope?.beginReadiness(0)
    let traversal = {
        try nativeRequireWindowReadyTraversal(window, pid, maxNodes: maxNodes, maxDepth: maxDepth,
            scope: scope, read: injected, readBatch: injectedBatch, isFinder: knownFinder)
    }
    if let scope { try scope.withReadinessContext(traversal) }
    else { try traversal() }
}

private func nativeRequireWindowReadyTraversal(_ window: AXUIElement, _ pid: pid_t,
                              maxNodes: Int = nativeWindowReadinessNodeLimit, maxDepth: Int = 64,
                              scope: NativeWindowReadScope? = nil,
                              read injected: AXRead? = nil,
                              readBatch injectedBatch: AXBatchRead? = nil,
                              isFinder knownFinder: Bool? = nil) throws {
    // (A file-list prune was here briefly; withdrawn — see above.)
    guard maxNodes > 0, maxDepth >= 0 else { throw NativeWindowInputError.refused("invalid readiness budget") }
    var packetNode: AXUIElement?
    var packet: [String: (AXError, CFTypeRef?)] = [:]
    // Diagnostic context only: where the last value came from, and how deep.
    var source = "scalar", depthNow = 0, absentError: AXError?
    func failed(_ name: String, _ kind: String, _ error: AXError? = nil) {
        scope?.recordReadinessFailure(attribute: name, source: source, kind: kind,
                                      error: error, depth: depthNow)
    }
    func raw(_ node: AXUIElement, _ name: String) throws -> (AXError, CFTypeRef?) {
        if let packetNode, CFEqual(packetNode, node), let result = packet[name] {
            source = "batch_field"
            return result
        }
        source = "scalar"
        if let injected { return try injected(node, name) }
        if let scope { return try scope.read(node, name) }
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(node, name as CFString, &value)
        return (error, value)
    }
    func budgeted<T>(_ body: () throws -> T) throws -> T {
        do { return try body() }
        catch NativeWindowInputError.refused(let why) where why == "window observation deadline expired" {
            throw NativeWindowInputError.refused("readiness budget exceeded: deadline")
        }
    }
    func optional(_ node: AXUIElement, _ name: String) throws -> CFTypeRef? {
        let (error, value) = try budgeted { try raw(node, name) }
        if error == .success, let value = value { return value }
        if [-25205, -25212].contains(error.rawValue) { absentError = error; return nil }
        if error != .success { scope?.recordReadinessAXFailure(error) }
        failed(name, error == .success ? "null_value" : "ax_error", error)
        throw NativeWindowInputError.refused("window state unavailable: \(name)")
    }
    func required(_ node: AXUIElement, _ name: String) throws -> CFTypeRef {
        guard let value = try optional(node, name) else {
            failed(name, "required_absent", absentError)
            throw NativeWindowInputError.refused("required AX attribute unavailable: \(name)")
        }
        return value
    }
    func role(_ node: AXUIElement) throws -> String {
        let value = try required(node, kAXRoleAttribute)
        guard let text = value as? String else {
            failed(kAXRoleAttribute, CFGetTypeID(value) == CFNumberGetTypeID() ? "numeric" : "malformed")
            throw NativeWindowInputError.refused("invalid AX role")
        }
        return text
    }
    func explicitlyFalse(_ node: AXUIElement, _ name: String) throws -> Bool {
        let value = try required(node, name)
        return CFGetTypeID(value) == CFBooleanGetTypeID() && (value as? Bool) == false
    }
    let app = AXUIElementCreateApplication(pid)
    let isFinder = knownFinder ?? (NSRunningApplication(processIdentifier: pid)?.bundleIdentifier == "com.apple.finder")
    /// AXModal must be false — except on Finder's measured desktop entry,
    /// which has none: absent or false there, never true.
    func topNotModal(_ top: AXUIElement, _ topRole: String) throws -> Bool {
        guard isFinder, topRole == "AXScrollArea",
              let parent = try optional(top, kAXParentAttribute),
              CFGetTypeID(parent) == AXUIElementGetTypeID(), CFEqual(parent as! AXUIElement, app) else {
            return try explicitlyFalse(top, "AXModal")
        }
        guard let value = try optional(top, "AXModal") else { return true }
        return CFGetTypeID(value) == CFBooleanGetTypeID() && (value as? Bool) == false
    }
    func windows() throws -> [AXUIElement] {
        guard let result = try required(app, kAXWindowsAttribute) as? [AXUIElement] else {
            failed(kAXWindowsAttribute, "malformed")
            throw NativeWindowInputError.refused("application windows malformed")
        }
        return result
    }
    scope?.markReadinessStage("windows")
    let before = try windows()
    scope?.setReadinessWindows(before.count)
    guard before.filter({ CFEqual($0, window) }).count == 1,
          try explicitlyFalse(window, kAXMinimizedAttribute) else {
        throw NativeWindowInputError.refused("target window missing, ambiguous or minimized")
    }
    var remaining = maxNodes
    var roles: [String] = []
    for (topIndex, top) in before.enumerated() {
        scope?.beginReadinessTop(topIndex, target: CFEqual(top, window))
        depthNow = 0
        try scope?.ready(forceFull: true)
        let topRole = try role(top)
        roles.append(topRole)
        guard try topNotModal(top, topRole) else { throw NativeWindowInputError.refused("modal or unknown window") }
        if let value = try optional(top, "AXSheets") {
            guard let sheets = value as? [AXUIElement], sheets.isEmpty else {
                if !(value is [AXUIElement]) { failed("AXSheets", "malformed") }
                throw NativeWindowInputError.refused("sheet or malformed sheet list")
            }
        }
        var stack: [(AXUIElement, Int)] = [(top, 0)]
        while let (node, depth) = stack.popLast() {
            depthNow = depth
            remaining -= 1
            guard remaining >= 0, depth <= maxDepth else {
                throw NativeWindowInputError.refused("readiness budget exceeded: nodes")
            }
            scope?.visitReadinessNode()
            scope?.recordReadinessIdentity(node)
            let timing = scope?.readinessNodeTiming()
            var measuredRole: String?
            defer { if let timing { scope?.recordReadinessRole(measuredRole, since: timing) } }
            // Cache only this DFS iteration. Top-level post-walk modal/sheet
            // reads below MUST query the provider again, even for a leaf top.
            defer { packetNode = nil; packet.removeAll(keepingCapacity: true) }
            if injectedBatch != nil || (injected == nil && scope != nil) {
                let names = [kAXRoleAttribute as String, "AXModal", kAXChildrenAttribute as String]
                let batch: [(AXError, CFTypeRef?)]?
                do {
                    batch = try budgeted({ try nativeReadinessBatch(node, names: names,
                                                                    scope: scope, read: injectedBatch) })
                } catch NativeWindowInputError.refused(let why) where why.hasPrefix("readiness batch") {
                    source = "batch_call"
                    let callFailed = why == "readiness batch failed"
                    failed("batch", callFailed ? "batch_failed" : "batch_malformed",
                           callFailed ? scope?.readinessAXError.flatMap { AXError(rawValue: Int32($0)) } : nil)
                    throw NativeWindowInputError.refused(why)
                }
                if let values = batch {
                    guard values.count == names.count else {
                        source = "batch_call"
                        failed("batch", "batch_malformed")
                        throw NativeWindowInputError.refused("readiness batch malformed")
                    }
                    packetNode = node
                    packet = Dictionary(uniqueKeysWithValues: zip(names, values))
                }
            }
            let nodeRole = try role(node)
            measuredRole = nodeRole
            if nodeRole.isEmpty { failed(kAXRoleAttribute, "empty") }
            guard !nodeRole.isEmpty, nodeRole != "AXSheet" else { throw NativeWindowInputError.refused("attached sheet or malformed role") }
            if depth > 0, let modal = try optional(node, "AXModal"),
               !(CFGetTypeID(modal) == CFBooleanGetTypeID() && (modal as? Bool) == false) {
                if CFGetTypeID(modal) != CFBooleanGetTypeID() { failed("AXModal", "malformed") }
                throw NativeWindowInputError.refused("modal or unknown element")
            }
            if let value = try optional(node, kAXChildrenAttribute) {
                guard let children = value as? [AXUIElement] else {
                    failed(kAXChildrenAttribute, "malformed")
                    throw NativeWindowInputError.refused("window children malformed")
                }
                // Queued siblings also need a visit. Bound all pending nodes,
                // rather than allowing every branch to enqueue the full cap.
                guard children.count <= remaining - stack.count else {
                    throw NativeWindowInputError.refused("readiness budget exceeded: nodes")
                }
                stack.append(contentsOf: children.reversed().map { ($0, depth + 1) })
            }
        }
        scope?.finishReadinessTop()
    }
    depthNow = 0
    scope?.markReadinessStage("recheck")
    try scope?.ready(forceFull: true)
    let after = try windows()
    guard after.count == before.count, zip(before, after).allSatisfy({ CFEqual($0.0, $0.1) }),
          try explicitlyFalse(window, kAXMinimizedAttribute) else {
        let sameOrder = after.count == before.count && zip(before, after).allSatisfy({ CFEqual($0.0, $0.1) })
        let sameMembers = after.count == before.count && before.allSatisfy { prior in after.filter { CFEqual(prior, $0) }.count == 1 }
        let addedRoles = after.filter { next in !before.contains { CFEqual(next, $0) } }.map { node -> String in
            let nodeRole = (try? role(node)) ?? "unavailable"
            let title = (try? optional(node, kAXTitleAttribute)) as? String ?? ""
            let subrole = (try? optional(node, kAXSubroleAttribute)) as? String ?? ""
            return "\(nodeRole)|\(subrole)|\(title)"
        }
        throw NativeWindowInputError.refused("window state changed during validation: before=\(before.count), after=\(after.count), same_order=\(sameOrder), same_members=\(sameMembers), added_roles=\(addedRoles)")
    }
    for (topIndex, pair) in zip(before, roles).enumerated() {
        let (top, topRole) = pair
        scope?.beginReadinessTop(topIndex, target: CFEqual(top, window), stage: "recheck")
        try scope?.ready(forceFull: true)
        guard try role(top) == topRole else {
            throw NativeWindowInputError.refused("window role changed during validation")
        }
        guard try topNotModal(top, topRole) else { throw NativeWindowInputError.refused("window became modal during validation") }
        // A sheet attached after the walk is caught here too (Codex review of 97398cb).
        if let value = try optional(top, "AXSheets") {
            guard let sheets = value as? [AXUIElement], sheets.isEmpty else {
                if !(value is [AXUIElement]) { failed("AXSheets", "malformed") }
                throw NativeWindowInputError.refused("sheet attached during validation")
            }
        }
    }
    scope?.markReadinessStage("complete")
}

func nativeTitlebarPoints(_ bounds: CGRect, _ anchor: CGRect) throws -> [CGPoint] {
    guard windowInputRectIsValid(bounds), windowInputRectIsValid(anchor),
          bounds.minX <= anchor.minX, anchor.maxX <= bounds.maxX,
          bounds.minY <= anchor.minY, anchor.maxY <= bounds.minY + min(bounds.height / 4, 50) else {
        throw NativeWindowInputError.refused("titlebar anchor outside upper window band")
    }
    // Keep the cheap original probes first, then search bounded gaps in unified
    // toolbars. A candidate still must hit the window itself before dispatch.
    let fractions: [CGFloat] = [0.5, 0.75, 0.25, 0.625, 0.375, 0.875, 0.125,
        0.5625, 0.4375, 0.6875, 0.3125, 0.8125, 0.1875, 0.9375, 0.0625]
    return fractions.map { CGPoint(x: bounds.minX + bounds.width * $0, y: anchor.midY) }
}

/// Reject narrow gaps beside buttons: the whole cross must remain blank chrome.
func nativeTitlebarPointIsClear(_ point: CGPoint, _ bounds: CGRect,
                               _ window: AXUIElement, _ pid: pid_t,
                               scope: NativeWindowReadScope?) throws -> Bool {
    let points = [point, CGPoint(x: point.x-4, y: point.y), CGPoint(x: point.x+4, y: point.y),
                  CGPoint(x: point.x, y: point.y-4), CGPoint(x: point.x, y: point.y+4)]
    guard points.allSatisfy({ bounds.contains($0) }) else { return false }
    for sample in points {
        try scope?.ready()
        let hit = try windowInputHit(sample, pid, scope: scope)
        guard try windowInputPID(hit) == pid, CFEqual(hit, window) else { return false }
    }
    try scope?.ready()
    return true
}

/// Read-only diagnostic: never returns coordinates, text, or an input binding.
func nativeTitlebarHitSummary(_ window: AXUIElement, _ pid: pid_t,
                             scope: NativeWindowReadScope) throws -> [[String: Any]] {
    func row(_ status: String, role: String = "other", owner: Bool = false,
             isWindow: Bool = false, sameWindow: Bool = false,
             readable: Bool = false, actions: Bool = false, clear: Bool = false) -> [String: Any] {
        ["status":status,"role":role,"same_owner":owner,"is_window":isWindow,
         "same_window":sameWindow,"actions_readable":readable,"has_actions":actions,"clear_margin":clear]
    }
    let points: [CGPoint]
    do {
        let bounds = try windowInputFrame(window, scope: scope)
        let close = try windowInputElement(windowInputAttribute(window, "AXCloseButton", scope: scope))
        guard try windowInputPID(close) == pid,
              try windowInputAttribute(close, kAXSubroleAttribute, scope: scope) as? String == "AXCloseButton",
              try CFEqual(windowInputAncestor(close, pid, scope: scope), window) else {
            throw NativeWindowInputError.refused("native titlebar anchor unavailable")
        }
        points = try nativeTitlebarPoints(bounds, windowInputFrame(close, scope: scope))
    } catch {
        try scope.ready(forceFull: true) // Budget/identity failures remain failures.
        return Array(repeating: row("anchor_unavailable"), count: 15)
    }
    let allowed = ["AXWindow", "AXToolbar", "AXGroup", "AXButton", "AXStaticText", "AXImage"]
    return try points.map { point in
        try scope.ready()
        let hit: AXUIElement
        do { hit = try windowInputHit(point, pid, scope: scope) }
        catch { try scope.ready(forceFull: true); return row("hit_unavailable") }
        let owner: Bool
        do { owner = try windowInputPID(hit) == pid }
        catch { try scope.ready(forceFull: true); return row("owner_unavailable") }
        guard owner else { return row("cross_process") }
        let role: String
        let ancestor: AXUIElement
        do {
            role = try windowInputRole(hit, scope: scope)
            ancestor = try windowInputAncestor(hit, pid, scope: scope)
        } catch { try scope.ready(forceFull: true); return row("ancestry_unavailable", owner: true) }
        var actions: CFArray?
        let actionError = try scope.withTimeout(hit) { AXUIElementCopyActionNames(hit, &actions) }
        try scope.ready()
        var clear = false
        if CFEqual(hit, window) {
            do {
                clear = try nativeTitlebarPointIsClear(point, windowInputFrame(window, scope: scope), window, pid, scope: scope)
            } catch {
                try scope.ready(forceFull: true)
                return row("margin_unavailable", role: allowed.contains(role) ? role : "other", owner: true,
                           isWindow: true, sameWindow: CFEqual(ancestor, window),
                           readable: actionError == .success, actions: actionError == .success && (actions as? [String])?.isEmpty == false)
            }
        }
        return row("read_complete", role: allowed.contains(role) ? role : "other", owner: true,
                   isWindow: CFEqual(hit, window), sameWindow: CFEqual(ancestor, window),
                   readable: actionError == .success, actions: actionError == .success && (actions as? [String])?.isEmpty == false,
                   clear: clear)
    }
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
            if try windowInputPID(hit) == pid, CFEqual(hit, window),
               try nativeTitlebarPointIsClear(point, bounds, window, pid, scope: scope) {
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

/// A popup can report its logical parent's AXWindow while its SPI window ID
/// identifies a different physical window. Keep both identities: readiness
/// and app focus belong to the parent; pointer events belong to the popup.
/// Caller must separately pin the control/list/row relationship and options.
func captureNativePopupWindowBinding(_ element:AXUIElement,_ popup:AXUIElement,
    _ pid:pid_t,expectedStart:Double,scope:NativeWindowReadScope) throws -> NativeWindowBinding {
    try scope.ready()
    guard pid>0,nativeProcessStartIdentity(pid)==expectedStart,
          try windowInputPID(element)==pid,try windowInputPID(popup)==pid,
          try windowInputRole(popup,scope:scope)=="AXScrollArea" else {
        throw NativeWindowInputError.refused("popup identity unavailable")
    }
    let parent=try windowInputAncestor(element,pid,scope:scope)
    guard try CFEqual(windowInputAncestor(popup,pid,scope:scope),parent) else {
        throw NativeWindowInputError.refused("popup logical window changed")
    }
    let getWindow=try WindowInputSPI.windowFunction()
    func number(_ node:AXUIElement) throws -> CGWindowID {
        var id:CGWindowID=0
        let error=try scope.withTimeout(node) {getWindow(node,&id)}
        guard error==0,id>0 else {throw NativeWindowInputError.refused("popup window ID unavailable")}
        return id
    }
    let parentID=try number(parent),popupID=try number(popup)
    let parentBounds=try windowInputFrame(parent,scope:scope)
    let bounds=try windowInputFrame(popup,scope:scope),rect=try windowInputFrame(element,scope:scope)
    guard popupID != parentID,try number(element)==popupID,
          try windowInputCGFrame(parentID,pid,scope:scope)==parentBounds,
          try windowInputCGFrame(popupID,pid,scope:scope)==bounds,
          rect.minX>=bounds.minX,rect.minY>=bounds.minY,
          rect.maxX<=bounds.maxX,rect.maxY<=bounds.maxY else {
        throw NativeWindowInputError.refused("popup physical geometry changed")
    }
    let point=CGPoint(x:rect.midX,y:rect.midY)
    var current=try windowInputHit(point,pid,scope:scope)
    var seen:[AXUIElement]=[]
    for depth in 0...20 {
        guard try windowInputPID(current)==pid,!seen.contains(where:{CFEqual($0,current)}) else {break}
        if CFEqual(current,element) {
            guard nativeProcessStartIdentity(pid)==expectedStart else {
                throw NativeWindowInputError.refused("popup process changed")
            }
            return NativeWindowBinding(element:element,window:parent,pid:pid,processStart:expectedStart,
                windowID:popupID,windowBounds:bounds,elementBounds:rect,point:point)
        }
        guard depth<20,try windowInputRole(current,scope:scope) != kAXWindowRole else {break}
        seen.append(current)
        current=try windowInputElement(windowInputAttribute(current,kAXParentAttribute,scope:scope))
    }
    throw NativeWindowInputError.refused("popup hit-test does not identify element or descendant")
}

/// The window server's id for an accessibility window, or nil.
///
/// The SPI that answers this is private to this file, and a capture needs the
/// same answer a click does — the window the OBSERVATION described, not one
/// that merely shares its title.
func nativeWindowID(_ element: AXUIElement, scope:NativeWindowReadScope? = nil) -> CGWindowID? {
    guard let ask = try? WindowInputSPI.windowFunction() else { return nil }
    var id = CGWindowID(0)
    guard let result = try? scope?.withTimeout(element, {ask(element, &id)}) ?? ask(element, &id),
          result == 0,id != 0 else {return nil}   // kAXErrorSuccess
    return id
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

func buildNativeWindowClick(_ binding: NativeWindowBinding, clickCount: Int = 1) throws -> NativeWindowClick {
    guard clickCount == 1 || clickCount == 2 else { throw NativeWindowInputError.refused("invalid click count") }
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
                                            clickCount: clickCount, pressure: pressure), let event = owner.cgEvent else {
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
              event.getIntegerValueField(.mouseEventClickState) == Int64(clickCount),
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

/// Safari exposes a semantic confirmation on its native URL field. It is
/// addressed to that element, rather than a keyboard event sent to the app's
/// key window (which can be the address suggestion popup).
/// Missing/changed action support returns false before input; malformed reads
/// throw. A performed confirmation is never followed by a keyboard fallback.
func nativeSafariAddressConfirmEligible(_ element: AXUIElement,
                                       read: AXRead,
                                       actions: (AXUIElement) throws -> [String]) throws -> Bool {
    func value(_ node: AXUIElement, _ name: String) throws -> CFTypeRef? {
        let (status, value) = try read(node,name)
        if status == .attributeUnsupported || status == .noValue { return nil }
        guard status == .success, let value else {
            throw NativeWindowInputError.refused("Safari address state unavailable")
        }
        return value
    }
    guard try value(element,"AXRole") as? String == "AXTextField",
          try value(element,"AXIdentifier") as? String == "WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD",
          let enabled = try value(element,"AXEnabled"), CFGetTypeID(enabled) == CFBooleanGetTypeID(),
          enabled as? Bool == true,
          let text = try value(element,"AXValue") as? String,
          let url = URLComponents(string:text), ["http","https"].contains(url.scheme?.lowercased() ?? ""),
          let host = url.host, !host.isEmpty,
          try actions(element).contains("AXConfirm") else { return false }
    var node = element
    for _ in 0..<24 {
        guard let raw = try value(node,"AXParent"), CFGetTypeID(raw) == AXUIElementGetTypeID() else {
            throw NativeWindowInputError.refused("Safari address ancestry unavailable")
        }
        node = raw as! AXUIElement
        guard let role = try value(node,"AXRole") as? String else {
            throw NativeWindowInputError.refused("Safari address ancestry unreadable")
        }
        if ["AXWebArea","AXSheet","AXDialog","AXApplication"].contains(role) { return false }
        if role == "AXWindow" {
            return try value(node,"AXSubrole") as? String == "AXStandardWindow"
        }
    }
    throw NativeWindowInputError.refused("Safari address ancestry exceeded")
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

/// The app's whole exposed tree holds exactly one sheet, `sheet` (CFEqual),
/// whether a sheet is exposed as an AXSheet child or in a node's AXSheets
/// list (Codex review of 479acd0: AXSheets was not read). Nothing but that
/// sheet may say AXModal = true, on any node, not only at the top; a window
/// top must say AXModal = false; a non-window top (Finder's desktop
/// AXScrollArea) may lack it. Any unreadable role, children, sheets or modal
/// state, or a malformed value, refuses; so does another sheet, the sheet's
/// child entry seen twice, or none.
func nativeSheetSubmitReady(tops: [AXUIElement], sheet: AXUIElement, read: AXRead,
                            maxNodes: Int = 5000, maxDepth: Int = 64) throws {
    guard maxNodes > 0, maxDepth >= 0 else {
        throw NativeWindowInputError.refused("invalid readiness budget")
    }
    guard tops.count <= maxNodes else {
        throw NativeWindowInputError.refused("readiness traversal budget exceeded")
    }
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
    func modal(_ node: AXUIElement) throws -> Bool? {
        guard let raw = try value(node, "AXModal") else { return nil }
        guard CFGetTypeID(raw) == CFBooleanGetTypeID(), let flag = raw as? Bool else {
            throw NativeWindowInputError.refused("modal state malformed")
        }
        return flag
    }
    var childEntries = 0, remaining = maxNodes
    for top in tops {
        let topRole = try role(top)
        let topModal = try modal(top)
        if topRole == kAXWindowRole as String && topModal == nil {
            throw NativeWindowInputError.refused("window modal state unavailable")
        }
        var stack: [(AXUIElement, Int)] = [(top, 0)]
        while let (node, depth) = stack.popLast() {
            remaining -= 1
            guard remaining >= 0, depth <= maxDepth else {
                throw NativeWindowInputError.refused("readiness traversal budget exceeded")
            }
            let isSheet = CFEqual(node, sheet)
            if try role(node) == "AXSheet" {
                guard isSheet else { throw NativeWindowInputError.refused("another sheet is open") }
                childEntries += 1
            }
            if try modal(node) == true && !isSheet {
                throw NativeWindowInputError.refused("modal or unknown window")
            }
            if let raw = try value(node, "AXSheets") {
                guard let listed = raw as? [AXUIElement] else {
                    throw NativeWindowInputError.refused("sheet list malformed")
                }
                guard listed.allSatisfy({ CFEqual($0, sheet) }) else {
                    throw NativeWindowInputError.refused("another sheet is open")
                }
            }
            if let raw = try value(node, kAXChildrenAttribute as String) {
                guard let children = raw as? [AXUIElement] else {
                    throw NativeWindowInputError.refused("window children malformed")
                }
                // Every pending sibling still consumes a visit. Refuse an
                // impossible full traversal before allocating its queue; do
                // not prune rows or silently skip a possible modal element.
                guard children.count <= remaining - stack.count else {
                    throw NativeWindowInputError.refused("readiness traversal budget exceeded")
                }
                stack.append(contentsOf: children.reversed().map { ($0, depth + 1) })
            }
        }
    }
    guard childEntries == 1 else { throw NativeWindowInputError.refused("the sheet is missing or seen twice") }
}


// A window-relative, continuous left-button stroke. Coordinates use points
// from the observed window's top-left, just like click_point.
struct NativeWindowDrag {
    let path: [CGPoint]
    let duration: Double
}
struct NativeWindowStroke {
    let events: [CGEvent]
    let owners: [NSEvent]
    let offsets: [Double]
}
func buildNativeWindowDrag(_ binding: NativeWindowBinding, drag: NativeWindowDrag) throws -> NativeWindowStroke {
    guard drag.path.count >= 2, drag.path.count <= 14,
          drag.duration.isFinite, drag.duration >= 0.1, drag.duration <= 2,
          binding.windowID > 0, windowInputRectIsValid(binding.windowBounds) else {
        throw NativeWindowInputError.refused("invalid drag path or duration")
    }
    let bounds = binding.windowBounds
    for point in drag.path {
        guard point.x.isFinite, point.y.isFinite, point.x >= 0, point.y >= 0,
              point.x < bounds.width, point.y < bounds.height else {
            throw NativeWindowInputError.refused("drag point outside observed window")
        }
    }
    let lengths = zip(drag.path, drag.path.dropFirst()).map { hypot($1.x-$0.x,$1.y-$0.y) }
    let total = lengths.reduce(0,+)
    guard total > 0 else { throw NativeWindowInputError.refused("empty drag stroke") }
    let count = max(2, Int(ceil(drag.duration * 30)))
    var points = [drag.path[0]], offsets = [0.0]
    var travelled = 0.0
    for i in lengths.indices {
        let length = lengths[i]
        if length == 0 { continue }
        let segments = max(1,Int(ceil(Double(count)*length/total)))
        for step in 1...segments {
            let t=Double(step)/Double(segments)
            // Keep every waypoint, including corners closer than one frame.
            points.append(step == segments ? drag.path[i+1] : CGPoint(
                x:drag.path[i].x+(drag.path[i+1].x-drag.path[i].x)*t,
                y:drag.path[i].y+(drag.path[i+1].y-drag.path[i].y)*t))
            offsets.append(drag.duration*(travelled+length*t)/total)
        }
        travelled += length
    }
    points.append(drag.path.last!);offsets.append(drag.duration)
    let (setLocation,getLocation) = try WindowInputSPI.locationFunctions()
    let number = windowInputCounter.next()
    let timestamp = ProcessInfo.processInfo.systemUptime
    var owners:[NSEvent]=[], events:[CGEvent]=[]
    for (i,relative) in points.enumerated() {
        let type:NSEvent.EventType = i == 0 ? .leftMouseDown : i == points.count-1 ? .leftMouseUp : .leftMouseDragged
        let global=CGPoint(x:bounds.minX+relative.x,y:bounds.minY+relative.y)
        let pressure:Float = type == .leftMouseUp ? 0 : 1
        guard let owner=NSEvent.mouseEvent(with:type,location:CGPoint(x:relative.x,y:bounds.height-relative.y),
            modifierFlags:[],timestamp:timestamp+offsets[i],windowNumber:Int(binding.windowID),context:nil,
            eventNumber:number,clickCount:1,pressure:pressure),let event=owner.cgEvent else {
            throw NativeWindowInputError.refused("drag event construction failed")
        }
        event.flags=[];event.location=global
        event.setIntegerValueField(.mouseEventWindowUnderMousePointer,value:Int64(binding.windowID))
        event.setIntegerValueField(.mouseEventWindowUnderMousePointerThatCanHandleThisEvent,value:Int64(binding.windowID))
        if i > 0 {
            event.setIntegerValueField(.mouseEventDeltaX,value:Int64(relative.x.rounded()-points[i-1].x.rounded()))
            event.setIntegerValueField(.mouseEventDeltaY,value:Int64(relative.y.rounded()-points[i-1].y.rounded()))
        }
        setLocation(event,relative)
        guard event.location == global,getLocation(event) == relative,
              NSEvent(cgEvent:event)?.windowNumber == Int(binding.windowID),
              event.getIntegerValueField(.mouseEventNumber)==Int64(number),
              event.type == (i == 0 ? .leftMouseDown : i == points.count-1 ? .leftMouseUp : .leftMouseDragged) else {
            throw NativeWindowInputError.refused("drag event readback mismatch")
        }
        owners.append(owner);events.append(event)
    }
    return NativeWindowStroke(events:events,owners:owners,offsets:offsets)
}
