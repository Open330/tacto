"""Package a signed standalone developer preview for manual drag installation.

Creates a new compressed image only. No app launch, install, mount, permission
change, download or notarization. The existing app/image is never overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]


def private_parent(path):
    parent=path.parent;info=parent.lstat()
    if (not path.is_absolute() or parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700):
        raise ValueError('private_canonical_output_parent_required')


def app_manifest(app):
    root=app.resolve(strict=True)
    if app.is_symlink() or not app.is_dir() or app!=root or app.suffix!='.app':
        raise ValueError('canonical_app_directory_required')
    rows={}
    for path in sorted(app.rglob('*')):
        name=str(path.relative_to(app))
        if path.is_symlink():
            try:path.resolve(strict=True).relative_to(root)
            except (OSError,RuntimeError,ValueError):raise ValueError('app_symlink_escapes_or_is_broken') from None
            rows[name]={'symlink':os.readlink(path)}
        elif path.is_file():rows[name]={'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        elif not path.is_dir():raise ValueError('unsupported_app_entry')
    return rows


def run(argv):
    return subprocess.run(argv,check=True,capture_output=True,text=True,timeout=180)


def verify_app(app):
    import importlib.util
    spec=importlib.util.spec_from_file_location('standalone_builder',ROOT/'setup/build_standalone_preview.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    runtime=app/'Contents/Resources/runtime'
    for target,identifier in ((app,'ai.callabo.nlc.setup.standalone.preview'),
            (runtime/'helper/NLC Helper.app','ai.callabo.nlc.helper'),
            (runtime/'launcher/nlc','ai.callabo.nlc')):
        run(['codesign','--verify','--deep','--strict','-R='+module.requirement(identifier,signed_reference=app),str(target)])
    if not (runtime/'.venv/bin/python').is_file():raise ValueError('standalone_python_missing')


def build(app,output):
    app=Path(app).absolute();output=Path(output).absolute()
    private_parent(output)
    if output.suffix!='.dmg':raise ValueError('new_dmg_required')
    # Reserve before source/signature/tool access, including dangling symlinks.
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as sink:
        if platform.system()!='Darwin':raise RuntimeError('macOS_required')
        began=time.monotonic();before=app_manifest(app);verify_app(app)
        with tempfile.TemporaryDirectory(prefix='nlc-drag-preview-',dir=output.parent) as folder:
            base=Path(folder);stage=base/'volume';stage.mkdir(mode=0o700)
            copied=stage/'Tacto.app';shutil.copytree(app,copied,symlinks=True)
            # The output artifact stays private; the app root has normal drag
            # install permissions. Only this owned copy is changed.
            copied.chmod(0o755)
            if app_manifest(copied)!=before:raise RuntimeError('staged_app_content_changed')
            verify_app(copied)
            (stage/'Applications').symlink_to('/Applications',target_is_directory=True)
            (stage/'먼저 읽어주세요.txt').write_text(
                'Tacto 개발 미리보기\n\n'
                '1. Tacto.app을 Applications 폴더로 옮기세요.\n'
                '2. Applications에 옮긴 Tacto를 여세요.\n'
                '3. 시작 화면 안내에 따라 필요한 권한과 명령·스킬 연결을 설정하세요.\n\n'
                '권한은 macOS 설정에서 사용자가 직접 허용해야 합니다.\n'
                '현재 빌드 Mac에서 확인한 개발 미리보기이며 공증과 새 Mac 설치 실증은 아직 완료되지 않았습니다.\n',encoding='utf-8')
            image=base/'payload.dmg'
            run(['/usr/bin/hdiutil','create','-srcfolder',str(stage),'-format','UDZO',
                 '-volname','Tacto Preview','-nospotlight',str(image)])
            run(['/usr/bin/hdiutil','verify',str(image)])
            if app_manifest(app)!=before:raise RuntimeError('source_app_changed')
            with image.open('rb') as source:shutil.copyfileobj(source,sink,8*1024*1024)
            sink.flush();os.fsync(sink.fileno())
        digest=hashlib.sha256(output.read_bytes()).hexdigest()
        return {'scope':'local_drag_install_developer_preview_image','status':'passed',
                'app_entry':'Tacto.app','applications_link':'/Applications','format':'UDZO',
                'architecture':platform.machine(),'macos_version':platform.mac_ver()[0],
                'app_signature_verified':True,'image_checksum_verified':True,'source_app_unchanged':True,
                'image_sha256':digest,'image_bytes':output.stat().st_size,
                'elapsed_seconds':time.monotonic()-began,'mounted':False,'gui_launched':False,
                'installed':False,'permissions_changed':False,'notarized':False,'fresh_mac_verified':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args();report=args.report.absolute();private_parent(report)
    with os.fdopen(os.open(report,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'w') as stream:
        try:result=build(args.app,args.output)
        except Exception as error:
            json.dump({'status':'failed','error_class':type(error).__name__,'gui_launched':False,
                       'installed':False,'permissions_changed':False,'notarized':False},stream);stream.write('\n');raise
        json.dump(result,stream,indent=2);stream.write('\n')
    print('Created developer preview image; not installed or notarized: '+str(args.output))
