"""Opt-in real signed-helper pool test using four owned background AppKit apps.

Build helper/build.sh, helper/bundle.sh and launcher/build.sh first. Run with
TACTO_RUN_NATIVE_PARALLEL_TESTS=1; ordinary discovery does not touch the UI.
"""
from pathlib import Path
import os,sys,shutil,plistlib,subprocess,tempfile,unittest,json,time,threading,queue,concurrent.futures
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'launcher/tacto'

@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_PARALLEL_TESTS')=='1',
                     'opt-in signed native parallel helper receiving test')
class ParallelHelperNative(unittest.TestCase):
    def test_isolated_workers_receive_overlapping_ax_actions_without_activation(self):
        with tempfile.TemporaryDirectory(prefix='tacto-parallel-test-') as directory:
            b=Path(directory);root=ROOT
            built=subprocess.run(['swiftc',str(ROOT/'tests/fixtures/NativeParallelFixture.swift'),'-o',str(b/'fixture')],capture_output=True,text=True,timeout=60)
            self.assertEqual(0,built.returncode,built.stderr)
            for i in range(4):
                app=b/f'Fixture{i}.app';binary=app/'Contents/MacOS/Fixture'
                binary.parent.mkdir(parents=True);shutil.copy2(b/'fixture',binary)
                (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':f'ai.callabo.tacto.parallelfixture{i}','CFBundleName':f'Tacto Parallel {i}','CFBundleExecutable':'Fixture','CFBundlePackageType':'APPL','LSUIElement':True}))
                signed=subprocess.run(['codesign','--force','--sign','-','--identifier',f'ai.callabo.tacto.parallelfixture{i}',str(app)],capture_output=True,text=True,timeout=20)
                self.assertEqual(0,signed.returncode,signed.stderr)
            cli=LAUNCHER;env=dict(os.environ,TACTO_HELPER_MODE='warm',TACTO_HELPER_WAIT_SECONDS='0',TACTO_HELPER_IDLE_SECONDS='30')
            fixtures=[];sessions=[]
            def fixture(i):
             out=b/f'state-{i}.json';out.unlink(missing_ok=True)
             p=subprocess.Popen([str(b/f'Fixture{i}.app/Contents/MacOS/Fixture'),str(out),f'Tacto parallel {i}'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);fixtures.append(p)
             end=time.monotonic()+10
             while not out.exists():
              assert time.monotonic()<end and p.poll() is None;time.sleep(.05)
             return f'ai.callabo.tacto.parallelfixture{i}'
            def start(target):
             p=subprocess.Popen([str(cli),'session',target,'--helper','--idle-timeout','90'],env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
             q=queue.Queue();lines=[]
             def reader():
              for line in p.stdout:lines.append(line);q.put(line)
              q.put(None)
             threading.Thread(target=reader,daemon=True).start();sessions.append(p)
             end=time.monotonic()+30
             while True:
              line=q.get(timeout=max(.1,end-time.monotonic()))
              if line is None:raise RuntimeError('session failed: '+''.join(lines)+p.stderr.read())
              if 'TACTO_SESSION_READY' in line:return p,lines,q
              if time.monotonic()>end:raise RuntimeError('readiness timeout')
            def flow(target,i):return {'name':'parallel-owned-press','target':target,'steps':[{'action':'click','selector':{'identifier':'parallel-press'},'safety':{'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'owned fixture entry','scope':'entry'}},'postcondition':{'field':{'identifier':'parallel-editor'},'attribute':'Value','equals':f'pressed-Tacto parallel {i}'}}]}

            def finish(s,target,i):
             p,lines,q=s;p.stdin.write(json.dumps(flow(target,i))+'\n');p.stdin.flush();p.stdin.close()
             p.wait(timeout=40);err=p.stderr.read();(b/f'session-{i}.log').write_text(''.join(lines)+err)
             assert p.returncode==0,('session',i,p.returncode,''.join(lines),err)
             return err
            try:
             targets=[fixture(i) for i in range(4)];front=json.loads((b/'state-0.json').read_text())['front']
             held=[start(t) for t in targets]
             print('Four signed sessions READY simultaneously while no earlier session has completed',flush=True)
             busy=subprocess.run([str(cli),'apps','--helper'],env=env,capture_output=True,text=True,timeout=15)
             assert busy.returncode!=0 and 'helper_busy' in busy.stdout+busy.stderr,(busy.stdout,busy.stderr)
             # Release slot 3 without mutation to test contention while slot 0 remains live.
             held[3][0].stdin.close();held[3][0].wait(timeout=15)
             duplicate=subprocess.run([str(cli),'observe',targets[0],'--helper','--json'],env=env,capture_output=True,text=True,timeout=15)
             assert duplicate.returncode!=0 and 'target_busy' in duplicate.stdout+duplicate.stderr,(duplicate.stdout,duplicate.stderr)
             cross=subprocess.run([str(cli),'observe',targets[0],'--helper','--json'],env=dict(env,TACTO_HELPER_MODE='per_command'),capture_output=True,text=True,timeout=20)
             assert cross.returncode!=0 and 'target_busy' in cross.stdout+cross.stderr,(cross.stdout,cross.stderr)
             print('Full pool bounded; same-app and cross-mode contention refused before dispatch',flush=True)
             with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
              logs=list(pool.map(lambda row:finish(row[1],targets[row[0]],row[0]),enumerate(held[:3])))
             pids=[]
             for log in logs:
              for line in log.splitlines():
               try:d=json.loads(line)
               except ValueError:continue
               if d.get('effective')=='warm':pids.append(d['helper_pid'])
             assert len(set(pids))==3,pids
             time.sleep(.15)
             states=[json.loads((b/f'state-{i}.json').read_text()) for i in range(4)]
             assert all(states[i]['text']==f'pressed-Tacto parallel {i}' for i in range(3)),states
             assert states[3]['text']=='initial-Tacto parallel 3'
             assert max(row['started'] for row in states[:3]) < min(row['ended'] for row in states[:3]),('AX handlers did not overlap',states)
             assert all(row['front']==front for row in states),('foreground changed',front,states)
             # Released app can be observed afresh; no previous connection's pin is needed.
             again=subprocess.run([str(cli),'observe',targets[0],'--helper','--json'],env=env,capture_output=True,text=True,timeout=20)
             assert again.returncode==0,(again.stdout,again.stderr)
             report={'passed':True,'four_connections_ready_simultaneously':True,'distinct_worker_pids_for_three_writes':len(set(pids)),'owned_app_values_verified':3,'three_AX_callbacks_overlap_verified':True,'fourth_app_unchanged':True,'foreground_unchanged':True,'pool_exhaustion_verified':True,'same_app_contention_verified':True,'cross_mode_contention_verified':True,'release_and_fresh_observation_verified':True,'scope':'signed development launcher/helper; four owned background AppKit apps; no external service or user document'}
             (b/'native-validation.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
            finally:
             for p in sessions:
              if p.poll() is None:
               try:p.stdin.close()
               except Exception:pass
               try:p.wait(timeout=5)
               except subprocess.TimeoutExpired:p.kill();p.wait()
              for stream in (p.stdin,p.stdout,p.stderr):
               if stream is not None and not stream.closed:stream.close()
             for p in fixtures:
              p.terminate()
             for p in fixtures:
              try:p.wait(timeout=5)
              except subprocess.TimeoutExpired:p.kill();p.wait()
