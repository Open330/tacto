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


def normalize_python_metadata(runtime, original_prefix):
    """Remove the build machine prefix from copied build-time metadata only."""
    changed = 0
    for path in (runtime/'.venv/lib').glob('python*/_sysconfigdata*.py'):
        before = path.read_text()
        after = before.replace(str(original_prefix), '/opt/tacto-runtime')
        if after != before:
            path.write_text(after)
            changed += 1
    return changed


def require_private_paths_absent(directory, prefixes):
    needles = [str(path).encode() for path in prefixes if str(path) not in ('', '/')]
    scanned = 0
    for path in directory.rglob('*'):
        if path.is_file() and not path.is_symlink():
            scanned += 1
            data = path.read_bytes()
            if any(needle in data for needle in needles):
                raise RuntimeError('private_build_path_in_bundle')
    return scanned


def bundled_import_valid(data, runtime):
    try:
        root=runtime.resolve(strict=True)
        if type(data) is not dict or not all(isinstance(data.get(key),str)
                and Path(data[key]).resolve(strict=True).is_relative_to(root) for key in ('tacto','prefix','helper')):
            return False
        helper = root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
        return helper.is_file() and Path(data['helper']).resolve(strict=True) == helper.resolve(strict=True)
    except (OSError,RuntimeError,ValueError):return False


def requirement(identifier, signed_reference=None):
    reference = signed_reference or ROOT/'helper/Tacto Helper.app'
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
            'setup/PermissionState.swift','docs/assets/tacto-app.icns','docs/assets/tacto-logo.svg',
            'docs/assets/tacto-menu-template.svg','helper/bundle.sh',
            'launcher/tacto.swift','SigningPolicy.swift','signing.sh','skill/install.sh','skill/SKILL.md',
            'helper/Tacto Helper.app/Contents/MacOS/TactoHelper','helper/Tacto Helper.app/Contents/Info.plist')]
        paths+=sorted((ROOT/'tacto').glob('*.py'))
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
    with tempfile.TemporaryDirectory(prefix='tacto-standalone-build-',dir=output.parent) as folder:
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
        if (site/'tacto').exists():shutil.rmtree(site/'tacto')
        shutil.copytree(ROOT/'tacto',site/'tacto',ignore=ignored)
        if (site/'nlc').exists():shutil.rmtree(site/'nlc')
        shutil.copytree(ROOT/'nlc',site/'nlc',ignore=ignored)
        report['python_metadata_normalized'] = normalize_python_metadata(runtime, base)
        # A dylib's install ID is build metadata too; preserve relocatable loads.
        for library in (runtime/'.venv/lib').glob('libpython*.dylib'):
            run(['install_name_tool', '-id', '@rpath/'+library.name, str(library)])
        # Ship deterministic checked-hash bytecode before sealing the bundle.
        # -B at every owned runtime entry reads these caches but never creates
        # new files inside the signed app on a user's first command.
        run([str(runtime/'.venv/bin/python'),'-I','-B','-m','compileall','-q',
             '--invalidation-mode','checked-hash','-s',str(runtime/'.venv'),
             '-p','tacto-runtime',str(runtime/'.venv')])
        report['precompiled_python_files']=sum(1 for _ in (runtime/'.venv').rglob('*.pyc'))
        # Only shipped skill/docs; never copy mutable local flows or reviews.
        shutil.copytree(ROOT/'skill',runtime/'skill',symlinks=False,ignore=ignored)
        shutil.copytree(ROOT/'docs',runtime/'docs',symlinks=False,ignore=ignored)
        (runtime/'helper').mkdir()
        shutil.copytree(ROOT/'helper/Tacto Helper.app',runtime/'helper/Tacto Helper.app',symlinks=True)
        run(['codesign','--verify','--deep','--strict','-R='+requirement('ai.callabo.nlc.helper'),str(runtime/'helper/Tacto Helper.app')])
        for path in runtime.rglob('*'):
            if path.is_symlink():
                try:path.resolve(strict=True).relative_to(runtime.resolve())
                except (ValueError,OSError):raise RuntimeError('runtime symlink escapes or is broken') from None
        (runtime/'launcher').mkdir()
        launcher=runtime/'launcher/tacto'
        launcher_source=Path(folder)/'launcher-source';launcher_source.mkdir()
        shutil.copy2(ROOT/'launcher/tacto.swift',launcher_source/'main.swift')
        run(['swiftc','-O','-file-prefix-map',str(ROOT)+'=/tacto-source',str(launcher_source/'main.swift'),str(ROOT/'SigningPolicy.swift'),'-o',str(launcher)])
        sign(launcher,identity,'ai.callabo.nlc',requirement('ai.callabo.nlc'))
        run(['codesign','--verify','-R='+requirement('ai.callabo.nlc'),str(launcher)])
        native=list(native_files(runtime/'.venv'))
        # Native extensions/dylibs before the interpreter, all same identity.
        for path in sorted(native,key=lambda p:(p.parent.name=='bin',str(p))):sign(path,identity)
        report['native_runtime_files_signed']=len(native)
        executable=contents/'MacOS/TactoSetup'
        for name in ('tacto-app.icns','tacto-logo.svg','tacto-menu-template.svg'):
            shutil.copy2(ROOT/'docs/assets'/name,resources/name)
        # The embedded permission holder keeps its identity and requirement,
        # but receives the same product icon instead of a generic application.
        import plistlib
        helper_app=runtime/'helper/Tacto Helper.app'
        helper_resources=helper_app/'Contents/Resources'
        helper_resources.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'docs/assets/tacto-app.icns',helper_resources/'tacto-app.icns')
        helper_plist=helper_app/'Contents/Info.plist'
        helper_info=plistlib.loads(helper_plist.read_bytes())
        helper_info['CFBundleIconFile']='tacto-app.icns'
        helper_info['CFBundleName']='Tacto Helper'
        helper_info['CFBundleDisplayName']='Tacto Helper'
        helper_info['CFBundleDevelopmentRegion']='en'
        helper_info['LSHasLocalizedDisplayName']=True
        for language in ('en','ko'):
            localized=helper_resources/(language+'.lproj')
            localized.mkdir(exist_ok=True)
            (localized/'InfoPlist.strings').write_bytes(plistlib.dumps({
                'CFBundleName':'Tacto Helper','CFBundleDisplayName':'Tacto Helper'}))
        helper_plist.write_bytes(plistlib.dumps(helper_info))
        sign(helper_app,identity,'ai.callabo.nlc.helper',requirement('ai.callabo.nlc.helper'))
        run(['codesign','--verify','--deep','--strict','-R='+requirement('ai.callabo.nlc.helper'),str(helper_app)])
        run(['swiftc','-file-prefix-map',str(ROOT)+'=/tacto-source',str(ROOT/'setup/main.swift'),str(ROOT/'setup/PermissionState.swift'),'-o',str(executable)])
        import plistlib
        (contents/'Info.plist').write_bytes(plistlib.dumps({
            'CFBundleDisplayName':'Tacto','CFBundleName':'Tacto',
            'CFBundleIconFile':'tacto-app.icns',
            'CFBundleExecutable':'TactoSetup','CFBundleIdentifier':'ai.callabo.nlc.setup.standalone.preview',
            'CFBundlePackageType':'APPL','CFBundleShortVersionString':'0.1.0','CFBundleVersion':'1',
            'LSUIElement':True,
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
        'import tacto,tacto.helper,Foundation,Quartz,ApplicationServices,json,sys; print(json.dumps({"tacto":tacto.__file__,"prefix":sys.prefix,"helper":tacto.helper.HELPER}))'],cwd='/').stdout)
    if not bundled_import_valid(report['relocated_import'],output/'Contents/Resources/runtime'):
        raise RuntimeError('runtime imported outside bundle')
    run([str(python),'-I','-B','-m','tacto.cli','--help'],cwd='/')
    run([str(python),'-I','-B','-m','nlc.cli','--help'],cwd='/')
    run([str(python),'-I','-B','-c',
         'import tacto.flow,nlc.flow; assert tacto.flow is nlc.flow; assert tacto.flow.Flow is nlc.flow.Flow'],cwd='/')
    # Exercise the actual signed launcher, not only a Python -B probe. A
    # missing -B here used to pass packaging then invalidate its own signature.
    for _ in range(2):
        run([str(output/'Contents/Resources/runtime/launcher/tacto'),'--help'],cwd='/')
        run(['codesign','--verify','--deep','--strict','-R='+owner_requirement,str(output)])
    report['signature_preserved_after_launcher_runs']=True
    run(['codesign','--verify','--deep','--strict','-R='+owner_requirement,str(output)])
    report['outer_permission_owner_requirement_verified']=True
    report['signature_verified_after_import']=True
    report['private_path_files_scanned'] = require_private_paths_absent(output, (Path.home(), ROOT))
    report['private_build_paths_absent'] = True
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
