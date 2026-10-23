"""Execute the actual native click routing when pointer readiness consumes time."""
from pathlib import Path
import json,subprocess,tempfile,unittest,shutil,sys
ROOT=Path(__file__).resolve().parents[1]
@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift required')
class FinderSemanticRouteTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.work=tempfile.TemporaryDirectory(prefix='tacto-finder-route-');base=Path(cls.work.name);cls.binary=base/'route'
  source=(ROOT/'helper/TactoHelper.swift').read_text();click=source[source.index('    case "click":'):source.index('    case "set_value":')]
  route=click[click.index('        var advertised: CFArray?'):click.index('        if let token = savePanelToken,')]
  harness=r'''import Foundation
import ApplicationServices
let scenario=CommandLine.arguments[1]
struct FakeElement {}
let el=FakeElement(),deadline=5.0,name="click",obsID="observed"
let savePanelToken:String? = scenario == "save_panel" ? "bound" : nil
var now=0.0,calls:[String]=[]
func AXUIElementCopyActionNames(_ e:FakeElement,_ out:inout CFArray?) -> AXError {
 let names:[String]=scenario == "ax_press" ? [kAXPressAction as String] : []
 out=unsafeBitCast(names as NSArray,to:CFArray.self);return .success
}
func selectListItem(_ e:FakeElement,deadline:Double)->[String:Any]? {return nil}
func selectTableRow(_ e:FakeElement,deadline:Double)->[String:Any]? {
 calls.append("table")
 if scenario == "not_a_row" {return nil}
 if now>=deadline || scenario == "already_expired" {return ["type":"error","effect":"not_started"]}
 if scenario == "unknown_selection" {return ["type":"error","effect":"unknown"]}
 return ["type":"acted","input_route":"ax_row_selection"]
}
func pointerClick()->[String:Any] {
 calls.append("pointer");now=10
 if scenario == "pointer_unknown" {return ["type":"error","effect":"unknown","posted_or_attempted_events":1]}
 if scenario == "pointer_success" {return ["type":"acted","posted_or_attempted_events":2]}
 return ["type":"error","effect":"not_started","posted_or_attempted_events":0]
}
func run(_ target:String)->[String:Any] {
'''+route+r'''
 return ["type":"ax_press"]
}
let target=scenario.hasPrefix("generic") || ["pointer_unknown","pointer_success"].contains(scenario) ? "org.example.table" : "com.apple.finder"
let result=run(target)
let data=try JSONSerialization.data(withJSONObject:["result":result,"calls":calls],options:[.sortedKeys]);print(String(data:data,encoding:.utf8)!)
'''
  (base/'main.swift').write_text(harness)
  r=subprocess.run(['swiftc',str(base/'main.swift'),'-o',str(cls.binary)],capture_output=True,text=True,timeout=60)
  if r.returncode:raise RuntimeError(r.stderr)
 @classmethod
 def tearDownClass(cls):cls.work.cleanup()
 def run_route(self,scenario):
  r=subprocess.run([str(self.binary),scenario],capture_output=True,text=True,timeout=10);self.assertEqual(0,r.returncode,r.stderr);return json.loads(r.stdout)
 def test_finder_selects_without_spending_any_pointer_budget(self):
  r=self.run_route('crowded_finder');self.assertEqual(['table'],r['calls']);self.assertEqual('acted',r['result']['type'])
 def test_generic_table_keeps_existing_pointer_first_order(self):
  r=self.run_route('generic_expired');self.assertEqual(['pointer','table'],r['calls']);self.assertEqual('not_started',r['result']['effect'])
 def test_uncertain_table_selection_is_never_followed_by_pointer(self):
  r=self.run_route('unknown_selection');self.assertEqual(['table'],r['calls']);self.assertEqual('unknown',r['result']['effect'])
 def test_existing_deadline_never_triggers_a_pointer_attempt(self):
  r=self.run_route('already_expired');self.assertEqual(['table'],r['calls']);self.assertEqual('not_started',r['result']['effect'])
 def test_save_panel_and_axpress_skip_table_selection(self):
  for scenario in ('save_panel','ax_press'):
   with self.subTest(scenario=scenario):self.assertNotIn('table',self.run_route(scenario)['calls'])
 def test_non_row_finder_control_still_uses_pointer(self):
  r=self.run_route('not_a_row');self.assertEqual('pointer',r['calls'][1]);self.assertEqual('not_started',r['result']['effect'])
 def test_successful_or_uncertain_pointer_is_not_followed_by_selection(self):
  for scenario in ('pointer_success','pointer_unknown'):
   with self.subTest(scenario=scenario):self.assertEqual(['pointer'],self.run_route(scenario)['calls'])
