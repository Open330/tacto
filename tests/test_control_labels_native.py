import json,os,plistlib,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'launcher/tacto'
@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_LABEL_TESTS')=='1','opt-in signed multiline control receiving test')
class NativeLabels(unittest.TestCase):
 def test_raw_newline_and_literal_backslash_titles_remain_distinct(self):
  with tempfile.TemporaryDirectory(prefix='tacto-label-test-') as directory:
   base=Path(directory);app=base/'OwnedLabels.app';binary=app/'Contents/MacOS/OwnedLabels';binary.parent.mkdir(parents=True)
   subprocess.run(['swiftc',str(ROOT/'tests/fixtures/NativeLabelFixture.swift'),'-o',str(binary)],check=True,capture_output=True)
   target='ai.callabo.tacto.labelfixture'
   (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':target,'CFBundleName':'Tacto Label Fixture','CFBundleExecutable':'OwnedLabels','CFBundlePackageType':'APPL','LSUIElement':True}))
   subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
   state=base/'state.json';p=subprocess.Popen([str(binary),str(state)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   try:
    deadline=time.monotonic()+10
    while not state.exists():self.assertLess(time.monotonic(),deadline);time.sleep(.05)
    title='미디어 크리에이터 아이콘\n\n크리에이터 공인, 콘텐츠 제작자, 아티스트 및 인플루언서에게 적합합니다.\nmedia_creator'
    steps=[]
    for text,expected in ((title,'1,0'),(title.replace('\n',r'\n'),'1,1')):
     steps.append({'action':'click','selector':{'role':'Button','title':text},'safety':{'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'scope':'entry'}},'postcondition':{'field':{'identifier':'label-result'},'attribute':'Value','equals':expected}})
    flow=base/'flow.json';flow.write_text(json.dumps({'name':'lossless-label-receiving','target':target,'steps':steps}))
    r=subprocess.run([str(LAUNCHER),'run',str(flow),'--helper','--yes'],capture_output=True,text=True,env=dict(os.environ,TACTO_HELPER_MODE='warm'),timeout=40)
    self.assertEqual(0,r.returncode,r.stdout+r.stderr);self.assertEqual([1,1],json.loads(state.read_text())['counts'])
    print('Signed AX receiving: real multiline and literal backslash-n buttons each pressed exactly once',flush=True)
   finally:p.terminate();p.wait(timeout=5)
