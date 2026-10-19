"""Explicit opening never bypasses validation or retries an uncertain action."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock,patch
from tacto.protocol import native_opening_parameters_problem
ROOT=Path(__file__).resolve().parents[1]

class NativeOpeningActionTests(unittest.TestCase):
    def test_invalid_action_refuses_before_transport_or_element_read(self):
        from tacto.helper import HelperDriver
        for value in (None,True,1,[],{},'AXCancel','AXPress ', ''):
            action={'name':'open_select','element_index':0,'opening_action':value}
            self.assertEqual('invalid_opening_action',native_opening_parameters_problem(action))
            with patch.object(HelperDriver,'__init__',return_value=None):driver=HelperDriver()
            driver._request=Mock(side_effect=AssertionError('must not request'))
            self.assertEqual('not_started',driver.act('unused','owned',action)['effect'])
            if importlib.util.find_spec('ApplicationServices'):
                from tacto.driver import Driver,NotStarted
                direct=object.__new__(Driver);direct._element=Mock(side_effect=AssertionError('must not resolve'))
                with self.assertRaises(NotStarted):direct._open_select(None,action)
                direct._element.assert_not_called()

    def test_old_helper_refuses_explicit_route_before_transport(self):
        from tacto.helper import HelperDriver
        with patch.object(HelperDriver,'__init__',return_value=None):driver=HelperDriver()
        driver.capabilities={'action.open_select'}
        driver._request=Mock(side_effect=AssertionError('must not request old helper'))
        for value in ('AXPress','AXShowMenu'):
            result=driver.act('unused','owned',{'name':'open_select','element_index':0,'opening_action':value})
            self.assertEqual('unsupported_opening_action',result['error'])
            self.assertEqual('not_started',result['effect'])
        driver._request.assert_not_called()

    def test_browser_never_silently_ignores_native_opening_parameter(self):
        from tacto.browser import BrowserDriver
        driver=object.__new__(BrowserDriver)
        driver._document=Mock(side_effect=AssertionError('must not access page'))
        for value in ('AXPress','AXShowMenu',None):
            result=driver.act('unused','owned',{'name':'open_select','element_index':0,'opening_action':value})
            self.assertEqual('unsupported_opening_action',result['error'])
            self.assertEqual('not_started',result['effect'])
        driver._document.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('ApplicationServices'),'AX metadata modules required')
    def test_direct_explicit_press_calls_once_for_unsupported_timeout_and_success(self):
        from types import SimpleNamespace
        from tacto.driver import Driver,NotStarted,_AfterOpening,AX_ACTION_UNSUPPORTED,AX_CANNOT_COMPLETE
        for status in (AX_ACTION_UNSUPPORTED,AX_CANNOT_COMPLETE,0):
            direct=object.__new__(Driver);direct._element=Mock(return_value=42)
            direct._menu_slot_busy=Mock(return_value=None);direct._frontmost=Mock(return_value=False)
            direct._app_menus=Mock(side_effect=[[],[99]])
            action={'name':'open_select','element_index':0,'opening_action':'AXPress'}
            with patch('tacto.driver._attr',side_effect=lambda _,name:'AXPopUpButton' if name=='AXRole' else True if name=='AXEnabled' else None), \
                 patch('tacto.driver._aborted_now',return_value=False),patch('tacto.driver._elem_label',return_value='owned'), \
                 patch('tacto.driver.AXUIElementPerformAction',return_value=status) as perform,patch('tacto.driver.time.sleep'):
                if status==AX_ACTION_UNSUPPORTED:
                    with self.assertRaises(NotStarted):direct._open_select(SimpleNamespace(pid=1,obs_id='owned'),action)
                elif status==AX_CANNOT_COMPLETE:
                    with self.assertRaises(_AfterOpening):direct._open_select(SimpleNamespace(pid=1,obs_id='owned'),action)
                else:self.assertTrue(direct._open_select(SimpleNamespace(pid=1,obs_id='owned'),action)['opened'])
                perform.assert_called_once_with(42,'AXPress')

    @unittest.skipUnless(shutil.which('swift'),'Swift required')
    def test_swift_validation_matches_python_before_app_lookup(self):
        text=(ROOT/'helper/TactoHelper.swift').read_text();start=text.index('func nativeOpeningParametersProblem(')
        function=text[start:text.index('\n}\n',start)+3]
        rows=[{}, {'opening_action':'AXShowMenu'},{'opening_action':'AXPress'}]+[
            {'opening_action':value} for value in (None,True,1,[],{},'AXCancel','AXPress ','')]
        data=json.dumps(rows).encode().hex()
        source='import Foundation\n'+function+'\nlet hex="'+data+'"\n'+"""
var data=Data();var cursor=hex.startIndex
while cursor<hex.endIndex {let next=hex.index(cursor,offsetBy:2);data.append(UInt8(hex[cursor..<next],radix:16)!);cursor=next}
let rows=try! JSONSerialization.jsonObject(with:data) as! [[String:Any]]
let output=rows.map {nativeOpeningParametersProblem($0) as Any? ?? NSNull()}
print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!)
"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.swift';binary=Path(folder)/'probe';path.write_text(source)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(path),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
        self.assertEqual([native_opening_parameters_problem(row) for row in rows],json.loads(result.stdout))

    @unittest.skipUnless(shutil.which('swift'),'Swift required')
    def test_real_launcher_validator_accepts_only_named_opening_parameter(self):
        text=(ROOT/'launcher/tacto.swift').read_text()
        schema=text[text.index('let allowedTypes:'):text.index('// MARK: - Socket plumbing')]
        rows=[{}, {'opening_action':'AXShowMenu'},{'opening_action':'AXPress'}]+[
            {'opening_action':v} for v in (None,True,1,[],{},'AXCancel','AXPress ','')]
        data=json.dumps(rows).encode().hex()
        source='import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+'\nlet hex="'+data+'"\n'+"""
var data=Data();var cursor=hex.startIndex
while cursor<hex.endIndex {let next=hex.index(cursor,offsetBy:2);data.append(UInt8(hex[cursor..<next],radix:16)!);cursor=next}
let rows=try! JSONSerialization.jsonObject(with:data) as! [[String:Any]]
let output=rows.map { extra -> Bool in
 var action:[String:Any]=["name":"open_select","element_index":0]
 for (k,v) in extra {action[k]=v}
 return vetted(["type":"act","obs_id":"owned","target":"owned","action":action])==nil
}
print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!)
"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.swift';path.write_text(source)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([native_opening_parameters_problem(row) is None for row in rows],json.loads(result.stdout))

    @unittest.skipUnless(shutil.which('swift'),'Swift required')
    def test_actual_swift_opening_fragment_stops_on_uncertain_or_unsupported_press(self):
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        start=text.index('        let openingAction=action["opening_action"]')
        fragment=text[start:text.index('        usleep(400_000)',start)]
        source=r"""
import Foundation
import ApplicationServices
enum Injected:Error {case changed}
var mode="ok",calls:[String]=[]
class Scope {
 func ready() throws {}
 func withTimeout<T>(_ node:Int,_ operation:()->T) throws -> T {
  if mode=="before" {throw Injected.changed}
  let result=operation()
  if mode=="after" {throw Injected.changed}
  return result
 }
}
func AXUIElementPerformAction(_ node:Int,_ action:CFString)->AXError {
 let name=action as String;calls.append(name)
 if mode=="unsupported" {return .actionUnsupported}
 if mode=="timeout" {return .cannotComplete}
 if mode=="fallback" && name=="AXShowMenu" {return .actionUnsupported}
 return .success
}
func comboBoxOpenButton(_ node:Int,_ pid:pid_t)->Int? {nil}
func open(_ action:[String:Any])->[String:Any] {
 let el=42,p:pid_t=42,openingScope=Scope()
"""+fragment+r"""
 return ["type":"acted","opened":true]
}
var rows:[[String:Any]]=[]
for kind in ["ok","unsupported","timeout","before","after","fallback"] {
 mode=kind;calls=[]
 let result=open(kind=="fallback" ? [:]:["opening_action":"AXPress"])
 rows.append(["kind":kind,"calls":calls,"result":result])
}
print(String(data:try! JSONSerialization.data(withJSONObject:rows),encoding:.utf8)!)
"""
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'main.swift';path.write_text(source)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        rows={r['kind']:r for r in json.loads(result.stdout)}
        for kind in ('ok','unsupported','timeout','after'):self.assertEqual(['AXPress'],rows[kind]['calls'])
        self.assertEqual([],rows['before']['calls'])
        for kind in ('unsupported','before'):self.assertEqual('not_started',rows[kind]['result']['effect'])
        for kind in ('timeout','after'):self.assertEqual('unknown',rows[kind]['result']['effect'])
        self.assertEqual(['AXShowMenu','AXPress'],rows['fallback']['calls'])
        self.assertTrue(rows['ok']['result']['opened'])
