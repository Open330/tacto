"""open_folder's request, run for real with a fake LaunchServices call.

Asked at most once; not asked when stopped or out of time; after asking, a
missing or failed answer or a later stop is `unknown`, never `notStarted`."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
import Foundation
func label(_ o: OpenFolderOutcome) -> String {
    switch o { case .notStarted: return "notStarted"; case .opened: return "opened"; case .unknown: return "unknown" }
}
var out: [String: String] = [:]
func run(_ name: String, stoppedAt: Int = -1, deadline: Double = 100, clock start: Double = 1,
         answer: Bool? = true, stopAfterRequest: Bool = false, lateBy: Double? = nil) {
    var checks = 0, requests = 0, stop = false, clock = start
    let o = openFolderBounded(deadline: deadline, stopped: {
        checks += 1; if checks == stoppedAt { stop = true }; return stop
    }, now: { clock }, request: { done in
        requests += 1
        if stopAfterRequest { stop = true }
        if let lateBy { clock = deadline + lateBy }
        if let answer { done(answer) }
    })
    out[name] = "\(label(o)) requests=\(requests)"
}
run("ok")
run("stopped before", stoppedAt: 1)
run("deadline passed", deadline: 1, clock: 2)
run("no answer", deadline: 1.05, clock: 1, answer: nil)
run("error", answer: false)
run("stopped after asking", stopAfterRequest: true)
run("late answer", deadline: 2, clock: 1, lateBy: 1)
print(String(data: try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys]), encoding: .utf8)!)
'''


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'macOS Swift required')
class OpenFolderSwiftTests(unittest.TestCase):
    def test_outcomes(self):
        with tempfile.TemporaryDirectory(prefix='nlc-open-folder-') as directory:
            source, binary = Path(directory) / 'main.swift', Path(directory) / 'check'
            source.write_text(HARNESS)
            built = subprocess.run(['xcrun', 'swiftc', '-warnings-as-errors', str(source),
                str(ROOT / 'helper/WindowInput.swift'), str(ROOT / 'helper/WindowDispatch.swift'),
                str(ROOT / 'helper/FrontmostProcess.swift'), '-o', str(binary)],
                capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({
            'ok': 'opened requests=1',
            'stopped before': 'notStarted requests=0',
            'deadline passed': 'notStarted requests=0',
            'no answer': 'unknown requests=1',
            'error': 'unknown requests=1',
            'stopped after asking': 'unknown requests=1',
            'late answer': 'unknown requests=1',
        }, json.loads(result.stdout))


if __name__ == '__main__':
    unittest.main()
