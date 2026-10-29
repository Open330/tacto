"""Opt-in durable input barrier, not automatic resume or completion evidence.

Use the same private directory for one logical run, including after restart.
Existing slots refuse dispatch even when a reply was received. Reconcile actual
receiving state before choosing how to continue; never delete slots to retry.
Only hashes and closed reply categories are stored, not screen text/actions.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import threading


def operation_fingerprint(target, action, expect=None):
    return hashlib.sha256(json.dumps({'target':target,'action':action,'expect':expect},
        sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def _replay_fingerprint(target, action, expect=None):
    # Index changes alone cannot turn a completed GUI commit into new input.
    stable={k:v for k,v in action.items() if k!='element_index'} if type(action) is dict else action
    return operation_fingerprint(target,stable,expect)


class DurableDispatchDriver:
    def __init__(self, driver, directory):
        self.driver=driver;self.directory=Path(directory);self._sequence=0;self._blocked=False
        self._journal_lock=threading.RLock()
        self._reconciliation_attempted=False;self._reconciled_prefix=None
        self._reconciled_operations=frozenset()
        if not self.directory.is_absolute():raise ValueError('absolute journal directory required')
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=0o700):raise ValueError('private owned directory required')
        self._directory_fd=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        opened=os.fstat(self._directory_fd)
        if ((opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino)
                or opened.st_uid!=os.getuid() or stat.S_IMODE(opened.st_mode)!=0o700):
            self.close_journal()
            raise ValueError('journal directory changed while opening')

    def __getattr__(self, name):return getattr(self.driver,name)

    def close_journal(self):
        with self._journal_lock:
            if self._directory_fd is not None:
                os.close(self._directory_fd);self._directory_fd=None

    def __enter__(self):return self
    def __exit__(self,*args):self.close_journal()

    def act(self, obs_id, target, action, **kwargs):
        with self._journal_lock:
            if kwargs.get('sensitive_input') is True:
                metadata={'name':'credential_input','element_index':action.get('element_index')}
                return self._act(obs_id,target,metadata,
                    expect={'credential_element':action.get('element_index')},
                    _dispatch=lambda:self.driver.act(obs_id,target,action,**kwargs))
            return self._act(obs_id,target,action,**kwargs)

    def credential_input(self,obs_id,target,index,value,**kwargs):
        with self._journal_lock:
            if self._blocked:
                return {'type':'error','error':'dispatch_journal_requires_reconciliation','effect':'not_started'}
            if kwargs.get('check_only'):
                return self.driver.credential_input(obs_id,target,index,value,**kwargs)
            # Reserve the write just like any other input, but never hash or
            # persist a credential value (even a low-entropy password).
            return self._act(obs_id,target,{'name':'credential_input','element_index':index},
                expect={'credential_origin':kwargs.get('expected_origin') or '', 'credential_element':index},
                _dispatch=lambda:self.driver.credential_input(obs_id,target,index,value,**kwargs))

    def credential_submit(self,obs_id,target,index,**kwargs):
        with self._journal_lock:
            return self._act(obs_id,target,{'name':'credential_submit','element_index':index},
                expect={'credential_origin':kwargs.get('expected_origin') or '', 'credential_element':index},
                _dispatch=lambda:self.driver.credential_submit(obs_id,target,index,**kwargs))

    def _act(self, obs_id, target, action, _dispatch=None, **kwargs):
        if type(action) is dict and action.get('name')=='read':
            return self.driver.act(obs_id,target,action,**kwargs)
        if self._reconciled_prefix is not None:
            from .journal_reconciliation import _prefix_unchanged
            if not _prefix_unchanged(self,self._reconciled_prefix):
                self._blocked=True
                return {'type':'error','error':'dispatch_journal_requires_reconciliation','effect':'not_started'}
        sequence=self._sequence;self._sequence+=1
        if self._blocked or sequence>=10000 or self._directory_fd is None:
            return {'type':'error','error':'dispatch_journal_unavailable','effect':'not_started'}
        fd=None
        try:
            digest=operation_fingerprint(target,action,kwargs.get('expect'))
            if self._reconciled_prefix is not None and _replay_fingerprint(
                    target,action,kwargs.get('expect')) in self._reconciled_operations:
                self._blocked=True
                return {'type':'error','error':'dispatch_journal_reconciled_operation_replay','effect':'not_started'}
            fd=os.open(f'{sequence:05d}.jsonl',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,
                0o600,dir_fd=self._directory_fd)
            with os.fdopen(fd,'w',closefd=False) as file:
                file.write(json.dumps({'schema':'tacto-dispatch-v1','slot':sequence,
                    'operation_sha256':digest,'state':'reserved'})+'\n');file.flush();os.fsync(fd)
            # Persist the directory entry before sending any input. A torn
            # record still blocks reuse because O_EXCL does not parse it.
            os.fsync(self._directory_fd)
        except FileExistsError:
            self._blocked=True
            return {'type':'error','error':'dispatch_journal_requires_reconciliation','effect':'not_started'}
        except (OSError,ValueError,TypeError):
            self._blocked=True
            if fd is not None:os.close(fd)
            return {'type':'error','error':'dispatch_journal_unavailable','effect':'not_started'}
        try:
            # Any exception/crash leaves the reservation durable. The caller
            # retains its normal unknown-effect and causal receipt handling.
            try:reply=_dispatch() if _dispatch is not None else self.driver.act(obs_id,target,action,**kwargs)
            except BaseException:
                self._blocked=True
                raise
            category=('acted' if type(reply) is dict and reply.get('type')=='acted' and not reply.get('unverified')
                else 'not_started' if type(reply) is dict and reply.get('effect')=='not_started'
                else 'unknown')
            if category=='unknown':self._blocked=True
            try:
                with os.fdopen(fd,'a',closefd=False) as file:
                    file.write(json.dumps({'state':'reply_received','category':category})+'\n')
                    file.flush();os.fsync(fd)
            except OSError:
                self._blocked=True
                # Input already ran; never describe logging failure as no effect.
                return {'type':'error','error':'dispatch_journal_write_failed','effect':'unknown'}
            return reply
        finally:os.close(fd)
