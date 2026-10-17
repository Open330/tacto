"""Execute the helper's actual URL parser without AX or user input."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class SwiftDocumentReferenceTests(unittest.TestCase):
    def test_real_reference_rename_delete_and_strict_path_parity(self):
        source = (Path(__file__).resolve().parents[1] / 'helper/NLCHelper.swift').read_text()
        functions = source[source.index('func documentURLPath('):source.index('/// True is an NSNumber')]
        harness = r'''
import Foundation
import Darwin
enum DocumentError: Error { case fail(String) }
''' + functions + r'''
let manager = FileManager.default
var root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString).resolvingSymlinksInPath()
try manager.createDirectory(at: root, withIntermediateDirectories: false)
let canonicalRoot = realpath(root.path, nil)!
root = URL(fileURLWithPath: String(cString: canonicalRoot))
free(canonicalRoot)
defer { try? manager.removeItem(at: root) }
let original = root.appendingPathComponent("원본 😀.rtf")
try Data("owned fixture".utf8).write(to: original)
let reference = (original as NSURL).perform(NSSelectorFromString("fileReferenceURL"))!.takeUnretainedValue() as! NSURL
let raw = reference.absoluteString!
precondition((reference as NSURL).isFileReferenceURL())
let first = try documentURLPath(raw)
precondition(first == original.path, "resolved=\(first) expected=\(original.path) raw=\(raw)")
let renamed = root.appendingPathComponent("변경 😀.rtf")
try manager.moveItem(at: original, to: renamed)
let second = try documentURLPath(raw)
precondition(second == renamed.path)
try manager.removeItem(at: renamed)
do { _ = try documentURLPath(raw); fatalError("deleted reference accepted") }
catch { }
let package = root.appendingPathComponent("owned.rtfd")
try manager.createDirectory(at: package, withIntermediateDirectories: false)
let packagePath = try documentURLPath(package.absoluteString + "/")
precondition(packagePath == package.path)
for invalidSuffix in [renamed.absoluteString + "/", package.absoluteString + "//", root.appendingPathComponent("absent.rtfd").absoluteString + "/"] {
    do { _ = try documentURLPath(invalidSuffix); fatalError("noncanonical suffix accepted") }
    catch { }
}
let missing = root.appendingPathComponent("missing.rtf")
let missingPath = try documentURLPath(missing.absoluteString)
precondition(missingPath == missing.path)
for invalid in ["file:///.file/../etc/passwd", "file:///.file/%2e%2e/etc/passwd", "file://remote/.file/id=1.2", "file:///.file/%zz", "file:///.file/id=1.2?query", "https://example.com/x"] {
    do { _ = try documentURLPath(invalid); fatalError("malformed URL accepted") }
    catch { }
}
print("reference rename/delete, missing path, malformed URLs: PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script, binary = root / 'main.swift', root / 'probe'
            script.write_text(harness)
            built = subprocess.run(['swiftc', str(script), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('PASS', result.stdout)
