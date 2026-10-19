"""Host-owned link record for a runtime moved on the same filesystem.

Not a signature attestation or a sandbox against another process of this user.
It grants no GUI permission. A copied/rebuilt runtime needs normal installation;
matching names or prefixes never authorize adoption of a broken foreign link.
Identity and launcher/SKILL.md hashes bind the links, not every runtime file.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat


def identity(path):
    st=path.lstat()
    if path.is_symlink() or st.st_uid!=os.getuid():raise ValueError('unowned source')
    return [st.st_dev,st.st_ino]


def source(root,kind):
    target=root/('launcher/tacto' if kind=='command' else 'skill')
    body=target if kind=='command' else target/'SKILL.md'
    st=body.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid()
            or st.st_size>64*1024*1024 or body.is_symlink()):raise ValueError('invalid source')
    return target,identity(target),hashlib.sha256(body.read_bytes()).hexdigest()


def link_path(path):
    path=Path(path)
    if not path.is_absolute():raise ValueError('absolute link required')
    return path.parent.resolve()/path.name


def read_record(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1
                or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size>1024*1024):raise ValueError('invalid record')
        with os.fdopen(fd,'rb',closefd=False) as stream:payload=stream.read(1024*1024+1)
        if len(payload)>1024*1024:raise ValueError('record too large')
        data=json.loads(payload)
        if type(data) is not dict or set(data)!={'schema','root_identity','entries'} or data['schema']!='nlc-links-v1':raise ValueError('invalid schema')
        def stamp(value):return type(value) is list and len(value)==2 and all(type(v) is int and v>=0 for v in value)
        if not stamp(data['root_identity']):raise ValueError('invalid identity')
        if type(data['entries']) is not list or len(data['entries'])>1000:raise ValueError('invalid entries')
        seen=set()
        for entry in data['entries']:
            if (type(entry) is not dict or set(entry)!={'kind','link','target','identity','sha256'}
                    or entry['kind'] not in ('command','skill') or not stamp(entry['identity'])
                    or not all(isinstance(entry.get(k),str) for k in ('link','target','sha256'))
                    or not Path(entry['link']).is_absolute() or not Path(entry['target']).is_absolute()
                    or len(entry['sha256'])!=64 or any(c not in '0123456789abcdef' for c in entry['sha256'])
                    or entry['link'] in seen):raise ValueError('invalid entry')
            seen.add(entry['link'])
        return data
    finally:os.close(fd)


def authorize(record,root,link,kind):
    if kind not in ('command','skill'):return False
    try:
        data=read_record(record);root=Path(root).resolve(strict=True);link=link_path(link)
        if identity(root)!=data['root_identity'] or not link.is_symlink():return False
        if link.lstat().st_uid!=os.getuid():return False
        target,stamp,digest=source(root,kind)
        for entry in data['entries']:
            if type(entry) is not dict or set(entry)!={'kind','link','target','identity','sha256'}:return False
            if entry['link']==str(link):
                return (entry['kind']==kind and os.readlink(link)==entry['target']
                        and entry['identity']==stamp and entry['sha256']==digest)
    except (OSError,ValueError,TypeError,KeyError,RecursionError):return False
    return False


def write_record(path,root,command_link,skill_links,*,command_links=()):
    root=Path(root).resolve(strict=True)
    data={'schema':'nlc-links-v1','root_identity':identity(root),'entries':[]}
    seen=set()
    for kind,raw in [('command',command_link),*[('command',p) for p in command_links],*[('skill',p) for p in skill_links]]:
        link=link_path(raw)
        if str(link) in seen:continue
        seen.add(str(link));target,stamp,digest=source(root,kind)
        if not link.is_symlink() or link.lstat().st_uid!=os.getuid() or os.readlink(link)!=str(target):raise ValueError('link not installed')
        data['entries'].append({'kind':kind,'link':str(link),'target':str(target),'identity':stamp,'sha256':digest})
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as file:json.dump(data,file);file.write('\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['authorize','write'])
    parser.add_argument('--record',required=True);parser.add_argument('--root',required=True)
    parser.add_argument('--link',required=True);parser.add_argument('--kind',choices=['command','skill'])
    parser.add_argument('--skill-link',action='append',default=[])
    parser.add_argument('--command-link',action='append',default=[])
    args=parser.parse_args()
    if args.mode=='authorize':return 0 if authorize(args.record,args.root,args.link,args.kind) else 1
    try:write_record(args.record,args.root,args.link,args.skill_link,command_links=args.command_link)
    except (OSError,ValueError,TypeError):return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
