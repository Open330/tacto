"""Owned signed evaluation worker; no reusable native resume authority."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

TARGET = 'ai.callabo.tacto.save-writer-fixture'


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > 65536):
            raise ValueError('private worker record required')
        data = os.read(fd, 65537)
        def version(info):
            # Reading may change atime. It is not a content/identity mutation.
            return (info.st_dev,info.st_ino,info.st_uid,info.st_nlink,info.st_mode,
                    info.st_size,info.st_mtime_ns,info.st_ctime_ns)
        if len(data) != before.st_size or version(os.fstat(fd)) != version(before):
            raise ValueError('worker record changed')
        return json.loads(data)
    finally:
        os.close(fd)


def write_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file); file.flush(); os.fsync(file.fileno())


def configuration(path):
    cfg = private_json(path)
    if type(cfg) is not dict or set(cfg) != {'work','nonce','pid','start','callback_dev','callback_ino'}:
        raise ValueError('invalid worker configuration')
    work = Path(cfg['work']) if type(cfg['work']) is str else Path('.')
    caches = (Path.home()/'Library/Caches').resolve()
    if (not work.is_absolute() or work.resolve() != work or work.parent != caches
            or not work.name.startswith('tacto-save-writer-')):
        raise ValueError('owned fixture work directory required')
    info = work.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError('private worker directory required')
    if (type(cfg['nonce']) is not str or re.fullmatch('[0-9a-f]{32}',cfg['nonce']) is None
            or type(cfg['pid']) is not int or cfg['pid'] <= 0
            or type(cfg['start']) not in (float,int) or not 0 < cfg['start'] < 1e12
            or any(type(cfg[key]) is not int or cfg[key] < 0 for key in ('callback_dev','callback_ino'))):
        raise ValueError('invalid producer identity')
    if Path(path) != work/'worker-config.json':
        raise ValueError('worker configuration location changed')
    return cfg, work


def execute(driver, config_path, phase):
    from tacto.helper import InheritedHelperDriver
    from tacto.dispatch_journal import DurableDispatchDriver
    from tacto.producer_file_receipt import ProducerObservation, ProducerFileReceipt
    from tacto.commit_evidence import make_event, evidence_error
    from tacto.driver import Driver
    from tacto.native_process_identity import process_start_identity
    from dataclasses import asdict
    if not isinstance(driver,InheritedHelperDriver) or phase not in ('dispatch','restart','reconcile'):
        raise ValueError('signed worker phase required')
    cfg,work = configuration(config_path)
    filename='fresh-'+cfg['nonce']+'.rtf'
    body='{\\rtf1\\ansi TACTO owned save '+cfg['nonce']+'}'
    destination=work/'output';path=destination/filename
    fd=os.open(work/'callback.jsonl',os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info=os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_nlink!=1
                or stat.S_IMODE(info.st_mode)!=0o600
                or (info.st_dev,info.st_ino)!=(cfg['callback_dev'],cfg['callback_ino'])):
            raise ValueError('producer channel changed')
        def observed():
            data=os.pread(fd,16385,0)
            if len(data)>16384 or not data.endswith(b'\n'):raise ValueError('producer channel unavailable')
            rows=[json.loads(line) for line in data.splitlines()]
            if rows[:1]!=[{'callback':'ready','nonce':cfg['nonce']}]:raise ValueError('producer not ready')
            if len(rows)==1:return ProducerObservation(TARGET,cfg['pid'],cfg['start'],cfg['nonce'],0,'ready')
            expected={'callback':'written','nonce':cfg['nonce'],'path':str(path),
                      'sha256':hashlib.sha256(body.encode()).hexdigest(),'response':1}
            if rows[1:]!=[expected] or type(rows[1]['response']) is not int:
                raise ValueError('producer completion inconsistent')
            return ProducerObservation(TARGET,cfg['pid'],cfg['start'],cfg['nonce'],1,'written',str(path),expected['sha256'])
        if phase=='reconcile':
            from tacto.journal_reconciliation import CommitReconciliation,reconcile_journal
            from tacto.commit_evidence import CommitEvidence,ProducerArtifactEvidence
            from tacto.existing_file_receipt import _file_state
            from examples.probes.signed_native_continuation import run
            original=private_json(work/'worker-original-commit.json')
            if type(original) is not dict or set(original)!={'event','expected'}:
                raise ValueError('original host commit unavailable')
            expected=original['expected']
            known={'artifact_evidence_scope':'producer','producer':TARGET,'producer_pid':cfg['pid'],
                'producer_start_time':cfg['start'],'producer_nonce':cfg['nonce'],
                '저장 위치':str(destination),'저장한 이름':filename,'다시 읽은 내용':body}
            if expected!=known:raise ValueError('original producer expectation changed')
            record=work/'dispatch-journal/00000.jsonl';before=record.read_bytes()
            def read(slot,event):
                state=observed()
                if state.status!='written' or _file_state(str(path))[5]!=body.encode():
                    raise ValueError('fresh causal producer evidence unavailable')
                if observed()!=state:raise ValueError('producer callback changed')
                return CommitEvidence(event['event_id'],event['action_digest'],ProducerArtifactEvidence(
                    'fresh-native-reconciliation',str(path),state.sha256,TARGET,cfg['pid'],cfg['start'],cfg['nonce'],state.sequence))
            with DurableDispatchDriver(driver,work/'dispatch-journal') as guard:
                count=reconcile_journal(guard,[CommitReconciliation(original['event'],expected)],read)
                continuation=run(driver,guard)
            if record.read_bytes()!=before or observed().sequence!=1:
                raise ValueError('original commit was changed or repeated')
            write_json(work/'worker-restart.json',{'pid':os.getpid(),'reconciled_slots':count,
                'underlying_input_calls':continuation['new_input_calls'],'receiving_sequence':1,
                'fresh_bytes_match':_file_state(str(path))[5]==body.encode(),'original_record_unchanged':True,
                'journal_slots':len(list((work/'dispatch-journal').iterdir())),'continuation':continuation})
            return True
        if phase=='restart':
            # Read receiving state first, but never turn its presence into
            # permission to bypass the journal or resend the Save request.
            state=observed()
            if state.status!='written' or path.read_bytes()!=body.encode():raise ValueError('restart receiving state unavailable')
            calls=[]
            class NoInput:
                def act(self,*args,**kwargs):
                    calls.append(1);raise AssertionError('restart attempted underlying input')
            with DurableDispatchDriver(NoInput(),work/'dispatch-journal') as guarded:
                reply=guarded.act('fresh-worker',TARGET,{'name':'click','element_index':0})
            if reply.get('error')!='dispatch_journal_requires_reconciliation' or calls:
                raise ValueError('restart barrier failed')
            write_json(work/'worker-restart.json',{'pid':os.getpid(),'refusal':reply,
                'underlying_input_calls':0,'receiving_sequence':state.sequence,'fresh_bytes_match':True})
            return True
        local=Driver()
        if local._resolve(TARGET,launch=False)!=cfg['pid'] or process_start_identity(cfg['pid'])!=cfg['start']:
            raise ValueError('owned producer process changed')
        obs=driver.observe(TARGET,'tree_full')
        if obs.get('type')!='observation':raise ValueError('worker observation unavailable')
        windows=[int(line.split()[0]) for line in obs['tree'].splitlines()
                 if len(line.split())>1 and line.split()[1]=='Window']
        if not 0<len(windows)<=4:raise ValueError('worker window count unavailable')
        panels=[driver.save_panel(obs['obs_id'],TARGET,index,require_format=True) for index in windows]
        panels=[panel for panel in panels if panel.get('type')=='save_panel'
            and panel.get('directory')==str(destination) and panel.get('filename')==filename
            and panel.get('format')=='rtf' and panel.get('original_changes_setting')==0]
        if len(panels)!=1:raise ValueError('worker save panel unavailable')
        panel=panels[0];action=panel['commit_control']['action']
        event=make_event(action,['write_persistent'],TARGET,obs_id=obs['obs_id'])
        receipt=ProducerFileReceipt(destination,filename,body,producer=TARGET,pid=cfg['pid'],
            start_time=cfg['start'],nonce=cfg['nonce'],read_producer=observed,timeout_seconds=3)
        context=receipt.prepare_commit(event)
        write_json(work/'worker-original-commit.json',{'event':event,'expected':receipt.commit_expected})
        class CrashAfterReceipt:
            def act(self,*args,**kwargs):
                reply=driver.act(*args,**kwargs)
                proof=receipt.after_commit(event,reply,context)
                if evidence_error({**event,'commit_evidence':proof},receipt.commit_expected) is not None:
                    raise ValueError('worker causal receipt unavailable')
                write_json(work/'worker-proof.json',{'pid':os.getpid(),'event':event,
                    'evidence':asdict(proof.evidence),'dispatch_type':reply.get('type')})
                os._exit(17) # Real worker death, before journal reply logging.
        with DurableDispatchDriver(CrashAfterReceipt(),work/'dispatch-journal') as guarded:
            guarded.act(obs['obs_id'],TARGET,action,save_panel_token=panel['panel_token'],
                risk_class='preapproved',approved=True,intent_text='Save evaluator-owned fresh fixture artifact')
        raise ValueError('worker did not terminate at receipt boundary')
    finally:
        os.close(fd)
