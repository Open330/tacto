import Foundation
import AppKit
import Darwin

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
    let listener = NSXPCListener(machServiceName:TactoResident.service)
    listener.delegate = delegate; residentDelegate = delegate; residentListener = listener
    listener.resume()
}
