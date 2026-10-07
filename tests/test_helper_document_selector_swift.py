"""Run the helper's document reader against isolated AX/process boundaries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swift'), 'macOS Swift required')
class HelperDocumentSelectorSwiftTests(unittest.TestCase):
    def test_selected_path_excludes_foreign_bodies_and_token_rejects_path_drift(self):
        source = (ROOT / 'helper/NLCHelper.swift').read_text()
        section = source[source.index('enum DocumentError:'):source.index('struct PinnedSavePanel {')]
        start = section.index('func documentProcessIdentity(')
        end = section.index('/// Checked before every AX read', start)
        section = section[:start] + section[end:]
        harness = r'''
import Foundation
import AppKit
import ApplicationServices
func sha256Hex(_ data: Data) -> String { "unused-export-hash" }
struct StopState { var isStopped = false }
var abortState = StopState()
var latestDocumentToken: String? = nil
let own = AXUIElementCreateApplication(10001)
let foreign = AXUIElementCreateApplication(10002)
let body = AXUIElementCreateApplication(10003)
var windows = [foreign, own]
var path = "/private/tmp/nlc-document-owned.rtf"
var stamp = 10.0
var calls = 0
var bodyReads = 0
var driftDuringText = false
var bodyWindow = own
let text = "한😀x"
func documentProcessIdentity(_ target: String) throws -> (pid_t, Double) { (42, stamp) }
func attr(_ element: AXUIElement, _ name: String) -> AnyObject? {
    var output: AnyObject?
    return AXUIElementCopyAttributeValue(element, name as CFString, &output) == .success ? output : nil
}
func AXUIElementCopyAttributeValue(_ element: AXUIElement, _ name: CFString,
                                  _ output: UnsafeMutablePointer<AnyObject?>) -> AXError {
    calls += 1
    let key = name as String
    if key == "AXWindows" { output.pointee = windows as NSArray; return .success }
    if key == "AXSubrole" { output.pointee = "AXStandardWindow" as NSString; return .success }
    if key == "AXDocument" {
        if CFEqual(element, foreign) { return AXError(rawValue: -25212)! }
        output.pointee = URL(fileURLWithPath: path).absoluteString as NSString; return .success
    }
    precondition(!CFEqual(element, foreign), "foreign subtree was read")
    if key == "AXWindow" { output.pointee = bodyWindow; return .success }
    if key == "AXRole" { output.pointee = (CFEqual(element, body) ? "AXTextArea" : "AXWindow") as NSString }
    else if key == "AXIdentifier" { output.pointee = "First Text View" as NSString }
    else if key == "AXChildren" {
        if CFEqual(element, body) { return AXError(rawValue: -25212)! }
        output.pointee = [body] as NSArray
    } else if key == "AXNumberOfCharacters" { output.pointee = NSNumber(value: text.utf16.count) }
    else if key == "AXValue" {
        precondition(CFEqual(element, body)); bodyReads += 1
        output.pointee = text as NSString
        if driftDuringText { path = "/private/tmp/nlc-document-replaced.rtf" }
    } else { preconditionFailure(key) }
    return .success
}
func AXUIElementCopyParameterizedAttributeValue(_ element: AXUIElement, _ name: CFString,
                                               _ parameter: CFTypeRef,
                                               _ output: UnsafeMutablePointer<AnyObject?>) -> AXError {
    precondition(CFEqual(element, body)); bodyReads += 1
    output.pointee = text as NSString; return .success
}
'''
        checks = r'''
let selected = documentText("com.apple.TextEdit", token:nil, budget:100, selectedPath:path)
precondition(selected["type"] as? String == "document_text", "\(selected)")
precondition(selected["text"] as? String == text)
let token = selected["document_token"] as! String
let before = bodyReads
let pinnedWindow = try documentWindowForToken("com.apple.TextEdit", token)
precondition(CFEqual(pinnedWindow, own))
precondition(bodyReads == before)
try documentValidateElement(body, window: own)
bodyWindow = foreign
var rejected = false
do { try documentValidateElement(body, window: own) } catch { rejected = true }
precondition(rejected && bodyReads == before)
bodyWindow = own
path = "/private/tmp/nlc-document-replaced.rtf"
let stale = documentText("com.apple.TextEdit", token:token, budget:100)
precondition(stale["effect"] as? String == "not_started")
precondition(bodyReads == before)
precondition(documentTokens.isEmpty)
path = "/private/tmp/nlc-document-owned.rtf"
windows = [own, own]
let ambiguous = documentText("com.apple.TextEdit", token:nil, budget:100, selectedPath:path)
precondition(ambiguous["effect"] as? String == "not_started")
precondition(bodyReads == before)
let previousCalls = calls
for invalid in ["relative", "/tmp/../x", "/private/tmp/x\n", "//private/tmp/x"] {
    let refused = documentText("com.apple.TextEdit", token:nil, budget:100, selectedPath:invalid)
    precondition(refused["effect"] as? String == "not_started")
}
precondition(calls == previousCalls)
windows = [foreign, own]; driftDuringText = true
let raced = documentText("com.apple.TextEdit", token:nil, budget:100, selectedPath:path)
precondition(raced["effect"] as? String == "not_started")
precondition(raced["text"] == nil && documentTokens.isEmpty)
// A token issued without a path selector pins the native document, not its
// location. Save As can update AXDocument without invalidating that identity.
driftDuringText = false; windows = [own]
path = "/private/tmp/nlc-document-owned.rtf"
let original = documentText("com.apple.TextEdit", token:nil, budget:100)
precondition(original["type"] as? String == "document_text")
let movingToken = original["document_token"] as! String
windows = [foreign, own]
path = "/private/tmp/nlc-document-saved-as.rtf"
let relocated = documentText("com.apple.TextEdit", token:movingToken, budget:100)
precondition(relocated["type"] as? String == "document_text", "\(relocated)")
precondition(relocated["document_token"] as? String == movingToken)
precondition(relocated["document_path"] as? String == path)
precondition(relocated["text"] as? String == text)
precondition(relocated["source_format"] == nil)
let relocatedWindow = try documentWindowForToken("com.apple.TextEdit", movingToken)
precondition(CFEqual(relocatedWindow, own))
// The same path flexibility cannot hide a change during a complete read.
driftDuringText = true
let movingRace = documentText("com.apple.TextEdit", token:movingToken, budget:100)
precondition(movingRace["effect"] as? String == "not_started" && movingRace["text"] == nil)
print("PASS")
'''
        with tempfile.TemporaryDirectory(prefix='nlc-document-selector-') as directory:
            script = Path(directory) / 'main.swift'
            script.write_text(harness + section + checks)
            result = subprocess.run(['swift', str(script)], capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'PASS')
