"""Host supplied operation-bound commit evidence, separate from model claims.

This is an in-process trust boundary, not a cryptographic attestation: the host
callback must actually inspect an artifact/delivery receipt. A dataclass or its
hash alone cannot prove that a remote service accepted a message.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePath
import re
import uuid
import math


def action_digest(action, effects, target, *, obs_id=None):
    payload = {'action': action, 'effects': sorted(effects), 'target': target}
    if obs_id is not None:
        if not isinstance(obs_id, str) or not obs_id.strip():
            raise ValueError('observation identity must be a nonempty string')
        payload['obs_id'] = obs_id
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def make_event(action, effects, target, state='dispatched', *, obs_id=None):
    action = json.loads(json.dumps(action))
    effects = sorted(effects)
    event = {'event_id': uuid.uuid4().hex,
             'action_digest': action_digest(action, effects, target, obs_id=obs_id),
             'action': action, 'effects': effects, 'target': target, 'state': state}
    if obs_id is not None:
        event['obs_id'] = obs_id
    return event


def _event_binding_matches(event, identifier, digest):
    """Checkpoint the original binding after host reads; not an atomic lease."""
    try:
        return (isinstance(identifier, str)
                and event['event_id'] == identifier
                and event['action_digest'] == digest
                and action_digest(event['action'], event['effects'], event['target'],
                                  obs_id=event.get('obs_id')) == digest)
    except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
        return False


@dataclass(frozen=True)
class ArtifactEvidence:
    receipt_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class SavedFileStateEvidence:
    """Matching saved contents; does not identify the writer or confirm Save."""
    receipt_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class ProducerArtifactEvidence:
    """Trusted host application callback + file readback, not kernel attestation."""
    receipt_id: str
    path: str
    sha256: str
    producer: str
    producer_pid: int
    producer_start_time: float
    nonce: str
    completion_sequence: int


@dataclass(frozen=True)
class AttachmentEvidence:
    """Host-read attachment bytes, not a filename or model assertion alone."""
    filename: str
    sha256: str
    size_bytes: int


def attachment_manifest_valid(manifest):
    # Immutable, bounded and exact: no path adoption, duplicate filenames or
    # silently ignored records. Same-name attachments need a different adapter.
    if type(manifest) is not tuple or len(manifest)>100:return False
    names=set()
    for item in manifest:
        if (type(item) is not AttachmentEvidence or type(item.filename) is not str
                or not item.filename or item.filename in ('.','..')
                or '/' in item.filename or '\\' in item.filename
                or not item.filename.isprintable() or item.filename.strip()!=item.filename
                or not _digest(item.sha256) or type(item.size_bytes) is not int
                or not 0<=item.size_bytes<=4*1024**3):return False
        try:
            if len(item.filename.encode('utf-8'))>1024:return False
        except UnicodeError:return False
        if item.filename in names:return False
        names.add(item.filename)
    return True


@dataclass(frozen=True)
class DeliveryEvidence:
    receipt_id: str
    recipient: str
    account: str
    body_sha256: str
    attachments: tuple[AttachmentEvidence, ...] | None = None


@dataclass(frozen=True)
class TranscriptEvidence:
    """The sender's own conversation window, re-read after the send: it still
    names `recipient` and its new last message is the sent text. Not delivery:
    nothing here comes from the receiving side."""
    receipt_id: str
    recipient: str
    body_sha256: str


@dataclass(frozen=True)
class NavigationEvidence:
    """The browser window, re-read after the press, shows `url` loaded. Not a
    server receipt: what the site did with the request is not observed."""
    receipt_id: str
    url: str


@dataclass(frozen=True)
class CommitEvidence:
    event_id: str
    action_digest: str
    evidence: ArtifactEvidence | SavedFileStateEvidence | ProducerArtifactEvidence | DeliveryEvidence | TranscriptEvidence | NavigationEvidence


@dataclass(frozen=True)
class SavePanelCommitPreparation:
    """Host-only dispatch attachment; not approval or completion evidence."""
    event_id: str
    action_digest: str
    obs_id: str
    panel_token: str
    receipt_context: object


def save_panel_dispatch_parameters(event, context, capabilities):
    """Validate a typed host attachment before passing its live token to act."""
    if not isinstance(context, SavePanelCommitPreparation):
        return {}
    if (type(context) is not SavePanelCommitPreparation
            or not _text(context.event_id) or context.event_id != event.get('event_id')
            or not _text(context.obs_id) or context.obs_id != event.get('obs_id')
            or not isinstance(context.panel_token, str) or not context.panel_token
            or len(context.panel_token) > 128
            or 'action.click.save_panel_guard' not in (capabilities or ())):
        raise ValueError('invalid save-panel commit attachment')
    action = event.get('action')
    effects = set(event.get('effects', ()))
    if (not isinstance(action, dict) or set(action) != {'name', 'element_index'}
            or action['name'] != 'click' or type(action['element_index']) is not int
            or action['element_index'] < 0 or 'write_persistent' not in effects
            or effects - {'read', 'write_ephemeral', 'write_persistent'}):
        raise ValueError('save-panel attachment requires one file-write click')
    digest = action_digest(action, effects, event['target'], obs_id=context.obs_id)
    if context.action_digest != digest or event.get('action_digest') != digest:
        raise ValueError('save-panel commit action changed')
    return {'save_panel_token': context.panel_token}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def producer_identity_valid(producer,pid,start,nonce):
    if (not _text(producer) or type(pid) is not int or not 0<pid<=0x7FFFFFFF
            or type(start) not in (int,float) or not _text(nonce)):
        return False
    try:return math.isfinite(float(start)) and float(start)>0
    except (OverflowError,ValueError):return False


def evidence_error(event, expected=None):
    """Return a reason unless a typed host receipt matches event and expectation."""
    proof = event.get('commit_evidence')
    if type(proof) is not CommitEvidence:
        return 'missing_typed_host_commit_evidence'
    if not _text(event.get('event_id')) or proof.event_id != event['event_id']:
        return 'commit_event_mismatch'
    try:
        if 'obs_id' in event and not _text(event['obs_id']):
            return 'invalid_commit_event'
        digest = action_digest(event['action'], event['effects'], event['target'],
                               obs_id=event.get('obs_id'))
    except (KeyError, TypeError, ValueError):
        return 'invalid_commit_event'
    if proof.action_digest != digest or event.get('action_digest') != digest:
        return 'commit_action_mismatch'
    expected = expected or {}
    detail = proof.evidence
    effects = set(event['effects'])
    if type(detail) in (ArtifactEvidence,SavedFileStateEvidence,ProducerArtifactEvidence):
        scope = expected.get('artifact_evidence_scope')
        if scope not in (None,'artifact','saved_state','producer'):
            return 'artifact_expectation_invalid'
        if type(detail) is SavedFileStateEvidence and scope != 'saved_state':
            return 'saved_state_expectation_missing'
        if scope == 'producer' and type(detail) is not ProducerArtifactEvidence:
            return 'artifact_producer_evidence_missing'
        if type(detail) is ProducerArtifactEvidence:
            if (not producer_identity_valid(detail.producer,detail.producer_pid,detail.producer_start_time,detail.nonce)
                    or type(detail.completion_sequence) is not int
                    or detail.completion_sequence<=0):
                return 'invalid_producer_artifact_evidence'
            producer_expected = {key:expected.get(key) for key in
                ('producer','producer_pid','producer_start_time','producer_nonce')}
            if scope != 'producer' or any(value is None for value in producer_expected.values()):
                return 'producer_expectation_missing'
            if not producer_identity_valid(producer_expected['producer'],producer_expected['producer_pid'],
                    producer_expected['producer_start_time'],producer_expected['producer_nonce']):
                return 'producer_expectation_invalid'
            if (event.get('target') != detail.producer or detail.producer != producer_expected['producer'] or detail.producer_pid != producer_expected['producer_pid']
                    or detail.producer_start_time != producer_expected['producer_start_time']
                    or detail.nonce != producer_expected['producer_nonce']):
                return 'artifact_producer_mismatch'
        # A file-write receipt proves neither deletion of another resource nor
        # a payment, permission change, or delivery. Mixed consequential
        # effects require evidence for every effect, which this type cannot
        # supply. Read/ephemeral UI work may accompany the witnessed operation.
        if ('write_persistent' not in effects or
                effects - {'write_persistent', 'read', 'write_ephemeral'}):
            return 'artifact_effect_mismatch'
        if not _text(detail.receipt_id) or not _text(detail.path) or not PurePath(detail.path).is_absolute() or not _digest(detail.sha256):
            return 'invalid_artifact_evidence'
        if '저장 위치' in expected and (not _text(expected['저장 위치']) or str(PurePath(detail.path).parent) != str(PurePath(expected['저장 위치']))):
            return 'artifact_location_mismatch'
        if '저장한 이름' in expected and PurePath(detail.path).name != expected['저장한 이름']:
            return 'artifact_name_mismatch'
        content = expected.get('다시 읽은 내용')
        if content is not None and (not isinstance(content, str) or hashlib.sha256(content.encode()).hexdigest() != detail.sha256):
            return 'artifact_content_mismatch'
    elif type(detail) is DeliveryEvidence:
        if ('transmit' not in effects or
                effects - {'transmit', 'read', 'write_ephemeral'}):
            return 'delivery_effect_mismatch'
        if not all(_text(v) for v in (detail.receipt_id, detail.recipient, detail.account)) or not _digest(detail.body_sha256):
            return 'invalid_delivery_evidence'
        if 'attachments' in expected:
            expected_attachments=expected['attachments']
            if not attachment_manifest_valid(expected_attachments):
                return 'invalid_delivery_attachments'
            if detail.attachments is None:
                return 'delivery_attachments_unobserved'
            if not attachment_manifest_valid(detail.attachments):
                return 'invalid_delivery_attachments'
            if detail.attachments!=expected_attachments:
                return 'delivery_attachment_mismatch'
        elif detail.attachments is not None:
            return 'delivery_attachment_expectation_missing'
        for key, value in [('받는 사람', detail.recipient), ('보내는 계정', detail.account)]:
            if key in expected and expected[key] != value:
                return 'delivery_destination_mismatch'
        body = expected.get('보낸 내용')
        if body is not None and (not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != detail.body_sha256):
            return 'delivery_content_mismatch'
        if '보낸 내용 sha256' in expected:
            expected_hash = expected['보낸 내용 sha256']
            if not _digest(expected_hash) or expected_hash != detail.body_sha256:
                return 'delivery_content_mismatch'
    elif type(detail) is TranscriptEvidence:
        if ('transmit' not in effects or
                effects - {'transmit', 'read', 'write_ephemeral'}):
            return 'transcript_effect_mismatch'
        if not all(_text(v) for v in (detail.receipt_id, detail.recipient)) or not _digest(detail.body_sha256):
            return 'invalid_transcript_evidence'
        # Without the approved recipient and text there is nothing to hold
        # the window to: a message shown in some room is not this send.
        recipient, body = expected.get('받는 사람'), expected.get('보낸 내용')
        if not _text(recipient) or not isinstance(body, str):
            return 'transcript_expectation_missing'
        if recipient != detail.recipient:
            return 'transcript_recipient_mismatch'
        if hashlib.sha256(body.encode()).hexdigest() != detail.body_sha256:
            return 'transcript_content_mismatch'
    elif type(detail) is NavigationEvidence:
        if ('transmit' not in effects or
                effects - {'transmit', 'read', 'write_ephemeral'}):
            return 'navigation_effect_mismatch'
        if not _text(detail.receipt_id) or not _text(detail.url):
            return 'invalid_navigation_evidence'
        destination = expected.get('이동할 주소')
        if not _text(destination):
            return 'navigation_expectation_missing'
        if destination != detail.url:
            return 'navigation_destination_mismatch'
    else:
        return 'unsupported_commit_evidence'
    return None
