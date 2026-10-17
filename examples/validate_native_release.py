"""Sixty actual Finder/Safari/TextEdit workflows through one signed candidate.

Fixture bootstrap opens only an explicitly owned text file and public Wikipedia.
UI actions run via Tacto. No model, personal files, auth or permission changes.
Raw observations/traces stay in the caller's private output directory.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import time
import uuid

from tacto.flow import Selector, pin_params, _element_block
from release_identity import identity


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    path.chmod(0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    base = args.output.resolve(); base.mkdir(mode=0o700)
    launcher = args.launcher.resolve(strict=True)
    doc = base/'Tacto-release-owned.txt'; doc.write_text('release seed\n'); doc.chmod(0o600)
    folder_names = ['Tacto-'+uuid.uuid4().hex[:8]+'-'+suffix for suffix in ('A','B')]
    for name in folder_names:
        folder=base/name;folder.mkdir()
        for i in range(3): (folder/f'owned-{i}.txt').write_text(f'owned file {i}')
    before=identity(launcher)
    rows=[]; sequence=0
    def command(argv, label):
        nonlocal sequence
        sequence += 1
        began=time.monotonic()
        result=subprocess.run([str(launcher), *argv], capture_output=True, text=True, timeout=45)
        for suffix, text in [('out', result.stdout), ('err', result.stderr)]:
            p=base/f'{sequence:03}-{label}.{suffix}';p.write_text(text);p.chmod(0o600)
        return result, time.monotonic()-began
    def observe(app):
        result,_=command(['observe',app,'--helper','--all-windows'],'observe')
        if result.returncode: raise RuntimeError('fresh observation failed')
        return result.stdout
    def flow(name, app, steps, *, edit=False):
        p=base/(name+'.json');write(p,{'schema':2,'name':name,'target':app,'steps':steps})
        result, seconds=command(['run',str(p),'--helper','--think','0','--no-indicator','--yes',
                                 '--trace',str(base/(name+'-trace.json'))],name)
        if edit:
            # Persistent edits deliberately remain unverified until the bound
            # save below supplies a typed receipt. Never call this task done yet.
            valid=result.returncode==1 and 'commit_unverified' in result.stdout
        else: valid=result.returncode==0
        if not valid: raise RuntimeError(name+' runtime refused; inspect private log')
        return seconds
    def post(field, value, attribute='Title'):
        return {'field':field,'attribute':attribute,'equals':value}
    def ephemeral():
        return {'effects':['write_ephemeral'],'reversibility':'reversible',
                'resource':{'object_type':'owned release task','scope':'entry'}}
    def row(name, rep, seconds, passed):
        rows.append({'scenario':name,'repetition':rep,'seconds':seconds,'passed':passed})
        write(base/'progress.json',{'rows':rows})
        print(json.dumps(rows[-1]),flush=True)
        # Failed workflows remain in the cohort; continue independent tasks.
    subprocess.run(['open','-g','-a','TextEdit',str(doc)],check=True,capture_output=True)
    subprocess.run(['open','-g','-a','Safari','https://en.wikipedia.org/wiki/Computer'],check=True,capture_output=True)
    # Readiness is observed; bootstrap delay is outside the timed workflows.
    deadline=time.monotonic()+15
    while 'Heading  Title: Computer,' not in observe('com.apple.Safari'):
        if time.monotonic()>deadline: raise RuntimeError('owned browser bootstrap timeout')
        time.sleep(.25)
    try:
        for i in range(10):
            folder=base/folder_names[i%2]
            seconds=flow(f'finder-open-{i}','com.apple.finder',[{'action':'open_folder',
                'params':{'path':str(folder)},'postcondition':post({'role':'Window','title':folder.name},folder.name),
                'safety':ephemeral()}])
            tree=observe('com.apple.finder')
            good=f'Title: {folder.name},' in tree and all(f'owned-{n}.txt' in tree for n in range(3))
            row('finder_folder_navigation',i,seconds,good)
            selected={'role':'TextField','value':f'owned-{i%3}.txt','ancestor':folder.name,'click_ancestor':'Row'}
            seconds=flow(f'finder-select-{i}','com.apple.finder',[{'action':'click','selector':selected,
                'postcondition':{'field':selected,'contains':'[selected]'},'safety':ephemeral()}])
            tree=observe('com.apple.finder');index,_=Selector(**selected).resolve(tree)
            block=_element_block(tree,index)
            # Restrict the count to the owned file-list subtree, not sidebar selections.
            list_index,_=Selector(role='Outline',identifier='ListView',ancestor=folder.name).resolve(tree)
            listing=_element_block(tree,list_index)
            row('finder_single_file_selection',i,seconds,'[selected]' in block.splitlines()[0]
                and sum(' Row' in line and '[selected]' in line for line in listing.splitlines())==1)
            for mode,text in [('single',f'Tacto release {i}'),('multiline',f'출시 검증 {i}\n한글과 emoji 😀\n끝')]:
                field={'role':'TextArea','identifier':'First Text View','ancestor':str(doc)}
                params={'value':text}
                safety={'effects':['write_persistent'],'reversibility':'recoverable',
                        'resource':{'object_type':'owned test document','object_identity':doc.name,'scope':'item'}}
                binding={'bundle_id':'com.apple.TextEdit','role':'TextArea','identifier':'First Text View',
                         'operation':'set_value','params_digest':pin_params(params),'resource_identity':doc.name,
                         'resource_identity_from':{'role':'Window','title':doc.name,'attribute':'Title','in_focused_window':True}}
                seconds=flow(f'textedit-{mode}-{i}','com.apple.TextEdit',[{'action':'set_value','selector':field,
                    'params':params,'intent_text':'Edit only the evaluator-created temporary document',
                    'postcondition':post(field,text.replace('\n','\\n'),'Value'),'safety':safety,'binding':binding}],edit=True)
                result,spent=command(['replay','textedit_save','-p','document='+str(doc),'-p','name='+doc.name,
                    '--helper','--yes','--think','0','--no-indicator','--trace',str(base/f'save-{mode}-{i}.json')],'save')
                row('textedit_'+mode+'_edit_save',i,seconds+spent,result.returncode==0 and doc.read_text()==text)
                rows[-1]['save_exit'] = result.returncode
                rows[-1]['save_requires_foreground'] = 'needs_foreground' in result.stdout
            seconds=flow(f'safari-search-{i}','com.apple.Safari',[
                {'action':'click','selector':{'role':'Link','title':'Search','ancestor':'Computer - Wikipedia'},
                 'postcondition':{'field':{'role':'ComboBox','title':'Search Wikipedia'},'contains':'Search Wikipedia'},'safety':ephemeral()},
                {'action':'set_value','selector':{'role':'ComboBox','title':'Search Wikipedia'},'params':{'value':'Computer hardware'},
                 'postcondition':post({'role':'ComboBox','title':'Search Wikipedia'},'Computer hardware','Value'),'safety':ephemeral()},
                {'action':'click','selector':{'role':'Button','title':'Search'},
                 'postcondition':post({'role':'Heading','title':'Computer hardware'},'Computer hardware'),'safety':ephemeral()}])
            tree=observe('com.apple.Safari')
            row('safari_public_search',i,seconds,'Window  Title: Computer hardware - Wikipedia,' in tree
                and 'Heading  Title: Computer hardware,' in tree)
            seconds=flow(f'safari-link-{i}','com.apple.Safari',[{'action':'click',
                'selector':{'role':'Link','title':'computer','desc':None,'allow_nth':0,'ancestor':'Computer hardware - Wikipedia'},
                'postcondition':post({'role':'Heading','title':'Computer'},'Computer'),'safety':ephemeral()}])
            tree=observe('com.apple.Safari')
            row('safari_public_link_navigation',i,seconds,'Window  Title: Computer - Wikipedia,' in tree
                and 'Heading  Title: Computer,' in tree)
    finally:
        versions={}
        for name,path in [('Finder','/System/Library/CoreServices/Finder.app'),('Safari','/Applications/Safari.app'),
                          ('TextEdit','/System/Applications/TextEdit.app')]:
            versions[name]=plistlib.loads((Path(path)/'Contents/Info.plist').read_bytes()).get('CFBundleShortVersionString')
        write(base/'report.json',{'passed':len(rows)==60 and all(r['passed'] for r in rows) and before==identity(launcher),'candidate_identity':before,'identity_unchanged':before==identity(launcher),'rows':rows,
            'counts':dict(Counter(r['scenario'] for r in rows)), 'app_versions':versions,
            'macos':subprocess.check_output(['sw_vers','-productVersion'],text=True).strip(),
            'launcher_sha256':hashlib.sha256(launcher.read_bytes()).hexdigest(),'model_requests':0,
            'scope':'actual native apps; deterministic public Wikipedia queries; owned temporary files; saved-state receipts, not writer attribution'})

    if len(rows)!=60 or not all(r['passed'] for r in rows):
        raise SystemExit(1)


if __name__=='__main__':main()
