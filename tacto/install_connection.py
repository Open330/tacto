"""Explicit setup-button connection with private relocation records.

No GUI permission request. Success requires both install and read-only check.
The record binds selected link identities/bytes, not every runtime file.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import signal
import subprocess
import uuid
from .install_record import authorize, identity, read_record, link_path


def run_install(command):
    with subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL,start_new_session=True) as process:
        try:
            process.wait(timeout=30)
        except BaseException:
            try:os.killpg(process.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            process.wait()
            raise
        return process.returncode


def _requested_links(bin_dir, skill_dirs):
    """Mirror installer destinations only for record reuse, never link authority.

    The installer --check remains required and performs its own selection.
    Unresolved/relative defaults cannot authorize reuse.
    """
    home = Path.home()
    command = Path(bin_dir if bin_dir is not None else (os.environ.get('TACTO_BIN_DIR') or str(home/'.local/bin')))
    dirs = list(skill_dirs)
    if not dirs:
        dirs = [home/'.claude/skills', home/'.codex/skills']
        for key in ('CLAUDE_CONFIG_DIR', 'CODEX_HOME'):
            if os.environ.get(key): dirs.append(Path(os.environ[key])/'skills')
        dirs.extend(p for p in (home/'Library/Application Support/asx/profiles').glob('*/skills') if p.is_dir())
    return {str(link_path(command/'tacto')):'command',
            **{str(link_path(Path(parent)/'tacto')):'skill' for parent in dirs}}


def _record_matches_request(record, data, root, requested):
    if data['root_identity'] != identity(root): return False
    entries = {entry['link']: entry for entry in data['entries']}
    if set(entries) != set(requested): return False
    for link, kind in requested.items():
        entry = entries[link]
        target = root/('launcher/tacto' if kind=='command' else 'skill')
        if (entry['kind'] != kind or entry['target'] != str(target)
                or not authorize(record, root, link, kind)): return False
    return True


def connect(root, state_dir, *, bin_dir=None, skill_dirs=()):
    outcome={'status':'failed','checked':False,'record_written':False}
    root=Path(root).resolve(strict=True);state_dir=Path(state_dir)
    if not state_dir.is_absolute():return {**outcome,'error':'invalid_state_directory'}
    for path in [bin_dir,*skill_dirs]:
        if path is not None and not Path(path).is_absolute():
            return {**outcome,'error':'invalid_install_destination'}
    # The host selected this state directory. Refuse foreign/symlink final
    # entries; parent paths and other same-user processes are not a sandbox.
    state_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    info=state_dir.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
            or stat.S_IMODE(info.st_mode)!=0o700):
        return {**outcome,'error':'invalid_state_directory'}
    fd=os.open(state_dir/'connection.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or info.st_nlink!=1 or stat.S_IMODE(info.st_mode)!=0o600):
            return {**outcome,'error':'invalid_connection_lock'}
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return {**outcome,'error':'connection_busy'}
        args=[]
        if bin_dir is not None:args+=['--bin-dir',str(bin_dir)]
        for path in skill_dirs:args+=['--skill-dir',str(path)]
        script=root/'skill/install.sh'
        requested=_requested_links(bin_dir, skill_dirs)
        records=[]
        catalog_full=False
        for entry in state_dir.iterdir():
            if entry.name.endswith('.json'):
                records.append(entry)
                if len(records)>=1000:
                    catalog_full=True;break
        previous=None
        for record in sorted(records,key=lambda p:p.name,reverse=True):
            try:
                data=read_record(record)
                entries=data['entries']
                if _record_matches_request(record, data, root, requested):
                    check=run_install(['/bin/bash',str(script),'--check',*args])
                    if check==0 and _record_matches_request(record,read_record(record),root,requested):
                        return {**outcome,'status':'connected','checked':True,'record_reused':True}
                # The command may have been removed while a recorded skill
                # still proves this runtime's relocation. Each actual link
                # replacement is independently authorized by the installer.
                if (previous is None and data['root_identity']==identity(root) and any(
                        authorize(record,root,entry['link'],entry['kind']) for entry in entries)):
                    previous=record
            except (OSError,ValueError,TypeError,RecursionError):continue
        if catalog_full:return {**outcome,'error':'record_catalog_full'}
        receipt=state_dir/('links-'+uuid.uuid4().hex+'.json')
        command=['/bin/bash',str(script),*args,'--record',str(receipt)]
        if previous is not None:command+=['--repair-from',str(previous)]
        code=run_install(command)
        outcome['record_written']=receipt.is_file() and not receipt.is_symlink()
        if code!=0:return {**outcome,'error':'installation_failed'}
        check=run_install(['/bin/bash',str(script),'--check',*args])
        if check!=0:return {**outcome,'error':'connection_check_failed'}
        outcome['checked']=True
        if not outcome['record_written']:return {**outcome,'error':'record_missing'}
        return {**outcome,'status':'connected'}
    finally:os.close(fd)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True);parser.add_argument('--state-dir',required=True)
    parser.add_argument('--bin-dir');parser.add_argument('--skill-dir',action='append',default=[])
    args=parser.parse_args()
    try:result=connect(args.root,args.state_dir,bin_dir=args.bin_dir,skill_dirs=args.skill_dir)
    except subprocess.TimeoutExpired:result={'status':'failed','error':'installation_timeout'}
    except (OSError,ValueError):result={'status':'failed','error':'connection_unavailable'}
    print(json.dumps(result))
    return 0 if result['status']=='connected' else 1


if __name__=='__main__':raise SystemExit(main())
