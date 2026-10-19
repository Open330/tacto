"""Production first-control metadata reads; no AX input or model calls."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swift'), 'macOS Swift required')
class SelectionLookupMetadataTests(unittest.TestCase):
    def test_label_batch_optional_fields_fallback_abort_and_deadline(self):
        helper = (ROOT/'helper/TactoHelper.swift').read_text()
        window = (ROOT/'helper/WindowInput.swift').read_text()
        def extract(source, prefix):
            part = source[source.index(prefix):]
            return part[:part.index('\n}\n') + 3]
        support = r'''
import Foundation
import ApplicationServices
let node=AXUIElementCreateApplication(10001)
let labelAttrs=["AXTitle","AXDescription","AXValue","AXHelp","AXIdentifier","AXPlaceholderValue","AXSubrole"]
let labelNames=["Title","Description","Value","Help","Identifier","Placeholder","Subrole"]
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
typealias AXBatchRead=(AXUIElement,[String]) throws -> (AXError,CFArray?)
var mode="normal",stopped=false,identity=true,batches=0,scalars=0
func AXUIElementSetMessagingTimeout(_ el:AXUIElement,_ seconds:Float)->AXError {.success}
func optionalError(_ status:AXError)->CFTypeRef {
 var error=status;return AXValueCreate(.axError,&error)!
}
func AXUIElementCopyMultipleAttributeValues(_ el:AXUIElement,_ names:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:inout CFArray?)->AXError {
 batches+=1
 if mode=="abort_batch" {stopped=true}
 if mode=="identity_batch" {identity=false}
 if mode=="late_batch" {Thread.sleep(forTimeInterval:0.08)}
 if mode.hasPrefix("scalar") || mode=="unsupported" {return .attributeUnsupported}
 if mode=="batch_failure" {return .cannotComplete}
 if mode=="malformed" {out=[] as CFArray;return .success}
 let missing=optionalError(.attributeUnsupported)
 out=["Choice\nOne" as CFString,missing,"Alpha" as CFString,missing,
      "choice" as CFString,missing,mode=="field_failure" ? optionalError(.cannotComplete):missing] as CFArray
 return .success
}
func AXUIElementCopyAttributeValue(_ el:AXUIElement,_ name:CFString,_ out:inout CFTypeRef?)->AXError {
 scalars+=1
 if mode=="scalar_abort" {stopped=true}
 if mode=="scalar_failure" {return .cannotComplete}
 if name as String=="AXTitle" {out="Choice\nOne" as CFString;return .success}
 if name as String=="AXValue" {out="Alpha" as CFString;return .success}
 if name as String=="AXIdentifier" {out="choice" as CFString;return .success}
 return .attributeUnsupported
}
'''
        body = r'''
var results:[[String:Any]]=[]
for name in ["normal","unsupported","batch_failure","field_failure","malformed","abort_batch","identity_batch","late_batch","expired","scalar_abort","scalar_failure"] {
 mode=name;stopped=false;identity=true;batches=0;scalars=0
 let deadline=ProcessInfo.processInfo.systemUptime+(name=="expired" ? -1:name=="late_batch" ? 0.03:10)
 let scope=NativeWindowReadScope(deadline:deadline,check:{
  if stopped || !identity {throw NativeWindowInputError.refused("changed")}
 })
 var answer:String?=nil
 do {answer=try selectionLookupLabel(node,scope:scope)} catch {}
 results.append(["mode":name,"answer":answer as Any? ?? NSNull(),"batches":batches,"scalars":scalars])
}
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        source = support + ''.join(extract(window, p) for p in (
            'final class NativeWindowReadScope {', 'func nativeReadinessBatch('))
        source += ''.join(extract(helper, p) for p in (
            '@Sendable func oneLineLabel(', 'func selectionLookupAttribute(',
            'func selectionLookupString(', 'func selectionLookupLabel(')) + body
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.swift'; path.write_text(source)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = {r['mode']: r for r in json.loads(result.stdout)}
        for name, row in rows.items():
            self.assertEqual('Title: Choice\\nOne, Value: Alpha, Identifier: choice'
                             if name in ('normal', 'unsupported') else None, row['answer'], name)
        for name in ('normal','batch_failure','field_failure','malformed','abort_batch','identity_batch','late_batch','expired'):
            self.assertEqual(0, rows[name]['scalars'], name)
        self.assertEqual(0, rows['expired']['batches'])
        self.assertEqual(1, rows['scalar_abort']['scalars'])
        self.assertEqual(1, rows['scalar_failure']['scalars'])

    def test_menu_ownership_never_treats_unreadable_or_truncated_tree_as_absence(self):
        helper = (ROOT/'helper/TactoHelper.swift').read_text()
        window = (ROOT/'helper/WindowInput.swift').read_text()
        def extract(source, prefix):
            part = source[source.index(prefix):]
            return part[:part.index('\n}\n') + 3]
        support = r'''
import Foundation
import ApplicationServices
let root=ApplicationServices.AXUIElementCreateApplication(10001),menu=ApplicationServices.AXUIElementCreateApplication(10002)
func AXUIElementCreateApplication(_ pid:pid_t)->AXUIElement {root}
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
var mode="present",stopped=false,copies=0
struct PendingSelect {let control:AXUIElement;let menu:AXUIElement;let popupWindowID:CGWindowID?}
enum ComboBoxContainerState {case closed,open(AXUIElement),unknown}
func comboBoxContainerState(_ node:AXUIElement,_ pid:pid_t,scope:NativeWindowReadScope?=nil)->ComboBoxContainerState {
 mode=="combo_open" ? .open(menu):mode.hasPrefix("combo_closed") ? .closed:.unknown
}
func nativePopupVisibility(_ id:CGWindowID,_ pid:pid_t,scope:NativeWindowReadScope?=nil) throws -> Bool {
 try scope?.ready();return mode=="combo_closed_visible"
}
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError {.success}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:inout CFTypeRef?)->AXError {
 copies+=1
 if mode=="abort" {stopped=true}
 if mode=="unreadable" {return .cannotComplete}
 if key as String=="AXRole" {out=(CFEqual(node,menu) ? "AXMenu":"AXApplication") as CFString;return .success}
 if key as String=="AXChildren" {
  out=(mode=="absent" ? []:mode=="overflow" ? Array(repeating:menu,count:61):mode=="malformed" ? [NSNumber(value:1)]:[menu]) as CFArray
  return .success
 }
 return .attributeUnsupported
}
'''
        body = r'''
var rows:[[String:Any]]=[]
for name in ["present","absent","unreadable","overflow","malformed","abort","combo_unknown","combo_open","combo_closed","combo_closed_visible"] {
 mode=name;stopped=false;copies=0
 let scope=NativeWindowReadScope(deadline:ProcessInfo.processInfo.systemUptime+10,check:{
  if stopped {throw NativeWindowInputError.refused("stopped")}
 })
 let pin=PendingSelect(control:root,menu:menu,popupWindowID:name.hasPrefix("combo_") ? 17:nil)
 var owned:Bool?=nil
 do {owned=try menuIsOurs(42,pin,scope:scope)} catch {}
 var discovered:Int?=nil
 do {discovered=try selectionOpeningMenus(42,scope:scope).count} catch {}
 rows.append(["mode":name,"owned":owned as Any? ?? NSNull(),"copies":copies,"discovered":discovered as Any? ?? NSNull()])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        source = support + extract(window, 'final class NativeWindowReadScope {')
        source += ''.join(extract(helper, p) for p in (
            'func selectionLookupAttribute(', 'func selectionLookupString(',
            'func selectionLookupMenuIsOwned(', 'func selectionOpeningMenus(', 'func menuIsOurs(_ pid:pid_t,')) + body
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'main.swift'; path.write_text(source)
            result = subprocess.run(['swift', str(path)], capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stderr)
        rows = {r['mode']: r for r in json.loads(result.stdout)}
        for name,row in rows.items():
            self.assertEqual(None if name in ('unreadable','overflow','malformed','abort') else
                             0 if name=='absent' else 1,row['discovered'],name)

        for name, row in rows.items():
            expected = True if name in ('present','combo_open','combo_closed_visible') else False if name in ('absent','combo_closed') else None
            self.assertEqual(expected, row['owned'], name)
        self.assertEqual(1, rows['abort']['copies'])

    def test_opening_metadata_refuses_unreadable_malformed_and_late_reads_before_action(self):
        helper=(ROOT/'helper/TactoHelper.swift').read_text()
        window=(ROOT/'helper/WindowInput.swift').read_text()
        def extract(source,prefix):
            part=source[source.index(prefix):]
            return part[:part.index('\n}\n')+3]
        opening=helper.split('    case "open_select":',1)[1]
        metadata=opening[opening.index('        let openingScope:'):opening.index('        guard ["AXPopUpButton"')]
        support=r'''
import Foundation
import ApplicationServices
let el=AXUIElementCreateApplication(10001),p:pid_t=10001
let obsID="o",target="fixture"
let labelAttrs=["AXTitle","AXDescription","AXValue","AXHelp","AXIdentifier","AXPlaceholderValue","AXSubrole"]
let labelNames=["Title","Description","Value","Help","Identifier","Placeholder","Subrole"]
enum NativeWindowInputError:Error {case refused(String),unavailable(String)}
typealias AXBatchRead=(AXUIElement,[String]) throws -> (AXError,CFArray?)
var mode="normal",stopped=false,copies=0
func AXUIElementSetMessagingTimeout(_ node:AXUIElement,_ seconds:Float)->AXError {.success}
func AXUIElementCopyMultipleAttributeValues(_ node:AXUIElement,_ keys:CFArray,
 _ options:AXCopyMultipleAttributeOptions,_ out:inout CFArray?)->AXError {.attributeUnsupported}
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:inout CFTypeRef?)->AXError {
 copies+=1
 if mode=="unreadable" {return .cannotComplete}
 if mode=="abort" {stopped=true}
 if mode=="late" {Thread.sleep(forTimeInterval:0.08)}
 switch key as String {
 case "AXRole":out="AXPopUpButton" as CFString
 case "AXEnabled":
  if mode=="missing_enabled" {return .attributeUnsupported}
  out=mode=="malformed_enabled" ? "true" as CFString:kCFBooleanTrue
 case "AXIdentifier":out="choice" as CFString
 case "AXTitle":out="Choice" as CFString
 default:return .attributeUnsupported
 }
 return .success
}
func initialSelectionReadScope(_ observation:String,_ name:String,_ node:AXUIElement,
 _ pid:pid_t,deadline:Double) throws -> NativeWindowReadScope {
 let scope=NativeWindowReadScope(deadline:deadline,check:{if stopped {throw NativeWindowInputError.refused("stopped")}})
 try scope.ready();return scope
}
'''
        body='func openingMetadata(deadline:Double)->[String:Any] {\n'+metadata+'''
 return ["type":"ready","role":role,"label":openingLabel]
}
var rows:[[String:Any]]=[]
for name in ["normal","missing_enabled","malformed_enabled","unreadable","abort","late","expired"] {
 mode=name;stopped=false;copies=0
 let result=openingMetadata(deadline:ProcessInfo.processInfo.systemUptime+(name=="expired" ? -1:name=="late" ? 0.03:10))
 rows.append(["mode":name,"result":result,"copies":copies])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        source=support+extract(window,'final class NativeWindowReadScope {')
        source+=extract(window,'func nativeReadinessBatch(')
        source+=''.join(extract(helper,prefix) for prefix in ('@Sendable func oneLineLabel(',
            'func selectionLookupAttribute(','func selectionLookupString(','func selectionLookupLabel('))+body
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';path.write_text(source)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,result.returncode,result.stderr)
        for row in json.loads(result.stdout):
            answer=row['result']
            if row['mode'] in ('normal','missing_enabled'):
                self.assertEqual('ready',answer['type'])
            else:
                self.assertEqual('not_started',answer['effect'])
        # The compiled block contains no input calls; the actual dispatch uses
        # these captured metadata and a final original-scope check.
        self.assertIn('do {try openingScope.ready()} catch',opening)
        self.assertIn('identifier: openingIdentifier, role: role',opening)
        self.assertIn('label: openingLabel, menu: fresh[0]',opening)
