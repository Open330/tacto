import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tacto.codex_result import codex_result, parse_codex_events, CodexProtocolError, _run_codex


def events():
    return [{'type': 'thread.started', 'thread_id': 'thread-1'},
            {'type': 'turn.started'},
            {'type': 'item.completed', 'item': {'id': 'a', 'type': 'agent_message', 'text': '{"done":true}'}},
            {'type': 'turn.completed', 'usage': {'input_tokens': 100, 'output_tokens': 10, 'cached_input_tokens': 60}}]


def stream(items):
    return '\n'.join(json.dumps(item) for item in items)


class CodexResultTests(unittest.TestCase):
    def test_nonterminal_startup_diagnostics_require_a_complete_matching_turn(self):
        diagnostic = {'type': 'item.completed', 'item': {
            'id': 'startup-warning', 'type': 'error', 'message': 'PRIVATE startup diagnostic'}}
        source = events()
        complete = [source[0], diagnostic] + source[1:]
        parsed = parse_codex_events(stream(complete), '{"done":true}')
        self.assertEqual(parsed['input_tokens'], 100)
        self.assertNotIn('PRIVATE', str(parsed))
        for bad in ([diagnostic] + source, complete[:-1],
                    source[:2] + [diagnostic] + source[2:],
                    [source[0]] + [diagnostic]*33 + source[1:]):
            with self.subTest(sequence_length=len(bad)), self.assertRaises(CodexProtocolError):
                parse_codex_events(stream(bad), '{"done":true}')

    def test_parses_observed_usage_and_thread_separately(self):
        result = parse_codex_events(stream(events()), '{"done":true}\n')
        self.assertEqual('thread-1', result['provider_thread_id'])
        self.assertEqual(100, result['input_tokens'])
        self.assertEqual(60, result['cached_input_tokens'])

    def test_partial_error_and_multiple_terminals_cannot_succeed(self):
        bad = [events()[:-1], events() + [{'type': 'error', 'message': 'private'}],
               events() + [events()[-1]], events()[:2] + [{'type': 'turn.failed'}],
               events()[1:], events()[:2] + [{'type': 'item.completed', 'item': {'type': 'error'}}] + events()[2:]]
        for sequence in bad:
            with self.subTest(sequence=sequence), self.assertRaises(CodexProtocolError):
                parse_codex_events(stream(sequence), '{"done":true}')
        with self.assertRaises(CodexProtocolError):
            parse_codex_events(stream(events()), 'different answer')
        with self.assertRaises(CodexProtocolError):
            parse_codex_events('not json\n' + stream(events()), '{"done":true}')

    def test_usage_booleans_negative_and_invalid_cache_rejected(self):
        for key, value in [('input_tokens', True), ('output_tokens', -1),
                           ('cached_input_tokens', 101), ('cached_input_tokens', False)]:
            sequence = events()
            sequence[-1]['usage'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(CodexProtocolError):
                parse_codex_events(stream(sequence), '{"done":true}')

    def test_tool_activity_cannot_be_accepted_as_a_planning_answer(self):
        for phase in ('started', 'updated', 'completed'):
            for item_type in ('command_execution', 'mcp_tool_call', 'web_search',
                              'file_change', 'todo_list', 'future_tool', None):
                source = events()
                item = {'type': 'item.' + phase, 'item': {
                    'id': 'tool', 'type': item_type, 'status': 'completed'}}
                with self.subTest(phase=phase, item_type=item_type), \
                     self.assertRaises(CodexProtocolError):
                    parse_codex_events(stream(source[:2] + [item] + source[2:]),
                                       '{"done":true}')

    def test_reasoning_before_final_answer_is_still_accepted(self):
        source = events()
        reasoning = {'type': 'item.completed', 'item': {
            'id': 'reason', 'type': 'reasoning', 'text': 'analysis'}}
        parsed = parse_codex_events(stream(source[:2] + [reasoning] + source[2:]),
                                    '{"done":true}')
        self.assertEqual('{"done":true}', parsed['text'])
        self.assertNotIn('analysis', str(parsed))

    def test_adapter_rejects_tool_answer_but_preserves_complete_turn_usage(self):
        source = events()
        tool = {'type': 'item.completed', 'item': {
            'id': 'tool', 'type': 'command_execution', 'status': 'completed'}}

        def launch(argv, prompt, timeout, *, cwd=None):
            Path(argv[argv.index('--output-last-message')+1]).write_text('{"done":true}')
            return subprocess.CompletedProcess(argv, 0,
                stream(source[:2] + [tool] + source[2:]), '')

        with patch('tacto.codex_result._run_codex', side_effect=launch):
            result = codex_result('q')
        self.assertEqual('error', result.status)
        self.assertEqual('CodexProtocolError', result.error.code)
        self.assertIsNone(result.text)
        self.assertEqual(100,result.input_tokens)
        self.assertEqual(10,result.output_tokens)
        self.assertEqual('thread-1',result.provider_thread_id)
        self.assertIsNone(result.cost)
        self.assertIsNone(result.model)

    def test_failure_and_timeout_keep_only_valid_complete_turn_metadata(self):
        for source in (stream(events()),stream(events()).encode()):
            with self.subTest(source_type=type(source).__name__),patch('tacto.codex_result._run_codex',side_effect=subprocess.TimeoutExpired('PRIVATE',1,output=source)):
                result=codex_result('q')
            self.assertEqual('timeout',result.status)
            self.assertIsNone(result.text)
            self.assertEqual(100,result.input_tokens)
            self.assertFalse(result.cancellation_supported)
            self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
        for source in (stream(events()[:-1]),stream(events()+[events()[-1]]),'not JSON',b'\xff'):
            with self.subTest(source=repr(source)[:80]),patch('tacto.codex_result._run_codex',side_effect=subprocess.TimeoutExpired('PRIVATE',1,output=source)):
                result=codex_result('q')
            self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
            self.assertIsNone(result.input_tokens)
        with patch('tacto.codex_result._run_codex',return_value=subprocess.CompletedProcess([],1,stream(events()),'PRIVATE STDERR')):
            result=codex_result('q')
        self.assertEqual('error',result.status);self.assertIsNone(result.text)
        self.assertEqual(10,result.output_tokens)
        self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))

    def test_rejected_final_file_and_invalid_usage_do_not_become_answers(self):
        def launch(argv,prompt,timeout, *, cwd=None):
            Path(argv[argv.index('--output-last-message')+1]).write_text('PRIVATE inconsistent answer')
            return subprocess.CompletedProcess(argv,0,stream(events()),'')
        with patch('tacto.codex_result._run_codex',side_effect=launch):result=codex_result('q')
        self.assertEqual('error',result.status);self.assertIsNone(result.text)
        self.assertEqual(100,result.input_tokens)
        self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
        source=events();source[-1]['usage']['input_tokens']=True
        with patch('tacto.codex_result._run_codex',return_value=subprocess.CompletedProcess([],1,stream(source),'')):
            result=codex_result('q')
        self.assertIsNone(result.input_tokens);self.assertIsNone(result.output_tokens)

    def test_owned_process_timeout_retains_completed_telemetry_without_an_answer(self):
        script='import sys,time;sys.stdin.read();print('+repr(stream(events()))+',flush=True);time.sleep(5)'
        def launch(argv,prompt,timeout, *, cwd=None):
            return _run_codex([sys.executable,'-c',script],prompt,.3,cwd=cwd)
        with patch('tacto.codex_result._run_codex',side_effect=launch):result=codex_result('q')
        self.assertEqual('timeout',result.status)
        self.assertEqual(100,result.input_tokens)
        self.assertEqual(10,result.output_tokens)
        self.assertIsNone(result.text);self.assertIsNone(result.cost)
        self.assertFalse(result.cancellation_supported)

    def test_failed_tool_telemetry_reaches_common_summary_without_tool_content(self):
        from tacto.provider_observation import capture_provider_calls,record_provider_result,summarize_provider_calls
        source=events()
        source.insert(2,{'type':'item.completed','item':{'type':'command_execution','status':'failed','command':'PRIVATE TOOL COMMAND'}})
        with patch('tacto.codex_result._run_codex',return_value=subprocess.CompletedProcess([],1,stream(source),'')),capture_provider_calls() as records:
            result=record_provider_result(codex_result('q'))
        self.assertEqual('error',result.status);self.assertIsNone(result.text)
        self.assertEqual(100,result.input_tokens)
        summary=summarize_provider_calls(records)
        self.assertEqual(100,summary['tokens']['total_input_tokens']['observed_sum'])
        self.assertEqual(1,summary['unknown_cost_records'])
        self.assertNotIn('PRIVATE',json.dumps(records))

    def test_explicit_adapter_stdin_private_file_and_unknown_metadata(self):
        paths = []
        def launch(argv, prompt, timeout, *, cwd=None):
            self.assertEqual('sensitive prompt', prompt)
            self.assertNotIn(prompt, argv)
            self.assertEqual('-', argv[-1])
            self.assertIn('--ephemeral', argv)
            self.assertEqual('read-only', argv[argv.index('--sandbox')+1])
            self.assertEqual('requested-model', argv[argv.index('--model')+1])
            path = Path(argv[argv.index('--output-last-message')+1])
            paths.append(path)
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            path.write_text('{"done":true}')
            return subprocess.CompletedProcess(argv, 0, stream(events()), '')
        with patch('tacto.codex_result._run_codex', side_effect=launch):
            result = codex_result('sensitive prompt', model='requested-model')
        self.assertEqual('ok', result.status)
        self.assertEqual(10, result.output_tokens)
        self.assertIsNone(result.model)
        self.assertIsNone(result.provider_request_id)
        self.assertIsNone(result.cost)
        self.assertFalse(result.cancellation_supported)
        self.assertFalse(paths[0].exists())

    def test_nonzero_exit_and_timeout_are_not_success(self):
        with patch('tacto.codex_result._run_codex', return_value=subprocess.CompletedProcess([], 1, stream(events()), '')):
            result = codex_result('q')
        self.assertEqual('error', result.status)
        with patch('tacto.codex_result._run_codex', side_effect=subprocess.TimeoutExpired('PRIVATE', 1)):
            result = codex_result('q')
        self.assertEqual('timeout', result.status)
        self.assertIsNone(result.input_tokens)
        self.assertNotIn('PRIVATE', json.dumps(result.to_dict()))

    def test_process_helper_passes_stdin_without_shell(self):
        result = _run_codex([sys.executable, '-c', 'import sys;print(sys.stdin.read())'], 'literal $(not shell)', 3)
        self.assertEqual(0, result.returncode)
        self.assertEqual('literal $(not shell)', result.stdout.strip())

    def test_login_and_quota_errors_remain_safe_reason_codes(self):
        for code, stdout, stderr, reason in ((1, '', 'usage limit reached SECRET', 'usage_limit'),
                                            (0, 'Please run /login', '', 'not_logged_in')):
            response = subprocess.CompletedProcess([], code, stdout, stderr)
            with patch('tacto.codex_result._run_codex', return_value=response):
                result = codex_result('private prompt')
            self.assertEqual(result.status, 'error')
            self.assertEqual(result.error.code, reason)
            self.assertNotIn('SECRET', json.dumps(result.to_dict()))

    def test_invalid_deadline_rejected_before_launch(self):
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(value=value), patch('tacto.codex_result._run_codex') as launch:
                result = codex_result('q', timeout=value)
                self.assertEqual('error', result.status)
                launch.assert_not_called()


class CodexInputTokenSemanticsTests(unittest.TestCase):
    def test_cached_input_is_not_added_again_and_cache_write_remains_optional(self):
        source = events()
        def parse():
            return parse_codex_events(stream(source), '{"done":true}')
        result = parse()
        self.assertEqual('total', result['input_tokens_basis'])
        self.assertEqual(100,result['total_input_tokens'])
        self.assertIsNone(result['cache_creation_input_tokens'])
        source[-1]['usage']['cache_write_input_tokens'] = 20
        result = parse()
        self.assertEqual(100,result['total_input_tokens'])
        self.assertEqual(20,result['cache_creation_input_tokens'])
        source[-1]['usage']['cache_write_input_tokens'] = True
        with self.assertRaises(CodexProtocolError): parse()


if __name__ == '__main__':
    unittest.main()
