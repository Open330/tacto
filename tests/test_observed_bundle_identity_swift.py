"""Bound metadata is refreshed, while OS start matching remains mandatory."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift required')
class ObservedBundleTests(unittest.TestCase):
    def test_metadata_is_auxiliary_and_never_retargets_the_pid(self):
        source=(ROOT/'helper/WindowInput.swift').read_text()
        start=source.index('func nativeObservedBundleMatches(')
        function=source[start:source.index('\n}\n',start)+3]
        script='''import Foundation
var reads=0
var metadata:(pid_t,String?,Bool)?=(42,"com.owned.app",false)
final class NSRunningApplication {
 let processIdentifier:pid_t,bundleIdentifier:String?,isTerminated:Bool
 init?(processIdentifier:pid_t) {
  reads += 1
  guard let value=metadata else { return nil }
  self.processIdentifier=value.0;bundleIdentifier=value.1;isTerminated=value.2
 }
}
'''+function+'''
precondition(nativeObservedBundleMatches(42,"COM.OWNED.APP"))
metadata=(42,"com.other.app",false)
precondition(!nativeObservedBundleMatches(42,"com.owned.app"))
metadata=(99,"com.owned.app",false)
precondition(!nativeObservedBundleMatches(42,"com.owned.app"))
metadata=(42,"com.owned.app",true)
precondition(!nativeObservedBundleMatches(42,"com.owned.app"))
metadata=nil
precondition(!nativeObservedBundleMatches(42,"com.owned.app"))
precondition(reads==5)
precondition(!nativeObservedBundleMatches(0,"com.owned.app"))
precondition(!nativeObservedBundleMatches(42,""))
precondition(reads==5)
print("BOUND_METADATA_PASS")
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(script)
            run=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(run.stdout.strip(),'BOUND_METADATA_PASS')
        helper=(ROOT/'helper/TactoHelper.swift').read_text()
        context=helper[helper.index('let observedBundle ='):helper.index('phase = "context"',helper.index('let observedBundle ='))]
        self.assertIn('nativeProcessStartIdentity(pid) == start',context)
        self.assertIn('latestElementGeneration == elementGeneration',context)
        self.assertIn('processForWindowTarget(target) == pid',context)
