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

// Each pool worker is a separate process. Within a worker, mutable AX/token
// state still belongs to exactly one connection. Other workers run in parallel.
final class ModeLease: @unchecked Sendable {
    static let shared = ModeLease()
    private let lock = NSLock()
    private var idleSeconds: Double = 120
    private var retiring = false
    private var owner: UUID?
    private var lastActivity = ProcessInfo.processInfo.systemUptime
    func take(_ id: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard owner == nil, !retiring else { return false }; owner = id; return true
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
    func setInitialIdle(_ seconds: Double) {
        lock.lock(); idleSeconds = seconds; lock.unlock()
    }
    func setIdle(_ seconds: Int, owner id: UUID) {
        lock.lock(); defer { lock.unlock() }
        if owner == id { idleSeconds = Double(min(600,max(1,seconds))) }
    }
    func beginIdleExit() -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard owner == nil, ProcessInfo.processInfo.systemUptime-lastActivity > idleSeconds else { return false }
        retiring = true; return true
    }
}

let modeWorker = DispatchQueue(label:"tacto.warm.driving-lease")

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
                if let idle = hello["idle_seconds"] as? NSNumber,
                   CFGetTypeID(idle) != CFBooleanGetTypeID(), idle.doubleValue == Double(idle.intValue),
                   (1...600).contains(idle.intValue) { ModeLease.shared.setIdle(idle.intValue,owner:id) }
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
