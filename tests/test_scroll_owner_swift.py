"""The actual scroll resolver must not choose a foreign or ambiguous scroller."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('swift'),'Swift macOS SDK required')
class ScrollOwnerSwiftTests(unittest.TestCase):
    def test_owned_unique_bar_and_closed_failures(self):
        source=(ROOT/'helper/TactoHelper.swift').read_text()
        part=source[source.index('@Sendable func scrollBar('):]
        function=part[:part.index('\n}\n')+3]
        support=r'''
import Foundation
import ApplicationServices
let nodes=(20..<24).map {AXUIElementCreateApplication(pid_t($0))}
var mode="valid"
func index(_ el:AXUIElement)->Int {nodes.firstIndex {CFEqual($0,el)}!}
func AXUIElementGetPid(_ el:AXUIElement,_ pid:UnsafeMutablePointer<pid_t>)->AXError {
 if mode=="owner_error" {return .cannotComplete}
 let i=index(el);pid.pointee=(mode=="foreign_bar" && i==2) || (mode=="foreign_parent" && i==1) || mode=="foreign_target" ? 99:42
 return .success
}
func attr(_ el:AXUIElement,_ key:String)->CFTypeRef? {
 let i=index(el)
 if key=="AXRole" {return ["AXStaticText","AXScrollArea","AXScrollBar","AXScrollBar"][i] as CFString}
 if key=="AXOrientation" {return mode=="unknown_orientation" ? nil:"AXVerticalOrientation" as CFString}
 if key=="AXParent" {
  if mode=="cycle" && i==0 {return nodes[0]}
  if mode=="bad_parent_type" && i==0 {return 1 as CFNumber}
  if mode=="wrong_bar_parent" && i==2 {return nodes[0]}
  return nodes[1]
 }
 return nil
}
func comboBoxChildren(_ el:AXUIElement,_ limit:Int)->[AXUIElement]? {
 if mode=="overflow" || mode=="unreadable_children" {return nil}
 return mode=="duplicate" ? [nodes[2],nodes[3]]:[nodes[2]]
}
var result:[String:Bool]=[:]
'''
        body=r'''
for value in ["valid","foreign_bar","foreign_parent","foreign_target","owner_error","wrong_bar_parent","bad_parent_type","cycle","duplicate","overflow","unreadable_children","unknown_orientation"] {
 mode=value;result[value]=scrollBar(near:nodes[0],vertical:true,expectedPID:42) != nil
}
print(String(data:try! JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory(prefix='tacto-scroll-owner-') as folder:
            path=Path(folder)/'main.swift';path.write_text(support+function+body)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        values=json.loads(result.stdout);self.assertTrue(values.pop('valid'))
        self.assertTrue(all(value is False for value in values.values()),values)
