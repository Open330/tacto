"""A disconnected reader must not erase a commit or dispatch it again."""
import unittest
import contextlib
import io
from unittest.mock import patch

from tacto.contract import Envelope, ContextBinding, issue_approval
from tacto.flow import Flow, Step, Selector, Postcondition, replay
from tacto.helper import HelperUnavailable
from tests.test_transcript_receipt import Chat, receipt, send_step


class DisconnectedChat(Chat):
    def __init__(self, *, lose_response=True):
        super().__init__()
        self.disconnected = False
        self.lose_response = lose_response

    def tree(self, target=None):
        return super().tree(target) + '\n  99 Button Title: Send'

    def observe(self, *args, **kwargs):
        if self.disconnected:
            raise HelperUnavailable('PRIVATE_OBSERVATION_SENTINEL')
        return super().observe(*args, **kwargs)

    def act(self, *args, **kwargs):
        result = super().act(*args, **kwargs)
        self.disconnected = True
        if self.lose_response:
            raise HelperUnavailable('reply unavailable')
        return result


class ReplayObservationFailureTests(unittest.TestCase):
    def run_send(self, *, post=True, lose_response=True, use_receipt=True, invalid_read=None):
        chat = DisconnectedChat(lose_response=lose_response)
        if invalid_read is not None:
            chat.wait_until = lambda *args, **kwargs: invalid_read
        step = send_step()
        if not post:
            env = Envelope.minimal()
            step = Step('click', Selector(role='Button', title='Send'),
                        safety=env.contract_for({'transmit'}, 'message'),
                        binding=ContextBinding(bundle_id='chat', role='Button', title='Send',
                                               operation='click'))
        follow = Step('read', safety=Envelope.minimal().contract_for({'read'}, 'entry'))
        rec = receipt(chat)
        hooks = dict(prepare_commit=rec.prepare_commit, after_commit=rec.after_commit,
                     commit_expected=rec.commit_expected) if use_receipt else {}
        escalations = []
        result = replay(chat, Flow('send_then_read', 'chat', [step, follow]),
                        {0: issue_approval(step.binding, step.safety)},
                        escalate=lambda why: escalations.append(why), budget=10, **hooks)
        self.assertFalse(result.ok)
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual(chat.messages.count('안녕'), 1)
        self.assertEqual(len(result.events), 1)
        self.assertEqual(escalations, [])
        self.assertNotIn('PRIVATE_OBSERVATION_SENTINEL', str(result.log))
        return result

    def test_confirmed_commit_and_receipt_survive_lost_response_and_failed_wait(self):
        result = self.run_send()
        self.assertEqual(result.events[0]['state'], 'confirmed')
        self.assertEqual(result.events[0]['dispatch_response'], {'type': 'error', 'effect': 'unknown'})
        self.assertEqual(len(result.commit_evidence), 1)
        self.assertEqual(result.log[-1]['error'], 'observation_unavailable')
        self.assertEqual(result.log[-1]['detail']['exception_class'], 'HelperUnavailable')

    def test_confirmed_commit_survives_failed_observe_without_postcondition(self):
        result = self.run_send(post=False)
        self.assertEqual(result.events[0]['state'], 'confirmed')
        self.assertEqual(len(result.commit_evidence), 1)

    def test_valid_dispatch_response_does_not_hide_later_reader_loss(self):
        result = self.run_send(lose_response=False)
        self.assertEqual(result.events[0]['state'], 'confirmed')
        self.assertNotIn('dispatch_response', result.events[0])
        self.assertEqual(result.log[-1]['error'], 'observation_unavailable')

    def test_without_receipt_uncertain_dispatch_remains_unknown(self):
        result = self.run_send(use_receipt=False)
        self.assertEqual(result.events[0]['state'], 'unknown')
        self.assertEqual(result.commit_evidence, {})
        self.assertEqual(result.log[-1]['error'], 'commit_unverified')

    def test_initial_reader_failure_is_sanitized_and_sends_nothing(self):
        chat = DisconnectedChat()
        chat.disconnected = True
        result = replay(chat, Flow('read', 'chat', [Step('read', safety=Envelope.minimal().contract_for({'read'}, 'entry'))]))
        self.assertFalse(result.ok)
        self.assertEqual(result.events, [])
        self.assertEqual(chat.calls, [])
        self.assertNotIn('PRIVATE_OBSERVATION_SENTINEL', str(result.log))

    def test_interrupt_is_not_converted_to_an_observation_error(self):
        chat = Chat()
        for kind in (KeyboardInterrupt, SystemExit):
            def interrupted(*args, **kwargs):
                raise kind
            chat.observe = interrupted
            with self.assertRaises(kind):
                replay(chat, Flow('read', 'chat', []))

    def test_private_exception_class_names_are_not_recorded(self):
        chat = Chat()
        private = type('PRIVATE_CLASS_SENTINEL', (Exception,), {})
        def failed(*args, **kwargs):
            raise private('PRIVATE_OBSERVATION_SENTINEL')
        chat.observe = failed
        result = replay(chat, Flow('read', 'chat', []))
        self.assertEqual(result.log[-1]['detail']['exception_class'], 'Exception')
        self.assertNotIn('PRIVATE', str(result.log))

    def test_malformed_post_commit_reader_keeps_confirmed_receipt_and_stops(self):
        for invalid in ([], {'type':'observation','tree':None}, {'type':'timeout','last_obs':[]},
                        {'type':'timeout','last_obs':{'tree':None}}):
            with self.subTest(invalid=invalid):
                result = self.run_send(invalid_read=invalid)
                self.assertEqual(result.events[0]['state'], 'confirmed')
                self.assertEqual(len(result.commit_evidence), 1)
                self.assertEqual(result.log[-1]['error'], 'observation_unavailable')
                self.assertEqual(result.log[-1]['detail']['reason'], 'invalid_response')

    def test_missing_initial_observation_is_a_closed_failure(self):
        chat = Chat()
        chat.observe = lambda *args, **kwargs: None
        result = replay(chat, Flow('read', 'chat', []))
        self.assertFalse(result.ok)
        self.assertEqual(result.events, [])
        self.assertEqual(chat.calls, [])

    def test_terminal_wait_error_never_escalates_or_repeats_confirmed_commit(self):
        for code in ('aborted_by_user', 'target_not_found', 'no_window_content', 'unknown_error'):
            for wrapped in (False, True):
                error = {'type':'error','error':code}
                response = {'type':'timeout','last_obs':error} if wrapped else error
                with self.subTest(code=code, wrapped=wrapped):
                    result = self.run_send(invalid_read=response)
                    self.assertEqual(result.events[0]['state'], 'confirmed')
                    self.assertEqual(len(result.commit_evidence), 1)
                    self.assertEqual(result.log[-1]['error'], code)

    def test_cli_returns_failure_and_separates_confirmed_commit_from_reader_loss(self):
        from tacto.cli import _run_flow
        chat = DisconnectedChat()
        rec = receipt(chat)
        with patch('tacto.cli._make_driver', return_value=chat), \
                patch('tacto.agent.Agent.commit_adapter_for', return_value=rec), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            code = _run_flow(Flow('send', 'chat', [send_step()]), ['--yes', '--no-indicator'])
        self.assertEqual(code, 1)
        self.assertIn('영수증으로 확인했지만', output.getvalue())
        self.assertIn('이후 화면을 읽지 못해', output.getvalue())
        self.assertNotIn('PRIVATE_OBSERVATION_SENTINEL', output.getvalue())
        self.assertEqual(len(chat.calls), 1)

    def test_visual_call_failure_preserves_prior_receipt_and_appended_partial_event(self):
        from tacto.commit_evidence import make_event
        chat = Chat()
        send = send_step()
        visual = Step('visual', params={'scope':'owned'}, intent_text='owned pixel',
                      safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'entry'))
        rec = receipt(chat)
        def partial(driver, target, step, index, result, scopes):
            result.events.append(make_event({'name':'click_point'}, {'write_ephemeral'},
                                            target, state='unknown', obs_id='pixel-observation'))
            raise HelperUnavailable('PRIVATE_VISUAL_SENTINEL')
        with patch('tacto.visual_replay.scope_problem', return_value=None), \
                patch('tacto.visual_replay.run_step', side_effect=partial) as run:
            result = replay(chat, Flow('send_then_visual', 'chat', [send, visual,
                            Step('read', safety=Envelope.minimal().contract_for({'read'}, 'entry'))]),
                            {0:issue_approval(send.binding, send.safety)},
                            prepare_commit=rec.prepare_commit, after_commit=rec.after_commit,
                            commit_expected=rec.commit_expected)
        self.assertFalse(result.ok)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(len(chat.calls), 1)
        self.assertEqual([e['state'] for e in result.events], ['confirmed', 'unknown'])
        self.assertEqual(len(result.commit_evidence), 1)
        self.assertEqual(result.log[-1]['stage'], 'visual_step')
        self.assertNotIn('PRIVATE_VISUAL_SENTINEL', str(result.log))

    def test_visual_call_interrupt_is_not_swallowed(self):
        visual = Step('visual', params={'scope':'owned'}, intent_text='owned pixel',
                      safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'entry'))
        for kind in (KeyboardInterrupt, SystemExit):
            with patch('tacto.visual_replay.scope_problem', return_value=None), \
                    patch('tacto.visual_replay.run_step', side_effect=kind), self.assertRaises(kind):
                replay(Chat(), Flow('visual', 'chat', [visual]))


if __name__ == '__main__':
    unittest.main()
