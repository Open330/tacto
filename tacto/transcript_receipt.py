"""Sender-side receipt for one message sent from a native chat window.

A chat app with no service API (KakaoTalk, 2026-10-10) still shows what was
sent: the room's window names the other party, and the new bubble appears at
the end of the conversation. This adapter reads both, through a reader that is
not the input backend (an independent accessibility observation), so that
"보냈습니까?" can be answered from the screen rather than from the key press:

- before the press: the window still names the approved recipient, and the
  messages already shown are recorded;
- after it: the same recipient, and a new last message that is exactly the
  approved text (one more occurrence of it than before).

What it certifies is narrower than delivery. The SENDER's window shows the
message as sent to that room; whether the other side received or read it is
not observable here, and is not claimed (see DeliveryEvidence for receipts
that come from the receiving side). One prepare, one after-commit.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import time
import threading
import uuid

from .artifact_receipt import ArtifactReceiptError
from .commit_evidence import CommitEvidence, TranscriptEvidence, action_digest, _event_binding_matches


class TranscriptReceiptError(ArtifactReceiptError):
    """Sanitized refusal; an uncertain send must not be repeated."""


PUBLIC_CODES = frozenset({
    'already_prepared', 'not_a_bound_send', 'event_malformed', 'reader_failed',
    'recipient_unreadable', 'recipient_mismatch', 'already_used', 'commit_mismatch',
    'commit_changed_during_prepare',
    'recipient_changed', 'message_not_shown',
})

_ATTRS = ('Value', 'Title', 'Description')


def _refuse(code):
    assert code in PUBLIC_CODES, code
    error = TranscriptReceiptError(code)
    error.public_code = code
    return error


def _text_of(line):
    from .flow import Selector
    for key in _ATTRS:
        value = Selector._attr(line, key)
        if value is not None:
            return value
    return None


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    action_digest: str
    shown_before: int
    # What the person is asked to approve, with the recipient as the window
    # names it right now — not as the recipe remembers it.
    confirmation: str


class TranscriptReceipt:
    """Witness one message in the sender's own conversation window.

    `read_tree(target)` must return a fresh accessibility tree from a reader
    other than the backend that sends (it must not replace the observation the
    send is addressed to). `recipient_field` selects the element naming the
    other party (usually the room window); `messages` selects every message
    element of the conversation, in order.
    """

    def __init__(self, read_tree, *, target, recipient, body, recipient_field, messages,
                 recipient_attribute='Title', timeout_seconds=5.0, poll_interval=.2,
                 clock=None, sleep=None):
        if not callable(read_tree):
            raise ValueError('read_tree must be callable')
        for name, value in (('target', target), ('recipient', recipient), ('body', body)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f'{name} must be non-empty text')
        if recipient_attribute not in _ATTRS:
            raise ValueError('recipient_attribute must be Value, Title or Description')
        if not recipient_field._names_something():
            raise ValueError('recipient_field must name something, not only a role')
        if not (messages._names_something() or messages.ancestor or messages.in_focused_window):
            raise ValueError('messages needs a scope: an ancestor or the focused window')
        for name, number in (('timeout_seconds', timeout_seconds), ('poll_interval', poll_interval)):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError(f'{name} must be finite and positive')
        self._read_tree = read_tree
        self._target, self._recipient, self._body = target, recipient, body
        self._recipient_field, self._messages = recipient_field, messages
        self._recipient_attribute = recipient_attribute
        self._timeout, self._poll = float(timeout_seconds), float(poll_interval)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._prepared = None
        self._prepare_attempted = False
        self._prepare_lock = threading.Lock()
        self._after_used = False

    @property
    def commit_expected(self):
        return {'받는 사람': self._recipient, '보낸 내용': self._body}

    def _read(self):
        try:
            tree = self._read_tree(self._target)
        except Exception:
            raise _refuse('reader_failed') from None
        if not isinstance(tree, str):
            raise _refuse('reader_failed')
        lines = tree.splitlines()
        try:
            index, _ = self._recipient_field.resolve(tree)
        except Exception:
            raise _refuse('recipient_unreadable') from None
        from .flow import Selector
        line = next((ln for ln in lines if ln.strip().split()[:1] == [str(index)]), None)
        recipient = Selector._attr(line, self._recipient_attribute) if line else None
        if not recipient:
            raise _refuse('recipient_unreadable')
        shown = []
        for found in self._messages.resolve_all(tree):
            item = next((ln for ln in lines if ln.strip().split()[:1] == [str(found)]), None)
            shown.append(_text_of(item) if item else None)
        return recipient, shown

    def prepare_commit(self, event):
        with self._prepare_lock:
            if self._prepare_attempted:
                raise _refuse('already_prepared')
            self._prepare_attempted = True
        action, effects = event.get('action'), set(event.get('effects') or ())
        sends = isinstance(action, dict) and (
            (action.get('name') == 'press_key' and action.get('key') == 'Return'
             and set(action) == {'name', 'key', 'element_index'})
            or (action.get('name') == 'click' and set(action) == {'name', 'element_index'}))
        if (event.get('target') != self._target or not sends
                or 'transmit' not in effects or effects - {'transmit', 'read', 'write_ephemeral'}):
            raise _refuse('not_a_bound_send')
        digest = action_digest(action, event['effects'], self._target, obs_id=event.get('obs_id'))
        if event.get('action_digest') != digest or not isinstance(event.get('event_id'), str):
            raise _refuse('event_malformed')
        identifier = event['event_id']
        recipient, shown = self._read()
        if recipient != self._recipient:
            raise _refuse('recipient_mismatch')
        if not _event_binding_matches(event, identifier, digest):
            raise _refuse('commit_changed_during_prepare')
        self._prepared = _Prepared(identifier, digest, shown.count(self._body),
                                   f"받는 사람: {recipient} · 보낼 문장: {self._body}")
        return self._prepared

    def after_commit(self, event, driver_result, context):
        if self._after_used:
            raise _refuse('already_used')
        self._after_used = True
        prepared = self._prepared
        if (type(context) is not _Prepared or context is not prepared
                or event.get('event_id') != prepared.event_id
                or event.get('action_digest') != prepared.action_digest):
            raise _refuse('commit_mismatch')
        deadline = self._clock() + self._timeout
        while True:
            recipient, shown = self._read()
            if recipient != self._recipient:
                raise _refuse('recipient_changed')
            if shown and shown[-1] == self._body and shown.count(self._body) > prepared.shown_before:
                return CommitEvidence(prepared.event_id, prepared.action_digest, TranscriptEvidence(
                    uuid.uuid4().hex, recipient, hashlib.sha256(self._body.encode()).hexdigest()))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _refuse('message_not_shown')
            self._sleep(min(self._poll, remaining))
