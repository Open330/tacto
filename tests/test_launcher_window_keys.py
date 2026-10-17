"""Execute the actual launcher request validator; no helper or UI process."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'), 'macOS Swift required')
class LauncherWindowKeyTests(unittest.TestCase):
    def test_save_panel_dispatch_token_only_on_exact_indexed_click(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        harness=r'''
let root:[String:Any] = ["type":"act","obs_id":"owned","target":"fixture",
    "save_panel_token":"panel", "action":["name":"click","element_index":1]]
var requests=[root]
for token:Any in [true,1,"",String(repeating:"x",count:129),NSNull()] {
    var r=root;r["save_panel_token"]=token;requests.append(r)
}
for action:[String:Any] in [
    ["name":"click","element_index":true], ["name":"click","element_index":-1],
    ["name":"click","element_index":1.5], ["name":"click","point":["x":1,"y":1]],
    ["name":"click","element_index":1,"extra":0], ["name":"prepare_window","element_index":1]
] {var r=root;r["action"]=action;requests.append(r)}
var r=root;r["type"]="observe";requests.append(r)
print(String(data:try! JSONSerialization.data(withJSONObject:requests.map { vetted($0) == nil }),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-save-guard-validator-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+harness)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True]+[False]*12)

    def test_authorized_window_point_exact_shape_and_deadline(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        harness=r'''
let root:[String:Any] = ["type":"act","obs_id":"owned","target":"fixture",
    "coordinate_authorized":true,"click_deadline_uptime":10.0,
    "action":["name":"click_point","element_index":0,"point":["x":280,"y":120]]]
var requests=[root]
for value:Any in [false,1,"yes"] {var r=root;r["coordinate_authorized"]=value;requests.append(r)}
var r=root;r.removeValue(forKey:"coordinate_authorized");requests.append(r)
for point:[String:Any] in [["x":true,"y":1],["x":-1,"y":1],["x":Double.nan,"y":1],["x":1],["x":1,"y":1,"extra":0]] {
    var r=root;r["action"]=["name":"click_point","element_index":0,"point":point];requests.append(r)
}
for action:[String:Any] in [
    ["name":"click_point","element_index":true,"point":["x":1,"y":1]],
    ["name":"click_point","point":["x":1,"y":1]],
    ["name":"click_point","element_index":0,"point":["x":1,"y":1],"window_index":0],
    ["name":"click","element_index":0]
] {var r=root;r["action"]=action;requests.append(r)}
print(String(data:try! JSONSerialization.data(withJSONObject:requests.map { vetted($0) == nil }),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-point-validator-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+harness)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True]+[False]*13)

    def test_document_selectors_and_scoped_observe_are_explicit(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        harness=r'''
let requests:[[String:Any]] = [
    ["type":"document_text", "target":"com.apple.TextEdit", "document_path":"/private/tmp/owned.rtf"],
    ["type":"observe", "target":"com.apple.TextEdit", "document_token":"abc"],
    ["type":"document_text", "target":"com.apple.TextEdit", "document_path":"relative"],
    ["type":"document_text", "target":"com.apple.TextEdit", "document_path":true],
    ["type":"document_text", "target":"com.apple.TextEdit", "document_path":"/private/tmp/owned.rtf", "document_token":"abc"],
    ["type":"observe", "target":"foreign", "document_token":"abc"],
    ["type":"observe", "target":"com.apple.TextEdit", "document_token":true],
    ["type":"observe", "target":"com.apple.TextEdit", "document_token":""],
    ["type":"observe", "target":"com.apple.TextEdit", "document_path":"/private/tmp/owned.rtf"]
]
print(String(data:try! JSONSerialization.data(withJSONObject:requests.map { vetted($0) == nil }),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-document-validator-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+harness)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout),[True,True]+[False]*7)

    def test_observed_index_navigation_only_exact_fields_and_deadline(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        harness=r'''
let root: [String: Any] = ["type":"act","obs_id":"owned","target":"fixture",
    "action":["name":"press_key","element_index":3,"key":"Tab"],
    "expect":["role":"TextArea","label":"input","attribute":"Identifier"]]
var cases: [[String: Any]] = []
for key in ["Tab","shift+Tab","Up","Down","Left","Right"] {
    var request=root;request["action"]=["name":"press_key","element_index":3,"key":key]
    cases.append(request)
}
var request=root;request["click_deadline_uptime"]=10.0;cases.append(request)
for action: [String: Any] in [
    ["name":"press_key","key":"Tab"], ["name":"press_key","element_index":true,"key":"Tab"],
    ["name":"press_key","element_index":0.5,"key":"Tab"],
    ["name":"press_key","element_index":3,"key":"Escape"],
    ["name":"press_key","element_index":3,"key":"cmd+q"],
    ["name":"press_key","element_index":3,"key":"Return"],
    ["name":"press_key","element_index":3,"key":"Tab","point":["x":1,"y":1]]
] { var request=root;request["action"]=action;cases.append(request) }
for deadline: Any in [true, 0, -1, Double.infinity, Double.nan] {
    var request=root;request["click_deadline_uptime"]=deadline;cases.append(request)
}
print(String(data:try! JSONSerialization.data(withJSONObject:cases.map { vetted($0) == nil }),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-key-validator-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw: Any?) -> Double? { 1 }\n'+schema+harness)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        # Return passes the launcher (2026-10-08): it cannot see roles, so the
        # helper scopes it to the Go to Folder path field. Everything else
        # here is still refused at the launcher.
        self.assertEqual(json.loads(result.stdout),[True]*7+[False]*5+[True]+[False]*6)
