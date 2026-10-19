"""Real provider intervention on owned-fixture drift, with checked automatic resume."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tacto.flow import Flow
from examples.benchmark_batch import BatchClient, manifest
from examples.benchmark_grouped_checks import variants
from examples.repeat_benchmark import Fixture, build, jobs, write


def sources():
    result=manifest();name='examples/benchmark_auto_repair.py'
    result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest();return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();base=Path(args.output).resolve();base.mkdir(mode=0o700)
    target='ai.callabo.nlc.repeatbench.'+uuid.uuid4().hex[:8]
    fixture=Fixture(base/'fixture',build(base,target));client=None;rows=[]
    recipe=variants(Flow.from_json(Path(args.source).read_text()),target)['grouped_checks']
    template=base/'template.json';template.write_text(recipe.to_json(compact=True));template.chmod(0o600)
    before=sources()
    try:
        directory=base/'batch';directory.mkdir(mode=0o700)
        client=BatchClient(template,directory,auto_repair=True)
        for n,job in enumerate(jobs(),1):
            fixture.setup(job,changed=n>=7)
            params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
            row=client.job(params);row['job']=job;row['oracle']=fixture.oracle(job);rows.append(row)
            write(base/'progress.json',{'rows':rows})
            print(json.dumps({'job':n,'passed':row['oracle']['passed'],'model_requests':row['runtime_result']['model_requests']}),flush=True)
            if row['exit']!=0 or not row['oracle']['passed'] or not row['oracle']['single_commit'] or row['oracle']['active']:
                raise ValueError('automatic recovery job failed')
        ending=client.finish();lines=list(client.lines);startup=client.startup_seconds;client.close();client=None
        providers=[json.loads(line.removeprefix('TACTO_AUTO_REPAIR_PROVIDER ')) for line in lines if line.startswith('TACTO_AUTO_REPAIR_PROVIDER ')]
        resumes=[json.loads(line.removeprefix('TACTO_AUTO_REPAIR_RESUME ')) for line in lines if line.startswith('TACTO_AUTO_REPAIR_RESUME ')]
        # Completion ambiguity must stop before the next queued task, with no model retry.
        negative=Flow.from_json(template.read_text());negative.steps[-1].postcondition.all_of[0].equals='not-done'
        path=base/'negative.json';path.write_text(negative.to_json(compact=True));path.chmod(0o600)
        directory=base/'negative';directory.mkdir(mode=0o700)
        job=dict(jobs()[0],job='negative-unknown');fixture.setup(job)
        params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
        client=BatchClient(path,directory,maximum=2,auto_repair=True)
        request=(json.dumps({'params':params})+'\n').encode()
        client.process.stdin.write(request*2);client.process.stdin.flush()
        failure=client.marker('TACTO_BATCH_RESULT ');stopped=client.finish();state=fixture.state()
        neg_pass=(failure['exit']==1 and failure['model_requests']==0 and stopped['completed']==0
                  and state['phase']=='done' and len([c for c in state['commits'] if c['job']==job['job']])==1)
        client.close();client=None
        after=sources();model_counts=[r['runtime_result']['model_requests'] for r in rows]
        passed=(len(rows)==10 and ending['completed']==10 and ending['exit']==0
                and model_counts==[0]*6+[1]+[0]*3 and len(providers)==1 and providers[0]['status']=='ok'
                and len(resumes)==1 and resumes[0]['remaining_steps']==2 and neg_pass and before==after)
        write(base/'report.json',{'passed':passed,'rows':rows,'batch_ending':ending,
            'active_seconds':startup+sum(r['seconds'] for r in rows)+ending['seconds'],
            'provider_calls':providers,'resumes':resumes,'model_requests':sum(model_counts),
            'negative':{'passed':neg_pass,'result':failure,'ending':stopped,'state':state},
            'source_before':before,'source_after':after,'source_stable':before==after,
            'scope':'ten owned fixture jobs; real Codex candidate selection and automatic checked suffix replay at job7',
            'limits':['one batch and one selector drift','not a Sky comparison','automatic unknown-effect retry intentionally unsupported']})
        if not passed:raise ValueError('benchmark conditions failed')
    finally:
        if client is not None:client.close()
        fixture.close()
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
