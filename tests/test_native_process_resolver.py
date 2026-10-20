"""Compare native PID selection with its prior contract across app collisions."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('swift'), 'Swift required')
class NativeProcessResolverTests(unittest.TestCase):
    def test_bundle_name_policy_order_and_case_contract_and_name_read_cost(self):
        source = (Path(__file__).resolve().parents[1] / 'helper/TactoHelper.swift').read_text()
        function = source[source.index('func pid(forBundle bundle: String)'):]
        function = function[:function.index('\n}\n') + 3]
        script = '''import Foundation
enum Policy { case regular, accessory, prohibited }
var nameReads = 0, snapshots = 0
final class NSRunningApplication {
 let bundleIdentifier:String?, name:String?, activationPolicy:Policy, processIdentifier:pid_t
 var localizedName:String? { nameReads += 1; return name }
 init(_ bundle:String?, _ name:String?, _ policy:Policy, _ pid:pid_t) {
  bundleIdentifier=bundle; self.name=name; activationPolicy=policy; processIdentifier=pid
 }
}
final class NSWorkspace {
 static let shared = NSWorkspace()
 var apps:[NSRunningApplication] = []
 var runningApplications:[NSRunningApplication] { snapshots += 1; return apps }
}
// Historical one-pass resolver, retained as a differential oracle.
func priorPID(_ bundle:String) -> pid_t? {
 let want=bundle.lowercased()
 var byBundle:[NSRunningApplication]=[], byName:[NSRunningApplication]=[]
 for app in NSWorkspace.shared.runningApplications {
  if app.bundleIdentifier?.lowercased()==want { byBundle.append(app) }
  else if app.localizedName?.lowercased()==want { byName.append(app) }
 }
 byName=byName.filter { $0.activationPolicy == .regular }
 for group in [byBundle,byName] where !group.isEmpty {
  let regular=group.filter { $0.activationPolicy == .regular }
  return (regular.first ?? group.first)?.processIdentifier
 }
 return nil
}
''' + function + '''
var cases:[(String?,String?,Policy)] = []
for bundle:String? in [nil,"com.target.app","COM.OTHER.APP"] {
 for name:String? in [nil,"com.target.app","Friendly"] {
  for policy in [Policy.regular,.accessory,.prohibited] { cases.append((bundle,name,policy)) }
 }
}
var checked=0
for first in cases {
 for second in cases {
  NSWorkspace.shared.apps=[NSRunningApplication(first.0,first.1,first.2,11),
                           NSRunningApplication(second.0,second.1,second.2,22)]
  for query in ["COM.TARGET.APP","com.other.app","FRIENDLY","missing"] {
   let expected=priorPID(query)
   nameReads=0; snapshots=0
   precondition(pid(forBundle:query)==expected,"PID selection contract changed")
   precondition(snapshots==1,"resolver must read exactly one fresh workspace snapshot")
   if NSWorkspace.shared.apps.contains(where:{$0.bundleIdentifier?.lowercased()==query.lowercased()}) {
    precondition(nameReads==0,"bundle lookup queried unrelated localized names")
   }
   checked += 1
  }
 }
}
NSWorkspace.shared.apps=[]
precondition(pid(forBundle:"missing")==nil)
print(checked)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-resolver-contract-') as work:
            path = Path(work) / 'main.swift'
            path.write_text(script)
            run = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertEqual('2916', run.stdout.strip())
