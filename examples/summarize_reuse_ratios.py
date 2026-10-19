"""Report actual mixed runs with setup and repair charged to each scenario."""
import argparse
import json
from pathlib import Path
from examples.repeat_benchmark import reuse_schedule, sum_usage
from examples.summarize_repeat_benchmark import measured_batch, savings


def summarize(result):
    summary=measured_batch(result)
    percent=result['reuse_percent'];rows=result['rows']
    if any(r['exit']!=0 for r in result['agent_runs']):raise ValueError('agent exit failure')
    if any(r['mode']=='flow' and (r.get('recovery',r)['exit']!=0) for r in rows):
        raise ValueError('final replay exit failure')
    selected={i for i,r in enumerate(rows,1) if r['mode']=='flow'}
    if selected!=reuse_schedule(percent):raise ValueError('actual routing differs from requested ratio')
    if len({r['job']['job'] for r in rows})!=10:raise ValueError('distinct runtime jobs required')
    seed=result.get('setup')
    if percent and (not seed or seed['exit']!=0 or not seed['oracle']['passed']):
        raise ValueError('verified authoring seed required')
    agent_jobs=[r for r in rows if r['mode']=='agent']
    setup_path=seed['trace_directory'] if seed else None
    repairs=[r for r in result['agent_runs'] if not r.get('job') and r['trace_directory']!=setup_path]
    replay=[r for r in rows if r['mode']=='flow']
    breakdown={
        'setup':{'seconds':seed['seconds'] if seed else 0,'usage':sum_usage([seed]) if seed else None},
        'agent_jobs':{'seconds':sum(r['seconds'] for r in agent_jobs),'count':len(agent_jobs),'usage':sum_usage(agent_jobs) if agent_jobs else None},
        'flow_attempts':{'seconds':sum(r['seconds'] for r in replay),'count':len(replay),'model_requests':0},
        'repairs':{'seconds':sum(r['seconds'] for r in repairs),'count':len(repairs),'usage':sum_usage(repairs) if repairs else None},
        'remaining_segments':{'seconds':sum(r.get('recovery',{}).get('seconds',0) for r in rows),
                              'count':sum('recovery' in r for r in rows),'model_requests':0}}
    summary.update(reuse_percent=percent,flow_jobs=len(replay),agent_jobs=len(agent_jobs),repair_invocations=len(repairs),
                   all_oracles_inactive=all(not r['oracle']['active'] for r in rows),breakdown=breakdown,
                   execution_seconds_excluding_setup=summary['seconds']-(seed['seconds'] if seed else 0))
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('report');p.add_argument('--output',required=True)
    args=p.parse_args();raw=json.loads(Path(args.report).read_text())
    batches=[summarize(r) for r in raw['results'] if r.get('passed') and r.get('kind')=='mixed-reuse']
    baseline=next(b for b in batches if b['reuse_percent']==0)
    out={'batches':batches,'savings_against_zero':{b['reuse_percent']:savings(baseline,b) for b in batches if b['reuse_percent']},
         'complete':{b['reuse_percent'] for b in batches}=={0,30,70,100},'raw_report':args.report,
         'definition':'ten runtime jobs; fresh authoring seed and repair charged separately and included in total',
         'comparison_eligible':False,'limits':['one batch per ratio, fixed execution order','owned fixture, not general application coverage',
         'CLI-resumed conversation, not persistent or fully scripted workflow','unknown serving model and billing parity',
         'single logical commit is not proof of one physical input','host-verified remaining segment, not automatic journal resume']}
    path=Path(args.output);path.write_text(json.dumps(out,indent=2));path.chmod(0o600)
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
