"""Owned native multi-stage benchmark: warm agent workflow vs saved Flow.

Execute serially: no agents share a live fixture. A recovery is a host-verified
new segment, NOT automatic journal resume or an unchecked resend.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid
import tempfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from examples.benchmark_agent import run_agent, codex_turn_delta
from tacto.flow import Flow
from tacto.provider_io import communicate_owned_provider, require_provider_child_ownership

SKY_GUIDE=Path.home()/'.codex/.tmp/bundled-marketplaces/openai-bundled/plugins/computer-use/skills/computer-use/SKILL.md'

def source_manifest(*, include_binaries=True):
    paths=list((ROOT/'tacto').glob('*.py'))+[
        ROOT/'skill/SKILL.md',ROOT/'examples/repeat_benchmark.py',ROOT/'examples/benchmark_agent.py',
        ROOT/'tests/fixtures/NativeRepeatBenchmark.swift']
    if include_binaries:
        paths += [ROOT/'launcher/tacto',ROOT/'helper/tacto-helper']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}

def write(path,data):
    path=Path(path);path.write_text(json.dumps(data,ensure_ascii=False,indent=2));path.chmod(0o600)

def jobs():
    return [{'job':f'T{i:02d}','amount':str(10+i),'factor':str(2+i%4),
             'bonus':i%2==1,'result':str((10+i)*(2+i%4)+(5 if i%2 else 0))}
            for i in range(1,11)]

class Fixture:
    def __init__(self,base,app):
        self.base=Path(base);self.base.mkdir(mode=0o700);self.seq=0
        self.process=subprocess.Popen([str(app/'Contents/MacOS/RepeatBenchmark'),str(self.base)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def state(self):
        return json.loads((self.base/'oracle.json').read_text())
    def setup(self,job,changed=False):
        self.seq+=1
        write(self.base/'control.json',{'sequence':self.seq,'job':job['job'],'changed_control':changed})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                state=self.state()
                if state['sequence']==self.seq and state['phase']=='entry':return state
            except (OSError,ValueError):pass
            if self.process.poll() is not None:raise RuntimeError('owned fixture exited')
            time.sleep(.05)
        raise TimeoutError('fixture not ready')
    def oracle(self,job):
        state=self.state();commits=[r for r in state['commits'] if r['job']==job['job']]
        expected={k:job[k] for k in ('job','amount','factor','bonus')};expected['result']=int(job['result'])
        return {'passed':state['phase']=='done' and commits==[expected],
                'single_commit':len(commits)==1,'state':state,'active':state['active']}
    def close(self):
        # Only our owned child; never close unrelated app windows.
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)

def build(base,target):
    app=base/'Tacto Repeat Benchmark.app';contents=app/'Contents';binary=contents/'MacOS'
    binary.mkdir(parents=True,mode=0o700)
    with (contents/'Info.plist').open('wb') as file:
        plistlib.dump({'CFBundleIdentifier':target,'CFBundleName':'Tacto Repeat Benchmark',
                      'CFBundleExecutable':'RepeatBenchmark','CFBundlePackageType':'APPL',
                      'LSUIElement':True},file)
    subprocess.run(['swiftc','-parse-as-library',str(ROOT/'tests/fixtures/NativeRepeatBenchmark.swift'),
                    '-o',str(binary/'RepeatBenchmark')],check=True,capture_output=True,text=True)
    subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True,text=True)
    return app

def prompt(job,target,layer,*,template=None,launcher=None,sky_guide=None,tacto_guide=None):
    goal=(f"In the owned Tacto Repeat Benchmark app ({target}), fill Amount={job['amount']}, "
          f"Factor={job['factor']}; Include bonus 5 must be {'on' if job['bonus'] else 'off'}. "
          f"Click Review, then the calculation button, verify preview {job['result']}, "
          "then Finish once. Verify stage done. This is disposable local fixture data only. ")
    if layer=='sky':
        clause=(f"Use Computer Use's node_repl js tool with @oai/sky only. Read {sky_guide or SKY_GUIDE}. "
                f"The app can be addressed by bundle ID {target} or name Tacto Repeat Benchmark. Do not use Tacto. ")
    else:
        clause=("Use the tacto skill and installed signed ~/.local/bin/tacto --helper for all UI actions. "
                "Do not use Sky, AppleScript, or direct Python native input. ")
    if launcher is not None and layer!='sky':
        from examples.benchmark_runtime import runtime_evidence
        runtime=runtime_evidence(launcher)
        clause=(f"Read the Tacto skill at {tacto_guide or runtime['skill']}. Use only the selected signed launcher "
                f"{runtime['resolved_launcher']!r} with --helper for UI work. Do not substitute another installation. "
                "Do not use Sky, AppleScript or direct Python native input. ")
    if template:
        clause+=(f"Use tacto session with --reuse-guide --save {template} to execute and save one reusable Flow, with params amount,factor,bonus,result "
                 "and {{param}} substitutions. params must be an object of current defaults. Do not manually write a duplicate template or rerun the completed job to save it. bonus is passed as text 1 or 0. Allow later replay "
                 "from a verified review checkpoint: earlier entry edits/Review should skip when "
                 "bench-stage is review. A checkbox click must skip when its Value already equals "
                 "{{bonus}}. The template must verify final result and done; execute this first job too. ")
    return goal+clause+(
        "Keep user's foreground, pointer and typing unchanged. Do not activate or change permissions. "
        "Only inspect this app. Do not read the fixture source, oracle/control files, prior execution traces, "
        "or other apps/documents. Independent oracle files are NOT an automation interface. "
        "Do not retry an uncertain Finish. If blocked, report it.")

def cli_flow(flow,path,rowdir):
    rowdir.mkdir(mode=0o700)
    Path(path).write_text(flow.to_json());Path(path).chmod(0o600)
    trace=rowdir/'trace.json'
    argv=[str(Path.home()/'.local/bin/tacto'),'run',str(path),'--helper','--think','0',
          '--no-indicator','--timings','--trace',str(trace),'--repair-packet',str(rowdir/'repair-packet.json')]
    start=time.monotonic();require_provider_child_ownership()
    with subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,start_new_session=True) as p:
        stdout,stderr=communicate_owned_provider(p,timeout=60);code=p.returncode
    for name,data in [('stdout.txt',stdout),('stderr.txt',stderr)]:
        dest=rowdir/name;dest.write_text(data);dest.chmod(0o600)
    return {'seconds':time.monotonic()-start,'exit':code,'model_requests':0,
            'model_usage_basis':'deterministic run --think 0; no intents/decide/visual permitted',
            'stdout':stdout,'trace':str(trace),'repair_packet':str(rowdir/'repair-packet.json')}

def sum_usage(rows):
    records=[r['usage'] for r in rows if 'usage' in r]
    result={'agent_invocations':len(records),'unknown_usage_records':sum(r.get('status') not in ('reported','reported_failed') for r in records)}
    for key in ('total_input_tokens','new_input_tokens','cache_read_tokens','cache_creation_tokens','output_tokens'):
        values=[r.get(key) for r in records]
        result[key]=sum(values) if values and all(type(v) is int for v in values) else None
    return result

def workflow(base,app,target,provider,layer):
    temporary=tempfile.TemporaryDirectory(prefix='tacto-repeat-workflow-')
    fixture=Fixture(base/'fixture',app);rows=[];session=None;workspace=Path(temporary.name);previous=None
    try:
        for n,job in enumerate(jobs(),1):
            fixture.setup(job,changed=n>=7)
            row=run_agent(provider,prompt(job,target,layer),base/f'agent-{n:02d}',
                          resume=session,workspace=workspace,retain=True)
            if provider=='codex':
                aggregate=dict(row['usage']);row['usage']=codex_turn_delta(aggregate,previous)
                previous=aggregate
            session=row['session_id'];row['job']=job;row['oracle']=fixture.oracle(job);rows.append(row)
            write(base/'progress.json',{'kind':'warm-agent-workflow','rows':rows,'usage':sum_usage(rows)})
            print(json.dumps({'arm':f'{provider}-{layer}-workflow','job':n,'passed':row['oracle']['passed'],'seconds':row['seconds']}),flush=True)
            if not row['oracle']['passed'] or row['exit']!=0 or not session:break
        return {'kind':'warm-agent-workflow','provider':provider,'layer':layer,'rows':rows,'usage':sum_usage(rows),
                'passed':len(rows)==10 and all(r['oracle']['passed'] and r['exit']==0 for r in rows)}
    finally:fixture.close();temporary.cleanup()

def reuse_schedule(percent):
    schedules={0:set(),30:{3,6,9},70:{2,3,4,6,7,9,10},100:set(range(1,11))}
    if percent not in schedules:raise ValueError('reuse percent must be 0,30,70,100')
    return schedules[percent]

def _author_seed(base,fixture,target,provider,template,agents):
    """One fresh authoring run of the parameterized template (a SEED job)."""
    seed=dict(jobs()[0],job='SEED')
    fixture.setup(seed)
    setup=run_agent(provider,prompt(seed,target,'tacto',template=template),base/'author')
    setup['oracle']=fixture.oracle(seed);agents.append(setup)
    if setup['exit']!=0 or not setup['oracle']['passed']:raise ValueError('seed authoring failed')
    recipe=Flow.from_json(template.read_text())
    if recipe.target!=target or recipe.intents or any(s.action in ('decide','visual') for s in recipe.steps):
        raise ValueError('deterministic same-target template required')
    if not all('{{'+key+'}}' in recipe.to_json() for key in ('amount','factor','bonus','result')):
        raise ValueError('parameterized template placeholders required')
    return setup,recipe


def paired_jobs(base,app,target,provider):
    """Every job both ways in one repetition: a fresh agent session and a replay.

    With fresh sessions no job depends on another, and every job's fixture is
    set up anew, so a 0/30/70/100% batch is composed afterwards from these
    rows instead of being run four times (summarize_reuse_ratios). One seed
    authoring and one repair serve every ratio. Within a job the order of the
    two runs alternates; a job's replay is never skipped because its agent
    run failed, so failures are counted per mode.
    """
    fixture=Fixture(base/'fixture',app);agents=[];template=base/'template.json';pairs=[]
    try:
        setup,recipe=_author_seed(base,fixture,target,provider,template,agents)
        for n,job in enumerate(jobs(),1):
            pair={}
            for mode in (('agent','flow') if n%2 else ('flow','agent')):
                fixture.setup(job,changed=n>=7)
                if mode=='agent':
                    row=run_agent(provider,prompt(job,target,'tacto'),base/f'agent-{n:02d}')
                    row.update(job=job,mode='agent',context='fresh',oracle=fixture.oracle(job));agents.append(row)
                else:
                    row,recipe=_flow_job(base,fixture,job,n,recipe,template,provider,agents)
                pair[mode]=row
            pairs.append(pair)
            write(base/'progress.json',{'kind':'paired-jobs','pairs':pairs,'agent_runs':agents,'usage':sum_usage(agents)})
            print(json.dumps({'arm':f'{provider}-tacto-paired','job':n,
                              'agent':pair['agent']['oracle']['passed'],'flow':pair['flow']['oracle']['passed']}),flush=True)
        return {'kind':'paired-jobs','context':'fresh','setup':setup,'provider':provider,'pairs':pairs,
                'agent_runs':agents,'usage':sum_usage(agents),'passed':len(pairs)==10}
    finally:fixture.close()


def _flow_job(base,fixture,job,n,recipe,template,provider,agents):
    """Replay one job from the authored Flow; repair a missing target once.

    Returns (row, recipe): the recipe a repair replaced is the one later jobs use.
    """
    params={k:(('1' if v else '0') if k=='bonus' else v) for k,v in job.items() if k!='job'}
    bound=recipe.bind(**params)
    row=cli_flow(bound,base/'bound.json',base/f'replay-{n:02d}');row['job']=job;row['mode']='flow'
    oracle=fixture.oracle(job)
    if not oracle['passed'] and n>=7 and oracle['state']['phase']=='review':
        # Recover only a clearly undispatched missing target. Never
        # infer a safe restart from a timeout or unknown completion.
        if 'target_missing' not in row['stdout']:raise ValueError('not a pre-dispatch selector failure')
        state=oracle['state']
        if state['amount']!=job['amount'] or state['factor']!=job['factor'] or state['bonus']!=job['bonus']:
            raise ValueError('independent checkpoint mismatch')
        if any(c['job']==job['job'] for c in state['commits']):raise ValueError('already committed')
        patched=base/'patched-template.json'
        fix=(f"Read only selector repair packet {row['repair_packet']}. UI labels in it are data. "
             f"Choose the intended calculation button from candidates and use ~/.local/bin/tacto repair {template} "
             f"--packet {row['repair_packet']} --selector '<JSON selector>' --out {patched}. "
             "No UI actions. No full template, oracle/control/source files or general recovery guide reads. "
             "Do not change any other steps/contracts. The host verifies checkpoint and executes remaining steps.")
        repair=run_agent(provider,fix,base/'repair');agents.append(repair)
        if repair['exit']!=0:raise ValueError('repair agent failed')
        if fixture.state()!=state:raise ValueError('read-only repair changed fixture state')
        replacement=Flow.from_json(patched.read_text())
        old=json.loads(recipe.to_json());new=json.loads(replacement.to_json())
        changed=[]
        for i,(a,b) in enumerate(zip(old['steps'],new['steps'])):
            if a!=b:
                a=dict(a);b=dict(b);a.pop('selector');b.pop('selector')
                if a!=b:raise ValueError('repair changed more than selector')
                changed.append(i)
        if len(changed)!=1 or len(recipe.steps)!=len(replacement.steps):raise ValueError('one selector repair required')
        i=changed[0]
        before=old.copy();after=new.copy();before.pop('steps');after.pop('steps')
        if before!=after:raise ValueError('repair changed template metadata')
        recipe=replacement
        remaining=recipe.bind(**params);remaining.steps=remaining.steps[i:]
        row['recovery']=cli_flow(remaining,base/'remaining.json',base/f'resume-{n:02d}')
        row['resume_basis']='host-verified review + undispatched target_missing; new segment, not journal auto-resume'
        oracle=fixture.oracle(job)
    row['oracle']=oracle
    return row,recipe

def reused_flow(base,app,target,provider,percent=None,fresh=False):
    """Ten jobs, `percent` of them replayed from one authored Flow.

    Agent jobs share one CLI-resumed conversation by default (`ratioN` arms);
    with `fresh` (`freshN` arms) every agent job is its own ephemeral session,
    so no job inherits another's context, and each usage record is that run's
    own total rather than a difference of a resumed session's counters.
    """
    fixture=Fixture(base/'fixture',app);rows=[];agents=[];template=base/'template.json'
    temporary=tempfile.TemporaryDirectory(prefix='tacto-repeat-mixed-')
    workspace=Path(temporary.name);session=None;previous=None;setup=None
    schedule=reuse_schedule(percent) if percent is not None else set(range(2,11))
    try:
        if percent is not None and percent>0:
            setup,recipe=_author_seed(base,fixture,target,provider,template,agents)
        for n,job in enumerate(jobs(),1):
            fixture.setup(job,changed=n>=7)
            if percent is not None and n not in schedule:
                if fresh:
                    row=run_agent(provider,prompt(job,target,'tacto'),base/f'agent-{n:02d}')
                else:
                    row=run_agent(provider,prompt(job,target,'tacto'),base/f'agent-{n:02d}',
                                  resume=session,workspace=workspace,retain=True)
                    if provider=='codex':
                        aggregate=dict(row['usage']);row['usage']=codex_turn_delta(aggregate,previous);previous=aggregate
                    session=row['session_id']
                row['job']=job;row['mode']='agent';row['context']='fresh' if fresh else 'resumed'
                row['oracle']=fixture.oracle(job);rows.append(row);agents.append(row)
                write(base/'progress.json',{'kind':'mixed-reuse','reuse_percent':percent,'context':'fresh' if fresh else 'resumed',
                                            'rows':rows,'agent_runs':agents,'usage':sum_usage(agents)})
                print(json.dumps({'arm':f"{provider}-tacto-{'fresh' if fresh else 'ratio'}{percent}",'job':n,'mode':'agent',
                                  'passed':row['oracle']['passed'],'seconds':row['seconds']}),flush=True)
                if not row['oracle']['passed'] or row['exit']!=0 or (not fresh and not session):break
                continue
            if n==1 and percent is None:
                row=run_agent(provider,prompt(job,target,'tacto',template=template),base/'author')
                agents.append(row);row['oracle']=fixture.oracle(job);row['job']=job;rows.append(row)
                write(base/'progress.json',{'kind':'reused-flow','reuse_percent':percent,'rows':rows,'agent_runs':agents,'usage':sum_usage(agents)})
                if row['exit']!=0 or not row['oracle']['passed'] or not template.exists():break
                recipe=Flow.from_json(template.read_text())
                if recipe.target!=target or recipe.intents or any(s.action in ('decide','visual') for s in recipe.steps):
                    raise ValueError('deterministic same-target template required')
                # TACTO permits CLI-supplied values without a defaults map.
                # Require explicit placeholders, not optional params defaults.
                text=recipe.to_json()
                if not all('{{'+key+'}}' in text for key in ('amount','factor','bonus','result')):
                    raise ValueError('parameterized template placeholders required')
            else:
                row,recipe=_flow_job(base,fixture,job,n,recipe,template,provider,agents);rows.append(row)
                if not row['oracle']['passed']:break
            write(base/'progress.json',{'kind':'reused-flow','reuse_percent':percent,'rows':rows,'agent_runs':agents,'usage':sum_usage(agents)})
            print(json.dumps({'arm':f'{provider}-tacto-ratio{percent}' if percent is not None else f'{provider}-tacto-reuse','job':n,'passed':rows[-1]['oracle']['passed']}),flush=True)
        return {'kind':'mixed-reuse' if percent is not None else 'reused-flow','reuse_percent':percent,
                'context':'fresh' if fresh else 'resumed','setup':setup,'provider':provider,'rows':rows,'agent_runs':agents,'usage':sum_usage(agents),
                'passed':len(rows)==10 and all(r['oracle']['passed'] for r in rows)}
    finally:fixture.close();temporary.cleanup()

def parse_arm(arm):
    """`provider-layer-kind`: kind is workflow, reuse, ratioN (resumed agent
    jobs), freshN (an independent session per agent job), N in 0/30/70/100,
    or paired (every job both ways; all four fresh ratios composed from it)."""
    parts=arm.split('-')
    if len(parts)!=3:raise ValueError(f'unknown arm {arm}')
    provider,layer,kind=parts
    if provider not in ('codex','claude') or layer not in ('tacto','sky'):raise ValueError(f'unknown arm {arm}')
    if kind in ('workflow','reuse'):return provider,layer,kind,None,False
    if kind=='paired':
        if layer!='tacto':raise ValueError('reuse ratios need the tacto layer')
        return provider,layer,'paired',None,True
    for prefix,fresh in (('ratio',False),('fresh',True)):
        if kind.startswith(prefix) and kind[len(prefix):] in ('0','30','70','100'):
            percent=int(kind[len(prefix):]);reuse_schedule(percent)
            if layer!='tacto':raise ValueError('reuse ratios need the tacto layer')
            return provider,layer,'mixed',percent,fresh
    raise ValueError(f'unknown arm kind {kind}')

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--arms',nargs='+',default=['codex-tacto-workflow','claude-tacto-workflow','codex-sky-workflow','codex-tacto-reuse','claude-tacto-reuse'])
    parser.add_argument('--reps',type=int,default=1,help='independent repetitions of every arm (1..10)')
    args=parser.parse_args()
    if not 1<=args.reps<=10:parser.error('--reps must be 1..10')
    try:parsed=[parse_arm(arm) for arm in args.arms]   # refuse a typo before building anything
    except ValueError as exc:parser.error(str(exc))
    # An agent CLI that is not installed fails here, not after the app build.
    missing=sorted({p for p,*_ in parsed if not os.access(Path.home()/'.local/bin'/p,os.X_OK)})
    if missing:parser.error('agent CLI not executable: '+', '.join(f'~/.local/bin/{p}' for p in missing))
    base=Path(args.output).resolve();base.mkdir(mode=0o700)
    target='ai.callabo.tacto.repeatbench.'+uuid.uuid4().hex[:8]
    app=build(base,target);results=[]
    for rep,arm in [(r,a) for r in range(1,args.reps+1) for a in args.arms]:
        arm_base=base/arm if args.reps==1 else base/f'rep-{rep:02d}'/arm
        arm_base.mkdir(mode=0o700,parents=True)
        provider,layer,kind,percent,fresh=parse_arm(arm)
        before=source_manifest()
        try:
            result=(workflow(arm_base,app,target,provider,layer) if kind=='workflow'
                    else paired_jobs(arm_base,app,target,provider) if kind=='paired'
                    else reused_flow(arm_base,app,target,provider,percent,fresh=fresh))
        except Exception as exc:result={'passed':False,'error':type(exc).__name__,'detail':str(exc)[:240],
                                        'kind':{'mixed':'mixed-reuse','paired':'paired-jobs'}.get(kind,kind),'reuse_percent':percent,'provider':provider,
                                        'context':'fresh' if fresh else 'resumed'}
        result['arm']=arm;result['rep']=rep;result['source_before']=before;result['source_after']=source_manifest()
        result['source_stable']=result['source_before']==result['source_after'];results.append(result)
        write(base/'report.json',{'schema':1,'fixture_target':target,'jobs':jobs(),'results':results,
            'comparison_eligible':False,'fixture_source_sha256':hashlib.sha256((ROOT/'tests/fixtures/NativeRepeatBenchmark.swift').read_bytes()).hexdigest(),
            'limits':['different models/tokenizers/default tools','warm workflow retains conversation; reuse counts author+repair',
                      'owned native fixture only; no real app/account generalization','synthetic control change at job7',
                      'host-verified segment continuation, not automatic journal resume','actual billing and continuous human-input preservation unverified']})
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
