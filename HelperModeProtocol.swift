// Optional benchmark transports. Default launches remain unchanged.
import Foundation
import Darwin
import CryptoKit

enum TactoMode {
    static let limit = 8 * 1024 * 1024
    static let workerCount = 4
    static func workerKey(_ build: String, slot: Int) -> String {
        SHA256.hash(data: Data("\(build):worker:\(slot)".utf8))
            .map { String(format:"%02x", $0) }.joined()
    }
    // Retry acquisition only. No application frame exists until this returns.
    static func waitForLease<T>(seconds: Double, attempt: () -> T,
                                busy: (T) -> Bool, cancelled: () -> Bool) -> T? {
        let deadline = ProcessInfo.processInfo.systemUptime + seconds
        var result = attempt()
        while busy(result) {
            if cancelled() { return nil }
            if ProcessInfo.processInfo.systemUptime >= deadline { return result }
            usleep(50_000)
            if cancelled() { return nil }
            result = attempt()
        }
        return result
    }
    static func buildKey(_ binary: URL) -> String? {
        guard let bytes = try? Data(contentsOf: binary) else { return nil }
        return SHA256.hash(data: bytes).map { String(format:"%02x", $0) }.joined()
    }
    static func directory(_ key: String) -> String? {
        let count = confstr(_CS_DARWIN_USER_TEMP_DIR,nil,0)
        guard count > 1, count <= 4096 else { return nil }
        var bytes = [CChar](repeating:0,count:count)
        guard confstr(_CS_DARWIN_USER_TEMP_DIR,&bytes,bytes.count) == count else { return nil }
        let parent = String(cString:bytes)
        var parentInfo = stat()
        guard lstat(parent,&parentInfo) == 0, parentInfo.st_uid == getuid(),
              parentInfo.st_mode & S_IFMT == S_IFDIR, parentInfo.st_mode & 0o777 == 0o700 else { return nil }
        let path = parent + (parent.hasSuffix("/") ? "" : "/") + "tacto-warm-\(key.prefix(20))"
        guard address(path+"/helper.sock") != nil else { return nil }
        if mkdir(path, 0o700) != 0 && errno != EEXIST { return nil }
        var info = stat()
        guard lstat(path, &info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFDIR, info.st_mode & 0o777 == 0o700 else { return nil }
        return path
    }
    static func address(_ path: String) -> sockaddr_un? {
        var addr = sockaddr_un(); addr.sun_family = sa_family_t(AF_UNIX)
        guard path.utf8.count < MemoryLayout.size(ofValue:addr.sun_path) else { return nil }
        withUnsafeMutableBytes(of:&addr.sun_path) { dst in
            Array(path.utf8CString).withUnsafeBytes { src in dst.copyBytes(from:src) }
        }
        return addr
    }
    static func connect(_ path: String) -> Int32? {
        let fd = socket(AF_UNIX,SOCK_STREAM,0)
        guard fd >= 0, var addr = address(path) else { return nil }
        let ok = withUnsafePointer(to:&addr) { p in p.withMemoryRebound(to:sockaddr.self,capacity:1) {
            Darwin.connect(fd,$0,socklen_t(MemoryLayout<sockaddr_un>.size)) == 0
        }}
        guard ok else { close(fd); return nil }; return fd
    }
    static func send(_ fd: Int32, _ object: [String:Any]) -> Bool {
        guard var data = try? JSONSerialization.data(withJSONObject:object) else { return false }
        data.append(10); var position = 0
        return data.withUnsafeBytes { bytes in
            while position < data.count {
                let n = write(fd, bytes.baseAddress!.advanced(by:position), data.count-position)
                if n < 0 && errno == EINTR { continue }
                if n <= 0 { return false }; position += n
            }
            return true
        }
    }
    // Used only for the tiny pre-request handshake; read exactly through LF,
    // never consume a following application frame.
    static func receive(_ fd: Int32, seconds: Double = 3) -> [String:Any]? {
        var bytes = Data(); let deadline = ProcessInfo.processInfo.systemUptime + seconds
        while bytes.count < 4096 {
            let left = deadline - ProcessInfo.processInfo.systemUptime
            if left <= 0 { return nil }
            var p = pollfd(fd:fd,events:Int16(POLLIN),revents:0)
            let result = poll(&p,1,Int32(max(1,left*1000)))
            if result < 0 && errno == EINTR { continue }
            guard result > 0 else { return nil }
            var byte: UInt8 = 0
            let n = read(fd,&byte,1)
            if n < 0 && errno == EINTR { continue }
            guard n == 1 else { return nil }
            if byte == 10 { return (try? JSONSerialization.jsonObject(with:bytes)) as? [String:Any] }
            bytes.append(byte)
        }
        return nil
    }
}

// Cross-process ownership also covers per-command helpers and different builds.
// Never unlink lock files: existing waiters must keep the same inode.
final class TactoScopeLease {
    private var descriptors: [String:(fd:Int32,shared:Bool)] = [:]
    func take(_ scope: String, shared: Bool = false) -> Bool {
        if let held = descriptors[scope] { return !held.shared || shared }
        let namespace = TactoMode.workerKey("tacto-resource-leases-v1", slot:0)
        guard let directory = TactoMode.directory(namespace) else { return false }
        let key = SHA256.hash(data:Data(scope.utf8)).map { String(format:"%02x",$0) }.joined()
        let fd = open(directory+"/"+key+".lock",O_CREAT|O_RDWR|O_NOFOLLOW|O_CLOEXEC,0o600)
        guard fd >= 0 else { return false }
        var info = stat()
        guard fstat(fd,&info) == 0, info.st_uid == getuid(),
              info.st_mode & S_IFMT == S_IFREG, info.st_mode & 0o777 == 0o600,
              flock(fd,(shared ? LOCK_SH : LOCK_EX)|LOCK_NB) == 0 else { close(fd); return false }
        descriptors[scope] = (fd,shared); return true
    }
    deinit {
        for entry in descriptors.values { _ = flock(entry.fd,LOCK_UN); close(entry.fd) }
    }
}
