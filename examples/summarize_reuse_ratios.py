"""Report actual mixed runs with setup and repair charged to each scenario."""
import argparse
import json
import statistics
from pathlib import Path
from examples.repeat_benchmark import reuse_schedule, sum_usage
from examples.summarize_repeat_benchmark import measured_batch, savings


def summarize(result,*,unknown_usage=False):
    summary=measured_batch(result,unknown_usage=unknown_usage)
    percent=result['reuse_percent'];rows=result['rows']
    if any(r['exit']!=0 for r in result['agent_runs']):raise ValueError('agent exit failure')
    if any(r['mode']=='flow' and (r.get('recovery',r)['exit']!=0) for r in rows):
        raise ValueError('final replay exit failure')
    selected={i for i,r in enumerate(rows,1) if r['mode']=='flow'}
    if selected!=reuse_schedule(percent):raise ValueError('actual routing differs from requested ratio')
    if len({r['job']['job'] for r in rows})!=10:raise ValueError('distinct runtime jobs required')
    context=result.get('context','resumed')
    if any(r.get('context',context)!=context for r in rows if r['mode']=='agent'):
        raise ValueError('agent job context differs from the arm')
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
    summary.update(reuse_percent=percent,context=context,flow_jobs=len(replay),agent_jobs=len(agent_jobs),repair_invocations=len(repairs),
                   all_oracles_inactive=all(not r['oracle']['active'] for r in rows),breakdown=breakdown,
                   execution_seconds_excluding_setup=summary['seconds']-(seed['seconds'] if seed else 0))
    return summary


METRICS=('seconds','total_input_tokens','new_input_tokens','output_tokens')


def _median(values):
    """None when any run's value is unknown: a missing count is not zero."""
    return statistics.median(values) if values and all(type(v) in (int, float) for v in values) else None


def summarize_groups(results):
    """Every run of each (context, percent), failed runs included.

    `failure_rate` counts runs that did not finish all ten jobs or whose
    routing, seed or replay could not be verified; medians use passed runs,
    and a passed run with unknown token counts keeps those medians unknown.
    """
    groups={}
    for result in results:
        if result.get('kind')!='mixed-reuse':continue
        key=(result.get('context','resumed'),result.get('reuse_percent'))
        group=groups.setdefault(key,{'context':key[0],'reuse_percent':key[1],'runs':0,'passed':[],'failures':[]})
        group['runs']+=1
        try:
            if not result.get('passed'):raise ValueError(result.get('error') or 'batch did not pass')
            group['passed'].append(summarize(result,unknown_usage=True))
        except (ValueError,KeyError,TypeError) as exc:
            group['failures'].append({'rep':result.get('rep'),'reason':str(exc)[:200]})
    out=[]
    for (context,percent),group in sorted(groups.items(),key=lambda kv:(kv[0][0],kv[0][1] if kv[0][1] is not None else -1)):
        passed=group['passed']
        out.append({'context':context,'reuse_percent':percent,'runs':group['runs'],'passed_runs':len(passed),
                    'failure_rate':round(1-len(passed)/group['runs'],4),'failures':group['failures'],
                    'median':{m:_median([b.get(m) for b in passed]) for m in METRICS},
                    'per_run':[{m:b.get(m) for m in METRICS} for b in passed]})
    for context in {g['context'] for g in out}:
        zero=next((g for g in out if g['context']==context and g['reuse_percent']==0),None)
        for g in out:
            if g['context']!=context or zero is None or g is zero:continue
            g['median_change_vs_zero']={m:(None if g['median'][m] is None or not zero['median'][m]
                                          else round(g['median'][m]/zero['median'][m]-1,4)) for m in METRICS}
    return out


def main():
    p=argparse.ArgumentParser();p.add_argument('report');p.add_argument('--output',required=True)
    args=p.parse_args();raw=json.loads(Path(args.report).read_text())
    batches=[summarize(r) for r in raw['results'] if r.get('passed') and r.get('kind')=='mixed-reuse']
    groups=summarize_groups(raw['results'])
    resumed=[b for b in batches if b.get('context','resumed')=='resumed']
    baseline=next((b for b in resumed if b['reuse_percent']==0),None)
    out={'groups':groups,'batches':batches,
         'savings_against_zero':({b['reuse_percent']:savings(baseline,b) for b in resumed if b['reuse_percent']}
                                 if baseline else None),
         'complete':{(g['context'],g['reuse_percent']) for g in groups if g['passed_runs']}>=
                    {(c,p) for c in {g['context'] for g in groups} for p in (0,30,70,100)},'raw_report':args.report,
         'definition':'ten runtime jobs; fresh authoring seed and repair charged separately and included in total',
         'comparison_eligible':False,'limits':['fixed execution order within a repetition','owned fixture, not general application coverage',
         "resumed: agent jobs share one CLI-resumed conversation; fresh: one ephemeral session per agent job",'unknown serving model and billing parity',
         'single logical commit is not proof of one physical input','host-verified remaining segment, not automatic journal resume']}
    path=Path(args.output);path.write_text(json.dumps(out,indent=2));path.chmod(0o600)
    print(json.dumps(out,indent=2))

if __name__=='__main__':main()
