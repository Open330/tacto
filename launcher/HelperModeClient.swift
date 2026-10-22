import Foundation
import AppKit
import Darwin

enum ModeConnection {
    case ready(Int32, String)
    case unavailable(String)
    case busy
    case cancelled
}

func modeDiagnostic(_ requested: String, _ effective: String, _ reason: String = "", pid: Int? = nil) {
    guard let bytes = try? JSONSerialization.data(withJSONObject:
        ["tacto_helper_mode":requested,"effective":effective,"reason":reason,"helper_pid":pid as Any? ?? NSNull()]) else { return }
    FileHandle.standardError.write(bytes); FileHandle.standardError.write(Data([10]))
}

func tryWarmConnection(_ key: String, path: String, idleSeconds: Int) -> ModeConnection {
    guard let fd = TactoMode.connect(path) else { return .unavailable("absent") }
    var yes: Int32 = 1
    _ = setsockopt(fd,SOL_SOCKET,SO_NOSIGPIPE,&yes,socklen_t(MemoryLayout<Int32>.size))
    guard peerSatisfies(fd,helperRequirement).0,
          TactoMode.send(fd,["protocol":1,"build":key,"idle_seconds":idleSeconds]),
          let answer = TactoMode.receive(fd) else { close(fd); return .unavailable("handshake_failed") }
    if answer["status"] as? String == "busy" { close(fd); return .busy }
    guard answer["status"] as? String == "ready", answer["build"] as? String == key,
          answer["protocol"] as? Int == 1 else { close(fd); return .unavailable("incompatible") }
    return .ready(fd,"warm")
}

func warmConnection(_ key: String, idleSeconds: Int) -> ModeConnection {
    guard let directory = TactoMode.directory(key) else { return .unavailable("private_directory") }
    let path = directory+"/helper.sock"
    switch tryWarmConnection(key,path:path,idleSeconds:idleSeconds) {
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
    switch tryWarmConnection(key,path:path,idleSeconds:idleSeconds) {
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
    config.arguments = ["--warm-server","--mode-idle",String(idleSeconds)]
    config.environment = env
    let done = DispatchSemaphore(value:0)
    var started: NSRunningApplication?
    NSWorkspace.shared.openApplication(at:helperApp,configuration:config) { app,_ in started=app; done.signal() }
    guard done.wait(timeout:.now()+10) == .success, started != nil else { return .unavailable("launch_failed") }
    let deadline = ProcessInfo.processInfo.systemUptime+5
    repeat {
        switch tryWarmConnection(key,path:path,idleSeconds:idleSeconds) {
        case .unavailable(let reason) where reason == "absent": usleep(20_000)
        case let result: return result
        }
    } while ProcessInfo.processInfo.systemUptime < deadline
    return .unavailable("startup_timeout")
}

// Waiting never opens an independent A helper to bypass a busy lease.
func acquireModeConnection(_ key: String, idleSeconds: Int, cancelled: () -> Bool) -> ModeConnection {
    let raw = Double(ProcessInfo.processInfo.environment["TACTO_HELPER_WAIT_SECONDS"] ?? "10") ?? 10
    let seconds = raw.isFinite ? min(30,max(0,raw)) : 10
    return TactoMode.waitForLease(seconds:seconds,
        attempt:{ warmConnection(key,idleSeconds:idleSeconds) },
        busy:{ if case .busy = $0 { return true }; return false },
        cancelled:cancelled) ?? .cancelled
}
