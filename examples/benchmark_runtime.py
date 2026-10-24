"""Identify the selected signed runtime separately from checkout harness code."""
import hashlib
import os
import shutil
import subprocess
from pathlib import Path


def codex_config_evidence():
    """Hash the profile inherited by the agent, without reading auth or values."""
    configured = os.environ.get('CODEX_HOME')
    home = Path(configured).expanduser() if configured else Path.home()/'.codex'
    if not home.is_absolute():
        raise ValueError('CODEX_HOME must be absolute for reproducible comparison')
    home = home.resolve()
    config = home/'config.toml'
    exists = config.is_file()
    return {'home':str(home),'home_source':'CODEX_HOME' if configured else 'default',
            'config_path':str(config),'exists':exists,
            'sha256':hashlib.sha256(config.read_bytes()).hexdigest() if exists else None,
            'scope':'profile config.toml only; not auth, global instructions, remote settings or serving model'}


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
    compatibility=package.with_name('tacto')
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
    aliases={'PATH/tacto':shutil.which('tacto'),
             'local/tacto':str(Path.home()/'.local/bin/tacto')}
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
    import re
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
    required_flags=requirement.get('command_flags',{})
    if (not isinstance(required_flags,dict) or any(
            command not in commands or not isinstance(flags,list) or
            not all(isinstance(flag,str) and re.fullmatch(r'--[a-z][a-z0-9-]*',flag) for flag in flags)
            for command,flags in required_flags.items())):
        raise ValueError('unsupported guide command flags')
    probe=r'''import importlib.util,json,re
spec=importlib.util.find_spec('tacto.command_help')
if spec is None: print(json.dumps({'supported':[]}))
else:
 from tacto.command_help import command_help
 help_text={c:command_help(c,['--help']) for c in COMMANDS}
 print(json.dumps({'supported':[c for c,h in help_text.items() if isinstance(h,str)],
                   'flags':{c:sorted(set(re.findall(r'(?<![\w-])--[a-z][a-z0-9-]*',h))) for c,h in help_text.items() if isinstance(h,str)}}))
'''.replace('COMMANDS',repr(commands))
    process=subprocess.run([str(Path(runtime['runtime_root'])/'.venv/bin/python'),'-I','-B','-c',probe],
                           capture_output=True,text=True,timeout=15)
    supported=[];available_flags={}
    if process.returncode==0:
        try:
            reply=json.loads(process.stdout)
            candidate=reply.get('supported',[])
            if isinstance(candidate,list) and all(isinstance(c,str) for c in candidate):supported=candidate
            candidate=reply.get('flags',{})
            if isinstance(candidate,dict):available_flags=candidate
        except (ValueError,AttributeError):pass
    missing=[command for command in commands if command not in supported]
    missing_flags={}
    for command,flags in required_flags.items():
        available=available_flags.get(command,[])
        if not isinstance(available,list) or not all(isinstance(f,str) for f in available):available=[]
        absent=[flag for flag in flags if flag not in available]
        if absent:missing_flags[command]=absent
    return {'compatible':not missing and not missing_flags,'declared_requirements':True,'commands':commands,
            'missing_commands':missing,'missing_flags':missing_flags,'probe_exit':process.returncode,
            'scope':'isolated runtime command-help probe only; no GUI/helper/model or task execution'}
