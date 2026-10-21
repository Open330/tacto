import Foundation

/// Connects, pings twice on one connection, prints JSON with timings.
guard let team = ownTeam() else { print("{\"error\":\"no verified team\"}"); exit(78) }
let started = Date()
let connection = NSXPCConnection(machServiceName: spikeMachService, options: [])
// The client pins the agent too: only our signed helper identity answers.
connection.setCodeSigningRequirement(teamRequirement(identifier: "ai.callabo.tacto.helper", team: team))
connection.remoteObjectInterface = NSXPCInterface(with: SpikeAgentProtocol.self)
connection.resume()

var results: [[String: Any]] = []
var failure: String?
let done = DispatchSemaphore(value: 0)

func call(_ remaining: Int) {
    let sent = Date()
    let proxy = connection.remoteObjectProxyWithErrorHandler { error in
        failure = "\(error)"; done.signal()
    } as? SpikeAgentProtocol
    proxy?.ping { info in
        var row = info as? [String: Any] ?? [:]
        row["round_trip_ms"] = Int(Date().timeIntervalSince(sent) * 1000)
        results.append(row)
        if remaining > 1 { call(remaining - 1) } else { done.signal() }
    }
}
call(2)
_ = done.wait(timeout: .now() + 15)
connection.invalidate()
let out: [String: Any] = ["total_ms": Int(Date().timeIntervalSince(started) * 1000),
                          "error": failure ?? NSNull(), "calls": results]
let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
print(String(data: data, encoding: .utf8)!)
