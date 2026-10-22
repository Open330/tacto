"""Exercise the native metadata parser and preference reader, without live input."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

@unittest.skipUnless(sys.platform == 'darwin', 'native Foundation implementation')
class RuntimeStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='tacto-runtime-test-')
        cls.folder = Path(cls.temp.name)
        source = cls.folder/'main.swift'
        source.write_text('''import Foundation
if CommandLine.arguments[1] == "preference" {
    print(TactoRuntimeStatus.showBanner(in:URL(fileURLWithPath:CommandLine.arguments[2])))
} else if CommandLine.arguments[1] == "helper" {
    let raw = try! JSONSerialization.jsonObject(with:Data(CommandLine.arguments[2].utf8)) as! [String:Any]
    let settings = TactoRuntimeStatus.helperSettings(raw["object"] as! [String:Any],environment:raw["environment"] as! [String:String])
    print("\\(settings.mode) \\(settings.idleSeconds)")
} else {
    let raw = try! JSONSerialization.jsonObject(with:Data(CommandLine.arguments[2].utf8)) as! [String:Any]
    var info = raw
    if info["uid"] as? String == "current" { info["uid"] = getuid() }
    if let run = TactoRuntimeStatus.Run(info) {
        print("valid \\(run.active) \\(run.stopped)")
    } else { print("invalid") }
}
''')
        cls.binary=cls.folder/'probe'
        subprocess.run(['swiftc',str(source),str(ROOT/'RuntimeStatus.swift'),'-o',str(cls.binary)],check=True,capture_output=True,text=True)
    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()
    def probe(self, mode, value):
        return subprocess.check_output([str(self.binary),mode,value],text=True).strip()
    def test_metadata_lifecycle_and_invalid_types(self):
        valid=dict(schema=1,uid='current',id='A9CC39E1-CCDD-46C5-8815-0ABABC136FEA',pid=123,target='Calculator',active=True,stopped=False)
        self.assertEqual(self.probe('metadata',json.dumps(valid)),'valid true false')
        self.assertEqual(self.probe('metadata',json.dumps(dict(valid,active=False,stopped=True))),'valid false true')
        for key,value in [('schema',True),('schema',1.5),('uid',-1),('uid',True),('id','not-uuid'),('pid',True),('pid',1.5),('pid',0),('pid',2**40),('target',''),('target','x'*257),('target','x\ny'),('active',1),('stopped','false')]:
            with self.subTest(key=key,value=value):
                self.assertEqual(self.probe('metadata',json.dumps(dict(valid,**{key:value}))),'invalid')
    def test_banner_preference_strict_default_and_opt_out(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); file=root/'preferences.json'
            self.assertEqual(self.probe('preference',folder),'true')
            for value,expected in [(False,'false'),(True,'true'),(0,'true'),('false','true'),(None,'true')]:
                file.write_text(json.dumps(dict(schema=1,show_control_banner=value)))
                self.assertEqual(self.probe('preference',folder),expected)
            for text in ['{',json.dumps(dict(schema=True,show_control_banner=False)),json.dumps(dict(schema=2,show_control_banner=False)),' '*17000]:
                file.write_text(text)
                self.assertEqual(self.probe('preference',folder),'true')
            file.unlink()
            target=root/'elsewhere';target.write_text('{"schema":1,"show_control_banner":false}')
            file.symlink_to(target)
            self.assertEqual(self.probe('preference',folder),'true')
    def test_helper_defaults_gui_values_and_environment_priority(self):
        def probe(obj=None, env=None):
            return self.probe('helper',json.dumps({'object':obj or {},'environment':env or {}}))
        self.assertEqual('per_command 120',probe())
        self.assertEqual('warm 600',probe({'keep_helper_warm':True,'helper_idle_minutes':10}))
        self.assertEqual('warm 60',probe({'keep_helper_warm':True,'helper_idle_minutes':1}))
        self.assertEqual('per_command 120',probe({'keep_helper_warm':True},{'TACTO_HELPER_MODE':'per_command'}))
        self.assertEqual('warm 3',probe({}, {'TACTO_HELPER_MODE':'warm','TACTO_HELPER_IDLE_SECONDS':'3'}))
        self.assertEqual('per_command 120',probe({'keep_helper_warm':True},{'TACTO_HELPER_MODE':'resident'}))
        for value in (True,0,11,2.5,'10'):
            self.assertEqual('warm 120',probe({'keep_helper_warm':True,'helper_idle_minutes':value}))
        for value in (1,'true',None):
            self.assertEqual('per_command 120',probe({'keep_helper_warm':value}))
        for value in ('0','601','NaN','oops'):
            self.assertEqual('warm 120',probe({}, {'TACTO_HELPER_MODE':'warm','TACTO_HELPER_IDLE_SECONDS':value}))

    def test_opt_out_keeps_abort_and_session_guards_before_panel_placement(self):
        text=(ROOT/'helper/TactoHelper.swift').read_text()
        gate=text.split('func ensureVisibleControl(',1)[1].split('/// Why the last',1)[0]
        self.assertLess(gate.index('guard sessionVisible()'),gate.index('if !banner'))
        self.assertLess(gate.index('guard !abortState.isStopped'),gate.index('if !banner'))
        self.assertLess(gate.index('if !banner'),gate.index('readyControlTargetFrames('))
        self.assertNotIn('NSStatusBar.system.statusItem',text)
        self.assertIn('watchForAbort()',text)
