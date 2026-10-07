// Fresh public Process Manager reads. NSWorkspace can retain its foreground
// snapshot until its runloop runs; that snapshot cannot authorize a focus write.
// The public deprecated APIs remain exported on the tested host. Swift marks
// them unavailable at compile time, so bind the documented C ABI dynamically.
import Darwin

private struct NativeFrontProcessSerial {
    var high: UInt32 = 0
    var low: UInt32 = 0
}

func nativeFrontmostProcessPID() -> pid_t? {
    guard MemoryLayout<NativeFrontProcessSerial>.size == 8,
          let handle = dlopen("/System/Library/Frameworks/ApplicationServices.framework/Frameworks/HIServices.framework/HIServices",
                              RTLD_LAZY | RTLD_LOCAL) else { return nil }
    defer { dlclose(handle) }
    guard let frontSymbol = dlsym(handle, "GetFrontProcess"),
          let pidSymbol = dlsym(handle, "GetProcessPID") else { return nil }
    typealias Front = @convention(c) (UnsafeMutableRawPointer?) -> Int16
    typealias PID = @convention(c) (UnsafeRawPointer?, UnsafeMutablePointer<pid_t>?) -> Int32
    let front = unsafeBitCast(frontSymbol, to: Front.self)
    let resolve = unsafeBitCast(pidSymbol, to: PID.self)
    var first = NativeFrontProcessSerial()
    var second = NativeFrontProcessSerial()
    var pid: pid_t = -1
    guard withUnsafeMutablePointer(to: &first, { front(UnsafeMutableRawPointer($0)) }) == 0,
          first.high != 0 || first.low != 0,
          withUnsafePointer(to: first, { resolve(UnsafeRawPointer($0), &pid) }) == 0,
          pid > 0,
          withUnsafeMutablePointer(to: &second, { front(UnsafeMutableRawPointer($0)) }) == 0,
          first.high == second.high && first.low == second.low else { return nil }
    return pid
}
