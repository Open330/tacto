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


@dataclass(frozen=True)
class ArtifactEvidence:
    receipt_id: str
    path: str
    sha256: str


@dataclass(frozen=True)
class DeliveryEvidence:
    receipt_id: str
    recipient: str
    account: str
    body_sha256: str


@dataclass(frozen=True)
class CommitEvidence:
    event_id: str
    action_digest: str
    evidence: ArtifactEvidence | DeliveryEvidence


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
    if type(detail) is ArtifactEvidence:
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
        for key, value in [('받는 사람', detail.recipient), ('보내는 계정', detail.account)]:
            if key in expected and expected[key] != value:
                return 'delivery_destination_mismatch'
        body = expected.get('보낸 내용')
        if body is not None and (not isinstance(body, str) or hashlib.sha256(body.encode()).hexdigest() != detail.body_sha256):
            return 'delivery_content_mismatch'
    else:
        return 'unsupported_commit_evidence'
    return None
