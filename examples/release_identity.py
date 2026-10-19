"""Local provenance for release validation; never a substitute for task results."""
import hashlib
import json
import os
from pathlib import Path
import subprocess


def python_source_hashes(runtime, *, bundled_only=False):
    """Hash the implementation plus its optional legacy import shim."""
    runtime = Path(runtime)
    primary = None
    for name in ('tacto', 'nlc'):
        packages = list((runtime/'.venv/lib').glob(f'python*/site-packages/{name}'))
        if len(packages) > 1:
            raise ValueError(f'multiple bundled {name} packages')
        candidate = packages[0] if packages else None
        if candidate is None and not bundled_only:
            candidate = runtime/name
        if candidate is not None and candidate.exists():
            if not (candidate/'__init__.py').is_file():
                raise ValueError(f'invalid {name} package')
            primary = candidate
            break
    if primary is None:
        raise ValueError('runtime Python package unavailable')
    sources = {str(path.relative_to(primary)): hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(primary.rglob('*.py'))}
    compatibility = primary.with_name('nlc')
    if primary.name == 'tacto' and compatibility.is_dir():
        sources.update({'nlc/'+str(path.relative_to(compatibility)):
                        hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(compatibility.rglob('*.py'))})
    return sources


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
