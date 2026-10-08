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
    let focused: AXUIElement
    func matches(_ other: NativeUserInputState) -> Bool {
        differences(other).isEmpty
    }
    func differences(_ other: NativeUserInputState) -> [String] {
        var fields: [String] = []
        if foreground != other.foreground { fields.append("foreground") }
        if pointer != other.pointer { fields.append("pointer") }
        if !CFEqual(focused, other.focused) { fields.append("focus") }
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
    guard error == .success, let raw, CFGetTypeID(raw) == AXUIElementGetTypeID(),
          nativeFrontmostProcessPID() == foreground,
          ProcessInfo.processInfo.systemUptime < deadline else {
        throw NativeWindowInputError.unavailable("user focus unavailable or foreground changed")
    }
    return NativeUserInputState(foreground: foreground, pointer: pointer, focused: raw as! AXUIElement)
}

func dispatchNativeWindowPreparation(
    _ binding: NativeWindowBinding, deadline: Double,
    stopped: () -> Bool, context: () throws -> Void,
    now: () -> Double = { ProcessInfo.processInfo.systemUptime },
    readInput: (Double) throws -> NativeUserInputState = readNativeUserInputState,
    validate: (NativeWindowBinding) throws -> Void = { try validateNativeWindowBinding($0) },
    post: (CGEvent, pid_t) throws -> Void = { event, pid in event.postToPid(pid) },
    key: String? = nil, routing: ((NativeWindowBinding, Bool) throws -> Void)? = nil,
    preflight: (() throws -> Void)? = nil, pointClick: Bool = false,
    windowInput: NativeOpaqueWindowInput? = nil
) throws -> NativeWindowPreparation {
    var attempted = 0
    func ready() throws {
        guard !stopped() else { throw NativeWindowInputError.refused("user stopped input") }
        guard deadline.isFinite, now() < deadline else {
            throw NativeWindowInputError.refused("window input deadline expired")
        }
        try context()
    }
    do {
        try ready()
        guard windowInput != nil || key != nil || pointClick || (binding.pointKind == .titlebar && CFEqual(binding.element, binding.window)) else {
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
        if pointClick {
            guard key == nil, binding.pointKind == .windowPoint, CFEqual(binding.element, binding.window), routing != nil else {
                throw NativeWindowInputError.refused("coordinate input requires a bound window and routing check")
            }
        }
        try validate(binding); try ready()
        let initial = try readInput(deadline)
        guard initial.foreground != binding.pid else {
            throw NativeWindowInputError.refused("background window preparation requires a background process")
        }
        var owners: [NSEvent] = [], events: [CGEvent] = []
        if let input = windowInput {
            try routing?(binding, false); try ready()
            let pair: NativeWindowKeyPair
            switch input {
            case .key(let value): pair = try buildNativeWindowKey(binding.windowID, key: value)
            case .text(let value): pair = try buildNativeWindowText(binding.windowID, text: value)
            }
            owners = pair.owners; events = [pair.down, pair.up]
        } else if let key {
            try routing?(binding, true); try ready()
            // Return reaches here only through press_key's dialog-text-field check.
            let pair = try buildNativeWindowKey(binding.windowID, key: key, submit: true)
            owners = pair.owners; events = [pair.down, pair.up]
        } else if pointClick {
            try routing?(binding, false); try ready()
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
                try ready(); try validate(binding); try ready()
                if number == 0 { try preflight?(); try ready() }
                try routing?(binding, number == 0); try ready()
                let before = try readInput(deadline)
                guard initial.matches(before) else {
                    throw NativeWindowInputError.refused("user input state changed before input: " + initial.differences(before).joined(separator: ","))
                }
                try ready()
                attempted += 1 // A failed post cannot establish no effect.
                try post(event, binding.pid)
                try ready()
                let after = try readInput(deadline)
                guard before.matches(after) else {
                    throw NativeWindowInputError.refused("user input state changed during input: " + before.differences(after).joined(separator: ","))
                }
                try validate(binding); try ready()
                try routing?(binding, false); try ready()
                let confirmed = try readInput(deadline)
                guard before.matches(confirmed) else {
                    throw NativeWindowInputError.refused("user input state changed during validation: " + before.differences(confirmed).joined(separator: ","))
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
