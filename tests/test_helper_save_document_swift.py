"""Execute the actual helper handler with injected AX/process boundaries."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class HelperSaveDocumentSwiftTests(unittest.TestCase):
    def test_document_token_scope_freshness_and_mid_read_stop(self):
        source = (ROOT / 'helper/TactoHelper.swift').read_text()
        pins = source[source.index('struct PinnedSavePanel {'):source.index('func saveDirectoryResource(')]
        handler = source[source.index('func readSavePanel('):source.index('// Read-only: resolve only a running process', source.index('func readSavePanel('))]
        harness = r'''
import Foundation
import ApplicationServices
struct StopState { var isStopped = false }
var abortState = StopState()
enum DocumentError: Error { case fail(String) }
enum NativeSavePanelError: Error { case unavailable(String) }
struct PinnedDocument { let pid: pid_t; let stamp: Double; let window: AXUIElement; let body: AXUIElement }
struct NativeSaveDirectoryResource { let path: String }
struct NativeSavePanelWitness {
    let directory = NativeSaveDirectoryResource(path: "/owned")
    let filename = "owned.rtf"
    let format: String? = "rtf"
    let formatValue: String? = "Rich Text Document"
    let keepChanges: Int? = 0
}
let window = AXUIElementCreateApplication(10001)
let body = AXUIElementCreateApplication(10002)
let otherWindow = AXUIElementCreateApplication(10003)
var latestElements = [window]
var latestObsID: String? = "obs"
var latestTargetName = "com.apple.TextEdit"
var latestTargetPID: pid_t = 42
var latestProcessStart: Double? = 10
var processStart = 10.0
var stamp = 20.0
var documentTokens: [(token: String, document: PinnedDocument)] = []
func pin(_ token: String, _ heldWindow: AXUIElement = window) {
    documentTokens.append((token, PinnedDocument(pid: 42, stamp: 20, window: heldWindow, body: body)))
}
func pid(forBundle: String) -> pid_t? { 42 }
func nativeProcessStartIdentity(_ pid: pid_t) -> Double? { processStart }
func documentProcessIdentity(_ target: String) throws -> (pid_t, Double) { (42, stamp) }
func documentWindowForToken(_ target: String, _ token: String) throws -> AXUIElement {
    guard let pinned = documentTokens.first(where: { $0.token == token })?.document,
          pinned.stamp == stamp else { throw DocumentError.fail("stale token") }
    return pinned.window
}
func documentCheckAbort() throws { if abortState.isStopped { throw DocumentError.fail("stopped") } }
func documentIsWholeNumber(_ raw: Any) -> NSNumber? {
    guard let n = raw as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(),
          n.doubleValue.isFinite, n.doubleValue == Double(n.intValue) else { return nil }
    return n
}
var reads = 0
var expireOnRead: Int? = nil
var stopOnRead: Int? = nil
func AXUIElementSetMessagingTimeout(_ element: AXUIElement, _ timeout: Float) -> AXError { .success }
func AXUIElementGetPid(_ element: AXUIElement, _ pid: UnsafeMutablePointer<pid_t>) -> AXError {
    pid.pointee = 42; return .success
}
func AXUIElementCopyAttributeValue(_ element: AXUIElement, _ name: CFString,
                                  _ value: UnsafeMutablePointer<AnyObject?>) -> AXError {
    reads += 1
    if reads == expireOnRead { documentTokens.removeAll() }
    if reads == stopOnRead { abortState.isStopped = true }
    if name as String == "AXWindows" { value.pointee = [window] as NSArray }
    else { value.pointee = (CFEqual(element, body) ? "AXTextArea" : "AXWindow") as NSString }
    return .success
}
func saveDirectoryResource(_ raw: AnyObject) throws -> NativeSaveDirectoryResource {
    NativeSaveDirectoryResource(path: "/owned")
}
final class NativeSavePanelReader {
    var budgetUsage: [String:Int] { [:] }
    let readAttribute: (AXUIElement, String) throws -> (AXError, AnyObject?)
    let check: () throws -> Void
    init(readAttribute: @escaping (AXUIElement, String) throws -> (AXError, AnyObject?),
         directoryResource: @escaping (AnyObject) throws -> NativeSaveDirectoryResource,
         check: @escaping () throws -> Void) throws { self.readAttribute = readAttribute; self.check = check }
    func read(_ window: AXUIElement, requireFormat: Bool) throws -> NativeSavePanelWitness {
        _ = try readAttribute(window, "AXRole"); return NativeSavePanelWitness()
    }
    func revalidate(_ window: AXUIElement, witness: NativeSavePanelWitness, requireFormat: Bool) throws {
        _ = try readAttribute(window, "AXRole")
    }
    func bindCommit(_ witness: NativeSavePanelWitness, elements: [AXUIElement]) throws -> Int { try check(); return 0 }
    func validateDocument(_ window: AXUIElement, body: AXUIElement) throws { _ = try readAttribute(body, "AXRole") }
}
'''
        checks = r'''
func refused(_ response: [String: Any], _ code: String = "save_panel_unavailable") {
    precondition(response["error"] as? String == code, "\(response)")
    precondition(response["effect"] as? String == "not_started")
}
var request: [String: Any] = ["type":"save_panel", "target":"com.apple.TextEdit", "obs_id":"obs", "window_element_index":0]
let generic = readSavePanel(request)
precondition(generic["type"] as? String == "save_panel")
precondition(generic["document_token"] == nil)
pin("docA"); pin("docB"); pin("foreignWindow", otherWindow)
request["document_token"] = "docA"
let bound = readSavePanel(request)
precondition(bound["document_token"] as? String == "docA", "\(bound)")
let heldToken = bound["panel_token"] as! String
request["panel_token"] = heldToken
precondition(readSavePanel(request)["panel_token"] as? String == heldToken)
request["document_token"] = nil
refused(readSavePanel(request))
request["document_token"] = "docB"
refused(readSavePanel(request))
request["document_token"] = "docA"
request["panel_token"] = generic["panel_token"]
refused(readSavePanel(request))
request["panel_token"] = nil
request["document_token"] = "foreignWindow"
refused(readSavePanel(request))
request["document_token"] = "unknown"
refused(readSavePanel(request))
request["document_token"] = "docA"
stamp = 21
refused(readSavePanel(request))
stamp = 20
expireOnRead = reads + 3
let beforeExpiry = reads
refused(readSavePanel(request))
precondition(reads == beforeExpiry + 3)
pin("docA"); expireOnRead = nil
stopOnRead = reads + 3
let beforeStop = reads
refused(readSavePanel(request), "aborted_by_user")
precondition(reads == beforeStop + 3)
print("SWIFT_SAVE_DOCUMENT_HANDLER_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-save-document-handler-') as directory:
            main = Path(directory) / 'main.swift'
            binary = Path(directory) / 'check'
            main.write_text(harness + pins + handler + checks)
            built = subprocess.run(['xcrun', 'swiftc', str(main), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'SWIFT_SAVE_DOCUMENT_HANDLER_PASS')
