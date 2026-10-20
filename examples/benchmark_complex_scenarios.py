"""Owned 11-step form workflow: repeat, drift, captures, delays and expected stops."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tacto.flow import Flow
from examples.benchmark_batch import BatchClient, manifest as batch_manifest
from examples.repeat_benchmark import write


def recipe(target):
    def check(name,value):return {'field':{'identifier':'complex-'+name},'attribute':'Value','equals':value}
    def grouped(name,value,*others):return {**check(name,value),'all_of':list(others)}
    steps=[{'action':'set_value','selector':{'identifier':'complex-'+name},'params':{'value':'{{'+name+'}}'},
            'postcondition':check(name,'{{'+name+'}}')} for name in ('amount','factor','discount','reference')]
    steps += [
        {'action':'click','selector':{'identifier':'complex-bonus'},'skip_if':check('bonus','{{bonus}}'),'postcondition':check('bonus','{{bonus}}')},
        {'action':'click','selector':{'identifier':'complex-review'},'postcondition':grouped('stage','review',
            *(check(k,'{{'+k+'}}') for k in ('amount','factor','discount','reference','bonus')))},
        {'action':'click','selector':{'identifier':'complex-compute'},'postcondition':grouped('stage','computed',check('preview','{{result}}'))},
        {'action':'set_value','selector':{'identifier':'complex-audit-input'},'params':{'value':'{{result}}'},'postcondition':check('audit-input','{{result}}')},
        {'action':'click','selector':{'identifier':'complex-audit'},'postcondition':grouped('stage','audited',check('audit-input','{{result}}'),check('reference','{{reference}}'))},
        {'action':'click','selector':{'identifier':'complex-finish'},'postcondition':grouped('stage','done',check('preview','{{result}}'),check('commits','1'))},
        {'action':'read','selector':{'identifier':'complex-preview'},'postcondition':check('preview','{{result}}'),'answers':True,
         'safety':{'effects':['read']},'captures':[{'name':name,'field':{'identifier':'complex-'+field},'attribute':'Value'}
              for name,field in [('result','preview'),('status','stage'),('reference','reference')]]}
    ]
    return Flow.from_json(json.dumps({'name':'complex-workflow','target':target,'schema':3,
        'params':{'amount':'11','factor':'3','discount':'2','reference':'case / 한글 ✓','bonus':'1'},
        'derived':{'result':'{{amount}}*{{factor}}+5*{{bonus}}-{{discount}}'},
        'safety_defaults':{'effects':['write_ephemeral'],'data_classes':['none'],
            'resource':{'object_type':'owned disposable form','object_identity':'complex benchmark inputs','scope':'entry'},
            'destination':{'kind':'same_app'},'reversibility':'reversible','unknowns':[]},'steps':steps}))


def job(n,name):
    amount=10+n;factor=2+n%4;discount=n%3;bonus=n%2
    return {'job':name,'amount':str(amount),'factor':str(factor),'discount':str(discount),
            'reference':f'{name} / 한글 ✓','bonus':bool(bonus),'result':amount*factor+5*bonus-discount}


def params(values):
    return {k:str(int(v)) if k=='bonus' else v for k,v in values.items() if k not in ('job','result')}


class Fixture:
    def __init__(self,base,target):
        self.base=base/'fixture';self.base.mkdir(mode=0o700);self.sequence=0
        app=base/'TACTO Complex Benchmark.app';binary=app/'Contents/MacOS';binary.mkdir(parents=True,mode=0o700)
        with (app/'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier':target,'CFBundleName':'TACTO Complex Benchmark','CFBundleExecutable':'ComplexBenchmark',
                          'CFBundlePackageType':'APPL','LSUIElement':True},stream)
        subprocess.run(['swiftc','-parse-as-library',str(ROOT/'tests/fixtures/NativeComplexBenchmark.swift'),'-o',str(binary/'ComplexBenchmark')],check=True,capture_output=True)
        subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
        self.process=subprocess.Popen([str(binary/'ComplexBenchmark'),str(self.base)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    def state(self):return json.loads((self.base/'oracle.json').read_text())
    def setup(self,values,mode='normal',version=1):
        self.sequence+=1;write(self.base/'control.json',{'sequence':self.sequence,'job':values['job'],'mode':mode,'version':version})
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            try:
                state=self.state()
                if state['sequence']==self.sequence and state['phase']=='entry':return
            except (OSError,ValueError):pass
            if self.process.poll() is not None:raise RuntimeError('owned fixture exited')
            time.sleep(.05)
        raise TimeoutError('owned fixture setup')
    def oracle(self,values):
        state=self.state();actions=[a for a in state['actions'] if a['sequence']==self.sequence]
        commits=[c for c in state['commits'] if c['sequence']==self.sequence]
        expected={**values,'sequence':self.sequence}
        counts={name:sum(a['operation']==name for a in actions) for name in ('review','compute','audit','finish')}
        return {'passed':state['phase']=='done' and commits==[expected] and all(v==1 for v in counts.values()) and not state['active'],
                'state':state,'counts':counts,'commit_count':len(commits)}
    def close(self):
        if self.process.poll() is None:self.process.terminate()
        try:self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=3)


def manifest():
    result=batch_manifest()
    for name in ('examples/benchmark_complex_scenarios.py','tests/fixtures/NativeComplexBenchmark.swift'):
        result[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    base=Path(args.output).resolve();base.mkdir(mode=0o700);target='ai.callabo.tacto.complex.'+uuid.uuid4().hex[:8]
    fixture=Fixture(base,target);template=recipe(target);client=None;cases=[];before=manifest()
    def open_client(name,flow,maximum=1):
        directory=base/name;directory.mkdir(mode=0o700);path=directory/'flow.json'
        path.write_text(flow.to_json(compact=True));path.chmod(0o600)
        return BatchClient(path,directory,maximum=maximum,auto_repair=True,repair_packet=directory/'terminal-packet.json')
    def store(case):
        cases.append(case);write(base/'progress.json',{'cases':cases})
        print(json.dumps({'case':case['name'],'passed':case['passed']}),flush=True)
    try:
        client=open_client('repeat-and-double-drift',template,10);rows=[]
        for n in range(1,11):
            values=job(n,f'repeat-{n:02d}');fixture.setup(values,version=1)
            row=client.job(params(values));row.update(job=values,oracle=fixture.oracle(values));rows.append(row)
            write(base/'progress.json',{'cases':cases,'repeat_rows':rows})
            print(json.dumps({'job':n,'passed':row['oracle']['passed'],'model_requests':row['runtime_result']['model_requests']}),flush=True)
            if row['exit']!=0 or not row['oracle']['passed']:raise ValueError('complex repeat failed')
        ending=client.finish();lines=list(client.lines);client.close();client=None
        providers=[json.loads(s.removeprefix('TACTO_AUTO_REPAIR_PROVIDER ')) for s in lines if s.startswith('TACTO_AUTO_REPAIR_PROVIDER ')]
        store({'name':'eleven-step-repeat','rows':rows,'ending':ending,'provider_calls':providers,
            'passed':ending['completed']==10 and [r['runtime_result']['model_requests'] for r in rows]==[0]*10})
        for name,mode in [('double-drift-in-one-job','normal'),('delayed-transitions','delayed'),('captured-value-dataflow','normal')]:
            own=deepcopy(template)
            if name=='captured-value-dataflow':
                from tacto.flow import Capture,Selector
                own.steps[6].captures=[Capture(name='computed',field=Selector(identifier='complex-preview'))]
                own.steps[7].params['value']='{{computed}}'
            values=job(12,name);fixture.setup(values,mode=mode,version=2 if name=='double-drift-in-one-job' else 1)
            client=open_client(name,own);row=client.job(params(values));ending=client.finish();client.close();client=None
            oracle=fixture.oracle(values)
            store({'name':name,'result':row,'oracle':oracle,'passed':row['exit']==0 and oracle['passed'] and row['runtime_result']['model_requests']==(2 if name=='double-drift-in-one-job' else 0)})
        for name,mode,version in [('repair-budget-exhausted','triple-drift',2),('ambiguous-selector','ambiguous',1),
                                  ('unknown-completion','unknown-finish',1),('capture-failure-after-completion','normal',1),
                                  ('capture-dependent-repair-refused','normal',2)]:
            own=deepcopy(template)
            if name=='ambiguous-selector':own.steps[8].selector.identifier='complex-audit'
            if name=='capture-failure-after-completion':own.steps[-1].captures[0].field.identifier='missing-result'
            if name=='capture-dependent-repair-refused':
                from tacto.flow import Capture,Selector
                own.steps[5].captures=[Capture(name='checkpoint',field=Selector(identifier='complex-stage'))]
            values=job(13,name);fixture.setup(values,mode=mode,version=version)
            client=open_client(name,own,2)
            request=(json.dumps({'params':params(values)})+'\n').encode()
            client.process.stdin.write(request*2);client.process.stdin.flush()
            result=client.marker('TACTO_BATCH_RESULT ',timeout=180);ending=client.finish();lines=list(client.lines)
            oracle=fixture.oracle(values);client.close();client=None
            calls=result['model_requests'];counts=oracle['counts'];commits=oracle['commit_count']
            conditions=(result['exit']==1 and ending['completed']==0 and not oracle['state']['active'])
            if name=='repair-budget-exhausted':conditions &= calls==2 and commits==0 and counts=={'review':1,'compute':1,'audit':1,'finish':0}
            elif name=='ambiguous-selector':conditions &= calls<=1 and commits==0 and counts['audit']==0 and counts['finish']==0
            elif name in ('unknown-completion','capture-failure-after-completion'):conditions &= calls==0 and commits==1 and all(v==1 for v in counts.values())
            else:conditions &= calls==0 and commits==0 and counts=={'review':1,'compute':0,'audit':0,'finish':0}
            store({'name':name,'expected':'stop without queued replay','result':result,'ending':ending,'oracle':oracle,
                'stop_markers':[s for s in lines if s.startswith('TACTO_AUTO_REPAIR_STOP ')],'passed':bool(conditions)})
        after=manifest();passed=len(cases)==9 and all(c['passed'] for c in cases) and before==after
        write(base/'report.json',{'passed':passed,'cases':cases,'source_before':before,'source_after':after,'source_stable':before==after,
            'scope':'11-step owned native form, 10 repeats plus 8 additional completion/expected-stop cases',
            'limits':['one controlled app, not multiple real apps','host authored template, authoring tokens excluded',
                      'model usage belongs only to real repair calls','expected stops are not completed business tasks','not a Sky comparison']})
        if not passed:print('Some complex scenarios did not meet completion/coverage expectations; report preserved',flush=True)
    finally:
        if client is not None:client.close()
        fixture.close()
    print(str(base/'report.json'),flush=True)

if __name__=='__main__':main()
