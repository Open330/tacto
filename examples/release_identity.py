"""Local provenance for release validation; never a substitute for task results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def identity(launcher):
    runtime=Path(launcher).resolve(strict=True).parent.parent
    packages=list((runtime/'.venv/lib').glob('python*/site-packages/nlc'))
    package=packages[0] if runtime.name=='runtime' and len(packages)==1 else runtime/'nlc'
    result={'module_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(package.glob('*.py'))}}
    def code_hash(path):
        text=subprocess.run(['codesign','-dv','--verbose=4',str(path)],capture_output=True,text=True,check=True).stderr
        return next(line.split('=',1)[1] for line in text.splitlines() if line.startswith('CDHash='))
    result['cdhash']={name:code_hash(runtime/path) for name,path in {
        'launcher':'launcher/nlc','helper':'helper/NLC Helper.app','python':'.venv/bin/python'}.items()}
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
