"""Fresh artifact plus a trusted cooperating application's completion callback.

The callback reader must be host-owned and independently bound to the producer
instance. Typed data is not authentication or general OS writer attribution.
"""
from dataclasses import dataclass
import time
import threading
from .artifact_receipt import ArtifactReceiptError, FreshFileReceipt
from .commit_evidence import CommitEvidence, ProducerArtifactEvidence

PUBLIC_CODES = frozenset({'producer_observation_unavailable','producer_observation_invalid',
    'producer_already_prepared','producer_callback_not_ready','producer_commit_stale',
    'producer_callback_failed','producer_callback_inconsistent','producer_target_mismatch',
    'producer_callback_changed','producer_preparation_changed'})


def _refuse(code):
    assert code in PUBLIC_CODES
    error=ArtifactReceiptError(code)
    error.public_code=code
    return error


@dataclass(frozen=True)
class ProducerObservation:
    producer: str
    pid: int
    start_time: float
    nonce: str
    sequence: int
    status: str
    path: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class _Prepared:
    fresh_context: object
    sequence: int


class ProducerFileReceipt:
    def __init__(self,directory,filename,text,*,producer,pid,start_time,nonce,read_producer,
                 timeout_seconds=3,poll_interval=.02,clock=None,sleep=None):
        from .commit_evidence import producer_identity_valid
        if not producer_identity_valid(producer,pid,start_time,nonce) or not callable(read_producer):
            raise ValueError('invalid trusted producer configuration')
        self._identity=(producer,pid,float(start_time),nonce)
        self._reader=read_producer
        from .receipt_clock import checked_receipt_clock
        self._clock=checked_receipt_clock(clock)
        self._sleep=sleep or time.sleep
        self._timeout=timeout_seconds
        self._poll=poll_interval
        self._fresh=FreshFileReceipt(directory,filename,text,timeout_seconds=timeout_seconds,
            poll_interval=poll_interval,clock=self._clock,sleep=self._sleep)
        self._prepared=None
        self._prepare_attempted=False
        self._used=False
        self._lock=threading.Lock()

    @property
    def commit_expected(self):
        producer,pid,start,nonce=self._identity
        return {**self._fresh.commit_expected,'artifact_evidence_scope':'producer',
                'producer':producer,'producer_pid':pid,'producer_start_time':start,'producer_nonce':nonce}

    def _observe(self):
        from .commit_evidence import producer_identity_valid
        try: observed=self._reader()
        except Exception: raise _refuse('producer_observation_unavailable') from None
        if (type(observed) is not ProducerObservation
                or not producer_identity_valid(observed.producer,observed.pid,observed.start_time,observed.nonce)
                or (observed.producer,observed.pid,observed.start_time,observed.nonce)!=self._identity
                or type(observed.sequence) is not int or observed.sequence<0
                or observed.status not in ('ready','written','failed')):
            raise _refuse('producer_observation_invalid')
        return observed

    def prepare_commit(self,event):
        with self._lock:
            if self._prepare_attempted: raise _refuse('producer_already_prepared')
            self._prepare_attempted=True
            if event.get('target')!=self._identity[0]:raise _refuse('producer_target_mismatch')
            before=self._observe()
            if before.status!='ready' or before.path is not None or before.sha256 is not None:
                raise _refuse('producer_callback_not_ready')
            fresh_context=self._fresh.prepare_commit(event)
            # Filesystem preparation can race a producer completion or restart.
            # Do not hand out a dispatch context for a callback that was only
            # ready before those reads. A failed attempt remains consumed.
            after=self._observe()
            if after!=before:raise _refuse('producer_preparation_changed')
            self._prepared=_Prepared(fresh_context,before.sequence)
            return self._prepared

    def after_commit(self,event,driver_result,context):
        with self._lock:
            if self._used or context is None or context is not self._prepared:
                raise _refuse('producer_commit_stale')
            self._used=True
        if event.get('target')!=self._identity[0]:raise _refuse('producer_target_mismatch')
        identifier,digest=self._fresh._event(event)
        if (identifier,digest)!=(context.fresh_context.event_id,context.fresh_context.action_digest):
            raise _refuse('producer_commit_stale')
        if (type(driver_result) is not dict or driver_result.get('type') not in ('acted','error')
                or driver_result.get('effect')=='not_started'):
            raise ArtifactReceiptError('driver did not report dispatch')
        from .receipt_clock import receipt_deadline
        deadline=receipt_deadline(self._clock,self._timeout)
        while self._clock()<deadline:
            observed=self._observe()
            if observed.status=='failed':raise _refuse('producer_callback_failed')
            if observed.status=='written':
                expected=self.commit_expected
                from pathlib import Path
                import hashlib
                path=str(Path(expected['저장 위치'])/expected['저장한 이름'])
                digest=hashlib.sha256(expected['다시 읽은 내용'].encode('utf-8')).hexdigest()
                if (observed.sequence!=context.sequence+1 or observed.path!=path or observed.sha256!=digest):
                    raise _refuse('producer_callback_inconsistent')
                proof=self._fresh.after_commit(event,driver_result,context.fresh_context,deadline=deadline)
                if proof is None or self._clock()>=deadline:return None
                # File verification may run a slow host reader. Recheck the
                # independently bound producer callback before combining it
                # with the file proof; neither snapshot alone proves stability.
                after=self._observe()
                if self._clock()>=deadline:return None
                if after!=observed:raise _refuse('producer_callback_changed')
                producer,pid,start,nonce=self._identity
                return CommitEvidence(proof.event_id,proof.action_digest,ProducerArtifactEvidence(
                    proof.evidence.receipt_id,proof.evidence.path,proof.evidence.sha256,
                    producer,pid,start,nonce,observed.sequence))
            if observed.sequence!=context.sequence or observed.path is not None or observed.sha256 is not None:
                raise _refuse('producer_callback_inconsistent')
            self._sleep(min(self._poll,max(0,deadline-self._clock())))
        return None
