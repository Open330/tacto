"""Exercise production Swift selection readback against adversarial AX trees."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class SwiftSelectionIdentityTests(unittest.TestCase):
    def test_live_identity_many_controls_and_vanished_control(self):
        source = (Path(__file__).resolve().parents[1] / 'helper/TactoHelper.swift').read_text()
        function = re.search(r'^func rereadSelection\(.*?^\}', source, re.M | re.S).group()
        support = '''
import Foundation
final class Node {
    var values: [String: Any]
    init(_ values: [String: Any]) { self.values = values }
}
typealias AXUIElement = Node
typealias CFTypeRef = AnyObject
typealias CFString = String
struct ReadError { let rawValue: Int32 }
func AXUIElementCopyAttributeValue(_ node: Node, _ key: String, _ value: inout AnyObject?) -> ReadError {
    if let error = node.values[key + "Error"] as? Int32 { return ReadError(rawValue: error) }
    guard let result = node.values[key] else { return ReadError(rawValue: -25205) }
    value = result as AnyObject
    return ReadError(rawValue: 0)
}
let kAXRoleAttribute = "AXRole"
let kAXChildrenAttribute = "AXChildren"
let kAXValueAttribute = "AXValue"
var root = Node([:])
func AXUIElementCreateApplication(_ pid: pid_t) -> Node { root }
func attr(_ node: Node, _ key: String) -> Any? { node.values[key] }
func str(_ node: Node, _ key: String) -> String? {
    guard let text = node.values[key] as? String, !text.isEmpty else { return nil }; return text
}
func CFEqual(_ a: Node, _ b: Node) -> Bool { a === b }
func popup(_ value: String, _ id: String? = nil) -> Node {
    var values: [String: Any] = ["AXRole": "AXPopUpButton", "AXValue": value]
    if let id = id { values["AXIdentifier"] = id }
    return Node(values)
}
'''
        harness = '''
let original = popup("selected")
root.values = ["AXRole": "AXApplication", "AXChildren": (0..<20).map { popup("other-\\($0)") } + [original]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: nil, control: original) == "selected")
root.values = ["AXRole": "AXApplication", "AXChildren": [popup("selected")]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: nil, control: original) == nil)
root.values = ["AXRole": "AXApplication", "AXChildren": [popup("selected", "same"), popup("wrong", "same")]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "same", control: original) == nil)
root.values = ["AXRole": "AXApplication", "AXChildren": [popup("selected", "same")]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "same", control: original) == "selected")
root.values = ["AXRole": "AXApplication", "AXChildren": [popup("selected", "same")] + (0..<60).map { popup("other-\\($0)") }]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "same", control: original) == nil)
root.values = ["AXRole": "AXApplication", "AXChildren": [original] + (0..<60).map { popup("other-\($0)") }]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: nil, control: original) == "selected")
let unique = popup("selected", "unique")
root.values = ["AXRole": "AXApplication", "AXChildren": [unique]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "unique") == "selected")
let branches = (0..<60).map { _ in Node(["AXRole": "AXGroup", "AXChildren": (0..<60).map { _ in popup("other") }]) }
root.values = ["AXRole": "AXApplication", "AXChildren": [unique] + Array(branches.prefix(59))]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "unique", control: original) == nil)
let unread = Node(["AXRole": "AXGroup", "AXChildrenError": Int32(-25204),
    "AXChildren": [popup("wrong", "unique")]])
root.values = ["AXRole": "AXApplication", "AXChildren": [unique, unread]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "unique", control: original) == nil)
root.values = ["AXRole": "AXApplication", "AXChildren": [unread, original]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: nil, control: original) == "selected")
let empty = popup("", "")
root.values = ["AXRole": "AXApplication", "AXChildren": [empty]]
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "", control: original) == nil)
precondition(rereadSelection(1, role: "AXPopUpButton", identity: "", control: empty) == "")
print("SELECTION_IDENTITY_PASS")
'''
        # Execute the actual expressions at both wire-response call sites.
        expressions = re.findall(r'"reads_as": (.*?), "menu_left_open"', source)
        self.assertEqual(len(expressions), 2)
        for expression in expressions:
            harness += '\nfor value: String? in [nil, "", "Beta"] {\n'
            harness += 'let now = value; let readsAs = value\n'
            harness += 'let payload: [String: Any] = ["reads_as": ' + expression + ']\n'
            harness += 'let data = try JSONSerialization.data(withJSONObject: payload)\n'
            harness += 'let decoded = try JSONSerialization.jsonObject(with: data) as! [String: Any]\n'
            harness += 'if value == nil { precondition(decoded["reads_as"] is NSNull) }\n'
            harness += 'else { precondition(decoded["reads_as"] as? String == value) }\n}\n'
        with tempfile.TemporaryDirectory() as directory:
            script, binary = Path(directory) / 'main.swift', Path(directory) / 'probe'
            script.write_text(support + function + harness)
            built = subprocess.run(['swiftc', str(script), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('SELECTION_IDENTITY_PASS', result.stdout)
