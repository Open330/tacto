"""Owned 10-job batch drift/remaining-step benchmark; deterministic repair only."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tacto.flow import Flow
from tacto.repair_packet import patch_selector
from examples.benchmark_batch import BatchClient, manifest
from examples.benchmark_grouped_checks import variants
from examples.repeat_benchmark import Fixture, build, cli_flow, jobs, write


def sources():
    result=manifest()
    name='examples/benchmark_batch_repair.py'
    result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();base=Path(args.output).resolve();base.mkdir(mode=0o700)
    target='ai.callabo.nlc.repeatbench.'+uuid.uuid4().hex[:8]
    fixture=Fixture(base/'fixture',build(base,target));client=None;rows=[]
    recipe=variants(Flow.from_json(Path(args.source).read_text()),target)['grouped_checks']
    template=base/'template.json';template.write_text(recipe.to_json(compact=True));template.chmod(0o600)
    size={'expanded_bytes':len(recipe.to_json().encode()),'compact_bytes':template.stat().st_size}
    size['reduction_percent']=100*(1-size['compact_bytes']/size['expanded_bytes'])
    before=sources()
    try:
        directory=base/'batch-first';directory.mkdir(mode=0o700)
        packet=base/'repair-packet.json';client=BatchClient(template,directory,repair_packet=packet)
        for n,job in enumerate(jobs(),1):
            fixture.setup(job,changed=n>=7)
            params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
            row=client.job(params)
            if n==7:
                stopped=fixture.state();ending=client.finish();client.close();client=None
                if (row['exit']!=1 or row['runtime_result'].get('repair_packet')!=str(packet)
                        or ending['completed']!=6 or stopped['phase']!='review'
                        or stopped['amount']!=job['amount'] or stopped['factor']!=job['factor']
                        or stopped['bonus']!=job['bonus'] or any(c['job']==job['job'] for c in stopped['commits'])):
                    raise ValueError('undispatched checkpoint mismatch')
                selector={'role':'Button','identifier':'bench-compute-v2'}
                full=base/'patched-template.json';remaining=base/'remaining-template.json'
                patch_selector(template,packet,selector,full)
                patch_selector(template,packet,selector,remaining,remaining=True)
                if fixture.state()!=stopped:raise ValueError('offline patch changed state')
                segment=Flow.from_json(remaining.read_text()).bind(**params)
                continuation=cli_flow(segment,base/'bound-remaining.json',base/'continuation')
                row['continuation']=continuation;row['stopped']=stopped;row['ending']=ending
                row['remaining_step_count']=len(segment.steps)
                if continuation['exit']!=0:raise ValueError('remaining steps failed')
                directory=base/'batch-next';directory.mkdir(mode=0o700)
                client=BatchClient(full,directory,maximum=3)
            row['job']=job;row['oracle']=fixture.oracle(job);rows.append(row)
            write(base/'progress.json',{'rows':rows,'serialization':size})
            print(json.dumps({'job':n,'passed':row['oracle']['passed'],'repaired':n==7}),flush=True)
            if not row['oracle']['passed'] or not row['oracle']['single_commit'] or row['oracle']['active']:
                raise ValueError('independent completion oracle failed')
        ending=client.finish();client.close();client=None
        after=sources()
        passed=len(rows)==10 and ending['exit']==0 and ending['completed']==3 and before==after
        write(base/'report.json',{'passed':passed,'rows':rows,'serialization':size,
            'packet_bytes':packet.stat().st_size,'source_before':before,'source_after':after,
            'source_stable':before==after,'model_requests':0,
            'scope':'ten owned-fixture jobs; selector drift at job7; independent checkpoint and completion oracle',
            'limits':['repair selector predetermined by fixture; no agent-token comparison',
                'continuation explicitly gated by fixture checkpoint; no automatic resume']})
        if not passed:raise ValueError('benchmark incomplete')
    finally:
        if client is not None:client.close()
        fixture.close()
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
