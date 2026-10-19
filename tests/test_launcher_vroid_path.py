"""Exercise the signed launcher's real schema without dispatching UI input."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class LauncherVroidPathTests(unittest.TestCase):
    def test_waypoint_bounds_and_types(self):
        source=(ROOT/'launcher/tacto.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        checks=r'''
let point:[String:Any]=["x":800,"y":500]
let base:[String:Any]=["name":"vroid_drag","element_index":0,"from":point,"to":point,"duration":1.2,"title":"Trial.vroid"]
var actions=[base]
for via:Any in [[point],Array(repeating:point,count:12),Array(repeating:point,count:13),
 [["x":true,"y":500]],[["x":-1,"y":500]],[["x":800]],"invalid",NSNull()] {
 var action=base;action["via"]=via;actions.append(action)
}
let results=actions.map { action in vetted(["type":"act","target":"net.pixiv.vroid.macosx","obs_id":"owned","coordinate_authorized":true,"action":action])==nil }
print(String(data:try! JSONSerialization.data(withJSONObject:results),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'main.swift'
            path.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+checks)
            result=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual([True,True,True,False,False,False,False,False,False],json.loads(result.stdout))
