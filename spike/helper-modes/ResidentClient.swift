import Foundation
import AppKit
import Darwin

final class ModeReplyBox: @unchecked Sendable {
    let done = DispatchSemaphore(value:0)
    let lock = NSLock()
    var data: Data?
    var answered = false
    func answer(_ value: Data?) { lock.lock(); guard !answered else { lock.unlock(); return }; answered=true; data=value; lock.unlock(); done.signal() }
    func value() -> Data? { lock.lock(); defer { lock.unlock() }; return data }
}

func residentConnection(_ key: String) -> ModeConnection {
    let connection = NSXPCConnection(machServiceName:TactoResident.service,options:[])
    connection.setCodeSigningRequirement(helperRequirement)
    connection.remoteObjectInterface = NSXPCInterface(with:TactoModeRPC.self)
    connection.resume()
    let hello = ModeReplyBox()
    let proxy = connection.remoteObjectProxyWithErrorHandler { _ in hello.answer(nil) } as? TactoModeRPC
    proxy?.acquire(key) { info in hello.answer(try? JSONSerialization.data(withJSONObject:info)) }
    guard hello.done.wait(timeout:.now()+3) == .success, let bytes=hello.value(),
          let answer=(try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any] else {
        connection.invalidate(); return .unavailable("xpc_unregistered_or_refused")
    }
    if answer["status"] as? String == "busy" { connection.invalidate(); return .busy }
    guard answer["status"] as? String == "ready", answer["protocol"] as? Int == 1,
          answer["build"] as? String == key else {
        connection.invalidate(); return .unavailable("xpc_incompatible")
    }
    residentModePID = answer["pid"] as? Int
    var sockets: [Int32] = [0,0]
    guard socketpair(AF_UNIX,SOCK_STREAM,0,&sockets) == 0 else {
        connection.invalidate(); return .unavailable("xpc_bridge")
    }
    let bridge = sockets[1]
    var yes: Int32 = 1
    for fd in sockets { _ = setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,socklen_t(MemoryLayout<Int32>.size)) }
    DispatchQueue.global().async {
        defer { close(bridge); connection.invalidate() }
        var buffer = Data()
        while let message = readJSONLine(bridge,&buffer) {
            guard let frame=try? JSONSerialization.data(withJSONObject:message), frame.count <= TactoMode.limit else { return }
            let reply = ModeReplyBox()
            let remote = connection.remoteObjectProxyWithErrorHandler { _ in reply.answer(nil) } as? TactoModeRPC
            remote?.request(frame) { reply.answer($0) }
            // Once dispatched, timeout/invalidation closes the bridge. The
            // relay reports UNKNOWN and never reconnects/replays that request.
            guard reply.done.wait(timeout:.now()+120) == .success, let data=reply.value(),
                  data.count <= TactoMode.limit,
                  let object=(try? JSONSerialization.jsonObject(with:data)) as? [String:Any] else { return }
            writeJSONLine(bridge,object)
        }
    }
    return .ready(sockets[0],"resident")
}
