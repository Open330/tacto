"""Local provenance for release validation; never a substitute for task results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def python_source_hashes(runtime, *, bundled_only=False):
    """Hash the canonical Tacto implementation."""
    runtime = Path(runtime)
    packages = list((runtime/'.venv/lib').glob('python*/site-packages/tacto'))
    if len(packages) > 1:
        raise ValueError('multiple bundled tacto packages')
    primary = packages[0] if packages else (None if bundled_only else runtime/'tacto')
    if primary is None or not (primary/'__init__.py').is_file():
        raise ValueError('runtime Python package unavailable')
    return {str(path.relative_to(primary)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(primary.rglob('*.py'))}


def identity(launcher):
    runtime=Path(launcher).resolve(strict=True).parent.parent
    result={'module_sha256':python_source_hashes(runtime)}
    def code_hash(path):
        text=subprocess.run(['codesign','-dv','--verbose=4',str(path)],capture_output=True,text=True,check=True).stderr
        return next(line.split('=',1)[1] for line in text.splitlines() if line.startswith('CDHash='))
    helper=runtime/'helper/Tacto Helper.app'
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
