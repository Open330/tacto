"""Real Swift window-list failures must not masquerade as an empty list."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]


def diagnostic(**updates):
    return {'version':1,'status':'ok','ax_error':0,'raw_count':3,
            'role_window_count':2,'role_unavailable_count':0,'role_transient_count':0,
            'filtered_count':1,'badge_count':1,**updates}


class WindowDiagnosticContractTests(unittest.TestCase):
    def test_closed_schema_rejects_content_and_inconsistent_counts(self):
        from tacto.window_listing_diagnostic import window_listing_diagnostic
        good=diagnostic()
        self.assertEqual(window_listing_diagnostic(good),good)
        for updates in ({'PRIVATE_TREE':'PRIVATE_CONTENT'},{'version':True},
                        {'raw_count':True},{'raw_count':-1},{'raw_count':2147483648},
                        {'filtered_count':2},{'role_unavailable_count':2},
                        {'status':[]},{'status':'unknown'}, {'ax_error':-25204},
                        {'status':'role_unavailable'}, {'status':'no_value'},
                        {'status':'malformed_array'}, {'status':'ax_error'}, {'role_transient_count':1}):
            with self.subTest(updates=updates):
                self.assertIsNone(window_listing_diagnostic(diagnostic(**updates)))
        self.assertEqual(window_listing_diagnostic(diagnostic(status='role_unavailable',
            raw_count=4,role_unavailable_count=1))['status'],'role_unavailable')
        self.assertIsNotNone(window_listing_diagnostic(diagnostic(status='ax_error',ax_error=-25204,
            raw_count=0,role_window_count=0,filtered_count=0,badge_count=0)))

    def test_unreadable_window_list_never_triggers_empty_window_recovery(self):
        from tacto.helper import HelperDriver
        for required in (True,False):
            driver=object.__new__(HelperDriver);driver._last_action_at=0
            reply={'type':'error','error':'window_list_unavailable',
                   'window_listing_diagnostic':diagnostic(status='role_unavailable',
                       raw_count=4,role_unavailable_count=1)}
            driver._send=Mock(return_value=reply)
            with patch('tacto.helper._explain_no_window') as explain,patch('tacto.helper._unhide_target') as unhide:
                self.assertEqual(driver.observe('owned',require_window=required),reply)
            explain.assert_not_called();unhide.assert_not_called()
            self.assertEqual(driver._send.call_count,1)

    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('xcrun'),'macOS Swift required')
    def test_swift_classifies_transport_shape_roles_filter_and_invalid_geometry(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        begin=source.index('struct WindowListingDiagnostic {')
        end=source.index('// MARK: - A point is',begin)
        actual=source[begin:end]
        gate_begin=source.index('    if documentToken == nil && !["ok", "no_value"]')
        gate_end=source.index('    // Reading another app',gate_begin)
        gate=source[gate_begin:gate_end]
        # This pure listing diagnostic has no live session/window identity.
        # Scoped filtering is exercised by test_window_parallel_native.
        scope_begin=gate.index('    if let scope = ParallelWindowState.shared.current {')
        scope_end=gate.index('    if windows.isEmpty && requireWindow {',scope_begin)
        gate=gate[:scope_begin]+gate[scope_end:]
        actual+='\nfunc observationGate(_ windows:[AXUIElement],_ windowListing:WindowListingDiagnostic,_ requireWindow:Bool)->[String:Any] {\nlet documentToken:String?=nil;let target="owned";let p:pid_t=1\n'+gate+'return ["type":"observation"]\n}\n'
        support=r'''
import Foundation
import ApplicationServices
var data:[pid_t:[String:CFTypeRef]] = [:]
var failedRoles:Set<pid_t>=[]
var listError=AXError.success
var listNil=false
func node(_ pid:pid_t)->AXUIElement { AXUIElementCreateApplication(pid) }
func onScreenWindowCount(_ pid:pid_t)->Int? { nil }
func id(_ element:AXUIElement)->pid_t { var pid:pid_t=0; _=AXUIElementGetPid(element,&pid);return pid }
func AXUIElementCopyAttributeValue(_ node:AXUIElement,_ key:CFString,_ out:UnsafeMutablePointer<CFTypeRef?>)->AXError {
 if key as String=="AXWindows" {
  if listError != .success { return listError }
  if listNil { return .success }
 }
 if key as String=="AXRole" && failedRoles.contains(id(node)) { return .cannotComplete }
 guard let value=data[id(node)]?[key as String] else { return .attributeUnsupported }
 out.pointee=value;return .success
}
func attr(_ element:AXUIElement,_ name:String)->CFTypeRef? {
 var value:CFTypeRef?;return AXUIElementCopyAttributeValue(element,name as CFString,&value)==.success ? value:nil
}
func str(_ element:AXUIElement,_ name:String)->String? { attr(element,name) as? String }
func point(_ x:Double,_ y:Double)->AXValue { var p=CGPoint(x:x,y:y);return AXValueCreate(.cgPoint,&p)! }
func size(_ w:Double,_ h:Double)->AXValue { var s=CGSize(width:w,height:h);return AXValueCreate(.cgSize,&s)! }
'''.replace('==.success',' == .success')
        checks=r'''
var rows:[[String:Any]]=[]
func record(_ name:String) {
 var diagnostic=WindowListingDiagnostic()
 let windows=realWindows(of:node(1),diagnostic:&diagnostic)
 rows.append(["case":name,"windows":windows.map{id($0)},"diagnostic":diagnostic.metadata,
 "required":observationGate(windows,diagnostic,true),"optional":observationGate(windows,diagnostic,false)])
}
data=[1:["AXWindows":[] as CFArray]]
record("empty")
listError = .cannotComplete;record("transport");listError = .success
listNil=true;record("nil");listNil=false
listError = .noValue;record("no_value");listError = .success
 data[1]!["AXWindows"]="PRIVATE_BAD_LIST" as CFString;record("wrong_array")
 data[1]!["AXWindows"]=[NSNumber(value:42)] as CFArray;record("wrong_member")
data[1]!["AXWindows"]=[node(2),node(3),node(4)] as CFArray
data[2]=["AXRole":"AXWindow" as CFString,"AXPosition":point(0,0),"AXSize":size(800,600)]
data[3]=["AXRole":"AXHelpTag" as CFString]
data[4]=["AXRole":"AXWindow" as CFString,"AXSubrole":"AXDialog" as CFString,
 "AXTitle":"Window" as CFString,"AXIdentifier":"_NS:8" as CFString,
 "AXPosition":point(20,20),"AXSize":size(66,20)]
record("badge")
failedRoles=[3];record("role_failure");failedRoles=[]
data[3]!["AXRole"]=NSNumber(value:42);record("role_type")
data[3]!["AXRole"]="AXHelpTag" as CFString
 data[4]!["AXPosition"]="INVALID_AX_VALUE" as CFString;record("wrong_geometry")
 data[4]!["AXPosition"]=size(66,20);record("wrong_geometry_type")
 data[4]!["AXPosition"]=point(Double.nan,20);record("nonfinite_geometry")
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            src=Path(directory)/'main.swift';exe=Path(directory)/'check'
            src.write_text(support+actual+checks)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(src),'-o',str(exe)],capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            run=subprocess.run([str(exe)],capture_output=True,text=True,timeout=10)
            self.assertEqual(run.returncode,0,run.stderr)
        rows={r['case']:r for r in json.loads(run.stdout)}
        self.assertEqual(rows['empty']['diagnostic']['status'],'ok')
        for name,status in [('transport','ax_error'),('nil','no_value'),('no_value','no_value'),
                            ('wrong_array','malformed_array'),('wrong_member','malformed_array'),
                            ('role_failure','role_unavailable'),('role_type','role_unavailable')]:
            self.assertEqual(rows[name]['diagnostic']['status'],status)
        for name in ['empty','nil','no_value']:
            self.assertEqual(rows[name]['required']['error'],'no_window_content')
            self.assertEqual(rows[name]['optional']['type'],'observation')
        for name in ['transport','wrong_array','wrong_member','role_failure','role_type']:
            for mode in ['required','optional']:
                self.assertEqual(rows[name][mode]['error'],'window_list_unavailable')
        self.assertEqual(rows['badge']['windows'],[2])
        self.assertEqual(rows['badge']['diagnostic'],diagnostic())
        for name in ['wrong_geometry','wrong_geometry_type','nonfinite_geometry']:
            self.assertEqual(rows[name]['windows'],[2,4])
            self.assertEqual(rows[name]['diagnostic']['badge_count'],0)
        from tacto.window_listing_diagnostic import window_listing_diagnostic
        for row in rows.values():
            self.assertEqual(window_listing_diagnostic(row['diagnostic']),row['diagnostic'])
