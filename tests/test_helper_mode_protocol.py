"""Transport framing and private-path failures, without screen access."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift runtime')
class OptionalHelperProtocolTests(unittest.TestCase):
    def test_expired_callback_and_driving_lease(self):
        source = r'''
import Foundation
import AppKit
let clientRequirement = "unused-test-requirement"
enum PeerAuth { static func satisfies(fd:Int32, requirement:String) -> (Bool,String) { (false,"test") } }
class AbortStub { var stops = 0; func stopNow() { stops += 1 } }
let abortState = AbortStub()
func resetPersistentSession(starting:Bool) {}
func handle(_ value:[String:Any]) -> [String:Any] { value }
func serve(connection:Int32) {}
let epoch = ModeEpoch.shared
let previous = epoch.snapshot(); epoch.rotate()
var mutations = 0
precondition(!epoch.perform(previous) { mutations += 1 })
precondition(epoch.perform(epoch.snapshot()) { mutations += 1 })
precondition(mutations == 1)
let lease = ModeLease.shared
let a = UUID(), b = UUID()
precondition(lease.take(a)); precondition(!lease.take(b)); lease.stop(a)
precondition(abortState.stops == 1)
lease.release(b); precondition(lease.owns(a))
lease.release(a); precondition(lease.take(b))
lease.stop(a); precondition(abortState.stops == 1)
lease.stop(b); precondition(abortState.stops == 2); lease.release(b)
print("lease-and-callback-ok")
'''
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / 'main.swift'; main.write_text(source)
            binary = Path(directory) / 'test-lease'
            built = subprocess.run(['swiftc', str(main), str(ROOT / 'HelperModeProtocol.swift'),
                                    str(ROOT / 'helper/HelperModes.swift'), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            done = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual('lease-and-callback-ok', done.stdout.strip())

    def test_frames_and_private_directory_validation(self):
        source = r'''
import Foundation
import Darwin
let key = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
let path = "/tmp/tacto-warm-\(getuid())-\(key.prefix(20))"
defer { unlink(path); rmdir(path) }
guard let created = TactoMode.directory(key) else { fatalError("directory missing") }
var info = stat(); precondition(lstat(created,&info) == 0 && info.st_mode & 0o777 == 0o700)
chmod(created,0o755); precondition(TactoMode.directory(key) == nil)
chmod(created,0o700); rmdir(created)
precondition(symlink("/tmp",path) == 0); precondition(TactoMode.directory(key) == nil); unlink(path)
var pair: [Int32] = [0,0]; precondition(socketpair(AF_UNIX,SOCK_STREAM,0,&pair) == 0)
defer { close(pair[0]); close(pair[1]) }
precondition(TactoMode.send(pair[0],["step":1]))
precondition(TactoMode.send(pair[0],["step":2]))
precondition(TactoMode.receive(pair[1])?["step"] as? Int == 1)
precondition(TactoMode.receive(pair[1])?["step"] as? Int == 2)
let bad = Data("bad\n".utf8); bad.withUnsafeBytes { _ = write(pair[0],$0.baseAddress!,bad.count) }
precondition(TactoMode.receive(pair[1]) == nil)
print("{\"frames_preserved\":true,\"unsafe_directory_refused\":true,\"malformed_refused\":true}")
'''
        with tempfile.TemporaryDirectory() as directory:
            main = Path(directory) / 'main.swift'; main.write_text(source)
            binary = Path(directory) / 'test-protocol'
            built = subprocess.run(['swiftc', str(main), str(ROOT / 'HelperModeProtocol.swift'),
                                    '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(0, built.returncode, built.stderr)
            done = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(0, done.returncode, done.stderr)
            self.assertEqual({'frames_preserved': True, 'unsafe_directory_refused': True,
                              'malformed_refused': True}, json.loads(done.stdout))
