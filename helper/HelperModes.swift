import Foundation
import AppKit
import Darwin

// A timed-out asynchronous stream callback must not revive an older run's
// capture after its lease ends. Rotate before clearing shared UI state.
final class ModeEpoch: @unchecked Sendable {
    static let shared = ModeEpoch()
    private let lock = NSLock()
    private var value = UUID()
    func snapshot() -> UUID { lock.lock(); defer { lock.unlock() }; return value }
    func rotate() { lock.lock(); value = UUID(); lock.unlock() }
    @discardableResult func perform(_ expected: UUID, _ body: () -> Void) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard expected == value else { return false }; body(); return true
    }
}

// Prototype boundary: all mutable helper state is protected by one driving
// lease. A second connection is refused BEFORE any screen request. No replay.
final class ModeLease: @unchecked Sendable {
    static let shared = ModeLease()
    private let lock = NSLock()
    private var owner: UUID?
    private var lastActivity = ProcessInfo.processInfo.systemUptime
    func take(_ id: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard owner == nil else { return false }; owner = id; return true
    }
    func owns(_ id: UUID) -> Bool { lock.lock(); defer { lock.unlock() }; return owner == id }
    func stop(_ id: UUID) {
        // Checking the owner and stopping must be one operation: a delayed
        // invalidation must not slip between release and a new owner's begin.
        lock.lock(); defer { lock.unlock() }
        if owner == id { abortState.stopNow() }
    }
    func release(_ id: UUID) {
        lock.lock(); defer { lock.unlock() }
        if owner == id { owner = nil; lastActivity = ProcessInfo.processInfo.systemUptime }
    }
    func expired(_ seconds: Double) -> Bool {
        lock.lock(); defer { lock.unlock() }
        return owner == nil && ProcessInfo.processInfo.systemUptime-lastActivity > seconds
    }
}

func modeRefusal(_ code: String) -> Data {
    (try? JSONSerialization.data(withJSONObject:["type":"error","error":code,"effect":"not_started"])) ?? Data()
}

// The serial worker owns globals; invalidation marks abort immediately and
// queues cleanup AFTER an in-flight request, before another lease can begin.
let modeWorker = DispatchQueue(label:"tacto.prototype.driving-lease")

final class ModeRPCSession: NSObject, TactoModeRPC, @unchecked Sendable {
    let id = UUID()
    let build: String
    private let lock = NSLock()
    private var connected = true
    private var acquired = false
    init(build: String) { self.build = build }
    func alive() -> Bool { lock.lock(); defer { lock.unlock() }; return connected }
    func acquire(_ requested: String, reply: @escaping (NSDictionary) -> Void) {
        lock.lock()
        guard connected, !acquired, requested == build else {
            lock.unlock(); reply(["status":"incompatible"]); return
        }
        guard ModeLease.shared.take(id) else { lock.unlock(); reply(["status":"busy"]); return }
        acquired = true; lock.unlock()
        modeWorker.async {
            guard self.alive() else { self.finish(); reply(["status":"closed"]); return }
            resetPersistentSession(starting:true)
            reply(["status":"ready","protocol":1,"build":self.build,"pid":getpid()])
        }
    }
    func request(_ frame: Data, reply: @escaping (Data) -> Void) {
        modeWorker.async {
            guard self.alive(), ModeLease.shared.owns(self.id) else { reply(modeRefusal("helper_lease_missing")); return }
            guard frame.count <= TactoMode.limit,
                  let message = (try? JSONSerialization.jsonObject(with:frame)) as? [String:Any] else {
                reply(modeRefusal("invalid_frame")); return
            }
            reply((try? JSONSerialization.data(withJSONObject:handle(message))) ?? modeRefusal("invalid_reply"))
        }
    }
    func finish() {
        if ModeLease.shared.owns(id) {
            resetPersistentSession(starting:false)
            ModeLease.shared.release(id)
        }
    }
    func invalidate() {
        lock.lock(); connected = false; lock.unlock()
        ModeLease.shared.stop(id)
        modeWorker.async { self.finish() }
    }
}

final class ModeXPCListener: NSObject, NSXPCListenerDelegate {
    let build: String
    init(build: String) { self.build = build }
    func listener(_ listener: NSXPCListener, shouldAcceptNewConnection connection: NSXPCConnection) -> Bool {
        connection.setCodeSigningRequirement(clientRequirement)
        let session = ModeRPCSession(build:build)
        connection.exportedInterface = NSXPCInterface(with:TactoModeRPC.self)
        connection.exportedObject = session
        connection.invalidationHandler = { session.invalidate() }
        connection.interruptionHandler = { session.invalidate() }
        connection.resume(); return true
    }
}

// Objects must outlive startup. Only explicit --resident-agent uses launchd.
var residentListener: NSXPCListener?
var residentDelegate: ModeXPCListener?
func startResidentMode(_ build: String) {
    let delegate = ModeXPCListener(build:build)
    let listener = NSXPCListener(machServiceName:TactoMode.service)
    listener.delegate = delegate; residentDelegate = delegate; residentListener = listener
    listener.resume()
}

func startWarmMode(_ build: String) -> Bool {
    guard let directory = TactoMode.directory(build),
          var address = TactoMode.address(directory+"/helper.sock") else { return false }
    let fd = socket(AF_UNIX,SOCK_STREAM,0)
    guard fd >= 0 else { return false }
    let bound = withUnsafePointer(to:&address) { p in p.withMemoryRebound(to:sockaddr.self,capacity:1) {
        bind(fd,$0,socklen_t(MemoryLayout<sockaddr_un>.size)) == 0
    }}
    guard bound, listen(fd,8) == 0 else { close(fd); return false }
    chmod(directory+"/helper.sock",0o600)
    DispatchQueue.global().async {
        while true {
            let conn = accept(fd,nil,nil)
            if conn < 0 { if errno == EINTR { continue }; return }
            DispatchQueue.global().async {
                defer { close(conn) }
                var yes: Int32 = 1
                _ = setsockopt(conn,SOL_SOCKET,SO_NOSIGPIPE,&yes,socklen_t(MemoryLayout<Int32>.size))
                guard PeerAuth.satisfies(fd:conn,requirement:clientRequirement).0,
                      let hello = TactoMode.receive(conn), hello["build"] as? String == build,
                      hello["protocol"] as? Int == 1 else { return }
                let id = UUID()
                guard ModeLease.shared.take(id) else { _ = TactoMode.send(conn,["status":"busy"]); return }
                modeWorker.sync {
                    resetPersistentSession(starting:true)
                    guard TactoMode.send(conn,["status":"ready","protocol":1,"build":build,"pid":getpid()]) else {
                        resetPersistentSession(starting:false); ModeLease.shared.release(id); return
                    }
                    // Notice a disconnected client while an AX request is in flight.
                    // This monitor never consumes data and only stops its own lease.
                    DispatchQueue.global().async {
                        while ModeLease.shared.owns(id) {
                            var byte: UInt8 = 0
                            let n = recv(conn,&byte,1,MSG_PEEK | MSG_DONTWAIT)
                            if n == 0 { ModeLease.shared.stop(id); return }
                            usleep(100_000)
                        }
                    }
                    serve(connection:conn)
                    resetPersistentSession(starting:false)
                    ModeLease.shared.release(id)
                }
            }
        }
    }
    return true
}
