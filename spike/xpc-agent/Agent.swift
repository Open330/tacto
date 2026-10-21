import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

/// One object per connection: everything a client can change lives here, so a
/// new connection starts from nothing and an old one's state dies with it.
final class Session: NSObject, SpikeAgentProtocol {
    let id = UUID().uuidString
    var calls = 0

    func ping(reply: @escaping (NSDictionary) -> Void) {
        calls += 1
        reply([
            "pid": getpid(),
            "agent_started_at": agentStartedAt.timeIntervalSince1970,
            "session": id,
            "calls_on_this_connection": calls,
            "ax_trusted": AXIsProcessTrusted(),
            "screen_capture": CGPreflightScreenCaptureAccess(),
            "parent_pid": getppid(),
        ] as NSDictionary)
    }
}

let agentStartedAt = Date()
var openConnections = 0
var lastActivity = Date()

final class Listener: NSObject, NSXPCListenerDelegate {
    let clientRequirement: String

    init(clientRequirement: String) { self.clientRequirement = clientRequirement }

    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        // Checked by the system against the caller's audit token for every
        // message, not once against a pid.
        connection.setCodeSigningRequirement(clientRequirement)
        connection.exportedInterface = NSXPCInterface(with: SpikeAgentProtocol.self)
        connection.exportedObject = Session()
        DispatchQueue.main.async { openConnections += 1; lastActivity = Date() }
        let ended: () -> Void = { DispatchQueue.main.async { openConnections -= 1; lastActivity = Date() } }
        connection.invalidationHandler = ended
        connection.resume()
        return true
    }
}

guard let team = ownTeam() else {
    FileHandle.standardError.write("spike-agent: no verified team\n".data(using: .utf8)!)
    exit(78)
}
let delegate = Listener(clientRequirement: teamRequirement(identifier: "ai.callabo.tacto", team: team))
let listener = NSXPCListener(machServiceName: spikeMachService)
listener.delegate = delegate
listener.resume()

// Idle exit: with no connection for 10 s the agent leaves, and launchd starts
// it again on the next connection. Nothing holds a grant while unused.
Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
    if openConnections == 0 && Date().timeIntervalSince(lastActivity) > 10 { exit(0) }
}
NSApplication.shared.setActivationPolicy(.prohibited)
RunLoop.main.run()
