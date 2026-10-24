"""Fresh paired TACTO/Sky runs under fixed requested model and initial fixture state."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from examples.benchmark_agent import run_agent
from examples.benchmark_runtime import runtime_evidence, agent_evidence, runtime_aliases, guide_evidence, guide_runtime_compatibility, codex_config_evidence
from examples.repeat_benchmark import Fixture, build, jobs, prompt, source_manifest, write, SKY_GUIDE


def manifest(launcher=None, tacto_guide=None):
    result={"checkout/"+k:v for k,v in source_manifest(include_binaries=False).items()}
    if launcher is not None:
        result.update({"selected_runtime/"+k:v for k,v in runtime_evidence(launcher)["files"].items()})
    for name in ('examples/benchmark_matched_conditions.py','examples/benchmark_runtime.py','examples/agent_timeline.py'):
        result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    if tacto_guide is not None:
        result.update({'selected_guide/'+key:value for key,value in guide_evidence(tacto_guide)['files'].items()})
    result['agent_executable']=agent_evidence(Path.home()/'.local/bin/codex')['sha256']
    result['sky_guide']=hashlib.sha256(SKY_GUIDE.read_bytes()).hexdigest()
    return result


def aggregate(rows):
    result={}
    for arm in ('tacto','sky'):
        selected=[row for row in rows if row['arm']==arm]
        complete=all(r['exit']==0 and r['oracle']['passed'] for r in selected)
        ok=[r for r in selected if r['exit']==0 and r['oracle']['passed']]
        entry={'runs':len(selected),'passed':len(ok),
               'failure_rate':round(1-len(ok)/len(selected),4) if selected else None,
               'seconds':sum(r['seconds'] for r in selected),
               'mean_seconds':statistics.mean(r['seconds'] for r in selected) if selected else None,
               'median_seconds_passed':statistics.median(r['seconds'] for r in ok) if ok else None}
        # Per passed run; unknown in any run keeps the median unknown, not zero.
        for key in ('total_input_tokens','new_input_tokens','output_tokens'):
            vals=[r['usage'].get(key) for r in ok]
            entry['median_'+key+'_passed']=statistics.median(vals) if ok and all(type(v) is int for v in vals) else None
        for key in ('total_input_tokens','new_input_tokens','cache_read_tokens','output_tokens'):
            vals=[r['usage'].get(key) for r in selected]
            entry[key]=sum(vals) if selected and complete and all(type(v) is int for v in vals) else None
        # Failed tasks still incurred observed usage. Keep completed-task totals
        # separate from all-attempt spend; null never means zero cost.
        observed={}
        for key in ('total_input_tokens','new_input_tokens','cache_read_tokens','output_tokens'):
            values=[row['usage'].get(key) for row in selected]
            observed[key]={'sum':sum(value for value in values if type(value) is int) if any(type(value) is int for value in values) else None,
                           'unknown_records':sum(type(value) is not int for value in values)}
        entry['observed_usage_all_attempts']=observed
        result[arm]=entry
    return result


# Every arm starts from its own fixture process and data directory, so one
# arm's commits and control sequence can never be the next arm's starting state.
INITIAL_KEYS_COMPARED=('active','amount','bonus','commits','control_version','factor','job','phase','result','sequence')


def fresh_initial(state,job):
    """A fresh owned fixture: entry phase, no history, first control sequence."""
    if (state.get('active') or state.get('phase')!='entry' or state.get('commits')!=[]
            or state.get('sequence')!=1 or state.get('job')!=job['job']):
        raise ValueError('initial state not fresh')
    if set(state)!=set(INITIAL_KEYS_COMPARED):raise ValueError('initial state fields changed')
    return state


def close_fixture(fixture):
    """Stop this arm's own child and prove it ended before the next arm starts."""
    fixture.close()
    code=fixture.process.poll()
    if code is None:raise RuntimeError('owned fixture still running')
    return code


def run_arm(app,base,pair,arm,job,changed,run):
    """One arm in its own fixture process; the fixture is closed however it ends."""
    name=f'fixture-{pair:02d}-{arm}'
    fixture=Fixture(base/name,app);isolation={'directory':name,'pid':fixture.process.pid}
    try:
        initial=fresh_initial(fixture.setup(job,changed=changed),job)
        row=run()
        row.update(initial_state=initial,oracle=fixture.oracle(job))
    finally:
        isolation['exit_code']=close_fixture(fixture)
    row['fixture_isolation']=isolation
    return row


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True)
    p.add_argument('--model',default='gpt-6.1-sol');p.add_argument('--reasoning-effort',default='medium')
    p.add_argument('--timeout',type=int,default=240)
    p.add_argument('--launcher',default=str(Path.home()/'.local/bin/tacto'))
    p.add_argument('--tacto-guide',help='Explicit absolute SKILL.md override; runtime unchanged; guide subtree is hashed')
    p.add_argument('--cases',nargs='+',choices=['bonus_on','bonus_off','renamed_button'],default=['bonus_on','bonus_off','renamed_button'])
    p.add_argument('--preflight-only',action='store_true')
    p.add_argument('--profile',action='store_true',help='Record host JSONL event timing; request native --timings')
    p.add_argument('--reps',type=int,default=1,help='repetitions of every selected case pair (1..10)')
    args=p.parse_args()
    if not 1<=args.reps<=10:p.error('--reps must be 1..10')
    if not 1<=args.timeout<=1200:p.error('--timeout must be1..1200')
    if len(args.cases)!=len(set(args.cases)):p.error('duplicate cases')
    base=Path(args.output).resolve();base.mkdir(mode=0o700)
    # Resolve and hash before building or launching an app. No silent fallback
    # from a missing checkout binary to an unrelated installation.
    try:
        runtime=runtime_evidence(args.launcher)
        selected_guide=guide_evidence(args.tacto_guide or runtime['skill'])
        compatibility=guide_runtime_compatibility(runtime,selected_guide)
        if not compatibility['compatible']:
            write(base/'preflight.json',{'ready':False,'runtime':runtime,'selected_guide':selected_guide,
                  'compatibility':compatibility,'model_calls':0,'fixture_started':False})
            raise SystemExit(2)
        config_before=codex_config_evidence()
        before=manifest(args.launcher,selected_guide['path'])
        agent=agent_evidence(Path.home()/'.local/bin/codex')
        aliases=runtime_aliases(runtime)
        for executable in ('codex',):
            path=Path.home()/'.local/bin'/executable
            if not path.is_file():raise ValueError('agent executable missing: '+executable)
    except (OSError,ValueError) as exc:
        write(base/'preflight.json',{'ready':False,'error':str(exc),'model_calls':0,'fixture_started':False})
        raise SystemExit(2)
    write(base/'preflight.json',{'ready':True,'runtime':runtime,'runtime_aliases':aliases,'agent':agent,'selected_guide':selected_guide,'compatibility':compatibility,'agent_config':config_before,'source_manifest':before,'model_calls':0,'fixture_started':False})
    if args.preflight_only:return
    config_hash=config_before['sha256']
    target='ai.callabo.tacto.matched.'+uuid.uuid4().hex[:8]
    app=build(base,target);rows=[]
    cases=[('bonus_on',jobs()[0],False),('bonus_off',jobs()[1],False),('renamed_button',jobs()[6],True)]
    cases=[case for case in cases if case[0] in args.cases]
    # Pairs are numbered across repetitions, so arm order keeps alternating
    # and every run gets its own trace and fixture directory.
    schedule=[(rep,case) for rep in range(1,args.reps+1) for case in cases]
    for pair,(rep,(case,values,changed)) in enumerate(schedule,1):
        for arm in (('tacto','sky') if pair%2 else ('sky','tacto')):
            job=dict(values,job=f'P{pair:02d}')
            own_prompt=prompt(job,target,arm,launcher=args.launcher,tacto_guide=selected_guide['path'])
            if args.profile and arm=='tacto':
                own_prompt+=' Profiling: include --timings on your tacto session command; preserve timing output.'
            try:
                row=run_arm(app,base,pair,arm,job,changed,lambda:run_agent('codex',own_prompt,base/f'{pair:02d}-{arm}',
                    timeout=args.timeout,model=args.model,reasoning_effort=args.reasoning_effort,
                    **({'profile':True} if args.profile else {})))
            except BaseException as exc:
                # A crash/interrupt is an attempted arm, not a zero-cost skip.
                # Preserve completed arms even when setup/oracle/cleanup failed.
                # Do not publish exception text: providers may include private data.
                write(base/'report.json',{'passed':False,'aborted':True,
                    'comparison_eligible':False,'rows':rows,'summary':aggregate(rows),
                    'completed_arms':len(rows),'expected_arms':len(schedule)*2,
                    'interrupted_arm':{'pair':pair,'arm':arm,'case':case,'rep':rep,
                        'error_class':type(exc).__name__,'usage':None,
                        'cost_known':False,'task_success_verified':False},
                    'unattempted_arms':len(schedule)*2-len(rows)-1,
                    'source_before':before,'source_stable':None,'agent_config_before':config_before,
                    'requested_model':args.model,'requested_reasoning_effort':args.reasoning_effort,
                    'limits':['interrupted arm usage and task outcome unknown; completed rows are partial evidence',
                              'source stability and complete paired conditions not verified']})
                raise
            row.update(arm=arm,case=case,pair=pair,rep=rep,job=job)
            rows.append(row);write(base/'progress.json',{'rows':rows})
            print(json.dumps({'pair':pair,'arm':arm,'passed':row['oracle']['passed'],'seconds':row['seconds'],
                'input':row['usage'].get('total_input_tokens'),'new_input':row['usage'].get('new_input_tokens')}),flush=True)
    after=manifest(args.launcher,selected_guide['path']);summary=aggregate(rows)
    initial_matched=all(all(rows[i]['initial_state'][k]==rows[i+1]['initial_state'][k] for k in INITIAL_KEYS_COMPARED) for i in range(0,len(rows),2))
    config_after=codex_config_evidence()
    same_config=config_before==config_after
    passed=(len(rows)==len(schedule)*2 and all(r['exit']==0 and r['oracle']['passed'] and r['oracle']['single_commit']
                                and not r['oracle']['active'] for r in rows))
    write(base/'report.json',{'passed':passed,'rows':rows,'summary':summary,'source_before':before,'source_after':after,
        'source_stable':before==after,'comparison_eligible':False,'profiling_enabled':args.profile,
        'fixture_isolation':{'process_per_arm':True,'data_directory_per_arm':True,
            'previous_fixture_ended_before_next':all(type(r['fixture_isolation']['exit_code']) is int for r in rows),
            'initial_state_keys_compared':list(INITIAL_KEYS_COMPARED),'initial_state_keys_excluded':[]},
        'comparison_limits':['actual serving model, hidden context and full foreground/caret equivalence unverified'],
        'requested_conditions_matched':before==after and same_config and initial_matched,
        'runtime':runtime,'runtime_aliases':aliases,'agent':agent,'selected_guide':selected_guide,'compatibility':compatibility,'selected_cases':args.cases,'repetitions':args.reps,'initial_states_matched':initial_matched,'config_sha256':config_hash,
        'config_stable':same_config,'agent_config_before':config_before,'agent_config_after':config_after,'requested_model':args.model,'requested_reasoning_effort':args.reasoning_effort,
        'comparison_scope':'fresh independent sessions; a fresh owned fixture process and data directory per arm; same app build/task/default tools, timeout/model/settings; counterbalanced order',
        'limits':[f'selected case pairs, {args.reps} sample(s) per case','actual serving model and billing unverified',
            'server cache and global instructions not cleared','not Flow replay or scripted Sky reuse comparison',
            'fixture setup excluded; all agent instruction reading/authoring/execution included']})
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
