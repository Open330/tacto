import Foundation
import AppKit
import Darwin

enum ModeConnection {
    case ready(Int32, String)
    case unavailable(String)
    case busy
    case cancelled
}

var residentModePID: Int?
func modeDiagnostic(_ requested: String, _ effective: String, _ reason: String = "", pid: Int? = nil) {
    guard let bytes = try? JSONSerialization.data(withJSONObject:
        ["tacto_helper_mode":requested,"effective":effective,"reason":reason,"helper_pid":pid as Any? ?? NSNull()]) else { return }
    FileHandle.standardError.write(bytes); FileHandle.standardError.write(Data([10]))
}

func tryWarmConnection(_ key: String, path: String) -> ModeConnection {
    guard let fd = TactoMode.connect(path) else { return .unavailable("absent") }
    var yes: Int32 = 1
    _ = setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,socklen_t(MemoryLayout<Int32>.size))
    guard peerSatisfies(fd,helperRequirement).0,
          TactoMode.send(fd,["protocol":1,"build":key]),
          let answer = TactoMode.receive(fd) else { close(fd); return .unavailable("handshake_failed") }
    if answer["status"] as? String == "busy" { close(fd); return .busy }
    guard answer["status"] as? String == "ready", answer["build"] as? String == key,
          answer["protocol"] as? Int == 1 else { close(fd); return .unavailable("incompatible") }
    return .ready(fd,"warm")
}

func warmConnection(_ key: String) -> ModeConnection {
    guard let directory = TactoMode.directory(key) else { return .unavailable("private_directory") }
    let path = directory+"/helper.sock"
    switch tryWarmConnection(key,path:path) {
    case .unavailable(let reason) where reason == "absent": break
    case let result: return result
    }
    // Serialize cold launches without allowing an unsigned process to replace
    // the lock with a symlink. Subsequent callers recheck after taking it.
    let lock = open(directory+"/launch.lock",O_CREAT|O_RDWR|O_NOFOLLOW,0o600)
    guard lock >= 0 else { return .unavailable("launch_lock") }
    defer { close(lock) }
    var info = stat()
    guard fstat(lock,&info) == 0, info.st_uid == getuid(), info.st_mode & S_IFMT == S_IFREG,
          info.st_mode & 0o777 == 0o600 else { return .unavailable("launch_lock_owner") }
    let until = ProcessInfo.processInfo.systemUptime + 12
    while flock(lock,LOCK_EX|LOCK_NB) != 0 {
        if ProcessInfo.processInfo.systemUptime >= until { return .unavailable("launch_lock_timeout") }
        usleep(20_000)
    }
    defer { flock(lock,LOCK_UN) }
    switch tryWarmConnection(key,path:path) {
    case .unavailable(let reason) where reason == "absent": break
    case let result: return result
    }
    // A socket whose server exited is safe to remove only inside our checked
    // private build-specific directory, while holding the launch lock.
    var socketInfo = stat()
    if lstat(path,&socketInfo) == 0 {
        guard socketInfo.st_uid == getuid(), socketInfo.st_mode & S_IFMT == S_IFSOCK else {
            return .unavailable("unexpected_socket")
        }
        unlink(path)
    }
    let config = NSWorkspace.OpenConfiguration()
    config.activates = false; config.addsToRecentItems = false
    config.createsNewApplicationInstance = true; config.allowsRunningApplicationSubstitution = false
    let idle = min(600,max(1,Int(ProcessInfo.processInfo.environment["TACTO_HELPER_IDLE_SECONDS"] ?? "120") ?? 120))
    config.arguments = ["--warm-server","--mode-idle",String(idle)]
    config.environment = env
    let done = DispatchSemaphore(value:0)
    var started: NSRunningApplication?
    NSWorkspace.shared.openApplication(at:helperApp,configuration:config) { app,_ in started=app; done.signal() }
    guard done.wait(timeout:.now()+10) == .success, started != nil else { return .unavailable("launch_failed") }
    let deadline = ProcessInfo.processInfo.systemUptime+5
    repeat {
        switch tryWarmConnection(key,path:path) {
        case .unavailable(let reason) where reason == "absent": usleep(20_000)
        case let result: return result
        }
    } while ProcessInfo.processInfo.systemUptime < deadline
    return .unavailable("startup_timeout")
}

final class ModeReplyBox: @unchecked Sendable {
    let done = DispatchSemaphore(value:0)
    let lock = NSLock()
    var data: Data?
    var answered = false
    func answer(_ value: Data?) { lock.lock(); guard !answered else { lock.unlock(); return }; answered=true; data=value; lock.unlock(); done.signal() }
    func value() -> Data? { lock.lock(); defer { lock.unlock() }; return data }
}

func residentConnection(_ key: String) -> ModeConnection {
    let connection = NSXPCConnection(machServiceName:TactoMode.service,options:[])
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

// Waiting never opens an independent A helper to bypass a busy lease.
func acquireModeConnection(_ key: String, mode: String, cancelled: () -> Bool) -> ModeConnection {
    let raw = Double(ProcessInfo.processInfo.environment["TACTO_HELPER_WAIT_SECONDS"] ?? "10") ?? 10
    let seconds = raw.isFinite ? min(30,max(0,raw)) : 10
    return TactoMode.waitForLease(seconds:seconds,
        attempt:{ mode == "warm" ? warmConnection(key) : residentConnection(key) },
        busy:{ if case .busy = $0 { return true }; return false },
        cancelled:cancelled) ?? .cancelled
}
