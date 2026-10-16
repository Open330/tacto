"""Build a relocatable developer preview from the trusted local runtime.

No install, GUI launch, permission request, download or notarization. This copies
local dependencies; their release licensing/platform compatibility needs a
separate audit. Output is reserved before work and never replaces an existing app.
"""
import argparse
import hashlib
import time
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
MAGICS = {bytes.fromhex(x) for x in ('feedface','cefaedfe','feedfacf','cffaedfe','cafebabe','bebafeca')}


def ignored(directory, names):
    return [n for n in names if n == '__pycache__' or n.endswith(('.pyc','.pyo','.pth'))
            or n.startswith('__editable__') or n == 'direct_url.json']


def native_files(directory):
    for path in sorted(directory.rglob('*')):
        if path.is_symlink() or not path.is_file():continue
        with path.open('rb') as stream:magic=stream.read(4)
        if magic in MAGICS:yield path


def bundled_import_valid(data, runtime):
    try:
        root=runtime.resolve(strict=True)
        if type(data) is not dict or not all(isinstance(data.get(key),str)
                and Path(data[key]).resolve(strict=True).is_relative_to(root) for key in ('nlc','prefix','helper')):
            return False
        helper = root/'helper/NLC Helper.app/Contents/MacOS/NLCHelper'
        return helper.is_file() and Path(data['helper']).resolve(strict=True) == helper.resolve(strict=True)
    except (OSError,RuntimeError,ValueError):return False


def requirement(identifier, signed_reference=None):
    reference = signed_reference or ROOT/'helper/NLC Helper.app'
    result = subprocess.run(['codesign','-dv','--verbose=4',str(reference)],
                            capture_output=True,text=True,check=True)
    match = re.search(r'^TeamIdentifier=([A-Z0-9]{10})$', result.stderr, re.M)
    if not match or not re.fullmatch(r'[A-Za-z0-9.-]+',identifier):
        raise RuntimeError('valid Apple signing team/identifier unavailable')
    return f'identifier "{identifier}" and anchor apple generic and certificate leaf[subject.OU] = "{match[1]}"'


def run(argv, **kwargs):
    return subprocess.run(argv,check=True,capture_output=True,text=True,timeout=120,**kwargs)


def sign(path, identity, identifier=None, req=None):
    argv=['codesign','--force','--sign',identity,'--options','runtime','--timestamp=none']
    if identifier:argv+=['--identifier',identifier]
    if req:argv+=['-r=designated => '+req]
    run([*argv,str(path)])


def build(output, identity):
    if platform.system()!='Darwin':raise RuntimeError('macOS required')
    began=time.monotonic()
    def sources():
        paths=[ROOT/name for name in ('setup/build_standalone_preview.py','setup/main.swift',
            'setup/PermissionState.swift','launcher/nlc.swift','SigningPolicy.swift','signing.sh','skill/install.sh','skill/SKILL.md',
            'helper/NLC Helper.app/Contents/MacOS/NLCHelper','helper/NLC Helper.app/Contents/Info.plist')]
        paths+=sorted((ROOT/'nlc').glob('*.py'))
        return {str(path.relative_to(ROOT)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    output=Path(output).absolute()
    if output.suffix!='.app' or not output.parent.is_dir():raise ValueError('new .app in existing directory required')
    # mkdir is exclusive even for a dangling symlink; a partial failure remains
    # explicitly reserved, with no executable app copied into it.
    output.mkdir(mode=0o700)
    before=sources()
    report={'scope':'local_relocatable_developer_preview','architecture':platform.machine(),
            'notarized':False,'gui_launched':False,'installed':False,'macos_version':platform.mac_ver()[0]}
    with tempfile.TemporaryDirectory(prefix='nlc-standalone-build-',dir=output.parent) as folder:
        app=Path(folder)/output.name;contents=app/'Contents';resources=contents/'Resources'
        resources.mkdir(parents=True);(contents/'MacOS').mkdir()
        runtime=resources/'runtime';runtime.mkdir()
        info=json.loads(run([str(ROOT/'.venv/bin/python'),'-I','-B','-c',
            'import sys,json; print(json.dumps({"base":sys.base_prefix,"version":sys.version_info[:2]}))']).stdout)
        base=Path(info['base']).resolve();version='.'.join(map(str,info['version']))
        shutil.copytree(base,runtime/'.venv',symlinks=True,ignore=ignored)
        site=runtime/'.venv/lib'/('python'+version)/'site-packages'
        site.mkdir(parents=True,exist_ok=True)
        shutil.copytree(ROOT/'.venv/lib'/('python'+version)/'site-packages',site,
                        dirs_exist_ok=True,symlinks=True,ignore=ignored)
        if (site/'nlc').exists():shutil.rmtree(site/'nlc')
        shutil.copytree(ROOT/'nlc',site/'nlc',ignore=ignored)
        # Only shipped skill/docs; never copy mutable local flows or reviews.
        shutil.copytree(ROOT/'skill',runtime/'skill',symlinks=False,ignore=ignored)
        shutil.copytree(ROOT/'docs',runtime/'docs',symlinks=False,ignore=ignored)
        (runtime/'helper').mkdir()
        shutil.copytree(ROOT/'helper/NLC Helper.app',runtime/'helper/NLC Helper.app',symlinks=True)
        run(['codesign','--verify','--deep','--strict','-R='+requirement('ai.callabo.nlc.helper'),str(runtime/'helper/NLC Helper.app')])
        for path in runtime.rglob('*'):
            if path.is_symlink():
                try:path.resolve(strict=True).relative_to(runtime.resolve())
                except (ValueError,OSError):raise RuntimeError('runtime symlink escapes or is broken') from None
        (runtime/'launcher').mkdir()
        launcher=runtime/'launcher/nlc'
        launcher_source=Path(folder)/'launcher-source';launcher_source.mkdir()
        shutil.copy2(ROOT/'launcher/nlc.swift',launcher_source/'main.swift')
        run(['swiftc','-O',str(launcher_source/'main.swift'),str(ROOT/'SigningPolicy.swift'),'-o',str(launcher)])
        sign(launcher,identity,'ai.callabo.nlc',requirement('ai.callabo.nlc'))
        run(['codesign','--verify','-R='+requirement('ai.callabo.nlc'),str(launcher)])
        native=list(native_files(runtime/'.venv'))
        # Native extensions/dylibs before the interpreter, all same identity.
        for path in sorted(native,key=lambda p:(p.parent.name=='bin',str(p))):sign(path,identity)
        report['native_runtime_files_signed']=len(native)
        executable=contents/'MacOS/NLCSetup'
        run(['swiftc',str(ROOT/'setup/main.swift'),str(ROOT/'setup/PermissionState.swift'),'-o',str(executable)])
        import plistlib
        (contents/'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleDisplayName':'Tacto','CFBundleName':'Tacto',
            'CFBundleExecutable':'NLCSetup','CFBundleIdentifier':'ai.callabo.nlc.setup.standalone.preview',
            'CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1',
            'LSMinimumSystemVersion':platform.mac_ver()[0]}))
        (resources/'runtime.json').write_text(json.dumps({'bundledRoot':'runtime'}))
        owner_requirement=requirement('ai.callabo.nlc.setup.standalone.preview')
        sign(executable,identity,'ai.callabo.nlc.setup.standalone.preview',owner_requirement)
        sign(app,identity,'ai.callabo.nlc.setup.standalone.preview',owner_requirement)
        run(['codesign','--verify','--deep','--strict','-R='+owner_requirement,str(app)])
        shutil.copytree(app,output,dirs_exist_ok=True,symlinks=True)
    # Relocation happened after signing. Run no GUI command and write no caches.
    python=output/'Contents/Resources/runtime/.venv/bin/python'
    report['relocated_import']=json.loads(run([str(python),'-I','-B','-c',
        'import nlc,nlc.helper,Foundation,Quartz,ApplicationServices,json,sys; print(json.dumps({"nlc":nlc.__file__,"prefix":sys.prefix,"helper":nlc.helper.HELPER}))'],cwd='/').stdout)
    if not bundled_import_valid(report['relocated_import'],output/'Contents/Resources/runtime'):
        raise RuntimeError('runtime imported outside bundle')
    run([str(python),'-I','-B','-m','nlc.cli','--help'],cwd='/')
    run(['codesign','--verify','--deep','--strict','-R='+owner_requirement,str(output)])
    report['outer_permission_owner_requirement_verified']=True
    report['signature_verified_after_import']=True
    report['source_before']=before;report['source_after']=sources()
    report['source_stable']=before==report['source_after'];report['elapsed_seconds']=time.monotonic()-began
    if not report['source_stable']:raise RuntimeError('build sources changed')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--identity',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    fd=os.open(args.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as file:
        try:report=build(args.output,args.identity)
        except Exception as error:
            json.dump({'status':'failed','error_class':type(error).__name__,'scope':'standalone_preview_build','gui_launched':False},file);file.write('\n');raise
        json.dump({'status':'passed',**report},file);file.write('\n')
    print('Built developer preview; not launched, installed or notarized: '+str(args.output))
