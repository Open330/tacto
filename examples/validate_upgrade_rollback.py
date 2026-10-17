from pathlib import Path
import argparse
import uuid
from release_identity import identity
import hashlib,json,os,subprocess,time
parser=argparse.ArgumentParser(description='Exercise real installed command/skill links through upgrade and rollback; existing grants are read only.')
parser.add_argument('--candidate',type=Path,required=True,help='signed standalone Tacto.app')
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
out=args.output.resolve();out.mkdir(mode=0o700)
old=(Path.home()/'.local/bin/tacto').resolve(strict=True).parent.parent
new=args.candidate.resolve(strict=True)/'Contents/Resources/runtime'
links=[Path.home()/'.local/bin/tacto',Path.home()/'.local/bin/nlc',Path.home()/'.codex/skills/tacto',Path.home()/'.claude/skills/tacto']
def link_state():return {str(p):os.readlink(p) if p.is_symlink() else None for p in links}
def manifest():
 result={}
 roots=[Path.home()/'.local/share/nlc/flows',Path.home()/'.local/share/tacto/flows',Path.home()/'.config/tacto',Path.home()/'.config/nlc']
 configured=os.environ.get('XDG_DATA_HOME','')
 if configured and Path(configured).is_absolute():roots.append(Path(configured)/'nlc/flows')
 for root in roots:
  if root.is_dir():
   for p in root.rglob('*'):
    if p.is_file() and not p.is_symlink():result[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
 return result
assert old != new, 'distinct baseline and candidate required'
for root in (old,new):
 assert root.name=='runtime' and root.parent.name=='Resources' and root.parents[1].name=='Contents' and root.parents[2].suffix=='.app', 'standalone bundle layout required'
original=link_state(); before=manifest(); rows=[]
# Create one exclusively owned compatibility artifact in the real default store; remove only if unchanged.
flowdir=Path.home()/'.local/share/nlc/flows';flowdir.mkdir(parents=True,exist_ok=True,mode=0o700)
flow={'schema':2,'name':'release_compat_'+uuid.uuid4().hex[:12],'target':'com.apple.finder','steps':[{'action':'read','selector':{'role':'Window','title':'no-action-owned-probe'}}]}
p=flowdir/(flow['name']+'.json')
with p.open('x') as owned: owned.write(json.dumps(flow))
p.chmod(0o600)
digest=hashlib.sha256(p.read_bytes()).hexdigest()
def call(args,label,extra=None):
 r=subprocess.run(args,capture_output=True,text=True,timeout=45,env=extra)
 (out/(label+'.log')).write_text(r.stdout+r.stderr)
 if r.returncode:raise RuntimeError(label+' failed')
 return r.stdout
call(['/bin/bash',str(old/'skill/install.sh'),'--check'],'initial-check')
completed=False
reference=None
try:
 for n,(stage,root) in enumerate([('old',old),('upgrade',new),('rollback',old),('restore-old',old)]):
  started=time.monotonic();call(['/bin/bash',str(root/'skill/install.sh')],stage+'-install')
  call(['/bin/bash',str(root/'skill/install.sh'),'--check'],stage+'-check')
  doctor=call([str(Path.home()/'.local/bin/tacto'),'doctor','--check'],stage+'-doctor')
  shown=call([str(Path.home()/'.local/bin/tacto'),'show',flow['name']],stage+'-show')
  decoded=json.loads(shown)
  assert decoded['name']==flow['name'] and decoded['target']==flow['target']
  if reference is None:reference=decoded
  assert decoded==reference
  assert hashlib.sha256(p.read_bytes()).hexdigest()==digest
  assert {k:v for k,v in manifest().items() if k!=str(p)}==before
  app=root.parents[2]
  call(['codesign','--verify','--deep','--strict',str(app)],stage+'-signature')
  assert all((Path.home()/'.local/bin'/name).resolve()==root/'launcher/nlc' for name in ('tacto','nlc'))
  assert all((Path.home()/tool/'skills/tacto').resolve()==root/'skill' for tool in ('.codex','.claude'))
  rows.append({'candidate_identity':identity(root/'launcher/nlc'),'stage':stage,'passed':True,'seconds':time.monotonic()-started,'both_grants_ready':doctor.count('이미 켜져 있어요.')==2,'saved_flow_preserved':True,'user_state_unchanged':True,'links':link_state()})
 completed=True
finally:
 restore_error=None
 try:
  call(['/bin/bash',str(old/'skill/install.sh')],'restore-original-install')
  call(['/bin/bash',str(old/'skill/install.sh'),'--check'],'restore-original-check')
  assert link_state()==original
 except Exception as error:
  restore_error=type(error).__name__
  completed=False
 if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==digest:p.unlink()
 report={'passed':completed and link_state()==original and manifest()==before and all(r['passed'] and r['both_grants_ready'] for r in rows),'rows':rows,'restoration_error':restore_error,'original_links_restored':link_state()==original,'original_links':original,'final_links':link_state(),'user_state_unchanged':manifest()==before,'owned_compat_flow_sha256':digest,'scope':'actual command and both agent skill links, save-format compatibility, existing grants and signatures; no OS permission changes or cold-machine claims','permissions_requested':False}
 (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps({'passed':report['passed'],'stages':[r['stage'] for r in rows],'user_state_unchanged':report['user_state_unchanged']}))

if not report["passed"]:raise SystemExit(1)
