"""Local provenance for release validation; never a substitute for task results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def identity(launcher):
    runtime=Path(launcher).resolve(strict=True).parent.parent
    packages=list((runtime/'.venv/lib').glob('python*/site-packages/tacto'))
    # Preview 1 used nlc; prefer the canonical implementation when present.
    if not packages:
        packages=list((runtime/'.venv/lib').glob('python*/site-packages/nlc'))
    package=packages[0] if runtime.name=='runtime' and len(packages)==1 else runtime/'tacto'
    if not package.is_dir():
        package=runtime/'nlc'
    if not package.is_dir() or not any(package.glob('*.py')):
        raise ValueError('runtime Python implementation missing')
    result={'module_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob('*.py'))}}
    def code_hash(path):
        text=subprocess.run(['codesign','-dv','--verbose=4',str(path)],capture_output=True,text=True,check=True).stderr
        return next(line.split('=',1)[1] for line in text.splitlines() if line.startswith('CDHash='))
    helper=runtime/'helper/Tacto Helper.app'
    if not helper.is_dir():
        helper=runtime/'helper/NLC Helper.app'
    result['cdhash']={name:code_hash(path) for name,path in {
        'launcher':Path(launcher).resolve(strict=True), 'helper':helper,
        'python':runtime/'.venv/bin/python'}.items()}
    result['app_manifest_sha256']=None
    if runtime.name=='runtime' and runtime.parent.name=='Resources' and runtime.parents[1].name=='Contents':
        # Same file/symlink manifest used by setup/check_release.py.
        app=runtime.parents[2]
        subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True,capture_output=True)
        rows={}
        for p in sorted(app.rglob('*')):
            if p.is_symlink(): rows[str(p.relative_to(app))]={'symlink':os.readlink(p)}
            elif p.is_file(): rows[str(p.relative_to(app))]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
        result['app_manifest_sha256']=hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()
    return result
