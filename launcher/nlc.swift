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

// Resolve relative to the RUNNING binary, not #filePath — that is the source
// location baked in at build time, which is wrong the moment the binary moves.
let exe = URL(fileURLWithPath: CommandLine.arguments[0])
    .resolvingSymlinksInPath()
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
proc.arguments = ["-m", module] + Array(CommandLine.arguments.dropFirst())
proc.currentDirectoryURL = URL(fileURLWithPath: FileManager.default.currentDirectoryPath)

// A fixed, minimal environment: nothing a caller sets can change which code
// runs. PATH is needed for the tools a flow may legitimately shell out to
// (`open`, `caffeinate`); PYTHON* variables that could inject code are dropped.
var env = ProcessInfo.processInfo.environment
for key in ["PYTHONPATH", "PYTHONSTARTUP", "PYTHONHOME", "PYTHONEXECUTABLE",
            "PYTHONWARNINGS", "PYTHONINSPECT", "DYLD_INSERT_LIBRARIES",
            "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "NLC_HELPER_TOKEN"] {
    env.removeValue(forKey: key)
}
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
