"""Native selection parameters agree before transport or any AX lookup."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock

from tacto.protocol import native_selection_parameters_problem
from tacto.helper import InheritedHelperDriver

ROOT=Path(__file__).resolve().parents[1]
INVALID=[{'option_label':'Beta','value':value} for value in (None,1,True,[],{},'other','')]
INVALID += [{'option_label':value} for value in (None,1,False,[],{},'')]
INVALID += [{}]
INVALID += [{'option_label':'é','value':'e\u0301'},
            {'option_label':'가','value':'\u1100\u1161'}]
VALID=[{'option_label':'Beta'},{'option_label':'Beta','value':'Beta'},
       {'option_label':'한글 🧪','value':'한글 🧪'}, {'option_label':' ' ,'value':' '}]


class NativeSelectionParametersTests(unittest.TestCase):
    def test_invalid_parameters_never_reach_signed_transport(self):
        driver=object.__new__(InheritedHelperDriver)
        driver._send=Mock(side_effect=AssertionError('must not send'))
        for name in ('select_option','select_custom_option'):
            for params in INVALID:
                with self.subTest(name=name,params=params):
                    action={'name':name,'element_index':0,**params}
                    response=driver.act('unused','owned',action)
                    self.assertEqual('invalid_selection_parameters',response['error'])
                    self.assertEqual('not_started',response['effect'])
                    self.assertNotIn('Beta',response['detail'])
        driver._send.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC required')
    def test_both_direct_paths_refuse_before_element_lookup_or_pin_access(self):
        from tacto.driver import Driver,NotStarted
        driver=object.__new__(Driver)
        driver._element=Mock(side_effect=AssertionError('must not resolve AX element'))
        # _select deliberately absent: malformed input cannot inspect a pin.
        for name in ('_select_option','_select_custom_option'):
            for params in INVALID:
                with self.subTest(name=name,params=params):
                    with self.assertRaises(NotStarted):getattr(driver,name)(None,{'element_index':0,**params})
        driver._element.assert_not_called()

    def test_native_values_are_exact_labels_without_trimming_or_coercion(self):
        for params in VALID:self.assertIsNone(native_selection_parameters_problem(params))
        for params in INVALID:self.assertIsNotNone(native_selection_parameters_problem(params))

    def test_unpaired_surrogates_refuse_before_json_transport_or_native_lookup(self):
        driver=object.__new__(InheritedHelperDriver)
        driver._send=Mock(side_effect=AssertionError('must not serialize invalid UTF-8'))
        for label in ('\ud800','\udfff'):
            for name in ('select_option','select_custom_option'):
                action={'name':name,'element_index':0,'option_label':label,'value':label}
                self.assertEqual('invalid_option_label_encoding',native_selection_parameters_problem(action))
                self.assertEqual('not_started',driver.act('unused','owned',action)['effect'])
                if importlib.util.find_spec('ApplicationServices'):
                    from tacto.driver import Driver,NotStarted
                    direct=object.__new__(Driver)
                    direct._element=Mock(side_effect=AssertionError('must not resolve control'))
                    with self.assertRaises(NotStarted):getattr(direct,'_'+name)(None,action)
                    direct._element.assert_not_called()
        driver._send.assert_not_called()

    @unittest.skipUnless(shutil.which('xcrun'), 'Swift required')
    def test_actual_swift_guard_matches_python_for_json_types_and_unicode(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        start=source.index('func nativeSelectionParametersProblem(')
        end=source.index('\nfunc act(',start)
        function=source[start:end]
        rows=INVALID+VALID
        encoded=json.dumps(rows,ensure_ascii=False).encode().hex()
        harness='import Foundation\n'+function+'''
let hex = "HEX"
var data=Data();var cursor=hex.startIndex
while cursor < hex.endIndex {let next=hex.index(cursor,offsetBy:2);data.append(UInt8(hex[cursor..<next],radix:16)!);cursor=next}
let rows=try! JSONSerialization.jsonObject(with:data) as! [[String:Any]]
let output=rows.map {nativeSelectionParametersProblem($0) as Any? ?? NSNull()}
print(String(data:try! JSONSerialization.data(withJSONObject:output),encoding:.utf8)!)
'''.replace('HEX',encoded)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift';binary=Path(directory)/'probe';path.write_text(harness)
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors',str(path),'-o',str(binary)],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([native_selection_parameters_problem(row) for row in rows],json.loads(result.stdout))
