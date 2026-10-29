"""Opt-in signed same-app window isolation, wait/resume and cancellation test."""
from pathlib import Path
import subprocess,json,time,threading,queue,os,plistlib,concurrent.futures,unittest,sys,tempfile
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'launcher/tacto'

@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_PARALLEL_TESTS')=='1',
                     'opt-in signed same-app window receiving test')
class WindowParallelNative(unittest.TestCase):
    def test_same_app_windows_wait_resume_and_cancelled_activation(self):
        with tempfile.TemporaryDirectory(prefix='tacto-window-test-') as directory:
            base=Path(directory);root=ROOT;cli=LAUNCHER
            app=base/'OwnedWindows.app';binary=app/'Contents/MacOS/OwnedWindows';binary.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run(['swiftc',str(root/'tests/fixtures/NativeWindowParallelFixture.swift'),'-o',str(binary)],check=True,capture_output=True)
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'ai.callabo.tacto.scopedfixture','CFBundleName':'Tacto Scoped Fixture','CFBundleExecutable':'OwnedWindows','CFBundlePackageType':'APPL','LSUIElement':True}))
            subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
            state=base/'window-state.json';state.unlink(missing_ok=True)
            p=subprocess.Popen([str(binary),str(state)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            target='ai.callabo.tacto.scopedfixture';env=dict(os.environ,TACTO_HELPER_MODE='warm',TACTO_HELPER_WAIT_SECONDS='1',TACTO_HELPER_IDLE_SECONDS='30');sessions=[]
            def read_state():return json.loads(state.read_text())
            def start(window):
             args=[str(cli),'session',target,'--helper','--window-id',str(window)]
             child=subprocess.Popen(args,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1);lines=[];q=queue.Queue();sessions.append(child)
             def reader():
              for line in child.stdout:lines.append(line);q.put(line)
              q.put(None)
             threading.Thread(target=reader,daemon=True).start()
             return child,q,lines
            def ready(s,timeout=30):
             child,q,lines=s;end=time.monotonic()+timeout
             while True:
              line=q.get(timeout=max(.05,end-time.monotonic()))
              if line is None:raise AssertionError('closed before ready: '+''.join(lines)+child.stderr.read())
              if 'TACTO_SESSION_READY' in line:return
              assert time.monotonic()<end
            safety={'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'fixture entry','scope':'entry'}}
            def flow(text,write=True):
             steps=[]
             if write:
              steps=[{'action':'set_value','selector':{'identifier':'window-editor'},'params':{'value':text},'safety':safety,'postcondition':{'field':{'identifier':'window-editor'},'attribute':'Value','equals':text}}, {'action':'click','selector':{'identifier':'window-press'},'safety':safety,'postcondition':{'field':{'identifier':'window-editor'},'attribute':'Value','equals':text+'|pressed'}}]
             else:steps=[{'action':'read','selector':{'identifier':'window-editor'},'safety':{'effects':['read'],'reversibility':'reversible'},'postcondition':{'field':{'identifier':'window-editor'},'attribute':'Value','equals':text}}]
             return {'name':'owned-window-work','target':target,'steps':steps}
            def finish(s,f,name):
             child,q,lines=s;child.stdin.write(json.dumps(f)+'\n');child.stdin.flush();child.stdin.close();child.wait(timeout=30)
             stderr=child.stderr.read();(base/(name+'.log')).write_text(''.join(lines)+stderr)
             assert child.returncode==0,(name,''.join(lines),stderr)
            try:
             end=time.monotonic()+10
             while not state.exists():assert time.monotonic()<end;time.sleep(.05)
             before=read_state();ids=[w['window_id'] for w in before['windows']]
             first=start(ids[0]);ready(first);second=start(ids[1]);ready(second)
             assert 'Value: initial-1' not in ''.join(first[2]) and 'Value: initial-0' not in ''.join(second[2])
             discovery=subprocess.run([str(cli),'windows',target,'--helper'],env=env,capture_output=True,text=True,timeout=20)
             assert discovery.returncode==0,(discovery.stdout,discovery.stderr)
             assert len(json.loads(discovery.stdout)['windows'])==4
             print('Two sessions in distinct windows of one app ready; discovery works while both own scopes',flush=True)
             # An app activation must wait for scoped owners; cancelling it must not activate later.
             activation=subprocess.Popen([str(cli),'activate',target,'--helper'],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
             try:
              time.sleep(.3);assert activation.poll() is None
              assert read_state()['front']==before['front']
              activation.terminate();activation.wait(timeout=5)
             finally:
              if activation.poll() is None:activation.kill();activation.wait()
              activation.stdout.close();activation.stderr.close()
             time.sleep(.2)
             # A TextField without AXPress must never silently become a pointer click.
             rejected=flow('initial-2',False);rejected['steps'][0]['action']='click';rejected['steps'][0]['safety']=safety
             testfile=base/'ax-only-check.json';testfile.write_text(json.dumps(rejected))
             refusal=subprocess.run([str(cli),'run',str(testfile),'--helper','--window-id',str(ids[2])],env=env,capture_output=True,text=True,timeout=20)
             (base/'ax-only-refusal.log').write_text(refusal.stdout+refusal.stderr)
             assert refusal.returncode!=0 and 'window_scope_requires_ax' in refusal.stdout+refusal.stderr,(refusal.stdout,refusal.stderr)

             waiting=start(ids[0]);time.sleep(.25);assert waiting[0].poll() is None
             finish(first,flow('job-0'),'first');ready(waiting)
             print('Same-window contender waited inside helper and resumed after owner release',flush=True)
             with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
              tasks=[pool.submit(finish,second,flow('job-1'),'second'),pool.submit(finish,waiting,flow('job-0|pressed',False),'waiter')]
              for task in tasks:task.result()
             time.sleep(.15);after=read_state()
             assert after['windows'][0]['value']=='job-0|pressed' and after['windows'][1]['value']=='job-1|pressed',after
             assert [w['presses'] for w in after['windows']]==[1,1,0,0],after
             assert all(w['input_events']==0 for w in after['windows']),after
             assert after['front']==before['front'],(before,after)
             assert after['windows'][2]['value']=='initial-2' and after['windows'][3]['value']=='initial-3'
             report={'passed':True,'same_app_distinct_windows_ready':True,'window_discovery_while_scoped_sessions_active':True,'same_window_wait_and_resume':True,'cancelled_activation_never_takes_foreground':True,'unsupported_ax_has_no_pointer_fallback':True,'two_window_values_received':True,'press_counts':[1,1,0,0],'global_key_or_mouse_events':0,'sampled_foreground_unchanged':True,'model_calls':0}
             (base/'native-window-validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
            finally:
             for child in sessions:
              if child.poll() is None:
               try:child.stdin.close()
               except Exception:pass
               try:child.wait(timeout=3)
               except subprocess.TimeoutExpired:child.kill();child.wait()
              for stream in (child.stdin,child.stdout,child.stderr):
               if stream is not None and not stream.closed:stream.close()
             p.terminate();p.wait(timeout=5)
