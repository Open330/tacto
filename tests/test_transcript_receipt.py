"""Sending in a native chat window, end to end in replay: the step is
transmit, so it is gated for consent; once approved, a bound Return sends and
the receipt re-reads the SENDER's window — the room still names the approved
recipient and its new last message is the approved text. Not delivery."""
from dataclasses import replace
import unittest
from nlc.commit_evidence import TranscriptEvidence, CommitEvidence, evidence_error, make_event
from nlc.contract import Envelope, ContextBinding, issue_approval
from nlc.flow import Flow, Step, Selector, Postcondition, replay, pin_params
from nlc.transcript_receipt import TranscriptReceipt, TranscriptReceiptError

CAPS = {'action.press_key', 'press_key.element_bound', 'press_key.no_escape', 'press_key.return_bound'}
SENT = {'type': 'acted', 'input_route': 'window_bound_submit_key', 'posted_events': 1,
        'unverified': 'observe_effect', 'reobserve_required': True}


class Chat:
    capabilities = CAPS

    def __init__(self, *, title='Room A', delivers=True, shows=None):
        self.title = title; self.messages = ['hi', 'ok']; self.field = '안녕'
        self.delivers = delivers; self.shows = shows; self.calls = []

    def tree(self, target=None):
        lines = [f'0 Window  Title: {self.title}', '  1 List  Identifier: transcript']
        lines += [f'    {2 + i} StaticText  Value: {m}' for i, m in enumerate(self.messages)]
        n = 2 + len(self.messages)
        lines.append(f'  {n} TextArea  Value: {self.field}, Identifier: message-field [focused]')
        return '\n'.join(lines)

    def observe(self, *args, **kwargs):
        return {'type': 'observation', 'obs_id': 'obs', 'tree': self.tree()}

    def act(self, obs, target, action, **kwargs):
        if kwargs.get('risk_class') in ('confirm', 'preapproved') and kwargs.get('approved') is not True:
            return {'type': 'confirm_request', 'what': kwargs.get('intent_text'),
                    'risk_class': kwargs['risk_class']}
        self.calls.append((action, kwargs))
        if self.delivers:
            self.messages.append(self.shows if self.shows is not None else self.field)
            self.field = ''
        return dict(SENT)

    def wait_until(self, target, predicate, **kwargs):
        result = self.observe()
        return result if predicate(result['tree']) else {'type': 'timeout', 'last_obs': result}


def send_step():
    env = Envelope.minimal(); params = {'key': 'Return'}
    return Step('press_key', Selector(identifier='message-field'), params=params, is_submit=True,
                safety=env.contract_for({'transmit'}, '메시지'),
                binding=ContextBinding(bundle_id='chat', identifier='message-field',
                                       operation='press_key', params_digest=pin_params(params)),
                postcondition=Postcondition(field=Selector(identifier='message-field'),
                                            attribute='Value', equals=''))


def receipt(chat, **changes):
    options = dict(target='chat', recipient='Room A', body='안녕',
                   recipient_field=Selector(role='Window', title=['Room A', 'Room B']),
                   messages=Selector(role='StaticText', ancestor='transcript', allow_nth=None),
                   timeout_seconds=.05, poll_interval=.01)
    options.update(changes)
    return TranscriptReceipt(chat.tree, **options)


class TranscriptReplayTests(unittest.TestCase):
    def run_send(self, chat, rec, approve=True):
        step = send_step()
        approvals = {0: issue_approval(step.binding, step.safety)} if approve else None
        return replay(chat, Flow('send', 'chat', [step], schema=2), approvals,
                      prepare_commit=rec.prepare_commit, after_commit=rec.after_commit,
                      commit_expected=rec.commit_expected)

    def test_unapproved_send_is_only_asked(self):
        chat = Chat(); result = self.run_send(chat, receipt(chat), approve=False)
        self.assertFalse(result.ok)
        self.assertEqual(chat.calls, [])
        gated = next(e for e in result.log if e.get('status') == 'gated')
        # The question names the room as the window shows it, and the text.
        self.assertIn('받는 사람: Room A · 보낼 문장: 안녕', gated['detail']['what'])

    def test_approved_send_is_confirmed_by_the_window(self):
        chat = Chat(); result = self.run_send(chat, receipt(chat))
        self.assertTrue(result.ok, result.log)
        self.assertEqual(len(chat.calls), 1)
        self.assertIs(chat.calls[0][1].get('submit_authorized'), True)
        self.assertEqual(result.events[0]['state'], 'confirmed')
        proof = next(iter(result.commit_evidence.values()))
        self.assertIsInstance(proof.evidence, TranscriptEvidence)
        self.assertEqual(proof.evidence.recipient, 'Room A')
        self.assertEqual(result.events[0]['commit_evidence_kind'], 'sender_transcript')

    def test_wrong_room_is_refused_before_the_key(self):
        chat = Chat(title='Room B'); result = self.run_send(chat, receipt(chat))
        self.assertFalse(result.ok)
        self.assertEqual(chat.calls, [])
        # Its closed code is shown, not hidden behind the class name.
        self.assertIn('TranscriptReceiptError: recipient_mismatch', str(result.log))

    def test_not_shown_or_different_text_is_unverified_never_resent(self):
        for chat in (Chat(delivers=False), Chat(shows='안녕하세요')):
            with self.subTest(shows=chat.shows):
                result = self.run_send(chat, receipt(chat))
                self.assertFalse(result.ok)
                self.assertEqual(len(chat.calls), 1)

    def test_same_text_already_last_needs_one_more(self):
        chat = Chat(delivers=False); chat.messages.append('안녕')
        result = self.run_send(chat, receipt(chat))
        self.assertFalse(result.ok, result.log)


class TranscriptEvidenceTests(unittest.TestCase):
    def proof(self, effects=('transmit',)):
        event = make_event({'name': 'press_key', 'key': 'Return', 'element_index': 4},
                           list(effects), 'chat', obs_id='obs')
        import hashlib
        evidence = TranscriptEvidence('r', 'Room A', hashlib.sha256('안녕'.encode()).hexdigest())
        return {**event, 'commit_evidence': CommitEvidence(event['event_id'], event['action_digest'], evidence)}

    def test_requires_recipient_and_body(self):
        expected = {'받는 사람': 'Room A', '보낸 내용': '안녕'}
        self.assertIsNone(evidence_error(self.proof(), expected))
        self.assertEqual(evidence_error(self.proof(), {}), 'transcript_expectation_missing')
        self.assertEqual(evidence_error(self.proof(), {**expected, '받는 사람': 'Room B'}),
                         'transcript_recipient_mismatch')
        self.assertEqual(evidence_error(self.proof(), {**expected, '보낸 내용': '안녕!'}),
                         'transcript_content_mismatch')
        self.assertEqual(evidence_error(self.proof(('write_persistent',)), expected),
                         'transcript_effect_mismatch')

    def test_receipt_is_single_use(self):
        chat = Chat(); rec = receipt(chat)
        with self.assertRaises(TranscriptReceiptError):
            rec.after_commit({}, {}, None)
        with self.assertRaises(TranscriptReceiptError) as again:
            rec.after_commit({}, {}, None)
        self.assertEqual(again.exception.public_code, 'already_used')


if __name__ == '__main__':
    unittest.main()
