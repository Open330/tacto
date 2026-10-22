import Foundation
import Darwin
import CryptoKit

// A cross-command routing hint only. The helper hashes itself independently,
// and every connection still checks the signed peer and exact build handshake.
extension TactoMode {
    private static func fileKey(_ info: stat) -> String {
        let identity = "\(info.st_dev):\(info.st_ino):\(info.st_size):\(info.st_mtimespec.tv_sec):\(info.st_mtimespec.tv_nsec):\(info.st_ctimespec.tv_sec):\(info.st_ctimespec.tv_nsec)"
        return SHA256.hash(data:Data(identity.utf8)).map { String(format:"%02x",$0) }.joined()
    }
    private static func cacheDirectory(_ override: String?) -> String? {
        guard let path = override ?? userTemporaryDirectory().map({ $0+"tacto-build-keys" }) else { return nil }
        if mkdir(path,0o700) != 0 && errno != EEXIST { return nil }
        var info = stat()
        guard lstat(path,&info) == 0, info.st_mode & S_IFMT == S_IFDIR,
              info.st_uid == getuid(), info.st_mode & 0o777 == 0o700 else { return nil }
        return path
    }
    private static func readKey(_ path: String) -> String? {
        let fd = open(path,O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard fd >= 0 else { return nil }; defer { close(fd) }
        var info = stat()
        guard fstat(fd,&info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_mode & 0o777 == 0o600,
              info.st_nlink == 1, info.st_size == 64 else { return nil }
        var bytes = [UInt8](repeating:0,count:65)
        guard read(fd,&bytes,bytes.count) == 64,
              bytes.prefix(64).allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { return nil }
        return String(decoding:bytes.prefix(64),as:UTF8.self)
    }
    private static func storeKey(_ value: String, path: String, directory: String) {
        let temporary = directory+"/."+UUID().uuidString
        let fd = open(temporary,O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC,0o600)
        guard fd >= 0 else { return }; defer { close(fd); unlink(temporary) }
        let bytes = Array(value.utf8)
        let written = bytes.withUnsafeBytes { write(fd,$0.baseAddress!,bytes.count) }
        guard written == bytes.count, rename(temporary,path) == 0 else { return }
        // Prune only this cache's strict digest filenames, on a miss, never
        // sockets, locks, symlinks or files belonging to another user.
        guard let names = try? FileManager.default.contentsOfDirectory(atPath:directory), names.count > 64 else { return }
        let records: [(String,Int)] = names.compactMap { name in
            guard name.count == 71, name.hasSuffix(".sha256"),
                  name.prefix(64).utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }) else { return nil }
            let file = directory+"/"+name; var info = stat()
            guard file != path, lstat(file,&info) == 0, info.st_mode & S_IFMT == S_IFREG,
                  info.st_uid == getuid() else { return nil }
            return (file,Int(info.st_mtimespec.tv_sec))
        }
        for (file,_) in records.sorted(by:{$0.1 < $1.1}).prefix(max(0,records.count+1-64)) { unlink(file) }
    }
    // Cache is optional: unsafe/unavailable storage simply computes the key.
    // ctime supplements the requested dev/inode/size/mtime identity so restoring
    // a modified file's previous mtime cannot resurrect an older digest.
    static func cachedBuildKey(_ binary: URL, cacheRoot: String? = nil) -> String? {
        let fd = open(binary.path,O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard fd >= 0 else { return buildKey(binary) }; defer { close(fd) }
        var before = stat()
        guard fstat(fd,&before) == 0, before.st_mode & S_IFMT == S_IFREG,
              let directory = cacheDirectory(cacheRoot) else { return buildKey(binary) }
        let identity = fileKey(before), path = directory+"/"+identity+".sha256"
        var after = stat()
        if let value = readKey(path), fstat(fd,&after) == 0, fileKey(after) == identity { return value }
        let stream = FileHandle(fileDescriptor:fd,closeOnDealloc:false)
        guard let bytes = try? stream.readToEnd() else { return nil }
        let value = SHA256.hash(data:bytes).map { String(format:"%02x",$0) }.joined()
        guard fstat(fd,&after) == 0, fileKey(after) == identity else { return buildKey(binary) }
        storeKey(value,path:path,directory:directory)
        return value
    }
}
