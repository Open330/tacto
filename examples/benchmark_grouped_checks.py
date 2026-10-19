"""Paired owned-fixture comparison; fixed saved Flow, no author/model calls.

This transformation is specific to the known fixture, not a generic read eraser.
All checkpoint assertions remain; grouped checks require one consistent snapshot.
"""
import argparse
from copy import deepcopy
import json
import hashlib
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tacto.flow import Flow
from examples.repeat_benchmark import Fixture, build, cli_flow, jobs, source_manifest, write


def variants(template,target):
    baseline=deepcopy(template);baseline.target=target
    expected=['set_value','set_value','click','click','read','read','read','click','click','read']
    if [s.action for s in baseline.steps]!=expected:raise ValueError('known fixture ten-step source required')
    identities=['bench-amount','bench-factor','bench-bonus','bench-review','bench-amount',
                'bench-factor','bench-bonus','bench-compute','bench-finish','bench-preview']
    if [s.selector.identifier for s in baseline.steps]!=identities:raise ValueError('known fixture selectors required')
    setter=deepcopy(baseline)
    for i,read_i in ((0,4),(1,5)):
        if setter.steps[i].postcondition is not None:raise ValueError('expected unconditioned source inputs')
        setter.steps[i].postcondition=deepcopy(setter.steps[read_i].postcondition)
    grouped=deepcopy(setter);grouped.schema=3
    grouped.steps[3].postcondition.all_of.extend(deepcopy(grouped.steps[i].postcondition) for i in (4,5,6))
    done=grouped.steps[8].postcondition
    grouped.steps[8].postcondition=deepcopy(grouped.steps[9].postcondition)
    grouped.steps[8].postcondition.all_of.append(done)
    grouped.steps[8].answers=grouped.steps[9].answers
    grouped.steps=[grouped.steps[i] for i in (0,1,2,3,7,8)]
    return {'baseline':baseline,'setter_checks':setter,'grouped_checks':grouped}


def benchmark_manifest():
    manifest=source_manifest()
    manifest['examples/benchmark_grouped_checks.py']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return manifest


def timing(row):
    lines=Path(row['trace']).with_name('stderr.txt').read_text().splitlines()
    records=[json.loads(line[len('TACTO_TIMINGS '):]) for line in lines if line.startswith('TACTO_TIMINGS ')]
    if len(records)!=1:raise ValueError('one runtime timing record required')
    return records[0]


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();base=Path(args.output).resolve();base.mkdir(mode=0o700)
    target='ai.callabo.nlc.repeatbench.'+uuid.uuid4().hex[:8]
    app=build(base,target);fixture=Fixture(base/'fixture',app)
    flows=variants(Flow.from_json(Path(args.source).read_text()),target)
    before=benchmark_manifest();rows=[];negative=[];names=list(flows)
    try:
        for n,job in enumerate(jobs()):
            order=names[n%3:]+names[:n%3]
            for name in order:
                own=dict(job,job=name+'-'+job['job']);fixture.setup(own)
                params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
                row=cli_flow(flows[name].bind(**params),base/'bound.json',base/f'{n+1:02d}-{name}')
                row.update(variant=name,job=own,oracle=fixture.oracle(own));row['timing']=timing(row);rows.append(row)
                write(base/'progress.json',{'rows':rows})
                print(json.dumps({'variant':name,'job':n+1,'passed':row['oracle']['passed'],'seconds':row['seconds']}),flush=True)
                if row['exit']!=0 or not row['oracle']['passed']:raise ValueError('paired task failed')
        # Secondary assertion after Review must stop before Compute/Finish.
        for phase in ('review','finish'):
            job=dict(jobs()[0],job='negative-'+phase);fixture.setup(job)
            params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
            flow=flows['grouped_checks'].bind(**params)
            if phase=='review':flow.steps[3].postcondition.all_of[1].equals='unexpected-factor'
            else:flow.steps[-1].postcondition.all_of[0].equals='not-done'
            row=cli_flow(flow,base/'bound.json',base/('negative-'+phase));state=fixture.state()
            commits=[c for c in state['commits'] if c['job']==job['job']]
            expected_phase='review' if phase=='review' else 'done'
            expected_commits=0 if phase=='review' else 1
            passed=row['exit']==1 and state['phase']==expected_phase and len(commits)==expected_commits
            row.update(negative=phase,passed=passed,state=state);negative.append(row)
            if not passed:raise ValueError('failed conjunction did not stop as expected')
        summary=[]
        for name,flow in flows.items():
            selected=[r for r in rows if r['variant']==name]
            counts={method:sum(r['timing']['calls'].get(method,{}).get('count',0) for r in selected)
                    for method in ('observe','wait_until','act')}
            summary.append({'variant':name,'steps':len(flow.steps),'jobs':len(selected),
                            'seconds':sum(r['seconds'] for r in selected),'model_requests':0,'calls':counts,
                            'passed':all(r['oracle']['passed'] and r['oracle']['single_commit'] for r in selected)})
        write(base/'report.json',{'summary':summary,'rows':rows,'negative_tests':negative,
            'source_before':before,'source_after':benchmark_manifest(),'source_stable':before==benchmark_manifest(),
            'passed':len(rows)==30 and all(r['oracle']['passed'] for r in rows) and all(r['passed'] for r in negative),
            'scope':'fixed authored template; cyclic order per job, three variants, ten jobs each; no authoring/repair/model cost',
            'limits':['one paired batch, owned fixture only','all_of strengthens verification to a simultaneous snapshot',
                      'not a generic semantics-preserving optimizer for arbitrary read steps',
                      'logical commit counts do not prove exactly one physical input']})
    finally:fixture.close()
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
