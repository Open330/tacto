"""Transient window geometry cannot bypass the visible Stop control."""
import json,pathlib,subprocess,tempfile,unittest,sys
@unittest.skipUnless(sys.platform=='darwin','Swift/CoreGraphics required')
class ControlFrameReadiness(unittest.TestCase):
    def test_transient_permanent_and_abort_cases_without_target_input(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        text=(root/'helper/TactoHelper.swift').read_text()
        code=text[text.index('func readyControlTargetFrames('):text.index('func ensureVisibleControl(')]
        program='import Foundation\nimport CoreGraphics\n'+code+'''
var clock=0.0;var reads=0
let recovered=readyControlTargetFrames(read:{reads+=1;return reads==1 ? nil : []}, stopped:{false},now:{clock},pause:{clock+=$0})
let transient=recovered != nil && reads==2 && clock<=0.5
clock=0;reads=0
let failed=readyControlTargetFrames(read:{reads+=1;return nil}, stopped:{false},now:{clock},pause:{clock+=$0})
let bounded=failed==nil && clock<=0.501 && reads<=12
reads=0
let aborted=readyControlTargetFrames(read:{reads+=1;return []},stopped:{true})
let noRead=aborted==nil && reads==0
print(String(data:try! JSONSerialization.data(withJSONObject:["transient":transient,"bounded":bounded,"abort":noRead]),encoding:.utf8)!)
'''
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'main.swift';path.write_text(program)
            r=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,r.returncode,r.stderr)
        self.assertEqual({'transient':True,'bounded':True,'abort':True},json.loads(r.stdout))

    def test_stop_refusal_never_advises_changing_screen_permissions(self):
        root=pathlib.Path(__file__).resolve().parents[1]
        text=(root/'helper/TactoHelper.swift').read_text()
        code=text[text.index('func indicatorRefusal('):text.index('/// True only when AXWindows was read successfully')]
        program='import Foundation\nvar lastIndicatorRefusal="aborted_by_user"\nvar controlPanelDiagnostic:[String:Any]=[:]\nlet permissionHolderName="Tacto"\n'+code+\
                '\nprint(String(data:try! JSONSerialization.data(withJSONObject:indicatorRefusal()),encoding:.utf8)!)'
        with tempfile.TemporaryDirectory() as folder:
            path=pathlib.Path(folder)/'main.swift';path.write_text(program)
            r=subprocess.run(['swift',str(path)],capture_output=True,text=True,timeout=60)
        self.assertEqual(0,r.returncode,r.stderr)
        result=json.loads(r.stdout)
        self.assertEqual('aborted_by_user',result['error'])
        self.assertEqual('not_started',result['effect'])
        self.assertNotIn('화면 기록',result['detail'])
