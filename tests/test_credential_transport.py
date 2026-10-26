import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from tacto.helper import HelperDriver

ROOT=Path(__file__).resolve().parents[1]
SECRET='owned-transport-fixture-secret'

class Transport(unittest.TestCase):
    def driver(self,caps=True):
        d=object.__new__(HelperDriver);d.capabilities={'action.credential_input'} if caps else set();d.requests=[]
        def send(m):d.requests.append(m);return {'type':'acted'}
        d._send=send;return d
    def test_old_helper_refuses_before_any_plaintext_message(self):
        d=self.driver(False);r=d.credential_input('obs','com.apple.Safari',1,SECRET,expected_origin='https://example.test')
        self.assertEqual('not_started',r['effect']);self.assertFalse(d.requests)
    def test_live_scope_is_sent_on_write_submit_and_preflight(self):
        d=self.driver()
        self.assertEqual('credential_ready',d.credential_input('obs','com.apple.Safari',1,None,expected_origin='https://example.test',check_only=True)['type'])
        d.credential_input('obs','com.apple.Safari',1,SECRET,expected_origin='https://example.test');d.credential_submit('obs','com.apple.Safari',2,expected_origin='https://example.test')
        self.assertEqual(['read','set_value','click'],[r['action']['name'] for r in d.requests]);self.assertTrue(all(r['credential_origin']=='https://example.test' for r in d.requests))
        self.assertNotIn(SECRET,str(d.requests[0]));self.assertNotIn(SECRET,str(d.requests[-1]))
    def test_access_receipts_exclude_values(self):
        from tacto.access_log import AccessLog,AccessLoggingDriver
        with tempfile.TemporaryDirectory() as tmp:
            log=AccessLog('auth',root=Path(tmp)/'Tacto'/'Logs');d=AccessLoggingDriver(self.driver(),log)
            d.credential_input('obs','com.apple.Safari',1,SECRET,expected_origin='https://example.test');log.finish(0);log.close()
            content=''.join(p.read_text() for p in (Path(tmp)/'Tacto'/'Logs').glob('*.jsonl'))
            self.assertNotIn(SECRET,content);self.assertIn('credential_input',content);self.assertIn('com.apple.Safari',content)

@unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'Swift/macOS required')
class NativeScope(unittest.TestCase):
    def test_actual_native_origin_and_ancestor_logic(self):
        s=(ROOT/'helper/TactoHelper.swift').read_text();code=s[s.index('func credentialURLOrigin'):s.index('func credentialElementAllowed')]
        harness='''
let native:[(String,String?)]=[("AXTextField",nil),("AXWindow",nil)]
let own:[(String,String?)]=[("AXTextField",nil),("AXWebArea","https://example.test/login"),("AXWindow",nil)]
let cross:[(String,String?)]=[("AXTextField",nil),("AXWebArea","https://evil.test/"),("AXWebArea","https://example.test/"),("AXWindow",nil)]
let unknown:[(String,String?)]=[("AXTextField",nil),("AXWebArea",nil),("AXWindow",nil)]
print(String(data:try! JSONSerialization.data(withJSONObject:[credentialPathAllowed(native,origin:""),credentialPathAllowed(own,origin:"https://example.test"),credentialPathAllowed(cross,origin:"https://example.test"),credentialPathAllowed(unknown,origin:"https://example.test"),credentialPathAllowed(own,origin:""),credentialPathAllowed(native,origin:"https://example.test"),credentialURLOrigin("http://example.test/")==nil,credentialURLOrigin("http://127.0.0.1:8765/login")=="http://127.0.0.1:8765",credentialURLOrigin("https://user:password@example.test")==nil]),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'main.swift';p.write_text('import Foundation\n'+code+harness)
            r=subprocess.run(['swift',str(p)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,r.returncode,r.stderr);self.assertEqual([True,True,False,False,False,False,True,True,True],json.loads(r.stdout))
    def test_actual_launcher_rejects_scope_on_unrelated_operations(self):
        s=(ROOT/'launcher/tacto.swift').read_text();schema=s[s.index('let allowedTypes:'):s.index('// MARK: - Socket plumbing')]
        harness='''
let base:[String:Any]=["type":"act","obs_id":"own","target":"com.apple.Safari","credential_origin":"https://example.test","action":["name":"set_value","element_index":1,"value":"owned-fixture"]]
var cases=[base]
var native=base;native["credential_origin"]="";cases.append(native)
for raw:Any in [true,1,["x"],String(repeating:"x",count:2049)] {var r=base;r["credential_origin"]=raw;cases.append(r)}
var bad=base;bad["type"]="observe";cases.append(bad)
var menu=base;menu["action"]=["name":"menu","path":["File","Close"]];cases.append(menu)
print(String(data:try! JSONSerialization.data(withJSONObject:cases.map{vetted($0)==nil}),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'main.swift';p.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? {1}\n'+schema+harness)
            r=subprocess.run(['swift',str(p)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,r.returncode,r.stderr);self.assertEqual([True,True]+[False]*6,json.loads(r.stdout))
