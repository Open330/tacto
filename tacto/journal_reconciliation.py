"""Explicit host reconciliation of causal commit slots; no automatic replay.

The journal is a barrier, never a source of expected outcomes or approval.
Hosts retain the original plan and read fresh producer/receiver evidence. This
API cannot reconcile ephemeral inputs, saved-state-only or sender transcripts.
"""
from copy import deepcopy
import hashlib
import json
import math
import os
import re
import stat
import time

from .commit_evidence import (CommitEvidence, DeliveryEvidence, ProducerArtifactEvidence,
                              _event_binding_matches, evidence_error)
from .dispatch_journal import DurableDispatchDriver, operation_fingerprint, _replay_fingerprint


PUBLIC_CODES=frozenset({
    'invalid_original_commit','incomplete_delivery_expectation','incomplete_producer_expectation',
    'unsupported_commit_effects','invalid_journal_record','journal_changed','journal_directory_changed',
    'invalid_journal_catalog','invalid_reconciliation','invalid_commit_plan','reconciliation_cancelled',
    'reconciliation_deadline','fresh_journal_wrapper_required','commit_plan_mismatch',
    'causal_commit_unverified','duplicate_causal_receipt','reconciliation_unavailable'})


class JournalReconciliationError(ValueError):
    def __init__(self, code):
        if not isinstance(code,str) or code not in PUBLIC_CODES:code='reconciliation_unavailable'
        self.public_code=code
        super().__init__(code)


class CommitReconciliation:
    """Original host-owned event and expectation; never inferred from a log."""
    def __init__(self, event, expected, *, expect=None):
        event=deepcopy(event);expected=deepcopy(expected);expect=deepcopy(expect)
        if (type(event) is not dict or type(expected) is not dict
                or not _event_binding_matches(event,event.get('event_id'),event.get('action_digest'))):
            raise JournalReconciliationError('invalid_original_commit')
        effects=set(event['effects'])
        if effects <= {'transmit','read','write_ephemeral'} and 'transmit' in effects:
            if not all(isinstance(expected.get(k),str) and expected[k].strip()
                       for k in ('받는 사람','보내는 계정')) or not (
                       isinstance(expected.get('보낸 내용'),str)
                       or re.fullmatch('[0-9a-f]{64}',str(expected.get('보낸 내용 sha256','')))):
                raise JournalReconciliationError('incomplete_delivery_expectation')
            kind=DeliveryEvidence
        elif effects <= {'write_persistent','read','write_ephemeral'} and 'write_persistent' in effects:
            if (expected.get('artifact_evidence_scope')!='producer'
                    or not all(expected.get(k) is not None for k in (
                        'producer','producer_pid','producer_start_time','producer_nonce',
                        '저장 위치','저장한 이름','다시 읽은 내용'))):
                raise JournalReconciliationError('incomplete_producer_expectation')
            kind=ProducerArtifactEvidence
        else:
            raise JournalReconciliationError('unsupported_commit_effects')
        self._event,self._expected,self._kind=event,expected,kind
        self._operation=operation_fingerprint(event['target'],event['action'],expect)
        self._replay_operation=_replay_fingerprint(event['target'],event['action'],expect)

    def event(self):
        return deepcopy(self._event)


def _fingerprint(info):
    return (info.st_dev,info.st_ino,info.st_mode,info.st_uid,info.st_nlink,
            info.st_size,info.st_mtime_ns,info.st_ctime_ns)


def _object(pairs):
    value=dict(pairs)
    if len(value)!=len(pairs):raise JournalReconciliationError('invalid_journal_record')
    return value


def _slot(guard, number):
    descriptor=os.open(f'{number:05d}.jsonl',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,
                       dir_fd=guard._directory_fd)
    try:
        info=os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size>4096):
            raise JournalReconciliationError('invalid_journal_record')
        data=os.read(descriptor,4097)
        if _fingerprint(os.fstat(descriptor))!=_fingerprint(info) or len(data)!=info.st_size:
            raise JournalReconciliationError('journal_changed')
        named=os.stat(f'{number:05d}.jsonl',dir_fd=guard._directory_fd,follow_symlinks=False)
        if _fingerprint(named)!=_fingerprint(info):raise JournalReconciliationError('journal_changed')
        lines=data.splitlines()
        if not 1<=len(lines)<=2:raise JournalReconciliationError('invalid_journal_record')
        first=json.loads(lines[0],object_pairs_hook=_object)
        if (type(first) is not dict or set(first)!={'schema','slot','operation_sha256','state'}
                or first['schema']!='tacto-dispatch-v1' or type(first['slot']) is not int
                or first['slot']!=number or first['state']!='reserved'
                or not isinstance(first['operation_sha256'],str)
                or re.fullmatch('[0-9a-f]{64}',first['operation_sha256']) is None):
            raise JournalReconciliationError('invalid_journal_record')
        if len(lines)==2:
            reply=json.loads(lines[1],object_pairs_hook=_object)
            if (type(reply) is not dict or set(reply)!={'state','category'}
                    or reply['state']!='reply_received' or reply['category'] not in ('acted','not_started','unknown')):
                raise JournalReconciliationError('invalid_journal_record')
        return (number,first['operation_sha256'],hashlib.sha256(data).hexdigest(),_fingerprint(info))
    finally:os.close(descriptor)


def _directory(guard):
    info=os.fstat(guard._directory_fd);named=guard.directory.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
            or stat.S_IMODE(info.st_mode)!=0o700
            or (named.st_dev,named.st_ino)!=(info.st_dev,info.st_ino)
            or not stat.S_ISDIR(named.st_mode)):
        raise JournalReconciliationError('journal_directory_changed')
    return (info.st_dev,info.st_ino)


def _prefix(guard):
    identity=_directory(guard)
    names=sorted(os.listdir(guard._directory_fd))
    if (not names or len(names)>10000
            or names!=[f'{n:05d}.jsonl' for n in range(len(names))]):
        raise JournalReconciliationError('invalid_journal_catalog')
    return identity,tuple(_slot(guard,n) for n in range(len(names)))


def _prefix_unchanged(guard, prefix):
    try:
        identity,records=prefix
        return _directory(guard)==identity and all(_slot(guard,row[0])==row for row in records)
    except (OSError,ValueError,TypeError):return False


def reconcile_journal(guard, commits, read_evidence, *, timeout_seconds=30,
                      cancelled=None, clock=None):
    """Read-only host proof gate, then advance this fresh wrapper to NEW input.

    No slots are removed/rewritten. Every existing slot needs its original host
    commit and fresh causal proof. Default restart behavior stays a refusal.
    Callbacks must only read; direct use of the underlying driver bypasses this
    barrier. One ordered writer per directory remains required.
    """
    try:
        finite=type(timeout_seconds) in (int,float) and math.isfinite(timeout_seconds)
    except OverflowError:finite=False
    if (type(guard) is not DurableDispatchDriver or not callable(read_evidence) or not finite
            or timeout_seconds<=0 or (cancelled is not None and not callable(cancelled))
            or (clock is not None and not callable(clock))):
        raise JournalReconciliationError('invalid_reconciliation')
    now=clock if clock is not None else time.monotonic
    previous=[None]
    def timestamp():
        value=now()
        try:valid=type(value) in (int,float) and math.isfinite(value)
        except OverflowError:valid=False
        if not valid or (previous[0] is not None and value<previous[0]):
            raise JournalReconciliationError('invalid_reconciliation')
        previous[0]=value
        return value
    def ready():
        if cancelled is not None and cancelled():raise JournalReconciliationError('reconciliation_cancelled')
        if timestamp()>=deadline:raise JournalReconciliationError('reconciliation_deadline')
    with guard._journal_lock:
        if (guard._reconciliation_attempted or guard._blocked or guard._sequence!=0
                or guard._directory_fd is None):
            raise JournalReconciliationError('fresh_journal_wrapper_required')
        guard._reconciliation_attempted=True;guard._blocked=True
        try:
            if (type(commits) not in (list,tuple) or not commits or len(commits)>10000
                    or any(type(p) is not CommitReconciliation for p in commits)):
                raise JournalReconciliationError('invalid_commit_plan')
            plans=deepcopy(tuple(commits))
            deadline=timestamp()+timeout_seconds
            if not math.isfinite(deadline):raise JournalReconciliationError('invalid_reconciliation')
            ready();prefix=_prefix(guard);records=prefix[1]
            if len(plans)!=len(records):raise JournalReconciliationError('commit_plan_mismatch')
            receipts=set()
            for plan,row in zip(plans,records):
                ready()
                if plan._operation!=row[1]:raise JournalReconciliationError('commit_plan_mismatch')
                event=plan.event();proof=read_evidence(row[0],event)
                ready()
                if (type(proof) is not CommitEvidence or type(proof.evidence) is not plan._kind
                        or evidence_error({**plan._event,'commit_evidence':proof},plan._expected) is not None):
                    raise JournalReconciliationError('causal_commit_unverified')
                detail=proof.evidence
                identity=(('delivery',detail.account,detail.recipient,detail.receipt_id)
                    if type(detail) is DeliveryEvidence else
                    ('producer',detail.producer,detail.producer_pid,detail.producer_start_time,
                     detail.nonce,detail.completion_sequence))
                if identity in receipts:raise JournalReconciliationError('duplicate_causal_receipt')
                receipts.add(identity)
            ready()
            if guard._sequence!=0 or not _prefix_unchanged(guard,prefix) or _prefix(guard)!=prefix:
                raise JournalReconciliationError('journal_changed')
            guard._reconciled_prefix=prefix
            guard._reconciled_operations=frozenset(plan._replay_operation for plan in plans)
            guard._sequence=len(records);guard._blocked=False
            return len(records)
        except JournalReconciliationError:raise
        except Exception:
            raise JournalReconciliationError('reconciliation_unavailable') from None


def reconcile_and_replay(guard, commits, read_evidence, continuation, *,
                         timeout_seconds=30, cancelled=None, **replay_options):
    """Run a host-authored NEW Flow after fresh proof of the whole old prefix.

    This does not infer a cursor or skip steps in an old recipe. The host must
    retain and authorize a concrete continuation. Freeze its recipe and static
    approvals/expectations before readers run; evidence callbacks cannot replace
    those inputs. Ordinary replay approval, preparation and receipt gates apply.
    The supplied fresh wrapper remains caller-owned and is never replaced.
    """
    from .flow import Flow, replay
    if type(guard) is not DurableDispatchDriver:
        raise JournalReconciliationError('invalid_reconciliation')
    if type(continuation) is not Flow or type(continuation.schema) is not int or continuation.schema != 2:
        raise JournalReconciliationError('invalid_commit_plan')
    import inspect
    try:
        inspect.signature(replay).bind(guard, continuation, **replay_options)
    except TypeError:
        raise JournalReconciliationError('invalid_reconciliation') from None
    flow = deepcopy(continuation)
    options = dict(replay_options)
    # Hooks remain the original trusted callbacks, not deep-copied driver state.
    for key in ('approvals', 'commit_expected', 'visual_scopes'):
        if key in options:
            options[key] = (dict(options[key]) if key == 'visual_scopes'
                            and type(options[key]) is dict else deepcopy(options[key]))
    with guard._journal_lock:
        reconcile_journal(guard, commits, read_evidence,
                          timeout_seconds=timeout_seconds, cancelled=cancelled)
        return replay(guard, flow, **options)
