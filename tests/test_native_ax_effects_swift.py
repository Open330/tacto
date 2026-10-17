"""Execute the helper's actual AX error classifier without invoking UI APIs."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('xcrun'), 'Swift macOS SDK required')
class SwiftAXEffectTests(unittest.TestCase):
    def test_actual_classifier_marks_uncertain_errors_unknown(self):
        source = (ROOT/'helper/TactoHelper.swift').read_text()
        match = re.search(r'^func axActionFailureEffect\(_ error: AXError\) -> String \{\n.*?^\}',
                          source, re.M | re.S)
        self.assertIsNotNone(match)
        harness = '''
for code in [-25206, -25204, -25200, -25201, -25202, -25208, -25211] as [Int32] {
    let expected = code == -25206 ? "not_started" : "unknown"
    precondition(axActionFailureEffect(AXError(rawValue: code)!) == expected)
}
print("AX_EFFECT_CLASSIFICATION_PASS")
'''
        with tempfile.TemporaryDirectory(prefix='tacto-ax-effects-') as directory:
            script = Path(directory)/'main.swift'
            binary = Path(directory)/'check'
            script.write_text('import ApplicationServices\n' + match.group() + '\n' + harness)
            compilation = subprocess.run(['xcrun','swiftc',str(script),'-o',str(binary)],
                                         capture_output=True,text=True,timeout=60)
            self.assertEqual(0, compilation.returncode, compilation.stderr)
            output = subprocess.run([str(binary)],check=True,capture_output=True,text=True,timeout=5)
            self.assertEqual('AX_EFFECT_CLASSIFICATION_PASS',output.stdout.strip())


if __name__ == '__main__':
    unittest.main()
