// nlc — the signed entry point, and the only client the helper will talk to.
//
// A signed binary is not automatically a security principal. If it simply
// relayed whatever it was handed to a privileged helper, an attacker would run
// IT instead and the deputy problem would come straight back (advisor).
//
// So this launcher deliberately does ONE thing: it runs the NLC command line on
// a fixed interpreter and a fixed package directory, both baked in at build
// time. It does not take a script path, an interpreter override, an eval flag,
// or environment that could redirect what executes. What a caller can express
// through it is the NLC command surface — flows, which are declarative data
// evaluated against a safety policy and shown on screen while they run — not
// arbitrary code with the accessibility grant behind it.
//
// What this closes: another process can no longer drive the helper directly,
// because the helper requires its client to satisfy a code requirement that
// only this binary meets.
//
// What it does not close, stated plainly: someone who can run this launcher can
// still run flows. Flows are constrained (policy-gated, visible, interruptible),
// but they are not nothing. A flow's own contract is written by whoever wrote
// the flow, so policy protects a user from a mistaken recipe — not from a local
// attacker writing a dishonest one. The defence there is that the helper makes
// every read and every action visible and stoppable.

import Foundation

// The REAL path of this binary, from the kernel — not `CommandLine.arguments[0]`,
// which is whatever argv[0] the caller chose to pass. Trusting argv[0] let a
// caller point the launcher at their own .venv, which would have made the signed
// launcher the ancestor of their Python (advisor finding: a direct bypass).
func executablePath() -> String {
    var size = UInt32(4096)
    var buf = [CChar](repeating: 0, count: Int(size))
    guard _NSGetExecutablePath(&buf, &size) == 0 else { return "" }
    return String(cString: buf)
}

let exe = URL(fileURLWithPath: executablePath()).resolvingSymlinksInPath()
guard !exe.path.isEmpty else {
    FileHandle.standardError.write(Data("nlc: 실행 경로를 확인하지 못했습니다\n".utf8))
    exit(2)
}
let root = exe
    .deletingLastPathComponent()      // launcher/
    .deletingLastPathComponent()      // repo root
let python = root.appendingPathComponent(".venv/bin/python")
let module = "nlc.cli"

guard FileManager.default.isExecutableFile(atPath: python.path) else {
    FileHandle.standardError.write(Data(
        "nlc: \(python.path) 이 없습니다. `uv sync` 를 먼저 실행하세요.\n".utf8))
    exit(2)
}

let proc = Process()
proc.executableURL = python
// `-I` (isolated): without it `python -m` puts the CALLER's working directory
// first on sys.path, so "a fixed module" would not mean fixed code — a file
// named nlc.py beside the caller would win. -I also drops PYTHON* environment
// and the user site directory.
proc.arguments = ["-I", "-m", module] + Array(CommandLine.arguments.dropFirst())
proc.currentDirectoryURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)

// Allowlist, not denylist. Chasing individual dangerous variables means missing
// one; starting empty and adding what is needed cannot (advisor finding).
// Absolute paths are used for the few tools NLC shells out to, so an inherited
// PATH cannot shadow them.
let allowed = ["HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
               "SSL_CERT_FILE", "TERM"]
var env: [String: String] = [:]
let inherited = ProcessInfo.processInfo.environment
for key in allowed {
    if let v = inherited[key] { env[key] = v }
}
env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
env["NLC_LAUNCHER"] = "1"
proc.environment = env

do {
    try proc.run()
} catch {
    FileHandle.standardError.write(Data("nlc: 실행 실패 \(error)\n".utf8))
    exit(1)
}
proc.waitUntilExit()
exit(proc.terminationStatus)
