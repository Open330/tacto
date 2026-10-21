"""Build a versioned, timestamp-signed app from one clean committed tree.

Notarization, installation and publication are separate steps. No credentials
are read; pass an existing Developer ID signing identity explicitly.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
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
        or name in ('SigningPolicy.swift','RuntimeStatus.swift','signing.sh','pyproject.toml','README.md','README.ko.md'))]
    files={name:hashlib.sha256((root/name).read_bytes()).hexdigest()
           for name in selected if (root/name).is_file()}
    return {'schema':1,'workspace_commit':commit,'files':files,
            'digest':hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest(),
            'all_inputs_match_commit':True}


def require_timestamps(paths,run=subprocess.run):
    """Every packaged Mach-O, wherever it sits in the bundle, carries a secure timestamp."""
    for path in paths:
        metadata=run(['codesign','-dv','--verbose=4',str(path)],check=True,capture_output=True,text=True)
        if 'Timestamp=' not in metadata.stderr:
            raise RuntimeError(f'packaged Mach-O has no secure signing timestamp: {Path(path).name}')
    return len(paths)


def build(output,identity,tag):
    version,preview_number=release_version(tag)
    before=committed_sources()
    environment=dict(os.environ,TACTO_SIGN_IDENTITY=identity)
    for script in ('helper/build.sh','helper/bundle.sh'):
        subprocess.run(['bash',str(ROOT/script)],cwd=ROOT,env=environment,check=True,capture_output=True)
    output=Path(output).absolute()
    report=preview.build(output,identity)
    runtime=output/'Contents/Resources/runtime'
    # Package both guides using the same source as the runtime and native GUI.
    for name in ('README.md','README.ko.md'):
        (runtime/name).write_bytes((ROOT/name).read_bytes())
    provenance=output/'Contents/Resources/first-run-source.json'
    provenance.write_text(json.dumps(before,indent=2)+'\n')
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
            'private_paths_absent':True,'scanned_files':scanned}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--identity',required=True)
    parser.add_argument('--tag',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    fd=os.open(args.report,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        try:
            report=build(args.output,args.identity,args.tag)
        except Exception as error:
            json.dump({'status':'failed','error_class':type(error).__name__},stream);stream.write('\n')
            raise
        json.dump({'status':'passed',**report},stream,indent=2);stream.write('\n')
    print('Versioned distribution app built and verified; not notarized or published.')
