"""Identify the selected signed runtime separately from checkout harness code."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def runtime_evidence(launcher):
    selected=Path(launcher).expanduser()
    if not selected.is_absolute():
        raise ValueError('launcher must be an absolute executable path')
    entry=selected.resolve(strict=True)
    if not entry.is_file() or not os.access(entry,os.X_OK) or entry.parent.name!='launcher':
        raise ValueError('launcher must be a runtime/launcher executable')
    runtime=entry.parent.parent
    packages=list((runtime/'.venv/lib').glob('python*/site-packages/tacto'))
    if len(packages)>1:raise ValueError('selected runtime has multiple tacto packages')
    package=packages[0] if packages else runtime/'tacto'
    if not (package/'__init__.py').is_file():
        raise ValueError('selected runtime has no unambiguous tacto package')
    helper=runtime/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
    if not helper.is_file():
        raise ValueError('selected runtime helper requires a Tacto Helper.app layout (Preview2 or compatible)')
    skill=runtime/'skill/SKILL.md'
    python=runtime/'.venv/bin/python'
    paths=[entry,helper,skill,python,*sorted(package.rglob('*.py')),
           *sorted((runtime/'skill/references').glob('*.md'))]
    compatibility=package.with_name('nlc')
    if compatibility.is_dir():paths += sorted(compatibility.rglob('*.py'))
    files={str(path.relative_to(runtime)):hashlib.sha256(path.read_bytes()).hexdigest()
           for path in paths}
    return {'requested_launcher':str(selected),'resolved_launcher':str(entry),
            'runtime_root':str(runtime),'skill':str(skill),'package':str(package),
            'python_resolved':str(python.resolve(strict=True)),
            'files':files,'scope':'selected launcher/helper/python executable, package Python sources and skill; not a dependency or OS closure'}


def agent_evidence(executable):
    entry=Path(executable).expanduser().resolve(strict=True)
    if not entry.is_file() or not os.access(entry,os.X_OK):raise ValueError('agent is not executable')
    version=subprocess.run([str(entry),'--version'],capture_output=True,text=True,check=True,timeout=10).stdout.strip()
    return {'resolved_executable':str(entry),'sha256':hashlib.sha256(entry.read_bytes()).hexdigest(), 'version':version}


def runtime_aliases(runtime):
    """Alias alignment is environment evidence, not proof of agent execution."""
    expected=Path(runtime['resolved_launcher'])
    aliases={'PATH/tacto':shutil.which('tacto'), 'PATH/nlc':shutil.which('nlc'),
             'local/tacto':str(Path.home()/'.local/bin/tacto'),
             'local/nlc':str(Path.home()/'.local/bin/nlc')}
    paths={key:str(Path(path).resolve()) if path and Path(path).exists() else None
           for key,path in aliases.items()}
    skill=Path.home()/'.codex/skills/tacto/SKILL.md'
    paths['codex_skill']=str(skill.resolve()) if skill.exists() else None
    matched=all(path==str(expected) for key,path in paths.items() if key!='codex_skill')
    matched=matched and paths['codex_skill']==str(Path(runtime['skill']).resolve())
    return {'resolved_paths':paths,'matches_selected_runtime':matched,'execution_attribution_verified':False}


def guide_evidence(path):
    selected=Path(path).expanduser()
    if not selected.is_absolute():raise ValueError('guide must be an absolute SKILL.md path')
    selected=selected.resolve(strict=True)
    if selected.name!='SKILL.md' or not selected.is_file():raise ValueError('guide must name SKILL.md')
    selected.read_text(encoding='utf-8')
    files={str(item.relative_to(selected.parent)):hashlib.sha256(item.read_bytes()).hexdigest()
           for item in sorted(selected.parent.rglob('*.md')) if item.is_file()}
    requirements=selected.parent/'runtime-requirements.json'
    if requirements.is_file():files[requirements.name]=hashlib.sha256(requirements.read_bytes()).hexdigest()
    return {'path':str(selected),'files':files,
            'scope':'selected skill Markdown subtree; linked external docs not included; no runtime installation change'}


def guide_runtime_compatibility(runtime, guide):
    """Probe pure command-help support without starting GUI/helper or a model.

    New guide bundles declare requirements explicitly. Absence means no declared
    requirements, not proof that every sentence of a legacy guide is compatible.
    """
    import json
    spec=Path(guide['path']).parent/'runtime-requirements.json'
    if not spec.exists():
        return {'compatible':True,'declared_requirements':False,'commands':[],
                'scope':'no guide requirements declared; not a semantic compatibility proof'}
    requirement=json.loads(spec.read_text())
    if not isinstance(requirement,dict):raise ValueError('guide runtime requirements must be an object')
    commands=requirement.get('command_help')
    if (requirement.get('schema')!=1 or not isinstance(commands,list)
            or not all(isinstance(c,str) and c in ('session','batch','repair') for c in commands)):
        raise ValueError('unsupported guide runtime requirements')
    probe='''import importlib.util,json
spec=importlib.util.find_spec('tacto.command_help')
if spec is None: print(json.dumps({'supported':[]}))
else:
 from tacto.command_help import command_help
 print(json.dumps({'supported':[c for c in COMMANDS if isinstance(command_help(c,['--help']),str)]}))
'''.replace('COMMANDS',repr(commands))
    process=subprocess.run([str(Path(runtime['runtime_root'])/'.venv/bin/python'),'-I','-B','-c',probe],
                           capture_output=True,text=True,timeout=15)
    supported=[]
    if process.returncode==0:
        try:supported=json.loads(process.stdout).get('supported',[])
        except (ValueError,AttributeError):pass
    missing=[command for command in commands if command not in supported]
    return {'compatible':not missing,'declared_requirements':True,'commands':commands,
            'missing_commands':missing,'probe_exit':process.returncode,
            'scope':'isolated runtime command-help probe only; no GUI/helper/model or task execution'}
