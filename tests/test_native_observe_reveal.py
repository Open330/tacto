"""Execute the production native reveal walk/validator with deterministic AX data."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SUPPORT = r'''
import Foundation
import ApplicationServices
var graph: [pid_t: [String: CFTypeRef]] = [:]
func element(_ n: Int) -> AXUIElement { AXUIElementCreateApplication(pid_t(n)) }
func nodeID(_ node: AXUIElement) -> pid_t { var pid: pid_t = 0; AXUIElementGetPid(node, &pid); return pid }
func AXUIElementCopyAttributeValue(_ node: AXUIElement, _ key: CFString, _ out: UnsafeMutablePointer<CFTypeRef?>) -> AXError {
 guard let value = graph[nodeID(node)]?[key as String] else { return .attributeUnsupported }
 out.pointee = value; return .success
}
func AXUIElementCopyMultipleAttributeValues(_ node: AXUIElement, _ keys: CFArray, _ opts: AXCopyMultipleAttributeOptions, _ out: UnsafeMutablePointer<CFArray?>) -> AXError {
 out.pointee = (keys as! [String]).map { key -> CFTypeRef in
  if let value = graph[nodeID(node)]?[key] { return value }
  var error = AXError.attributeUnsupported; return AXValueCreate(.axError, &error)!
 } as CFArray
 return .success
}
func AXUIElementCopyActionNames(_ node: AXUIElement, _ out: UnsafeMutablePointer<CFArray?>) -> AXError {
 out.pointee = [] as CFArray; return .success
}
func AXUIElementSetMessagingTimeout(_ node: AXUIElement, _ timeout: Float) -> AXError { .success }
func documentURLSyntaxPath(_ text: String) -> String? { URL(string: text)?.path }
func put(_ n: Int, _ title: String, _ children: [Int] = []) {
 graph[pid_t(n)] = ["AXRole": "AXGroup" as CFString, "AXTitle": title as CFString,
                    "AXChildren": children.map(element) as CFArray]
}
func render(_ scan: ObservationRevealScan?) -> (String, [AXUIElement], Int) {
 var lines: [String] = [], elements: [AXUIElement] = []; var visited = 0, truncated = false
 walk(element(1), depth: 0, out: &lines, elements: &elements,
      visited: &visited, truncated: &truncated, reveal: scan)
 return (lines.joined(separator: "\n"), elements, visited)
}
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swift'), 'macOS Swift required')
class NativeRevealTests(unittest.TestCase):
    def execute(self, source):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'main.swift'
            path.write_text(source)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def test_real_walk_reveals_late_child_with_bounded_exact_scans(self):
        code = (ROOT / 'helper/TactoHelper.swift').read_text()
        code = code[code.index('let interactableRoles:'):code.index('// MARK: - Session state')]
        checks = r'''
put(1, "Window", Array(2...3001))
for n in 2...3001 { put(n, "file-\(n)") }
let baseline = render(nil)
precondition(!baseline.0.contains("Title: file-2999"))
let scan = ObservationRevealScan(["file-2999"])
let found = render(scan)
precondition(scan.complete && scan.scannedNodes == 2940)
precondition(found.0.contains("개 더 있음; 찾는 항목 1개 표시)") && scan.revealed == 1)
precondition(found.2 == baseline.2 + 1 && found.2 <= maxTreeNodes)
precondition(nodeID(found.1.last!) == 2999) // Real observation element index, not a scan-local index.
precondition(!found.0.contains("Title: file-2998"))
let miss = render(ObservationRevealScan(["file-29999"]))
precondition(miss.0 == baseline.0) // Exact equality; no substring expansion.
put(2999, "container", [4000]); put(4000, "nested-target")
let nested = render(ObservationRevealScan(["nested-target"]))
precondition(nested.0.contains("container") && nested.0.contains("nested-target"))
// Depth/node bounds are incomplete observations, never proof of absence.
put(2999, "deep", [4000]); put(4000, "a", [4001]); put(4001, "b", [4002])
put(4002, "c", [4003]); put(4003, "too-deep")
let depth = ObservationRevealScan(["too-deep"]); _ = render(depth)
precondition(!depth.complete)
put(2999, "wide", Array(4100...4150)); for n in 4100...4150 { put(n, "other") }
let wide = ObservationRevealScan(["absent"]); _ = render(wide)
precondition(!wide.complete)
// A normal rendering cutoff is incomplete too, even before a scan starts.
let budget = ObservationRevealScan(["absent"])
var cutLines: [String] = [], cutElements: [AXUIElement] = []; var cutVisited = 0, cut = false
walk(element(1), depth: 0, out: &cutLines, elements: &cutElements,
     visited: &cutVisited, truncated: &cut, nodeLimit: 2, reveal: budget)
precondition(!budget.complete && cutVisited == 2)
// Global node ceiling applies across all omitted children, not per container.
graph = [:]; put(1, "Window", Array(2...5201)); for n in 2...5201 { put(n, "other") }
let capped = ObservationRevealScan(["absent"]); _ = render(capped)
precondition(!capped.complete && capped.scannedNodes == 5000)
// An injected clock makes deadline behaviour deterministic without waiting.
var clock = 0.0
let timed = ObservationRevealScan(["absent"], now: { clock }, snapshot: { node, remaining in
 precondition(remaining > 0 && remaining <= 3); clock += 1; return ObservationNodeSnapshot(node)
})
_ = render(timed); precondition(!timed.complete && timed.scannedNodes <= 3)
let failed = ObservationRevealScan(["absent"], snapshot: { _, _ in nil })
_ = render(failed); precondition(!failed.complete)
// Boolean AXValue is not a rendered label and cannot be a match.
put(6000, ""); graph[6000]!["AXValue"] = kCFBooleanTrue
let boolScan = ObservationRevealScan(["1"]); precondition(!boolScan.matches(element(6000)))
put(6002, "e\u{0301}")
precondition(!ObservationRevealScan(["é"]).matches(element(6002)))
precondition(ObservationRevealScan(["é", "e\u{0301}"]).matches(element(6002)))
var axFailure = AXError.cannotComplete
put(6001, ""); graph[6001]!["AXTitle"] = AXValueCreate(.axError, &axFailure)!
let partial = ObservationRevealScan(["absent"]); precondition(!partial.matches(element(6001)) && !partial.complete)
let invalid: [Any] = [[], [""], [true], Array(repeating: "x", count: 9), ["a\nb"], [String(repeating: "x", count: 257)]]
precondition(invalid.allSatisfy { validRevealRequest($0) == nil })
precondition(validRevealRequest(["late-file", "한글"]) != nil)
print("{\"passed\":true,\"late_child\":2999,\"scan_nodes\":2940,\"global_cap\":5000}")
'''
        self.assertTrue(self.execute(SUPPORT + code + checks)['passed'])

    def test_launcher_accepts_only_bounded_observe_labels(self):
        code = (ROOT / 'launcher/tacto.swift').read_text()
        schema = code[code.index('let allowedTypes:'):code.index('// MARK: - Socket plumbing')]
        checks = r'''
let base: [String: Any] = ["type":"observe", "target":"owned.fixture", "reveal":["late-file"]]
var rows = [base]
for raw: Any in [[], [""], [true], [1], "file", ["a\nb"], ["a\u{007f}b"], Array(repeating:"x",count:9), [String(repeating:"x",count:257)]] {
 var row = base; row["reveal"] = raw; rows.append(row)
}
var wrong = base; wrong["type"] = "selection"; rows.append(wrong)
print(String(data:try! JSONSerialization.data(withJSONObject:rows.map{vetted($0)==nil}),encoding:.utf8)!)
'''
        result = self.execute('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n' + schema + checks)
        self.assertEqual([True] + [False] * 10, result)


if __name__ == '__main__':
    unittest.main()
