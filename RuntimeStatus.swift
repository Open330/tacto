import Foundation
import CoreFoundation

/// Ephemeral UI metadata only. Notifications never authorize input or reset an abort.
enum TactoRuntimeStatus {
    static let status = Notification.Name("ai.callabo.tacto.runtime.status")
    static let query = Notification.Name("ai.callabo.tacto.runtime.query")
    static let stop = Notification.Name("ai.callabo.tacto.runtime.stop")

    struct Run {
        let id: String
        let pid: Int32
        let target: String
        let stopped: Bool
        let active: Bool
        init?(_ info: [AnyHashable: Any]) {
            guard let schema = info["schema"] as? NSNumber,
                  CFGetTypeID(schema) != CFBooleanGetTypeID(), schema.doubleValue == 1,
                  let uid = info["uid"] as? NSNumber, CFGetTypeID(uid) != CFBooleanGetTypeID(), uid.doubleValue == Double(getuid()),
                  let id = info["id"] as? String, UUID(uuidString:id) != nil,
                  let pid = info["pid"] as? NSNumber, CFGetTypeID(pid) != CFBooleanGetTypeID(), pid.doubleValue > 0, pid.doubleValue <= Double(Int32.max), pid.doubleValue == Double(pid.int32Value),
                  let target = info["target"] as? String, !target.isEmpty, target.utf8.count <= 256, target.rangeOfCharacter(from:.controlCharacters) == nil,
                  let stopped = info["stopped"] as? NSNumber, CFGetTypeID(stopped) == CFBooleanGetTypeID(),
                  let active = info["active"] as? NSNumber, CFGetTypeID(active) == CFBooleanGetTypeID()
            else { return nil }
            self.id=id; self.pid=pid.int32Value; self.target=target
            self.stopped=stopped.boolValue; self.active=active.boolValue
        }
    }

    /// Read without creating files; malformed settings keep the visible default.
    static func showBanner(in directory: URL = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Library/Application Support/Tacto")) -> Bool {
        guard (try? directory.resourceValues(forKeys:[.isSymbolicLinkKey]).isSymbolicLink) != true else { return true }
        let fd = open(directory.appendingPathComponent("preferences.json").path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard fd >= 0 else { return true }
        defer { close(fd) }
        var info = stat()
        guard fstat(fd,&info) == 0, (info.st_mode & S_IFMT) == S_IFREG,
              info.st_uid == getuid(), info.st_size <= 16384 else { return true }
        var bytes = [UInt8](repeating:0,count:16385)
        let count = read(fd,&bytes,bytes.count)
        guard count >= 0, count <= 16384,
              let object = try? JSONSerialization.jsonObject(with:Data(bytes.prefix(count))) as? [String:Any],
              let schema = object["schema"] as? NSNumber,
              CFGetTypeID(schema) != CFBooleanGetTypeID(), schema.doubleValue == 1,
              let value = object["show_control_banner"] as? NSNumber,
              CFGetTypeID(value) == CFBooleanGetTypeID() else { return true }
        return value.boolValue
    }
}
