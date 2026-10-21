"""Summarize completed batches only; preserve unknowns and all repair costs."""
import argparse
import json
from pathlib import Path

FIELDS=('total_input_tokens','new_input_tokens','cache_read_tokens','output_tokens')

def measured_batch(result,*,unknown_usage=False):
    """`unknown_usage` keeps a verified batch with missing token counts, as None."""
    rows=result.get('rows',[])
    if not result.get('passed') or len(rows)!=10 or not all(r['oracle']['passed'] and r['oracle']['single_commit'] for r in rows):
        raise ValueError('ten independently verified jobs required')
    usage=result['usage']
    complete=not usage.get('unknown_usage_records') and all(type(usage.get(k)) is int for k in FIELDS)
    if not complete and not unknown_usage:raise ValueError('complete usage required')
    new=usage['total_input_tokens']-usage['cache_read_tokens'] if complete else None
    if new is not None and new<0:raise ValueError('cache read exceeds total input')
    seconds=sum(r['seconds']+r.get('recovery',{}).get('seconds',0) for r in rows)
    # Authoring is already row1; subsequent agent runs are recovery.
    if result.get('kind')=='mixed-reuse':
        # Agent jobs are already in rows. Seed and isolated repairs are additional.
        seconds+=sum(r['seconds'] for r in result.get('agent_runs',[]) if not r.get('job'))
    else:
        seconds+=sum(r['seconds'] for r in result.get('agent_runs',[])[1:])
    return {'arm':result['arm'],'jobs':10,'passed':True,'source_stable':result.get('source_stable'),
            'agent_invocations':usage['agent_invocations'],'seconds':seconds,
            **{k:usage.get(k) if complete else None for k in FIELDS},'reported_new_input_tokens':usage.get('new_input_tokens') if complete else None,
            'cache_creation_tokens':usage.get('cache_creation_tokens'),
            'new_input_tokens':new,'new_input_basis':'total_input_tokens - cache_read_tokens'}

def savings(baseline,reused):
    keys=(*FIELDS,'seconds')
    return {k:None if baseline[k]<=0 else 100*(1-reused[k]/baseline[k]) for k in keys}

def main():
    p=argparse.ArgumentParser();p.add_argument('reports',nargs='+');p.add_argument('--output',required=True)
    args=p.parse_args();batches=[]
    for filename in args.reports:
        for r in json.loads(Path(filename).read_text())['results']:
            if r.get('passed'):batches.append(measured_batch(r))
    reused=next(r for r in batches if r['arm']=='codex-tacto-reuse')
    result={'schema':1,'batches':batches,'savings_against':{r['arm']:savings(r,reused)
            for r in batches if r['arm']!=reused['arm']},'raw_reports':args.reports,
            'comparison_eligible':False,'billing_verified':False,
            'scope':'one owned-fixture batch per arm; warm CLI-resumed agent workflows, not a persistent or scripted Sky workflow',
            'benchmark_development_and_preflight_cost_included':False}
    path=Path(args.output);path.write_text(json.dumps(result,indent=2));path.chmod(0o600)
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
