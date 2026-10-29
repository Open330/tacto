"""Fixed four-job native runtime benchmark; no agent or model is invoked.

Compares a window-scoped candidate at concurrency 1/2/4 with a sequential
installed baseline. Run alone, after building the signed source launcher/helper.
"""
from pathlib import Path
import argparse,concurrent.futures,json,os,plistlib,random,statistics,subprocess,time,threading,hashlib
ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--repetitions',type=int,default=5)
    parser.add_argument('--baseline-cli',type=Path,default=Path('/Applications/Tacto.app/Contents/Resources/runtime/launcher/tacto'))
    args=parser.parse_args()
    if not args.output_dir.is_absolute() or args.output_dir.exists() or not 3<=args.repetitions<=20:
        parser.error('use a new absolute output directory and 3..20 repetitions')
    b=args.output_dir;b.mkdir(mode=0o700)
    candidate=ROOT/'launcher/tacto';baseline=args.baseline_cli.resolve()
    assert candidate.is_file() and baseline.is_file()
    app=b/'Benchmark.app';binary=app/'Contents/MacOS/Benchmark';binary.parent.mkdir(parents=True)
    built=subprocess.run(['swiftc',str(ROOT/'tests/fixtures/NativeWindowParallelFixture.swift'),'-o',str(binary)],capture_output=True,text=True,timeout=60)
    (b/'fixture-build.log').write_text(built.stdout+built.stderr);built.check_returncode()
    target='ai.callabo.tacto.windowbenchmark'
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':target,'CFBundleExecutable':'Benchmark','CFBundleName':'Tacto Window Benchmark','CFBundlePackageType':'APPL','LSUIElement':True}))
    subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
    state=b/'state.json';fixture=subprocess.Popen([str(binary),str(state)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    env=dict(os.environ,TACTO_HELPER_MODE='warm',TACTO_HELPER_IDLE_SECONDS='120')
    rows=[]
    def provenance(cli):
        helper=cli.parent.parent/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
        info=cli.parents[3]/'Info.plist'
        metadata=plistlib.loads(info.read_bytes()) if info.is_file() else {}
        result={'launcher_sha256':hashlib.sha256(cli.read_bytes()).hexdigest(),
                'helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest()}
        if cli==candidate:
            result.update(source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                          source_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT).strip()))
        else:result.update(release=metadata.get('TactoReleaseTag'),source_commit=metadata.get('TactoSourceCommit'))
        return result
    provenances={'candidate':provenance(candidate),'baseline':provenance(baseline)}
    def snapshot():
        result={}
        for line in subprocess.check_output(['ps','-axo','pid=,rss=,time=,comm='],text=True).splitlines():
            f=line.split(None,3)
            if len(f)!=4:continue
            if f[3] not in (str(candidate.parent.parent/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'),str(baseline.parent.parent/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper')):continue
            parts=f[2].split(':');cpu=float(parts[-1])+60*int(parts[-2])+(3600*int(parts[-3]) if len(parts)>2 else 0)
            result[int(f[0])]={'rss_kib':int(f[1]),'cpu_seconds':cpu,'arm':'candidate' if f[3].startswith(str(ROOT)+'/') else 'baseline'}
        return result
    def trial(label,cli,concurrency,round_index,scoped,timed=True):
        before_cpu=snapshot();samples=[];done=threading.Event()
        def monitor():
            while not done.is_set():
                samples.append(snapshot());done.wait(.2)
        watcher=threading.Thread(target=monitor,daemon=True);watcher.start();started=time.monotonic()
        def job(index):
            text=f'bench-{round_index}-{index}'
            selector={'identifier':'window-editor','ancestor':f'Tacto scoped window {index}'}
            safety={'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'benchmark entry','scope':'entry'}}
            flow={'name':'fixed-window-job','target':target,'steps':[
                {'action':'set_value','selector':selector,'params':{'value':text},'safety':safety,'postcondition':{'field':selector,'attribute':'Value','equals':text}},
                {'action':'click','selector':{'identifier':'window-press','ancestor':f'Tacto scoped window {index}'},'safety':safety,'postcondition':{'field':selector,'attribute':'Value','equals':text+'|pressed'}}]}
            path=b/f'{label}-{round_index}-{index}.json';path.write_text(json.dumps(flow))
            command=[str(cli),'run',str(path),'--helper','--think','0']
            if scoped:command+=['--window-id',str(ids[index])]
            p=subprocess.run(command,env=env,capture_output=True,text=True,timeout=45)
            (b/f'{label}-{round_index}-{index}.log').write_text(p.stdout+p.stderr)
            pids=[]
            for line in p.stderr.splitlines():
                try:note=json.loads(line)
                except ValueError:continue
                if note.get('effective')=='warm':pids.append(note['helper_pid'])
            return {'job':index,'passed':p.returncode==0 and len(pids)==1,'helper_pids':pids,'expected':text+'|pressed'}
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:jobs=list(pool.map(job,range(4)))
        finally:elapsed=time.monotonic()-started;done.set();watcher.join(2)
        current=json.loads(state.read_text())
        passed=all(j['passed'] and current['windows'][j['job']]['value']==j['expected'] for j in jobs)
        used={pid for job in jobs for pid in job['helper_pids']}
        final=snapshot();cpu=sum(max(0,final.get(pid,{}).get('cpu_seconds',0)-before_cpu.get(pid,{}).get('cpu_seconds',0)) for pid in used)
        peak=max((sum(sample.get(pid,{}).get('rss_kib',0) for pid in used) for sample in samples),default=0)
        total_peak=max((sum(value['rss_kib'] for value in sample.values() if value['arm']==label) for sample in samples),default=0)
        assert peak>0 and total_peak>=peak,'Worker RSS sampling missing; inspect process metadata'
        row={'peak_total_pool_rss_kib':total_peak,'arm':label,'round':round_index,'concurrency':concurrency,'fixed_jobs':4,'seconds':elapsed,'passed':passed,'worker_count':len(used),'peak_used_worker_rss_kib':peak,'sampled_worker_cpu_seconds':cpu,'model_calls':0,'agent_input_tokens':None,'agent_output_tokens':None}
        if timed:rows.append(row)
        print(json.dumps(row),flush=True)
        assert passed,'Receiving mismatch or failed job; preserve logs before any retry'
    try:
        end=time.monotonic()+10
        while not state.exists():assert time.monotonic()<end;time.sleep(.05)
        ids=[w['window_id'] for w in json.loads(state.read_text())['windows']]
        trial('baseline',baseline,1,'warmup',False,False)
        trial('candidate',candidate,4,'warmup',True,False)
        for i in range(args.repetitions):
            arms=[('baseline',baseline,1,False),('candidate',candidate,1,True),('candidate',candidate,2,True),('candidate',candidate,4,True)]
            random.Random(20261029+i).shuffle(arms)
            for label,cli,n,scoped in arms:trial(label,cli,n,i,scoped)
        summaries=[]
        for arm,n in (('baseline',1),('candidate',1),('candidate',2),('candidate',4)):
            group=[r for r in rows if r['arm']==arm and r['concurrency']==n];times=sorted(r['seconds'] for r in group)
            summaries.append({'arm':arm,'concurrency':n,'runs':len(group),'passed':sum(r['passed'] for r in group),'median_seconds':statistics.median(times),'p95_seconds':times[max(0,__import__('math').ceil(.95*len(times))-1)],'median_peak_used_worker_rss_mib':statistics.median(r['peak_used_worker_rss_kib']/1024 for r in group),'median_peak_total_pool_rss_mib':statistics.median(r['peak_total_pool_rss_kib']/1024 for r in group)})
        assert provenances=={'candidate':provenance(candidate),'baseline':provenance(baseline)},'Binary/source identity changed during measurement'
        report={'provenance':provenances,'passed':all(r['passed'] for r in rows),'jobs_per_run':4,'repetitions':args.repetitions,'model_calls':0,'agent_tokens_measured':False,'scope':'native runtime only; no agent/provider was invoked; baseline serial versus candidate window-scoped 1/2/4; RSS includes separate metrics for used workers and the whole warm pool; neither includes agent/CLI/application memory','summary':summaries,'runs':rows}
        (b/'REPORT.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'summary':summaries}),flush=True)
    finally:fixture.terminate();fixture.wait(timeout=5)

if __name__=='__main__':main()
