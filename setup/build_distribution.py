"""Build a versioned, timestamp-signed app from one clean committed tree.

Notarization, installation and publication are separate steps. No credentials
are read; pass an existing Developer ID signing identity explicitly.

Prepare .venv with `uv sync --frozen --all-extras`. Its locked dependencies are
checked without modifying it. The previous installed app is the default
baseline; --baseline also accepts a prior SOURCE-MANIFEST.json containing the
embedded distribution_inventory. Old releases can be compared via their .app.
Only a first distribution should use --initial-distribution. Intentional removals
need --removal-allowlist JSON: {"distributions":{"name":"reason"},
"native_paths":{"Contents/relative/path":"reason"}}; stale entries fail too.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import subprocess

import build_standalone_preview as preview

ROOT=Path(__file__).resolve().parents[1]


def release_version(tag):
    match=re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-preview\.([1-9][0-9]*))?',tag)
    if not match:
        raise ValueError('expected vMAJOR.MINOR.PATCH or vMAJOR.MINOR.PATCH-preview.N')
    return '.'.join(match.groups()[:3]),match.group(4)


def committed_sources(root=ROOT):
    def git(*args):
        return subprocess.check_output(['git','-C',str(root),*args])
    if git('status','--porcelain').strip():
        raise RuntimeError('distribution requires a clean committed tree')
    commit=git('rev-parse','HEAD').decode().strip()
    names=git('ls-files','-z').decode().split('\0')
    selected=[name for name in names if name and (
        name.startswith(('tacto/','helper/','launcher/','setup/','skill/','docs/'))
        or name in ('SigningPolicy.swift','RuntimeStatus.swift','signing.sh','pyproject.toml','uv.lock','README.md','README.ko.md'))]
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
           for name in selected if (root/name).is_file()}
    return {'schema':1,'workspace_commit':commit,'files':files,
            'digest':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
            'all_inputs_match_commit':True}


def distribution_packages(venv):
    """Read the actual copied site-packages, never the builder interpreter's environment."""
    sites=list((Path(venv)/'lib').glob('python*/site-packages'))
    if len(sites)!=1:
        raise RuntimeError('distribution environment must have exactly one site-packages directory')
    packages={}
    for dist in importlib.metadata.distributions(path=[str(sites[0])]):
        name=dist.metadata.get('Name','')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',name) or not dist.version:
            raise RuntimeError('distribution metadata has an invalid name or version')
        name=re.sub(r'[-_.]+','-',name).lower()
        if name in packages:
            raise RuntimeError('ambiguous duplicate distribution: '+name)
        packages[name]=dist.version
    if not packages:
        raise RuntimeError('distribution environment contains no package metadata')
    return dict(sorted(packages.items()))


def require_release_environment(root=ROOT,run=subprocess.run):
    """Read-only lock/extras check, before native builds, copying or signing.

    uv is already required by the standalone wheel builder. --check never
    installs; --inexact retains intentional extra packages for inventory review.
    The exact environment that preview.build copies is pinned explicitly.
    """
    root=Path(root)
    if not (root/'.venv/bin/python').is_file():
        raise RuntimeError('missing release environment; run uv sync --frozen --all-extras')
    result=run(['uv','sync','--frozen','--all-extras','--check','--inexact','--offline'],
               cwd=root,env=dict(os.environ,UV_PROJECT_ENVIRONMENT=str((root/'.venv').resolve())),
               capture_output=True,text=True,timeout=120)
    if result.returncode:
        raise RuntimeError('release environment is missing or mismatches locked dependencies/extras; '
                           'run uv sync --frozen --all-extras before building')
    return distribution_packages(root/'.venv')


def validate_inventory(inventory):
    if not isinstance(inventory,dict) or type(inventory.get('schema')) is not int or inventory['schema']!=1:
        raise ValueError('unsupported distribution inventory')
    packages=inventory.get('distributions');native=inventory.get('native_paths')
    if not isinstance(packages,dict) or not packages or not isinstance(native,list) or not native:
        raise ValueError('distribution inventory requires package and native path lists')
    if any(not isinstance(name,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',name)
           or not isinstance(version,str) or not version for name,version in packages.items()):
        raise ValueError('invalid inventory package metadata')
    if any(not isinstance(path,str) for path in native) or len(native)!=len(set(native)) or any(not path
            or path.startswith('/') or '..' in PurePosixPath(path).parts
            or str(PurePosixPath(path))!=path or any(ord(c)<32 for c in path) for path in native):
        raise ValueError('inventory native paths must be unique relative paths')
    return {'schema':1,'distributions':dict(sorted(packages.items())), 'native_paths':sorted(native)}


def app_inventory(app):
    app=Path(app)
    inventory={'schema':1,
               'distributions':distribution_packages(app/'Contents/Resources/runtime/.venv'),
               'native_paths':[path.relative_to(app).as_posix() for path in preview.native_files(app)]}
    return validate_inventory(inventory)


def read_baseline(path=None,*,initial_distribution=False):
    if initial_distribution:
        if path is not None:
            raise ValueError('initial distribution cannot also declare a baseline')
        return None
    path=Path(path) if path is not None else Path('/Applications/Tacto.app')
    if not path.exists():
        raise RuntimeError('previous release baseline required: pass --baseline <previous.app or SOURCE-MANIFEST.json>; '
                           'only the first distribution may use --initial-distribution')
    if path.is_dir():
        return app_inventory(path)
    raw=json.loads(path.read_text())
    inventory=raw.get('distribution_inventory') if isinstance(raw,dict) and 'distribution_inventory' in raw else raw
    return validate_inventory(inventory)


def read_removal_allowlist(path=None):
    if path is None:
        return {'distributions':{},'native_paths':{}}
    raw=json.loads(Path(path).read_text())
    if not isinstance(raw,dict) or set(raw)!={'distributions','native_paths'}:
        raise ValueError('removal allowlist requires distributions and native_paths reason maps')
    for kind,entries in raw.items():
        if not isinstance(entries,dict) or any(not isinstance(key,str) or not key
                or not isinstance(reason,str) or not reason.strip() for key,reason in entries.items()):
            raise ValueError('every intentional removal requires an exact name/path and nonempty reason')
        if any('*' in key or '?' in key for key in entries):
            raise ValueError('wildcards are not allowed in removal allowlists')
    return raw


def require_inventory_compatibility(current,baseline,allowlist):
    current=validate_inventory(current)
    if baseline is None:
        if any(allowlist.values()):
            raise ValueError('initial distribution cannot declare removals')
        return {'baseline':'initial_distribution','removed_distributions':[], 'removed_native_paths':[],
                'intentional_removals':allowlist,'passed':True}
    baseline=validate_inventory(baseline)
    removed={'distributions':sorted(set(baseline['distributions'])-set(current['distributions'])),
             'native_paths':sorted(set(baseline['native_paths'])-set(current['native_paths']))}
    for kind,names in removed.items():
        unapproved=set(names)-set(allowlist[kind]);unused=set(allowlist[kind])-set(names)
        if unapproved:
            raise RuntimeError('unapproved removed '+kind+': '+', '.join(sorted(unapproved)))
        if unused:
            raise ValueError('stale removal allowlist '+kind+': '+', '.join(sorted(unused)))
    return {'baseline':'previous_release',
            'baseline_inventory_sha256':hashlib.sha256(json.dumps(baseline,sort_keys=True).encode()).hexdigest(),
            'previous_distribution_count':len(baseline['distributions']),
            'previous_native_count':len(baseline['native_paths']),
            'removed_distributions':removed['distributions'],
            'removed_native_paths':removed['native_paths'],'intentional_removals':allowlist,'passed':True}


def require_timestamps(paths,run=subprocess.run):
    """Every packaged Mach-O, wherever it sits in the bundle, carries a secure timestamp."""
    for path in paths:
        metadata=run(['codesign','-dv','--verbose=4',str(path)],check=True,capture_output=True,text=True)
        if 'Timestamp=' not in metadata.stderr:
            raise RuntimeError(f'packaged Mach-O has no secure signing timestamp: {Path(path).name}')
    return len(paths)


def build(output,identity,tag,*,baseline=None,initial_distribution=False,removal_allowlist=None):
    version,preview_number=release_version(tag)
    before=committed_sources()
    source_packages=require_release_environment()
    previous=read_baseline(baseline,initial_distribution=initial_distribution)
    removals=read_removal_allowlist(removal_allowlist)
    if previous is not None:
        missing=set(previous['distributions'])-set(source_packages)-set(removals['distributions'])
        if missing:
            raise RuntimeError('unapproved removed distributions before build: '+', '.join(sorted(missing)))
    environment=dict(os.environ,TACTO_SIGN_IDENTITY=identity)
    for script in ('helper/build.sh','helper/bundle.sh'):
        subprocess.run(['bash',str(ROOT/script)],cwd=ROOT,env=environment,check=True,capture_output=True)
    output=Path(output).absolute()
    report=preview.build(output,identity)
    inventory=app_inventory(output)
    omitted={name for name,version in source_packages.items() if inventory['distributions'].get(name)!=version}
    if omitted:
        raise RuntimeError('packaged distributions differ from checked release environment: '+', '.join(sorted(omitted)))
    compatibility=require_inventory_compatibility(inventory,previous,removals)
    runtime=output/'Contents/Resources/runtime'
    # Package both guides using the same source as the runtime and native GUI.
    for name in ('README.md','README.ko.md'):
        (runtime/name).write_bytes((ROOT/name).read_bytes())
    provenance=output/'Contents/Resources/first-run-source.json'
    provenance.write_text(json.dumps({**before,'distribution_inventory':inventory,'inventory_compatibility':compatibility},indent=2)+'\n')
    info_path=output/'Contents/Info.plist'
    info=plistlib.loads(info_path.read_bytes())
    info.update(CFBundleShortVersionString=version,
                CFBundleVersion=preview_number or subprocess.check_output(['git','rev-list','--count','HEAD'],cwd=ROOT,text=True).strip(),
                TactoReleaseTag=tag,TactoBuildVariant='preview.'+preview_number if preview_number else 'release',
                TactoSourceCommit=before['workspace_commit'],TactoOnboardingSourceDigest=before['digest'])
    info_path.write_bytes(plistlib.dumps(info))

    def sign(path,identifier=None):
        arguments=['codesign','--force','--sign',identity,'--options','runtime','--timestamp']
        if identifier:
            arguments += ['--identifier',identifier,'-r=designated => '+preview.requirement(identifier)]
        subprocess.run([*arguments,str(path)],check=True,capture_output=True,timeout=120)
        metadata=subprocess.run(['codesign','-dv','--verbose=4',str(path)],check=True,capture_output=True,text=True)
        if 'Timestamp=' not in metadata.stderr:
            raise RuntimeError('secure signing timestamp missing')

    native=list(preview.native_files(runtime/'.venv'))
    for path in sorted(native,key=lambda p:(p.parent.name=='bin',str(p))):
        sign(path)
    helper=runtime/'helper/Tacto Helper.app'
    for path,identifier in ((helper/'Contents/MacOS/TactoHelper','ai.callabo.tacto.helper'),
                            (helper,'ai.callabo.tacto.helper'),
                            (runtime/'launcher/tacto','ai.callabo.tacto'),
                            (output/'Contents/MacOS/TactoSetup','ai.callabo.tacto.setup.standalone.preview'),
                            (output,'ai.callabo.tacto.setup.standalone.preview')):
        sign(path,identifier)
    subprocess.run(['codesign','--verify','--deep','--strict',str(output)],check=True,capture_output=True)
    if before != committed_sources():
        raise RuntimeError('source tree changed during distribution build')
    site=next((runtime/'.venv/lib').glob('python*/site-packages'))
    for path in (ROOT/'tacto').rglob('*.py'):
        if path.read_bytes() != (site/'tacto'/path.relative_to(ROOT/'tacto')).read_bytes():
            raise RuntimeError('packaged Python source differs')
    all_native=list(preview.native_files(output))
    require_timestamps(all_native)
    scanned=preview.require_private_paths_absent(output,(Path.home(),ROOT))
    return {**report,'version':tag,'source_commit':before['workspace_commit'],
            'source_digest':before['digest'],'all_inputs_match_commit':True,
            'signed_with_timestamp':True,'native_runtime_files_timestamped':len(native),
            'all_packaged_macho_timestamped':True,'packaged_macho_count':len(all_native),
            'private_paths_absent':True,'scanned_files':scanned,
            'all_extras_verified':True,'distribution_inventory':inventory,
            'inventory_compatibility':compatibility}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--identity',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--baseline',type=Path,help='previous release .app, inventory JSON or SOURCE-MANIFEST.json; default /Applications/Tacto.app')
    parser.add_argument('--initial-distribution',action='store_true',help='explicitly establish the first inventory without a previous release')
    parser.add_argument('--removal-allowlist',type=Path,help='JSON distributions/native_paths maps of exact removed names to reasons')
    args=parser.parse_args()
    fd=os.open(args.report,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        try:
            report=build(args.output,args.identity,args.tag,baseline=args.baseline,
                         initial_distribution=args.initial_distribution,removal_allowlist=args.removal_allowlist)
        except Exception as error:
            json.dump({'status':'failed','error_class':type(error).__name__},stream);stream.write('\n')
            raise
        json.dump({'status':'passed',**report},stream,indent=2);stream.write('\n')
    print('Versioned distribution app built and verified; not notarized or published.')
