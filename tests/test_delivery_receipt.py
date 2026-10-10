"""Authoritative inbox identity, not a sender's completion claim."""
from dataclasses import asdict, replace
import hashlib
import unittest

from nlc.commit_evidence import CommitEvidence, evidence_error, make_event
from nlc.delivery_receipt import (DeliveredMessageReceipt, DeliveryReceiptError,
                                  InboxDelivery, PendingDelivery)


class DeliveryReceiptTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.sleeps = []
        self.bind_calls = 0
        self.observed = None
        self.pending_override = None
        self.event = make_event({'name':'click', 'element_index':3}, {'transmit'},
                                'owned-mail', obs_id='observed')
        self.digest = hashlib.sha256('안녕\n'.encode()).hexdigest()
        self.delivered = InboxDelivery('request-1', 'message-1', 'sender', 'recipient',
                                       self.digest, 'delivered')
        self.receipt = self.new_receipt()

    def bind(self, event):
        self.bind_calls += 1
        return self.pending_override or PendingDelivery(
            'request-1', event['target'], event['obs_id'], event['action_digest'],
            'sender', 'recipient', self.digest)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def new_receipt(self, **kwargs):
        config = dict(target='owned-mail', account='sender', recipient='recipient',
                      text='안녕\n', bind_request=self.bind,
                      read_inbox=lambda _:self.observed, timeout_seconds=.03,
                      poll_interval=.01, clock=lambda:self.now, sleep=self.sleep)
        config.update(kwargs)
        return DeliveredMessageReceipt(**config)

    def test_new_exact_destination_record_issues_event_bound_evidence(self):
        context = self.receipt.prepare_commit(self.event)
        self.observed = self.delivered
        proof = self.receipt.after_commit(self.event, {'type':'acted'}, context)
        self.assertIs(type(proof), CommitEvidence)
        self.assertEqual('message-1', proof.evidence.receipt_id)
        self.assertIsNone(evidence_error({**self.event, 'commit_evidence':proof},
                                        {**self.receipt.commit_expected, '보낸 내용':'안녕\n'}))

    def test_common_verifier_independently_checks_host_body_digest(self):
        context = self.receipt.prepare_commit(self.event)
        self.observed = self.delivered
        proof = self.receipt.after_commit(self.event, {}, context)
        expected = self.receipt.commit_expected
        self.assertEqual(self.digest, expected['보낸 내용 sha256'])
        damaged = replace(proof, evidence=replace(proof.evidence, body_sha256='0'*64))
        self.assertEqual('delivery_content_mismatch', evidence_error(
            {**self.event, 'commit_evidence':damaged}, expected))
        self.assertIsNone(evidence_error({**self.event, 'commit_evidence':proof}, expected))

    def test_bad_or_conflicting_host_digest_never_passes_common_verifier(self):
        context = self.receipt.prepare_commit(self.event)
        self.observed = self.delivered
        proof = self.receipt.after_commit(self.event, {}, context)
        for value in (None, True, [], '0'*64, 'bad', self.digest.upper()):
            with self.subTest(value=value):
                self.assertEqual('delivery_content_mismatch', evidence_error(
                    {**self.event, 'commit_evidence':proof}, {'보낸 내용 sha256':value}))
        self.assertEqual('delivery_content_mismatch', evidence_error(
            {**self.event, 'commit_evidence':proof},
            {'보낸 내용 sha256':self.digest, '보낸 내용':'different'}))

    def test_service_message_identity_preserves_punctuation_and_unicode(self):
        for identity in ('message:123', 'message.123', '<message@service>', '사서함/메시지-123'):
            self.observed = None
            receipt = self.new_receipt();context = receipt.prepare_commit(self.event)
            self.observed = replace(self.delivered, message_id=identity)
            self.assertEqual(identity, receipt.after_commit(self.event, {}, context).evidence.receipt_id)

    def test_control_characters_whitespace_and_overlong_message_id_are_refused(self):
        for identity in (' message', 'message ', 'message\nother', 'message\x00other', 'x'*1025):
            self.observed = None
            receipt = self.new_receipt();context = receipt.prepare_commit(self.event)
            self.observed = replace(self.delivered, message_id=identity)
            with self.assertRaisesRegex(DeliveryReceiptError, 'inbox_receipt_mismatch'):
                receipt.after_commit(self.event, {}, context)

    def test_old_record_cannot_be_reused_as_a_new_send(self):
        self.observed = self.delivered
        with self.assertRaisesRegex(DeliveryReceiptError, 'delivery_already_present'):
            self.receipt.prepare_commit(self.event)

    def test_sent_marker_or_lost_response_without_inbox_record_never_completes(self):
        context = self.receipt.prepare_commit(self.event)
        with self.assertRaisesRegex(DeliveryReceiptError, 'delivery_not_observed_before_deadline'):
            self.receipt.after_commit(self.event, {'type':'acted', 'said':'Sent'}, context)
        self.assertAlmostEqual(.03, sum(self.sleeps))
        self.assertEqual(1, self.bind_calls)

    def test_lost_response_can_be_resolved_by_new_authoritative_record(self):
        context = self.receipt.prepare_commit(self.event)
        self.observed = self.delivered
        proof = self.receipt.after_commit(self.event, {'type':'error', 'effect':'unknown'}, context)
        self.assertEqual('message-1', proof.evidence.receipt_id)

    def test_every_receiving_field_must_match_and_be_delivered(self):
        for field, value in [('request_id','other'), ('account','other'),
                             ('recipient','other'), ('body_sha256','0'*64),
                             ('message_id',''), ('message_id',True),
                             ('state','queued'), ('state','accepted')]:
            with self.subTest(field=field):
                receipt = self.new_receipt()
                self.observed = None
                context = receipt.prepare_commit(self.event)
                self.observed = replace(self.delivered, **{field:value})
                with self.assertRaisesRegex(DeliveryReceiptError, 'inbox_receipt_mismatch'):
                    receipt.after_commit(self.event, {}, context)

    def test_model_json_is_not_a_typed_host_receipt(self):
        context = self.receipt.prepare_commit(self.event)
        self.observed = asdict(self.delivered)
        with self.assertRaisesRegex(DeliveryReceiptError, 'inbox_receipt_mismatch'):
            self.receipt.after_commit(self.event, {}, context)

    def test_binding_checks_live_observation_action_and_payload(self):
        for field, value in [('request_id',''), ('target','other'), ('obs_id','old'),
                             ('action_digest','0'*64), ('account','other'),
                             ('recipient','other'), ('body_sha256','0'*64)]:
            with self.subTest(field=field):
                receipt = self.new_receipt()
                expected = PendingDelivery('request-1', 'owned-mail', 'observed',
                    self.event['action_digest'], 'sender', 'recipient', self.digest)
                self.pending_override = replace(expected, **{field:value})
                with self.assertRaisesRegex(DeliveryReceiptError, 'delivery_binding_mismatch'):
                    receipt.prepare_commit(self.event)

    def test_only_one_preparation_and_one_after_attempt(self):
        context = self.receipt.prepare_commit(self.event)
        with self.assertRaisesRegex(DeliveryReceiptError, 'already_prepared'):
            self.receipt.prepare_commit(self.event)
        self.observed = self.delivered
        self.receipt.after_commit(self.event, {}, context)
        with self.assertRaisesRegex(DeliveryReceiptError, 'already_used'):
            self.receipt.after_commit(self.event, {}, context)

    def test_event_mutation_and_context_copy_are_refused(self):
        for change in ('event_id', 'action', 'context'):
            with self.subTest(change=change):
                receipt = self.new_receipt()
                context = receipt.prepare_commit(self.event)
                event = dict(self.event)
                if change == 'context': context = replace(context)
                elif change == 'event_id': event['event_id'] = 'other'
                else: event['action'] = {'name':'click', 'element_index':4}
                with self.assertRaises(DeliveryReceiptError):
                    receipt.after_commit(event, {}, context)

    def test_mixed_consequential_effects_and_invalid_input_fail_before_binding(self):
        for action, effects in [({'name':'click','element_index':True}, {'transmit'}),
                                ({'name':'click','element_index':3}, {'transmit','write_persistent'}),
                                ({'name':'click','element_index':3}, {'read'}),
                                ({'name':'press_key','key':'Tab'}, {'transmit'})]:
            receipt = self.new_receipt()
            event = make_event(action, effects, 'owned-mail', obs_id='observed')
            with self.assertRaisesRegex(DeliveryReceiptError, 'invalid_delivery_event'):
                receipt.prepare_commit(event)
        self.assertEqual(0, self.bind_calls)

    def test_return_uses_existing_protocol_and_still_requires_inbox_evidence(self):
        event = make_event({'name':'press_key','key':'Return'}, {'transmit'},
                           'owned-mail', obs_id='observed')
        context = self.receipt.prepare_commit(event)
        self.observed = self.delivered
        self.assertEqual(event['action_digest'], self.receipt.after_commit(event, {}, context).action_digest)

    def test_reader_and_binding_errors_are_closed_and_not_model_text(self):
        def broken(*args): raise RuntimeError('PRIVATE_SENTINEL')
        for field, code in [('bind_request','delivery_binding_unavailable'),
                            ('read_inbox','inbox_read_unavailable')]:
            receipt = self.new_receipt(**{field:broken})
            with self.assertRaises(DeliveryReceiptError) as failure:
                receipt.prepare_commit(self.event)
            self.assertEqual(code, str(failure.exception))

    def test_late_inbox_record_is_not_accepted(self):
        context = self.receipt.prepare_commit(self.event)
        def late(_):
            self.now += .04
            return self.delivered
        self.receipt._inbox = late
        with self.assertRaisesRegex(DeliveryReceiptError, 'delivery_not_observed_before_deadline'):
            self.receipt.after_commit(self.event, {}, context)

    def test_configuration_rejects_unbounded_or_non_host_readers(self):
        for field, value in [('timeout_seconds',True), ('timeout_seconds',float('inf')),
                             ('poll_interval',0), ('bind_request',{}), ('recipient',''),
                             ('text','x'*(4*1024*1024+1))]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.new_receipt(**{field:value})


if __name__ == '__main__':
    unittest.main()
