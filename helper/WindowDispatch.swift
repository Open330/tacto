// PID-addressed window preparation. Never posts to HID/session taps, activates
// NSWorkspace, moves the global pointer, or restores a former foreground app.
// Sequential observations cannot rule out a change and return between reads.
import Foundation
import AppKit
import ApplicationServices
import Darwin

struct NativeUserInputState {
    let foreground: pid_t
    let pointer: CGPoint
    // nil means AX explicitly reported noValue, never an unreadable attribute.
    let focused: AXUIElement?
    func matches(_ other: NativeUserInputState) -> Bool {
        differences(other).isEmpty
    }
    func differences(_ other: NativeUserInputState) -> [String] {
        var fields: [String] = []
        if foreground != other.foreground { fields.append("foreground") }
        if pointer != other.pointer { fields.append("pointer") }
        switch (focused, other.focused) {
        case (nil, nil): break
        case (let a?, let b?) where CFEqual(a, b): break
        default: fields.append("focus")
        }
        return fields
    }
}

struct NativeWindowDispatchFailure: Error {
    let detail: String
    let attempted: Int
    let stopped: Bool
}

struct NativeWindowPreparation {
    let posted: Int
    let foreground: pid_t
    let windowID: CGWindowID
}

func nativeUserFocusedElement(_ error: AXError, _ raw: AnyObject?) throws -> AXUIElement? {
    if error == .noValue, raw == nil { return nil }
    if error == .attributeUnsupported {
        throw NativeWindowInputError.unavailable("user focus unsupported")
    }
    guard error == .success else {
        throw NativeWindowInputError.unavailable("user focus query failed")
    }
    guard let raw, CFGetTypeID(raw) == AXUIElementGetTypeID() else {
        throw NativeWindowInputError.unavailable("user focus malformed")
    }
    return (raw as! AXUIElement)
}

func readNativeUserInputState(deadline: Double) throws -> NativeUserInputState {
    guard let foreground = nativeFrontmostProcessPID(), foreground > 0,
          let pointer = CGEvent(source: nil)?.location,
          pointer.x.isFinite, pointer.y.isFinite else {
        throw NativeWindowInputError.unavailable("user input state unavailable")
    }
    let app = AXUIElementCreateApplication(foreground)
    let remaining = deadline - ProcessInfo.processInfo.systemUptime
    guard remaining > 0, AXUIElementSetMessagingTimeout(app, Float(min(0.25, remaining))) == .success else {
        throw NativeWindowInputError.unavailable("user focus timeout unavailable")
    }
    var raw: AnyObject?
    let error = AXUIElementCopyAttributeValue(app, "AXFocusedUIElement" as CFString, &raw)
    guard AXUIElementSetMessagingTimeout(app, 0) == .success else {
        throw NativeWindowInputError.unavailable("user focus timeout reset failed")
    }
    let focused = try nativeUserFocusedElement(error, raw)
    guard nativeFrontmostProcessPID() == foreground else {
        throw NativeWindowInputError.unavailable("user foreground changed")
    }
    guard ProcessInfo.processInfo.systemUptime < deadline else {
        throw NativeWindowInputError.unavailable("user focus deadline expired")
    }
    return NativeUserInputState(foreground: foreground, pointer: pointer, focused: focused)
}

/// The one place a prepared event is posted to a process: both window-input
/// dispatchers (shared key/click path, and the Return sheet path) use it.
let nativePIDPost: (CGEvent, pid_t) throws -> Void = { event, pid in event.postToPid(pid) }

func dispatchNativeWindowPreparation(
    _ binding: NativeWindowBinding, deadline: Double,
    stopped: () -> Bool, context: () throws -> Void,
    now: () -> Double = { ProcessInfo.processInfo.systemUptime },
    readInput: (Double) throws -> NativeUserInputState = readNativeUserInputState,
    validate: (NativeWindowBinding) throws -> Void = { try validateNativeWindowBinding($0) },
    post: (CGEvent, pid_t) throws -> Void = nativePIDPost,
    key: String? = nil, routing: ((NativeWindowBinding, Bool) throws -> Void)? = nil,
    preflight: (() throws -> Void)? = nil, pointClick: Bool = false,
    windowInput: NativeOpaqueWindowInput? = nil, elementClicks: Int = 0,
    submitKey: Bool = false, finalTransition: ((_ checkpoint: () throws -> Void) throws -> Void)? = nil,
    headroomDiagnostic: (([String: Any]) -> Void)? = nil,
    foregroundClickFocus: ((NativeWindowBinding, AXUIElement?) throws -> Void)? = nil
) throws -> NativeWindowPreparation {
    var attempted = 0
    var validationSeconds = 0.0, routingSeconds = 0.0, inputSeconds = 0.0, contextSeconds = 0.0
    func validated() throws {
        let start = now()
        defer { validationSeconds = max(validationSeconds, max(0, now() - start)) }
        try validate(binding)
    }
    func routed(_ first: Bool) throws {
        let start = now()
        defer { routingSeconds = max(routingSeconds, max(0, now() - start)) }
        try routing?(binding, first)
    }
    func observedInput() throws -> NativeUserInputState {
        let start = now()
        defer { inputSeconds = max(inputSeconds, max(0, now() - start)) }
        return try readInput(deadline)
    }
    func ready() throws {
        guard !stopped() else { throw NativeWindowInputError.refused("user stopped input") }
        guard deadline.isFinite, now() < deadline else {
            throw NativeWindowInputError.refused("window input deadline expired")
        }
        let start = now()
        defer { contextSeconds = max(contextSeconds, max(0, now() - start)) }
        try context()
    }
    do {
        try ready()
        guard windowInput != nil || key != nil || pointClick || elementClicks != 0 || (binding.pointKind == .titlebar && CFEqual(binding.element, binding.window)) else {
            throw NativeWindowInputError.refused("window preparation requires a titlebar binding")
        }
        if let input = windowInput {
            guard input.valid, key == nil, !pointClick, binding.pointKind == .windowInput,
                  CFEqual(binding.element, binding.window), routing != nil else {
                throw NativeWindowInputError.refused("opaque input requires a bound window and routing check")
            }
        }
        if key != nil {
            guard binding.pointKind == .elementCenter, !CFEqual(binding.element, binding.window), routing != nil else {
                throw NativeWindowInputError.refused("navigation requires a bound input and routing check")
            }
        }
        if submitKey {
            guard key == "Return", windowInput == nil, !pointClick, elementClicks == 0 else {
                throw NativeWindowInputError.refused("a bound submit is Return to one bound input")
            }
        }
        if pointClick {
            guard key == nil, binding.pointKind == .windowPoint, CFEqual(binding.element, binding.window), routing != nil else {
                throw NativeWindowInputError.refused("coordinate input requires a bound window and routing check")
            }
        }
        if elementClicks != 0 {
            // An observed element, hit-tested at its centre, in the app's
            // focused window: one window-bound click, or two with the second
            // at click count 2 (2026-10-10, KakaoTalk's chat list rows offer
            // no AXPress and open on a double click).
            guard elementClicks == 1 || elementClicks == 2, key == nil, !pointClick, windowInput == nil, binding.pointKind == .elementCenter,
                  !CFEqual(binding.element, binding.window), routing != nil else {
                throw NativeWindowInputError.refused("element click requires a bound element and routing check")
            }
        }
        if finalTransition != nil {
            guard elementClicks==1,key==nil,!pointClick,windowInput==nil,!submitKey else {
                throw NativeWindowInputError.refused("final transition requires one bound element click")
            }
        }
        try validated(); try ready()
        let initial = try observedInput()
        // A single element click can address the already-frontmost window.
        // Other preparation/key/opaque routes retain their background contract.
        let foregroundClick = initial.foreground == binding.pid && elementClicks == 1
            && finalTransition == nil && foregroundClickFocus != nil
        guard initial.foreground != binding.pid || foregroundClick else {
            throw NativeWindowInputError.refused("background window preparation requires a background process")
        }
        if foregroundClick { try foregroundClickFocus?(binding, initial.focused); try ready() }
        var expectedInput = initial
        // An app in front moves focus to what was clicked a moment AFTER the
        // mouseDown is posted (an outline view, 2026-10-24). Reading once at
        // once took that late, legitimate change for the person's input and
        // stopped between mouseDown and mouseUp. Read until two reads agree
        // (bounded); foreground and pointer are still required unchanged.
        func settledInput() throws -> NativeUserInputState {
            var last = try observedInput()
            for _ in 0..<10 {
                try ready()
                Thread.sleep(forTimeInterval: 0.01)
                let next = try observedInput()
                if last.matches(next) { return next }
                last = next
            }
            return last
        }
        func acceptClickFocus(_ before: NativeUserInputState, _ after: NativeUserInputState) throws {
            if before.matches(after) {
                if foregroundClick { try foregroundClickFocus?(binding, after.focused) }
                return
            }
            guard foregroundClick, before.foreground == after.foreground,
                  before.pointer == after.pointer else {
                throw NativeWindowInputError.refused("user input state changed during input: " + before.differences(after).joined(separator: ","))
            }
            // A click may focus a row or load another tab in the SAME window.
            // Never accept an unreadable focus or a move to another window.
            try foregroundClickFocus?(binding, after.focused)
        }
        var owners: [NSEvent] = [], events: [CGEvent] = []
        if let input = windowInput {
            try routed(false); try ready()
            let pair: NativeWindowKeyPair
            switch input {
            case .key(let value): pair = try buildNativeWindowKey(binding.windowID, key: value)
            case .text(let value): pair = try buildNativeWindowText(binding.windowID, text: value)
            }
            owners = pair.owners; events = [pair.down, pair.up]
        } else if let key, submitKey {
            try routed(true); try ready()
            // Return to a bound input the host vouched for (declared submit,
            // binding, a postcondition not yet true). One keyDown, like the
            // Go to Folder path: the commit happens on it, and what it opens
            // or sends may take the focus before a keyUp could be checked.
            let pair = try buildNativeWindowKey(binding.windowID, key: key, submit: true)
            owners = pair.owners; events = [pair.down]
        } else if let key {
            try routed(true); try ready()
            // Navigation keys only. Return has its own paths (submitReturnInSheet, submitKey).
            let pair = try buildNativeWindowKey(binding.windowID, key: key)
            owners = pair.owners; events = [pair.down, pair.up]
        } else if elementClicks != 0 {
            try routed(false); try ready()
            let first = try buildNativeWindowClick(binding, clickCount: 1)
            owners = first.owners; events = [first.down, first.up]
            if elementClicks == 2 {
                let second = try buildNativeWindowClick(binding, clickCount: 2)
                owners += second.owners; events += [second.down, second.up]
            }
        } else if pointClick {
            try routed(false); try ready()
            let pair = try buildNativeWindowClick(binding)
            owners = pair.owners; events = [pair.down, pair.up]
        } else {
            let pair = try buildNativeWindowClick(binding)
            guard let owner = NSEvent.otherEvent(with: .appKitDefined, location: .zero,
                modifierFlags: NSEvent.ModifierFlags(rawValue: 0xC0000), timestamp: now(),
                windowNumber: Int(binding.windowID), context: nil,
                subtype: NSEvent.EventSubtype.applicationActivated.rawValue, data1: 0, data2: 0),
                let activation = owner.cgEvent, activation.type.rawValue == 13,
                activation.flags.rawValue == 0xC0000,
                let reflected = NSEvent(cgEvent: activation), reflected.type == .appKitDefined,
                reflected.subtype == .applicationActivated, reflected.windowNumber == Int(binding.windowID),
                reflected.data1 == 0, reflected.data2 == 0 else {
                throw NativeWindowInputError.unavailable("window activation event readback failed")
            }
            owners = [owner] + pair.owners; events = [activation, pair.down, pair.up]
        }
        // NSEvent owns its borrowed CG event; retain all owners through posting.
        return try withExtendedLifetime(owners) {
            for (number, event) in events.enumerated() {
                try ready(); try validated(); try ready()
                if number == 0 { try preflight?(); try ready() }
                try routed(number == 0); try ready()
                let before = try observedInput()
                guard expectedInput.matches(before) else {
                    throw NativeWindowInputError.refused("user input state changed before input: " + expectedInput.differences(before).joined(separator: ","))
                }
                try ready()
                if number == 0 {
                    // All future full checks still run. Admit the sequence only
                    // with headroom for their measured cost, rather than using
                    // most of the deadline on the first pass and posting a
                    // predictably incomplete click/key sequence. This estimate
                    // cannot bound a later provider slowdown; partial effects
                    // remain unknown and are never retried automatically.
                    let checks = submitKey ? 0 : Double(2 * events.count - 1)
                    let reads = submitKey ? 1 : Double(3 * events.count - 1)
                    let contexts = submitKey ? 1 : Double(12 * events.count)
                    let reserve = 2 * (checks * (validationSeconds + routingSeconds)
                        + reads * inputSeconds + contexts * contextSeconds) + 0.05
                    let remaining = deadline - now()
                    if let headroomDiagnostic {
                        let values = [reserve, max(0,remaining),validationSeconds,routingSeconds,inputSeconds,contextSeconds]
                        func millis(_ seconds: Double) -> Int {
                            guard seconds.isFinite else { return 600000 }
                            return Int(ceil(max(0,min(600000,seconds*1000))))
                        }
                        headroomDiagnostic(["required_ms":millis(reserve),"remaining_ms":millis(remaining),
                            "validation_ms":millis(validationSeconds),"routing_ms":millis(routingSeconds),
                            "input_ms":millis(inputSeconds),"context_ms":millis(contextSeconds),
                            "event_count":events.count,"saturated":values.contains { !$0.isFinite || $0>600 }])
                    }
                    guard reserve.isFinite, deadline - now() >= reserve else {
                        throw NativeWindowInputError.refused("window validation headroom insufficient")
                    }
                }
                attempted += 1 // A failed post cannot establish no effect.
                try post(event, binding.pid)
                try ready()
                let after = try foregroundClick ? settledInput() : observedInput()
                try acceptClickFocus(before, after)
                if foregroundClick {
                    try ready()
                    expectedInput = after
                    // mouseUp completes the bound pair. Navigation may have
                    // destroyed its old element; replay must observe the result.
                    if number == events.count - 1 { continue }
                }
                // A committed submit is expected to change the window, its
                // focus or the element itself; there is nothing left to post,
                // so it is not re-validated as if it should not have.
                if submitKey { continue }
                // Selection may destroy its popup on mouseUp. Only after the
                // complete pair, accept a caller-verified receiving transition.
                // Before mouseUp the original binding and routing still apply.
                if number==events.count-1,let finalTransition {
                    func checkpoint() throws {
                        try ready()
                        let confirmed=try observedInput()
                        guard before.matches(confirmed) else {
                            throw NativeWindowInputError.refused("user input state changed during selection verification")
                        }
                        try ready()
                    }
                    try checkpoint();try finalTransition(checkpoint);try checkpoint()
                    continue
                }
                try validated(); try ready()
                try routed(false); try ready()
                let confirmed = try observedInput()
                guard expectedInput.matches(confirmed) else {
                    throw NativeWindowInputError.refused("user input state changed during validation: " + expectedInput.differences(confirmed).joined(separator: ","))
                }
                try ready()
            }
            return NativeWindowPreparation(posted: attempted, foreground: initial.foreground,
                                           windowID: binding.windowID)
        }
    } catch {
        let detail: String
        switch error {
        case NativeWindowInputError.refused(let value), NativeWindowInputError.unavailable(let value),
             NativeWindowInputError.needsForeground(let value): detail = value
        default: detail = "window input context or system call failed"
        }
        throw NativeWindowDispatchFailure(detail: detail, attempted: attempted, stopped: stopped())
    }
}

// MARK: - Return into the Go to Folder sheet: dispatch (see WindowInput.swift)

/// Post one prepared keyDown after `check` (readiness, focus, target) passes,
/// with the person's input state unchanged around it. 0 or 1 event; never a
/// second attempt. Failures carry how many were attempted.
func dispatchSheetReturn(_ event: CGEvent, pid: pid_t, deadline: Double,
                         stopped: () -> Bool, context: () throws -> Void,
                         check: () throws -> Void,
                         sameProcess: () -> Bool = { true },
                         now: () -> Double = { ProcessInfo.processInfo.systemUptime },
                         readInput: (Double) throws -> NativeUserInputState = readNativeUserInputState,
                         post: (CGEvent, pid_t) throws -> Void = nativePIDPost)
    throws -> Int {
    var attempted = 0
    func ready() throws {
        guard !stopped() else { throw NativeWindowInputError.refused("user stopped input") }
        guard deadline.isFinite, now() < deadline else {
            throw NativeWindowInputError.refused("window input deadline expired")
        }
        try context()
    }
    do {
        try ready(); try check(); try ready()
        let initial = try readInput(deadline)
        guard initial.foreground != pid else {
            throw NativeWindowInputError.refused("Return requires the app to be in the background")
        }
        try ready(); try check(); try ready()
        let before = try readInput(deadline)
        guard initial.matches(before) else {
            throw NativeWindowInputError.refused("user input state changed before input: " + initial.differences(before).joined(separator: ","))
        }
        try ready()
        attempted = 1   // a failed post cannot establish no effect
        try post(event, pid)
        let after = try readInput(deadline)
        guard before.matches(after) else {
            throw NativeWindowInputError.refused("user input state changed during input: " + before.differences(after).joined(separator: ","))
        }
        // The sheet and field are expected to be gone now, so they are not
        // re-checked; but stop, the whole deadline, the observation context
        // and the receiving process still are (Codex: post-send checks are
        // narrowed, not dropped). Any of these failing is attempted 1.
        guard sameProcess() else { throw NativeWindowInputError.refused("process changed after input") }
        try ready()
        return attempted
    } catch {
        let detail: String
        switch error {
        case NativeWindowInputError.refused(let value), NativeWindowInputError.unavailable(let value),
             NativeWindowInputError.needsForeground(let value): detail = value
        default: detail = "window input context or system call failed"
        }
        throw NativeWindowDispatchFailure(detail: detail, attempted: attempted, stopped: stopped())
    }
}

/// Glue: resolve the field's sheet and parent window, fix their identities
/// and geometry, and send one keyDown(Return) addressed to the sheet's CG
/// window under `dispatchSheetReturn`'s checks. `refusal` is the target check
/// (returnTargetRefusal), passed in so this file does not reach into main.
func submitReturnInSheet(_ element: AXUIElement, _ pid: pid_t, expectedStart: Double,
                         scope: NativeWindowReadScope, deadline: Double,
                         stopped: () -> Bool, context: () throws -> Void,
                         refusal: (AXUIElement, @escaping AXRead) -> String?) throws -> (posted: Int, sheetID: CGWindowID) {
    try scope.ready()
    guard pid > 0, nativeProcessStartIdentity(pid) == expectedStart else {
        throw NativeWindowInputError.refused("process start identity mismatch")
    }
    let read: AXRead = { try scope.read($0, $1) }
    if let why = refusal(element, read) { throw NativeWindowInputError.refused("Return refused: \(why)") }
    var node = element, sheet: AXUIElement?
    for _ in 0..<32 {
        let parent = try windowInputElement(windowInputAttribute(node, kAXParentAttribute, scope: scope))
        if try windowInputRole(parent, scope: scope) == "AXSheet" { sheet = parent; break }
        node = parent
    }
    guard let sheet else { throw NativeWindowInputError.refused("sheet not found") }
    let parentWindow = try windowInputElement(windowInputAttribute(sheet, kAXParentAttribute, scope: scope))
    guard try windowInputRole(parentWindow, scope: scope) == kAXWindowRole as String,
          try windowInputPID(sheet) == pid, try windowInputPID(parentWindow) == pid else {
        throw NativeWindowInputError.refused("sheet is not attached to a window of this app")
    }
    let getWindow = try WindowInputSPI.windowFunction()
    var sheetID: CGWindowID = 0, parentID: CGWindowID = 0
    guard try scope.withTimeout(sheet, { getWindow(sheet, &sheetID) }) == 0,
          try scope.withTimeout(parentWindow, { getWindow(parentWindow, &parentID) }) == 0,
          sheetID > 0, parentID > 0, sheetID != parentID else {
        throw NativeWindowInputError.refused("sheet or window CG ID unavailable")
    }
    let sheetFrame = try windowInputFrame(sheet, scope: scope)
    let fieldFrame = try windowInputFrame(element, scope: scope)
    let parentFrame = try windowInputFrame(parentWindow, scope: scope)
    guard try windowInputCGFrame(sheetID, pid, scope: scope) == sheetFrame,
          try windowInputCGFrame(parentID, pid, scope: scope) == parentFrame,
          fieldFrame.minX >= sheetFrame.minX, fieldFrame.minY >= sheetFrame.minY,
          fieldFrame.maxX <= sheetFrame.maxX, fieldFrame.maxY <= sheetFrame.maxY else {
        throw NativeWindowInputError.refused("sheet, window or field geometry mismatch")
    }
    let app = AXUIElementCreateApplication(pid)
    func tops() throws -> [AXUIElement] {
        guard let list = try windowInputAttribute(app, kAXWindowsAttribute, scope: scope) as? [AXUIElement] else {
            throw NativeWindowInputError.refused("application windows malformed")
        }
        return list
    }
    let initialTops = try tops()
    let pair = try buildNativeWindowKey(sheetID, key: "Return", submit: true)
    let posted = try withExtendedLifetime(pair.owners) {
        try dispatchSheetReturn(pair.down, pid: pid, deadline: deadline, stopped: stopped, context: context,
            check: {
                guard nativeProcessStartIdentity(pid) == expectedStart else {
                    throw NativeWindowInputError.refused("process changed")
                }
                let now = try tops()
                guard now.count == initialTops.count, zip(now, initialTops).allSatisfy({ CFEqual($0.0, $0.1) }) else {
                    throw NativeWindowInputError.refused("windows changed")
                }
                try nativeSheetSubmitReady(tops: now, sheet: sheet, read: read)
                let focusedWindow = try windowInputElement(windowInputAttribute(app, kAXFocusedWindowAttribute, scope: scope))
                let focused = try windowInputElement(windowInputAttribute(app, kAXFocusedUIElementAttribute, scope: scope))
                guard CFEqual(focusedWindow, sheet), CFEqual(focused, element) else {
                    throw NativeWindowInputError.refused("the sheet's path field is not the app's focus")
                }
                if let why = refusal(element, read) { throw NativeWindowInputError.refused("Return refused: \(why)") }
                // Against what was fixed at the start, not just current vs
                // current (Codex review of 479acd0).
                let sheetNow = try windowInputFrame(sheet, scope: scope)
                let fieldNow = try windowInputFrame(element, scope: scope)
                guard sheetNow == sheetFrame, fieldNow == fieldFrame,
                      try windowInputCGFrame(sheetID, pid, scope: scope) == sheetFrame,
                      try windowInputFrame(parentWindow, scope: scope) == parentFrame,
                      try windowInputCGFrame(parentID, pid, scope: scope) == parentFrame else {
                    throw NativeWindowInputError.refused("sheet, window or field moved")
                }
                var up = element, owner: AXUIElement?
                for _ in 0..<32 {
                    let next = try windowInputElement(windowInputAttribute(up, kAXParentAttribute, scope: scope))
                    if try windowInputRole(next, scope: scope) == "AXSheet" { owner = next; break }
                    up = next
                }
                guard let owner, CFEqual(owner, sheet),
                      CFEqual(try windowInputElement(windowInputAttribute(sheet, kAXParentAttribute, scope: scope)), parentWindow) else {
                    throw NativeWindowInputError.refused("field or sheet reattached")
                }
            },
            sameProcess: { nativeProcessStartIdentity(pid) == expectedStart })
    }
    return (posted, sheetID)
}

// MARK: - open_folder: the LaunchServices request, bounded

enum OpenFolderOutcome: Equatable {
    case notStarted(String)    // nothing was asked of LaunchServices
    case opened                // asked, and it said it opened
    case unknown(String)       // asked; whether the folder is showing is not known
}

/// Ask once, only if not stopped and still in time at the moment of asking;
/// then wait for the answer no longer than the deadline. A stop noticed after
/// asking cannot un-ask, so it is `unknown`, never `notStarted`. Never asks twice.
func openFolderBounded(deadline: Double, stopped: () -> Bool,
                       now: () -> Double = { ProcessInfo.processInfo.systemUptime },
                       request: (@escaping @Sendable (Bool) -> Void) -> Void) -> OpenFolderOutcome {
    final class Box: @unchecked Sendable { var ok = false }
    let finished = DispatchSemaphore(value: 0), box = Box()
    guard !stopped() else { return .notStarted("stopped before opening") }
    guard deadline.isFinite, now() < deadline else { return .notStarted("deadline passed before opening") }
    request { ok in box.ok = ok; finished.signal() }
    let wait = max(0, deadline - now())
    guard finished.wait(timeout: .now() + wait) == .success else { return .unknown("no answer before the deadline") }
    // An answer that came in already signalled still has to be in time:
    // wait(0) succeeds for it (Codex review of 2f3f79a).
    guard now() < deadline else { return .unknown("answer arrived after the deadline") }
    guard box.ok else { return .unknown("LaunchServices reported an error") }
    guard !stopped() else { return .unknown("stopped after opening was asked") }
    return .opened
}


// One stroke, never retried. Expensive AX readiness runs at its boundaries;
// the timed path checks cancellation/context and external input without AX RPCs.
func dispatchNativeWindowDrag(_ binding:NativeWindowBinding,drag:NativeWindowDrag,deadline:Double,
    stopped:()->Bool,context:()throws->Void,validate:()throws->Void,
    now:()->Double={ProcessInfo.processInfo.systemUptime},
    readInput:(Double)throws->NativeUserInputState=readNativeUserInputState,
    pointer:()->CGPoint?={CGEvent(source:nil)?.location},
    foreground:()->pid_t?=nativeFrontmostProcessPID,
    post:(CGEvent,pid_t)throws->Void=nativePIDPost,
    sameProcess:()->Bool={true},windowUnchanged:()throws->Void={},sleep:(Double)->Void={Thread.sleep(forTimeInterval:$0)}) throws -> NativeWindowPreparation {
    var attempted=0,held=false
    var lastPoint=binding.point
    var stroke:NativeWindowStroke?
    func ready() throws {
        guard !stopped(),deadline.isFinite,now()<deadline else {
            throw NativeWindowInputError.refused("drag stopped or deadline expired")
        }
        try context()
    }
    do {
        try ready();try validate();try ready()
        let initial=try readInput(deadline)
        guard initial.foreground != binding.pid else {
            throw NativeWindowInputError.refused("window-bound drag requires a background app")
        }
        let prepared=try buildNativeWindowDrag(binding,drag:drag);stroke=prepared
        guard deadline-now()>drag.duration+0.1 else {
            throw NativeWindowInputError.refused("insufficient drag deadline")
        }
        try validate();try ready()
        guard initial.matches(try readInput(deadline)) else {
            throw NativeWindowInputError.refused("user input changed before drag")
        }
        let start=now()
        try withExtendedLifetime(prepared.owners) {
            for (i,event) in prepared.events.enumerated() {
                let due=start+prepared.offsets[i]
                while now()<due { try ready();sleep(max(0,min(0.01,due-now()))) }
                try ready()
                guard foreground()==initial.foreground,pointer()==initial.pointer else {
                    throw NativeWindowInputError.refused("user input changed during drag")
                }
                try windowUnchanged();try ready()
                lastPoint=event.location
                attempted+=1
                if event.type == .leftMouseDown { held=true }
                try post(event,binding.pid)
                if event.type == .leftMouseUp { held=false }
            }
        }
        try ready()
        guard initial.matches(try readInput(deadline)) else {
            throw NativeWindowInputError.refused("user input changed during drag")
        }
        try validate();try ready()
        return NativeWindowPreparation(posted:attempted,foreground:initial.foreground,windowID:binding.windowID)
    } catch {
        // Release only the same receiving process, even after cancellation.
        // Do not leave AppKit's tracking loop holding the button. No HID input.
        if held,sameProcess(),let up=stroke?.events.last {
            up.location=lastPoint
            if let (setLocation,_) = try? WindowInputSPI.locationFunctions() {
                setLocation(up,CGPoint(x:lastPoint.x-binding.windowBounds.minX,y:lastPoint.y-binding.windowBounds.minY))
                attempted+=1;try? post(up,binding.pid)
            }
        }
        let detail:String
        switch error {
        case NativeWindowInputError.refused(let value),NativeWindowInputError.unavailable(let value),
             NativeWindowInputError.needsForeground(let value):detail=value
        default:detail="drag dispatch failed"
        }
        throw NativeWindowDispatchFailure(detail:detail,attempted:attempted,stopped:stopped())
    }
}
