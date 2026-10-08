"""Actual helper key branch rejects malformed shapes before delegation."""
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'), 'macOS Swift required')
class HelperWindowKeyTests(unittest.TestCase):
    def test_actual_cf_boolean_index_guard_and_navigation_whitelist(self):
        helper=(ROOT/'helper/NLCHelper.swift').read_text()
        branch=helper[helper.index('    case "press_key":'):helper.index('    case "scroll":',helper.index('    case "press_key":'))]
        source=(ROOT/'helper/WindowInput.swift').read_text()
        keys=source[source.index('func nativeNavigationKey('):source.index('func buildNativeWindowKey(')]  # includes nativeSubmitKey
        harness=r'''
let input = AXUIElementCreateApplication(10001)
var calls = 0
func prepareBackgroundWindow(_ obs: String, _ target: String, _ element: AXUIElement,
    _ pid: pid_t, deadline: Double, key: String? = nil, expectation: [String:Any]? = nil,
    finalCheck: ((NativeWindowReadScope) -> String?)? = nil) -> [String:Any] {
    calls += 1; precondition(CFEqual(element,input)); return ["type":"acted","key":key!]
}
final class NativeWindowReadScope {
    func read(_ element: AXUIElement, _ name: String) throws -> (AXError, CFTypeRef?) { (.failure, nil) }
}
typealias AXRead = (AXUIElement, String) throws -> (AXError, CFTypeRef?)
// The fixture element is no Go to Folder path field, so Return is refused
// here by the helper's own scoping, before any dispatch.
func returnTargetRefusal(_ element: AXUIElement, read: AXRead? = nil) -> String? { "not the Go to Folder path field" }
func boundedAXRead(until deadline: Double) -> AXRead { { _, _ in (.failure, nil) } }
func submitReturnBackground(_ obs: String, _ target: String, _ element: AXUIElement,
    _ pid: pid_t, deadline: Double) -> [String:Any] { preconditionFailure("Return must be refused first here") }
func checked(_ action:[String:Any]) -> [String:Any] {
    let obsID="owned", target="fixture", p:pid_t=42, deadline=10.0
    let elements=[input]
    let expect:[String:Any]?=nil
    func element() -> AXUIElement? {
        guard let index=action["element_index"] as? Int, index>=0, index<elements.count else { return nil }
        return elements[index]
    }
    switch action["name"] as? String ?? "" {
''' + branch + r'''
    default:return ["type":"error"]
    }
}
var requests:[[String:Any]]=[]
for key in ["Tab","shift+Tab","Up","Down","Left","Right"] {
    requests.append(["name":"press_key","element_index":0,"key":key])
}
for action:[String:Any] in [
    ["name":"press_key","key":"Tab"],
    ["name":"press_key","element_index":true,"key":"Tab"],
    ["name":"press_key","element_index":false,"key":"Tab"],
    ["name":"press_key","element_index":0.5,"key":"Tab"],
    ["name":"press_key","element_index":-1,"key":"Tab"],
    ["name":"press_key","element_index":1,"key":"Tab"],
    ["name":"press_key","element_index":"0","key":"Tab"],
    ["name":"press_key","element_index":0,"key":"Escape"],
    ["name":"press_key","element_index":0,"key":"Return"],
    ["name":"press_key","element_index":0,"key":"cmd+q"],
    ["name":"press_key","element_index":0,"key":"Tab","text":"extra"]
] { requests.append(action) }
let responses=requests.map { checked($0) }
precondition(calls==6)
print(String(data:try! JSONSerialization.data(withJSONObject:responses),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='nlc-helper-key-shape-') as directory:
            script=Path(directory)/'main.swift'
            script.write_text('import Foundation\nimport AppKit\nimport ApplicationServices\n'+keys+harness)
            result=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(result.returncode,0,result.stderr)
        rows=json.loads(result.stdout)
        self.assertEqual([row['key'] for row in rows[:6]],['Tab','shift+Tab','Up','Down','Left','Right'])
        self.assertTrue(all(row['type']=='error' and row['effect']=='not_started' for row in rows[6:]))
