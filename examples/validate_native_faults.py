from pathlib import Path
import argparse
from release_identity import identity
import json,subprocess,time,threading,uuid,hashlib
parser=argparse.ArgumentParser(description='Actual owned-window and Finder failure/recovery checks; no models or permission changes.')
parser.add_argument('--launcher', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--document', type=Path, required=True, help='existing owned TextEdit document, read only')
args=parser.parse_args()
out=args.output.resolve();out.mkdir(mode=0o700)
cli=args.launcher.resolve(strict=True);rows=[];before=identity(cli)
existing=args.document.resolve(strict=True)
def write(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2));p.chmod(0o600)
def run(app,steps,name,expect=0):
 p=out/(name+'.json');trace=out/(name+'-trace.json');write(p,{'schema':2,'name':name,'target':app,'steps':steps})
 r=subprocess.run([str(cli),'run',str(p),'--helper','--think','0','--yes','--no-indicator','--trace',str(trace)],capture_output=True,text=True,timeout=40)
 (out/(name+'.out')).write_text(r.stdout+r.stderr)
 acts=[e for e in json.loads(trace.read_text())['events'] if e.get('kind')=='act'] if trace.exists() else []
 return r,acts
safety={'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'owned release fixture','scope':'entry'}}
read={'effects':['read'],'reversibility':'reversible','resource':{'object_type':'owned release fixture','scope':'entry'}}
def check(field,value,attr='Title'):return {'field':field,'attribute':attr,'equals':value}
def openstep(folder):return {'action':'open_folder','params':{'path':str(folder)},'postcondition':check({'role':'Window','title':folder.name},folder.name),'safety':safety}
def observe(app):
 r=subprocess.run([str(cli),'observe',app,'--helper','--all-windows'],capture_output=True,text=True,timeout=30);assert r.returncode==0;return r.stdout
def record(scenario,i,passed,**extra):
 rows.append({'scenario':scenario,'repetition':i,'passed':passed,**extra});write(out/'progress.json',{'rows':rows});print(json.dumps(rows[-1]),flush=True)
 if not passed:raise RuntimeError(scenario+' failed; inspect owned artifacts')
try:
 for i in range(3):
  name='Tacto-closed-'+uuid.uuid4().hex[:8]+'.txt';doc=out/name;doc.write_text('');doc.chmod(0o600)
  subprocess.run(['open','-g','-a','TextEdit',str(doc)],capture_output=True,check=True)
  deadline=time.monotonic()+10
  while str(doc) not in observe('com.apple.TextEdit'):
   if time.monotonic()>deadline:raise RuntimeError('owned bootstrap timeout')
  close={'action':'click','selector':{'role':'Button','subrole':'AXCloseButton','ancestor':str(doc)},'postcondition':{'absent':str(doc)},'safety':safety}
  r,acts=run('com.apple.TextEdit',[close],f'close-{i}');assert r.returncode==0 and len(acts)==1
  missing={'action':'set_value','selector':{'role':'TextArea','identifier':'First Text View','ancestor':str(doc)},'params':{'value':'MUST NOT SEND'},'postcondition':{'contains':'MUST NOT SEND'},'safety':safety}
  r,acts=run('com.apple.TextEdit',[missing],f'closed-refusal-{i}')
  safe=r.returncode==1 and 'target_missing' in r.stdout and not acts and doc.read_text()==''
  subprocess.run(['open','-g','-a','TextEdit',str(doc)],capture_output=True,check=True)
  good={'action':'read','selector':{'role':'TextArea','identifier':'First Text View','ancestor':str(doc)},'postcondition':check({'role':'Window','title':name},name),'safety':read}
  r2,_=run('com.apple.TextEdit',[good],f'closed-resume-{i}')
  record('owned_window_closed_and_reopened',i,safe and r2.returncode==0,refused_actions=len(acts),recovery='fresh observation after reopening exact owned file')
  r,_=run('com.apple.TextEdit',[close],f'close-cleanup-{i}');assert r.returncode==0
 for i in range(3):
  doc=out/('Tacto-delayed-'+uuid.uuid4().hex[:8]+'.txt');doc.write_text('');doc.chmod(0o600)
  fired=[]
  def later():
   p=subprocess.run(['open','-g','-a','TextEdit',str(doc)],capture_output=True);fired.append(p.returncode)
  timer=threading.Timer(1.2,later);timer.start()
  step={'action':'read','selector':{'role':'TextArea','identifier':'First Text View','ancestor':str(existing)},'postcondition':check({'role':'Window','title':doc.name},doc.name),'safety':read}
  started=time.monotonic();r,acts=run('com.apple.TextEdit',[step],f'delayed-{i}');elapsed=time.monotonic()-started;timer.join()
  record('delayed_owned_window',i,r.returncode==0 and fired==[0] and all(e['request']['action']['name']=='read' for e in acts) and str(doc) in observe('com.apple.TextEdit'),seconds=elapsed,mutating_inputs=sum(e['request']['action']['name']!='read' for e in acts))
  close={'action':'click','selector':{'role':'Button','subrole':'AXCloseButton','ancestor':str(doc)},'postcondition':{'absent':str(doc)},'safety':safety}
  cleanup,_=run('com.apple.TextEdit',[close],f'delayed-cleanup-{i}');assert cleanup.returncode==0
 for i in range(3):
  folder=out/('Tacto-target-'+uuid.uuid4().hex[:8]);folder.mkdir();old=folder/'before.txt';old.write_text('canary');new=folder/'after.txt'
  trap=out/('Tacto-trap-'+uuid.uuid4().hex[:8]);trap.mkdir()
  r,_=run('com.apple.finder',[openstep(folder)],f'target-setup-{i}');assert r.returncode==0
  old.rename(new)
  deadline=time.monotonic()+10
  while 'before.txt' in observe('com.apple.finder'):
   if time.monotonic()>deadline:raise RuntimeError('Finder did not observe rename')
   time.sleep(.1)
  stale={'action':'click','selector':{'role':'TextField','value':'before.txt','ancestor':folder.name,'click_ancestor':'Row'},'postcondition':{'contains':'before.txt'},'safety':safety}
  r,acts=run('com.apple.finder',[stale,openstep(trap)],f'target-refusal-{i}')
  safe=r.returncode==1 and 'target_missing' in r.stdout and not acts and new.read_text()=='canary' and trap.name not in observe('com.apple.finder')
  field={'role':'TextField','value':'after.txt','ancestor':folder.name,'click_ancestor':'Row'}
  r2,_=run('com.apple.finder',[{'action':'click','selector':field,'postcondition':{'field':field,'contains':'[selected]'},'safety':safety}],f'target-repair-{i}')
  record('target_renamed_then_reobserved',i,safe and r2.returncode==0,refused_actions=len(acts),recovery='new selector after fresh observed filename')
 for i in range(3):
  folder=out/('Tacto-partial-'+uuid.uuid4().hex[:8]);folder.mkdir();trap=out/('Tacto-unrun-'+uuid.uuid4().hex[:8]);trap.mkdir()
  stale={'action':'click','selector':{'role':'TextField','value':'does-not-exist.txt','ancestor':folder.name},'postcondition':{'contains':'UNREACHABLE'},'safety':safety}
  r,acts=run('com.apple.finder',[openstep(folder),stale,openstep(trap)],f'partial-{i}')
  safe=r.returncode==1 and 'target_missing' in r.stdout and len(acts)==1 and acts[0]['request']['action']['name']=='open_folder' and trap.name not in observe('com.apple.finder')
  r2,_=run('com.apple.finder',[openstep(trap)],f'partial-resume-{i}')
  record('flow_stops_after_failed_step',i,safe and r2.returncode==0,completed_prefix_actions=len(acts),recovery='explicit new flow; completed prefix not resent')
finally:
 write(out/'report.json',{'passed':len(rows)==12 and all(r['passed'] for r in rows) and before==identity(cli),'candidate_identity':before,'identity_unchanged':before==identity(cli),'rows':rows,'model_requests':0,'permission_revocation_tested':False,'scope':'actual signed helper; owned physical windows closed, delayed fixture open, real Finder rename and partial flow failure; explicit host-prescribed recovery; no permission changes','helper_sha256':hashlib.sha256((cli.parent.parent/'helper/NLC Helper.app/Contents/MacOS/NLCHelper').read_bytes()).hexdigest()})

if len(rows)!=12 or not all(row["passed"] for row in rows):raise SystemExit(1)
