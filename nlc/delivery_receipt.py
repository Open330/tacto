"""One host-bound request witnessed in the destination service's inbox.

The host supplies read-only UI binding and authoritative service readers.
Neither a model claim, a sender's Sent marker, nor a queued API response is
delivery evidence. This adapter grants no approval and sends nothing itself.
Services without an observable request identity and recipient inbox receipt
cannot use this adapter. Native chat applications are not implicitly supported.

This is an in-process host trust contract: callbacks must really inspect the
bound control and receiving service. Types and hashes do not authenticate a
service, or exclude a different writer using the same request identity.
"""
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import math
import re
import time

from .artifact_receipt import ArtifactReceiptError
from .commit_evidence import CommitEvidence, DeliveryEvidence, action_digest


PUBLIC_CODES = frozenset({
    'invalid_delivery_event', 'inbox_read_unavailable', 'inbox_receipt_mismatch',
    'already_prepared', 'delivery_binding_unavailable', 'delivery_binding_mismatch',
    'delivery_already_present', 'already_used', 'delivery_commit_mismatch',
    'delivery_not_observed_before_deadline',
})


class DeliveryReceiptError(ArtifactReceiptError):
    """Sanitized refusal; an uncertain transmission must not be resent."""


class InboxTemporarilyUnavailable(RuntimeError):
    """Trusted reader explicitly signals a retryable READ failure, never absence.

    Use only for temporary receiving-service availability errors. Authentication,
    incomplete/malformed records and programming errors must not use this signal.
    Its message is never included in receipt errors or evidence.
    """


class _TransientInboxRead(DeliveryReceiptError):
    pass


@dataclass(frozen=True)
class PendingDelivery:
    request_id: str
    target: str
    obs_id: str
    action_digest: str
    account: str
    recipient: str
    body_sha256: str


@dataclass(frozen=True)
class InboxDelivery:
    request_id: str
    message_id: str
    account: str
    recipient: str
    body_sha256: str
    state: str  # exactly 'delivered'; queued/accepted/sent are insufficient


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    digest: str
    binding: PendingDelivery


def _refuse(code, cls=DeliveryReceiptError):
    assert code in PUBLIC_CODES
    error = cls(code)
    error.public_code = code
    return error


def _text(value):
    return type(value) is str and bool(value.strip()) and len(value) <= 1024


def _identity(value):
    return type(value) is str and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) is not None


def _message_identity(value):
    # Opaque service identifiers are not URLs or executable syntax. Preserve
    # punctuation/Unicode rather than rewriting real receiving identities.
    return (type(value) is str and 0 < len(value) <= 1024
            and value.strip() == value and value.isprintable())


class DeliveredMessageReceipt:
    """Witness delivery of one explicitly bound, previously undelivered request.

    ``bind_request(event)`` must inspect the actual control/payload selected by
    the event's observation and action, returning PendingDelivery. Copying the
    expected fields or taking them from a model is not an implementation.
    ``read_inbox(request_id)`` returns None while no delivered record exists,
    or InboxDelivery from an authenticated authoritative receiving service.
    It must distinguish unavailable/incomplete reads by raising, not None.
    A reader may explicitly raise InboxTemporarilyUnavailable for a temporary
    read outage. Only after dispatch is that read repeated within the deadline;
    preparation still refuses because absence could not be verified.

    The service must bind request_id to this one immutable payload and stable
    message identity. The host owns/exclusively uses that request identity;
    no receipt before dispatch may already exist. Each adapter allows one
    preparation attempt and one after-commit call. Polling reads only, never
    clicks or retries the transmission. Deadlines cannot cancel OS/API calls.
    """
    def __init__(self, *, target, account, recipient, text, bind_request, read_inbox,
                 timeout_seconds=2.0, poll_interval=.02, clock=None, sleep=None):
        if not all(_text(v) for v in (target, account, recipient)):
            raise ValueError('delivery target/account/recipient must be nonempty strings')
        if type(text) is not str or len(text.encode('utf-8')) > 4 * 1024 * 1024:
            raise ValueError('delivery text must be within the UTF-8 byte budget')
        if not callable(bind_request) or not callable(read_inbox):
            raise ValueError('delivery requires trusted host readers')
        for value in (timeout_seconds, poll_interval):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError('delivery polling budgets must be finite and positive')
        self._target, self._account, self._recipient = target, account, recipient
        self._hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
        self._bind, self._inbox = bind_request, read_inbox
        self._timeout, self._poll = float(timeout_seconds), float(poll_interval)
        self._clock, self._sleep = clock or time.monotonic, sleep or time.sleep
        self._prepare_used = self._after_used = False
        self._prepared = None

    @property
    def commit_expected(self):
        return {'받는 사람': self._recipient, '보내는 계정': self._account,
                '보낸 내용 sha256': self._hash}

    def _digest(self, event):
        try:
            if type(event) is not dict:
                raise ValueError()
            effects, action = set(event['effects']), event['action']
            if (event['target'] != self._target or not _text(event['event_id'])
                    or not _text(event['obs_id']) or type(action) is not dict
                    or action.get('name') not in {'click', 'press_key'}
                    or 'transmit' not in effects
                    or effects - {'transmit', 'read', 'write_ephemeral'}):
                raise ValueError()
            if (('element_index' in action and
                 (type(action['element_index']) is not int or action['element_index'] < 0))
                    or (action['name'] == 'click' and set(action) != {'name', 'element_index'})
                    or (action['name'] == 'press_key' and
                        (set(action) not in ({'name', 'key'}, {'name', 'key', 'element_index'})
                         or action['key'] != 'Return'))):
                raise ValueError()
            digest = action_digest(action, effects, self._target, obs_id=event['obs_id'])
            if event['action_digest'] != digest:
                raise ValueError()
            return digest
        except (KeyError, TypeError, ValueError):
            raise _refuse('invalid_delivery_event') from None

    def _read(self, request_id):
        try:
            result = self._inbox(request_id)
        except InboxTemporarilyUnavailable:
            raise _refuse('inbox_read_unavailable', _TransientInboxRead) from None
        except Exception:
            raise _refuse('inbox_read_unavailable') from None
        if result is None:
            return None
        if (type(result) is not InboxDelivery
                or not all(type(value) is str for value in
                           (result.request_id, result.account, result.recipient,
                            result.body_sha256, result.state))
                or result.request_id != request_id
                or not _message_identity(result.message_id) or result.state != 'delivered'
                or result.account != self._account or result.recipient != self._recipient
                or result.body_sha256 != self._hash):
            raise _refuse('inbox_receipt_mismatch')
        return result

    def prepare_commit(self, event):
        if self._prepare_used:
            raise _refuse('already_prepared')
        self._prepare_used = True
        digest = self._digest(event)
        try:
            binding = self._bind(deepcopy(event))
        except Exception:
            raise _refuse('delivery_binding_unavailable') from None
        if (type(binding) is not PendingDelivery
                or not all(type(value) is str for value in
                           (binding.target, binding.obs_id, binding.action_digest,
                            binding.account, binding.recipient, binding.body_sha256))
                or not _identity(binding.request_id)
                or binding.target != self._target or binding.obs_id != event['obs_id']
                or binding.action_digest != digest or binding.account != self._account
                or binding.recipient != self._recipient or binding.body_sha256 != self._hash):
            raise _refuse('delivery_binding_mismatch')
        if self._read(binding.request_id) is not None:
            raise _refuse('delivery_already_present')
        self._prepared = _Prepared(event['event_id'], digest, binding)
        return self._prepared

    def after_commit(self, event, driver_result, context):
        if self._after_used:
            raise _refuse('already_used')
        self._after_used = True
        prepared = self._prepared
        if (type(context) is not _Prepared or context is not prepared or type(event) is not dict
                or event.get('event_id') != prepared.event_id
                or self._digest(event) != prepared.digest):
            raise _refuse('delivery_commit_mismatch')
        deadline = self._clock() + self._timeout
        last_code = 'delivery_not_observed_before_deadline'
        while True:
            # Do not start another remote read when the budget is already spent.
            # An in-progress reader still cannot be forcibly interrupted here.
            if self._clock() >= deadline:
                raise _refuse(last_code)
            try:
                delivered = self._read(prepared.binding.request_id)
            except _TransientInboxRead:
                delivered = None
                last_code = 'inbox_read_unavailable'
            else:
                last_code = 'delivery_not_observed_before_deadline'
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _refuse(last_code)
            if delivered is not None:
                return CommitEvidence(prepared.event_id, prepared.digest,
                                      DeliveryEvidence(delivered.message_id, self._recipient,
                                                       self._account, self._hash))
            self._sleep(min(self._poll, remaining))
