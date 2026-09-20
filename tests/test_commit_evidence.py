"""Model appearance checks cannot attest a persistent or external operation."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
import unittest

from nlc.commit_evidence import (ArtifactEvidence, DeliveryEvidence, CommitEvidence,
                                  make_event, evidence_error)
from nlc.outcome import Check, OutcomeSpec, verify, DONE

HASH = hashlib.sha256(b'hello').hexdigest()


class CommitEvidenceTests(unittest.TestCase):
    def event(self):
        return make_event({'name': 'click', 'element_index': 3}, ['transmit'], 'mail', 'confirmed')

    def proof(self, event):
        return CommitEvidence(event['event_id'], event['action_digest'],
                              DeliveryEvidence('server-message-1', 'mom', 'me', HASH))

    def test_receipt_binds_event_action_destination_and_body(self):
        event = self.event()
        event['commit_evidence'] = self.proof(event)
        expected = {'받는 사람': 'mom', '보내는 계정': 'me', '보낸 내용': 'hello'}
        self.assertIsNone(evidence_error(event, expected))
        for key, value in [('받는 사람', 'stranger'), ('보내는 계정', 'other'), ('보낸 내용', 'changed')]:
            self.assertIsNotNone(evidence_error(event, {**expected, key: value}))
        altered = deepcopy(event)
        altered['action']['element_index'] = 8
        self.assertEqual('commit_action_mismatch', evidence_error(altered))
        another = self.event()
        another['commit_evidence'] = event['commit_evidence']
        self.assertEqual('commit_event_mismatch', evidence_error(another))

    def test_model_dictionary_and_missing_receipt_not_evidence(self):
        event = self.event()
        event['commit_evidence'] = {'event_id': event['event_id'], 'source': 'host'}
        self.assertEqual('missing_typed_host_commit_evidence', evidence_error(event))
        event['commit_evidence'] = CommitEvidence(event['event_id'], event['action_digest'],
                                                DeliveryEvidence('', 'mom', 'me', HASH))
        self.assertEqual('invalid_delivery_evidence', evidence_error(event))

    def test_artifact_requires_receipt_and_exact_path_content(self):
        event = make_event({'name': 'click'}, ['write_persistent'], 'editor', 'confirmed')
        event['commit_evidence'] = CommitEvidence(event['event_id'], event['action_digest'],
                                      ArtifactEvidence('write-operation-1', '/tmp/result.txt', HASH))
        expected = {'저장 위치': '/tmp', '저장한 이름': 'result.txt', '다시 읽은 내용': 'hello'}
        self.assertIsNone(evidence_error(event, expected))
        self.assertEqual('artifact_location_mismatch', evidence_error(event, {**expected, '저장 위치': '/other'}))
        self.assertEqual('artifact_content_mismatch', evidence_error(event, {**expected, '다시 읽은 내용': 'wrong'}))

    def test_receipts_cannot_confirm_additional_consequential_effects(self):
        for primary, detail, error in (
                ('write_persistent', ArtifactEvidence('file-write', '/tmp/file.txt', HASH), 'artifact_effect_mismatch'),
                ('transmit', DeliveryEvidence('delivery', 'mom', 'me', HASH), 'delivery_effect_mismatch')):
            for additional in ('delete', 'financial', 'credential', 'install', 'system_config',
                               'transmit' if primary == 'write_persistent' else 'write_persistent'):
                with self.subTest(primary=primary, additional=additional):
                    event = make_event({'name': 'click'}, [primary, additional], 'app')
                    event['commit_evidence'] = CommitEvidence(event['event_id'], event['action_digest'], detail)
                    self.assertEqual(error, evidence_error(event))

    def test_receipt_allows_observation_and_ephemeral_work_with_its_own_effect(self):
        for primary, detail in (
                ('write_persistent', ArtifactEvidence('file-write', '/tmp/file.txt', HASH)),
                ('transmit', DeliveryEvidence('delivery', 'mom', 'me', HASH))):
            event = make_event({'name': 'click'}, [primary, 'read', 'write_ephemeral'], 'app')
            event['commit_evidence'] = CommitEvidence(event['event_id'], event['action_digest'], detail)
            self.assertIsNone(evidence_error(event))

    def test_noop_send_unrelated_success_marker_cannot_finish(self):
        spec = OutcomeSpec(kind='send_message', required=(Check('once', 'request', 'after', 'exactly_once'),))
        event = self.event()  # Previously model "Sent" text could create this.
        result = SimpleNamespace(ok=True, verified=True, witnessed={}, said=[],
                                 effects={'transmit'}, events=[event],
                                 commit_evidence={event['event_id']: event.get('commit_evidence')})
        self.assertNotEqual(DONE, verify(spec, result, after_tree='unrelated widget: Sent').state)

    def test_valid_evidence_and_duplicate_receipt(self):
        spec = OutcomeSpec(kind='send_message', must_read=('hello',),
                           required=(Check('once', 'request', 'after', 'exactly_once'),))
        event = self.event()
        event['commit_evidence'] = self.proof(event)
        result = SimpleNamespace(ok=True, verified=True, witnessed={}, said=['hello'],
                                 effects={'transmit'}, events=[event],
                                 commit_evidence={event['event_id']: event.get('commit_evidence')})
        self.assertEqual(DONE, verify(spec, result).state)
        result.events.append(deepcopy(event))
        self.assertNotEqual(DONE, verify(spec, result).state)

    def test_general_noop_send_and_unrelated_marker_remain_dispatched(self):
        from nlc.general import run
        from nlc.contract import Envelope
        class Noop:
            changed = False
            def observe(self, *args, **kwargs):
                return {'type': 'observation', 'obs_id': 'o', 'tree':
                        '0 Window Title: mail\n  1 Button Title: Send\n  2 TextField Identifier: recipient, Value: mom' +
                        ('\n  3 StaticText Title: unrelated Sent' if self.changed else '')}
            def probe(self, *args):
                return {'type': 'probe', 'line': '2 TextField Identifier: recipient, Value: mom'}
            def act(self, *args, **kwargs):
                self.changed = True  # No message sent, unrelated widget changed.
                return {'type': 'acted'}
            def wait_until(self, target, predicate):
                return self.observe()
        def plan(goal, cands, done):
            if done:
                return {'done': True}
            return {'actions': [{'do': 'click', 'candidate': next(c for c in cands if c['label'] == 'Send')}], 'expect': 'Sent'}
        def where(name, cands):
            c = next(c for c in cands if c['match'] == 'recipient')
            return {'pick': cands.index(c), 'candidate': c}
        spec = OutcomeSpec(kind='send_message', must_read=('mom',),
                           required=(Check('recipient', 'request', 'before_commit', 'reports'),
                                     Check('once', 'request', 'after', 'exactly_once')),
                           expected={'recipient': 'mom'})
        result = run(Noop(), 'mail', 'send', plan, spec=spec, ask_where=where,
                     approve=lambda _: True,
                     envelope=Envelope(quote='send', allows=frozenset({'transmit'}), scope='entry'))
        self.assertTrue(result.ok, result.why)
        self.assertTrue(result.visits[0].verified)
        self.assertEqual('dispatched', result.events[0]['state'])
        self.assertNotEqual(DONE, verify(spec, result).state)
