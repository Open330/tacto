"""Ten paired signed CLI jobs vs one bounded batch; no model calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tacto.flow import Flow
from tacto.provider_io import require_provider_child_ownership
from examples.repeat_benchmark import Fixture, build, cli_flow, jobs, source_manifest, write
from examples.benchmark_grouped_checks import variants


class BatchClient:
    def __init__(self,template,base,maximum=10,repair_packet=None,auto_repair=False,extra_flags=()):
        self.base=base;self.buffer=bytearray();self.lines=[]
        self.job_timeout=180 if auto_repair else 20
        stderr=base/'stderr.txt';self.stderr=stderr.open('w');stderr.chmod(0o600)
        require_provider_child_ownership();self.started=time.monotonic()
        command=[str(Path.home()/'.local/bin/tacto'),'batch',str(template),
            '--helper','--max-jobs',str(maximum),'--timings']
        if repair_packet is not None:command.extend(['--repair-packet',str(repair_packet)])
        if auto_repair:command.extend(['--auto-repair','codex'])
        command.extend(extra_flags)
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=self.stderr,start_new_session=True)
        try:self.marker('TACTO_BATCH_READY ',timeout=20)
        except Exception:
            self.close();raise
        self.startup_seconds=time.monotonic()-self.started
    def marker(self,prefix,timeout=20):
        deadline=time.monotonic()+timeout
        while True:
            if b'\n' in self.buffer:
                line,tail=self.buffer.split(b'\n',1);self.buffer=bytearray(tail)
                line=line.decode();self.lines.append(line)
                if line.startswith(prefix):return json.loads(line[len(prefix):])
                continue
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self.process.stdout],[],[],remaining)[0]:raise TimeoutError('batch marker')
            chunk=os.read(self.process.stdout.fileno(),65536)
            if not chunk:raise RuntimeError('batch ended before expected marker')
            self.buffer.extend(chunk)
    def job(self,params):
        started=time.monotonic()
        self.process.stdin.write((json.dumps({'params':params},separators=(',',':'))+'\n').encode());self.process.stdin.flush()
        result=self.marker('TACTO_BATCH_RESULT ',timeout=self.job_timeout)
        return {'seconds':time.monotonic()-started,'runtime_result':result,'exit':result['exit']}
    def finish(self):
        started=time.monotonic();result=self.marker('TACTO_BATCH_DONE ')
        code=self.process.wait(timeout=10);seconds=time.monotonic()-started
        return {'seconds':seconds,'exit':code,'completed':result['completed']}
    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.terminate();self.process.wait(timeout=5)
        if not self.process.stdin.closed:self.process.stdin.close()
        self.process.stdout.close();self.stderr.close()
        path=self.base/'stdout.txt';path.write_text('\n'.join(self.lines)+'\n');path.chmod(0o600)


def manifest():
    result=source_manifest()
    for name in ('examples/benchmark_batch.py','examples/benchmark_grouped_checks.py'):
        result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--source',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();base=Path(args.output).resolve();base.mkdir(mode=0o700)
    target='ai.callabo.nlc.repeatbench.'+uuid.uuid4().hex[:8]
    app=build(base,target);fixture=Fixture(base/'fixture',app)
    recipe=variants(Flow.from_json(Path(args.source).read_text()),target)['grouped_checks']
    template=base/'template.json';template.write_text(recipe.to_json());template.chmod(0o600)
    batchdir=base/'batch';batchdir.mkdir(mode=0o700);client=None;rows=[];shutdown=None;before=manifest()
    try:
        client=BatchClient(template,batchdir)
        for n,job in enumerate(jobs(),1):
            params={k:('1' if v else '0') if k=='bonus' else v for k,v in job.items() if k!='job'}
            for name in (('cli','batch') if n%2 else ('batch','cli')):
                own=dict(job,job=name+'-'+job['job']);fixture.setup(own)
                row=(cli_flow(recipe.bind(**params),base/'bound.json',base/f'cli-{n:02d}') if name=='cli' else client.job(params))
                row.update(variant=name,job=own,oracle=fixture.oracle(own));rows.append(row)
                write(base/'progress.json',{'rows':rows})
                print(json.dumps({'variant':name,'job':n,'passed':row['oracle']['passed'],'seconds':row['seconds']}),flush=True)
                if row['exit']!=0 or not row['oracle']['passed']:raise ValueError('paired job failed')
                if name=='batch' and n==10:shutdown=client.finish()
        startup=client.startup_seconds;client.close();client=None
        if shutdown['exit']!=0 or shutdown['completed']!=10:raise ValueError('batch incomplete')
        totals={name:sum(r['seconds'] for r in rows if r['variant']==name) for name in ('cli','batch')}
        totals['batch']+=startup+shutdown['seconds']
        # Unknown verification after Finish stops before the next buffered job.
        own=dict(jobs()[0],job='negative-batch');fixture.setup(own)
        failed=recipe.bind(**{k:('1' if v else '0') if k=='bonus' else v for k,v in jobs()[0].items() if k!='job'})
        failed.steps[-1].postcondition.all_of[0].equals='not-done'
        badtemplate=base/'negative-template.json';badtemplate.write_text(failed.to_json());badtemplate.chmod(0o600)
        negdir=base/'negative';negdir.mkdir(mode=0o700)
        client=BatchClient(badtemplate,negdir,maximum=2)
        client.process.stdin.write(b'{"params":{}}\n{"params":{}}\n');client.process.stdin.flush()
        first=client.marker('TACTO_BATCH_RESULT ');ending=client.finish();state=fixture.state()
        negative_passed=first['job']==1 and first['exit']==1 and ending['exit']==1 and ending['completed']==0 and state['phase']=='done' and len([c for c in state['commits'] if c['job']==own['job']])==1
        client.close();client=None
        write(base/'report.json',{'rows':rows,'seconds':totals,'batch_startup_seconds':startup,'batch_shutdown':shutdown,
            'saving_percent':100*(1-totals['batch']/totals['cli']),'negative':{'passed':negative_passed,'first':first,'ending':ending,'state':state},
            'source_before':before,'source_after':manifest(),'source_stable':before==manifest(),
            'passed':len(rows)==20 and all(r['oracle']['passed'] and r['oracle']['single_commit'] for r in rows) and negative_passed,
            'model_requests':0,'scope':'one fixed six-step Flow, ten paired jobs; alternating order',
            'timing_scope':'CLI full invocation vs batch startup+request/result round trips+shutdown; fixture prep and idle interleaved CLI jobs excluded',
            'limits':['one owned-fixture batch','not author/repair cost or model-token comparison','no automatic resume or exactly-once guarantee']})
    finally:
        if client is not None:client.close()
        fixture.close()
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
