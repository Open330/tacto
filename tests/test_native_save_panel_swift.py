"""Execute the native reader with real CF AX identities and injected attributes."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class NativeSavePanelSwiftTests(unittest.TestCase):
    def test_launcher_accepts_panel_fields_only_for_panel_reads(self):
        source = (ROOT / 'launcher/nlc.swift').read_text()
        protocol = source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        protocol += source[source.index('@Sendable func modelTimeout('):source.index('enum ModelProcessError:')]
        harness = '''
import Foundation
import CoreFoundation
var request: [String: Any] = ["type":"save_panel", "target":"owned", "obs_id":"o1",
    "window_element_index":7, "panel_token":"pinned", "require_format":true]
precondition(vetted(request) == nil)
for invalid: Any in [true, -1, 5000, 1.5, "7"] {
    request["window_element_index"] = invalid
    precondition(vetted(request) != nil)
}
request["window_element_index"] = 7
request["require_format"] = 1
precondition(vetted(request) != nil)
request["require_format"] = true
request["document_token"] = "foreign"
precondition(vetted(request) != nil)
request["target"] = "com.apple.TextEdit"
precondition(vetted(request) == nil)
request["document_token"] = true
precondition(vetted(request) != nil)
for name in ["window_element_index", "panel_token", "require_format"] {
    let other: [String: Any] = ["type":"observe", "target":"owned", name:7]
    precondition(vetted(other) != nil)
}
print("SWIFT_SAVE_PANEL_PROTOCOL_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='nlc-swift-save-protocol-') as directory:
            main = Path(directory) / 'main.swift'
            binary = Path(directory) / 'check'
            main.write_text('import Foundation\nimport CoreFoundation\n' + protocol + harness)
            built = subprocess.run(['xcrun', 'swiftc', str(main), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout.strip(), 'SWIFT_SAVE_PANEL_PROTOCOL_PASS')

    def test_native_settings_identity_drift_types_budget_and_stop(self):
        harness = r'''
import Foundation
import ApplicationServices
struct FixtureNode { let element: AXUIElement; var values: [String: AnyObject] }
var nodes: [FixtureNode] = []
func node(_ role: String, parent: AXUIElement? = nil, identifier: String? = nil) -> AXUIElement {
    let element = AXUIElementCreateApplication(pid_t(100000 + nodes.count))
    var values: [String: AnyObject] = ["AXRole": role as NSString, "AXChildren": [] as NSArray]
    if let parent { values["AXParent"] = parent }
    if let identifier { values["AXIdentifier"] = identifier as NSString }
    nodes.append(FixtureNode(element: element, values: values))
    if let parent {
        let index = nodes.firstIndex(where: { CFEqual($0.element, parent) })!
        let children = nodes[index].values["AXChildren"] as! [AXUIElement]
        nodes[index].values["AXChildren"] = (children + [element]) as NSArray
    }
    return element
}
func set(_ element: AXUIElement, _ key: String, _ value: AnyObject) {
    let index = nodes.firstIndex(where: { CFEqual($0.element, element) })!
    nodes[index].values[key] = value
}
let window = node("AXWindow")
let sheet = node("AXSheet", parent: window, identifier: "save-panel")
let group = node("AXGroup", parent: sheet)
let browser = node("AXBrowser", parent: group, identifier: "ColumnView")
let container = node("AXScrollArea", parent: browser)
let column = node("AXScrollArea", parent: container)
let list = node("AXList", parent: column)
let row = node("AXGroup", parent: list)
let url = node("AXTextField", parent: row)
set(url, "AXURL", "file:///owned" as NSString)
set(list, "AXSelectedChildren", [row] as NSArray)
let terminal = node("AXScrollArea", parent: container)
let terminalList = node("AXList", parent: terminal)
set(terminalList, "AXSelectedChildren", [] as NSArray)
let name = node("AXTextField", parent: group, identifier: "saveAsNameTextField")
set(name, "AXValue", "owned.rtf" as NSString)
let format = node("AXPopUpButton", parent: group, identifier: "ContentTypesPopup")
set(format, "AXValue", "Rich Text Document" as NSString)
let commit = node("AXButton", parent: group, identifier: "OKButton")
set(commit, "AXEnabled", kCFBooleanTrue)
let checkbox = node("AXCheckBox", parent: group, identifier: "KeepChangesButton")
set(checkbox, "AXValue", NSNumber(value: 0))
var calls = 0
var stopAfter: Int? = nil
var stopped = false
var inode: UInt64 = 100
let read: NativeSavePanelReader.AttributeReader = { element, key in
    calls += 1
    if calls == stopAfter { stopped = true }
    let value = nodes.first(where: { CFEqual($0.element, element) })!.values[key]
    return value == nil ? (.attributeUnsupported, nil) : (.success, value)
}
let resource: (AnyObject) throws -> NativeSaveDirectoryResource = { _ in
    NativeSaveDirectoryResource(path: "/owned", device: 42, inode: inode)
}
let check: () throws -> Void = { if stopped { throw NativeSavePanelError.unavailable("stopped") } }
let reader = try NativeSavePanelReader(readAttribute: read, directoryResource: resource, check: check)
let witness = try reader.read(window, requireFormat: true)
precondition(witness.filename == "owned.rtf" && witness.format == "rtf" && witness.keepChanges == 0)
precondition(witness.directory.path == "/owned" && CFEqual(witness.commit, commit))
try reader.revalidate(window, witness: witness, requireFormat: true)
let commitIndex = try reader.bindCommit(witness, elements: [name, commit])
precondition(commitIndex == 1)
func mustReject(_ operation: () throws -> Void) {
    do { try operation(); fatalError("unsafe witness accepted") }
    catch NativeSavePanelError.unavailable { }
    catch { fatalError("unexpected failure") }
}
mustReject { _ = try reader.bindCommit(witness, elements: [commit, commit]) }
mustReject { _ = try reader.bindCommit(witness, elements: [name]) }
set(name, "AXValue", "changed.rtf" as NSString)
mustReject { try reader.revalidate(window, witness: witness) }
set(name, "AXValue", "owned.rtf" as NSString)
set(format, "AXValue", "Plain Text" as NSString)
mustReject { try reader.revalidate(window, witness: witness) }
let plain = try reader.read(window)
precondition(plain.format == nil && plain.formatValue == "Plain Text")
mustReject { _ = try reader.read(window, requireFormat: true) }
set(format, "AXValue", "Rich Text Document" as NSString)
set(commit, "AXEnabled", NSNumber(value: 1))
mustReject { _ = try reader.read(window) }
set(commit, "AXEnabled", kCFBooleanTrue)
set(checkbox, "AXValue", NSNumber(value: 0.5))
mustReject { _ = try reader.read(window) }
set(checkbox, "AXValue", NSNumber(value: 1))
mustReject { try reader.revalidate(window, witness: witness) }
set(checkbox, "AXValue", NSNumber(value: 0))
inode = 101
mustReject { try reader.revalidate(window, witness: witness) }
inode = 100
set(terminalList, "AXSelectedChildren", [row] as NSArray)
mustReject { _ = try reader.read(window) }
set(terminalList, "AXSelectedChildren", [] as NSArray)
set(name, "AXParent", sheet)
mustReject { _ = try reader.read(window) }
set(name, "AXParent", group)
let tiny = try NativeSavePanelReader(readAttribute: read, directoryResource: resource, maxNodes: 8)
mustReject { _ = try tiny.read(window) }
let sourceGroup = node("AXScrollArea", parent: window)
let sourceBody = node("AXTextArea", parent: sourceGroup, identifier: "First Text View")
let sheetBody = node("AXTextArea", parent: sheet, identifier: "First Text View")
try reader.validateDocument(window, body: sourceBody)
mustReject { try reader.validateDocument(window, body: sheetBody) }
let foreignWindow = node("AXWindow")
mustReject { try reader.validateDocument(foreignWindow, body: sourceBody) }
let replacedBody = node("AXTextArea", parent: sourceGroup, identifier: "First Text View")
mustReject { try reader.validateDocument(window, body: sourceBody) }
set(sourceGroup, "AXChildren", [replacedBody] as NSArray)
mustReject { try reader.validateDocument(window, body: sourceBody) }
try reader.validateDocument(window, body: replacedBody)
set(replacedBody, "AXParent", sheet)
mustReject { try reader.validateDocument(window, body: replacedBody) }
set(replacedBody, "AXParent", sourceGroup)
mustReject { try tiny.validateDocument(window, body: replacedBody) }
stopAfter = calls + 2
let before = calls
mustReject { _ = try reader.read(window) }
precondition(calls == before + 2)
stopped = false; stopAfter = nil
set(group, "AXChildren", ["not an AX object"] as NSArray)
mustReject { _ = try reader.read(window) }
print("SWIFT_SAVE_PANEL_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='nlc-swift-save-panel-') as directory:
            source = Path(directory) / 'main.swift'
            binary = Path(directory) / 'check'
            source.write_text(harness)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/SavePanelObservation.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'SWIFT_SAVE_PANEL_PASS')


if __name__ == '__main__':
    unittest.main()
