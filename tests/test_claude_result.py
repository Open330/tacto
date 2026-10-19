import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tacto.claude_result import (ClaudeProtocolError, claude_result,
                               input_message, parse_claude_events, _run_claude)


def events(session='session-1'):
    return [
        {'type': 'system', 'subtype': 'init', 'session_id': session, 'tools': [], 'mcp_servers': []},
        {'type': 'rate_limit_event', 'session_id': session},
        {'type': 'assistant', 'session_id': session, 'parent_tool_use_id': None,
         'message': {'role': 'assistant', 'model': 'observed-model', 'usage': {'output_tokens': 3},
                     'content': [{'type': 'text', 'text': 'NLC-OK'}]}},
        {'type': 'result', 'session_id': session, 'subtype': 'success', 'is_error': False,
         'num_turns': 1, 'stop_reason': 'end_turn', 'permission_denials': [],
         'result': 'NLC-OK', 'usage': {'input_tokens': 10, 'output_tokens': 7, 'cache_read_input_tokens': 50},
         'total_cost_usd': 0.003, 'modelUsage': {'observed-model': {'costBasis': 'list'}}}]


def stream(items):
    return '\n'.join(json.dumps(item) for item in items)


class ClaudeResultTests(unittest.TestCase):
    def test_nonzero_complete_refusal_keeps_specific_failure_without_accepting_text(self):
        for exit_code in (0, 1):
            def launch(argv, *args):
                sid = argv[argv.index('--session-id') + 1]
                data = events(sid)
                data.insert(1, {'type': 'system', 'subtype': 'informational',
                               'session_id': sid, 'content': 'PRIVATE'})
                data.insert(-1, {'type': 'system', 'subtype': 'model_refusal_no_fallback',
                    'session_id': sid, 'uuid': '12345678-1234-1234-1234-123456789abc',
                    'original_model': 'observed-model', 'content': 'PRIVATE'})
                data[-1].update(is_error=True, num_turns=2)
                return exit_code, stream(data), 'PRIVATE'
            with self.subTest(exit_code=exit_code), patch('tacto.claude_result._run_claude', side_effect=launch) as call:
                result = claude_result('owned fixture')
            self.assertEqual('error', result.status)
            self.assertEqual('ClaudeProtocolError_model_refusal', result.error.code)
            self.assertIsNone(result.text)
            self.assertIsNone(result.model)
            self.assertIsNone(result.input_tokens)
            self.assertNotIn('PRIVATE', json.dumps(result.to_dict()))
            call.assert_called_once()

    def test_refusal_diagnostic_rejects_incomplete_foreign_or_tool_enabled_stream(self):
        from tacto.claude_result import _observed_refusal_no_fallback
        data = events()
        data.insert(-1, {'type': 'system', 'subtype': 'model_refusal_no_fallback',
            'session_id': 'session-1', 'uuid': '12345678-1234-1234-1234-123456789abc',
            'original_model': 'observed-model'})
        data[-1].update(is_error=True, num_turns=2)
        self.assertTrue(_observed_refusal_no_fallback(stream(data), 'session-1'))
        notice = {'type': 'system', 'subtype': 'ui_invalidate', 'event': 'ui.render',
                  'session_id': 'session-1', 'uuid': '12345678-1234-1234-1234-123456789abd'}
        self.assertTrue(_observed_refusal_no_fallback(stream([notice] + data), 'session-1'))
        for change in (
                data[:-1], data + [data[-1]], [notice, notice] + data,
                [{**data[0], 'tools': ['Bash']}] + data[1:],
                [{**data[0], 'session_id': 'foreign'}] + data[1:],
                data[:-1] + [{**data[-1], 'is_error': False}],
                data[:-1] + [{**data[-1], 'num_turns': True}],
                data[:-1] + [{**data[-1], 'num_turns': 3}],
                data[:-2] + [{**data[-2], 'uuid': 'bad'}] + data[-1:],
                data[:-2] + [{**data[-2], 'original_model': ''}] + data[-1:]):
            with self.subTest(change=change):
                self.assertFalse(_observed_refusal_no_fallback(stream(change), 'session-1'))

    def test_one_exact_content_free_render_notice_can_precede_init(self):
        notice = {'type': 'system', 'subtype': 'ui_invalidate', 'session_id': 'session-1',
                  'uuid': '12345678-1234-1234-1234-123456789abc', 'event': 'ui.render'}
        commands = {'type': 'system', 'subtype': 'commands_changed', 'session_id': 'session-1',
                    'uuid': '12345678-1234-1234-1234-123456789abd', 'commands': []}
        for prefix in ([notice], [notice, commands], [commands, notice]):
            result = parse_claude_events(stream(prefix + events()), 'session-1')
            self.assertEqual('NLC-OK', result['text'])
            self.assertEqual(7, result['output_tokens'])
        for variant in (
                [notice, notice] + events(),
                [notice] + events()[2:],
                [notice] + [{**events()[0], 'tools': ['Bash']}] + events()[1:],
                events() + [notice],
                [events()[0], notice] + events()[1:]):
            with self.subTest(variant=variant), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(variant), 'session-1')

    def test_render_notice_cannot_carry_instances_body_commands_or_unknown_identity(self):
        notice = {'type': 'system', 'subtype': 'ui_invalidate', 'session_id': 'session-1',
                  'uuid': '12345678-1234-1234-1234-123456789abc', 'event': 'ui.render'}
        for change in ({'instances': []}, {'instances': [{'command': 'PRIVATE'}]},
                       {'content': 'PRIVATE'}, {'tools': []}, {'commands': []},
                       {'event': 'ui.focus'}, {'event': ['ui.render']},
                       {'subtype': 'ui_focus'}, {'uuid': 'invalid'}, {'uuid': None},
                       {'uuid': notice['uuid'].upper()}, {'session_id': 'other'}):
            with self.subTest(change=change), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream([{**notice, **change}] + events()), 'session-1')
        for field in notice:
            missing = {k: v for k, v in notice.items() if k != field}
            with self.subTest(missing=field), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream([missing] + events()), 'session-1')

    def test_one_exact_empty_commands_notice_can_precede_init(self):
        notice = {'type':'system','subtype':'commands_changed','session_id':'session-1',
                  'uuid':'12345678-1234-1234-1234-123456789abc','commands':[]}
        self.assertEqual(parse_claude_events(stream([notice] + events()), 'session-1')['text'], 'NLC-OK')
        for prefix in ([notice, notice], [{**notice, 'commands':['unexpected']}],
                       [{**notice, 'uuid':'malformed'}], [{**notice, 'extra':'unobserved'}],
                       [{**notice, 'session_id':'other'}], [notice, events()[2]]):
            with self.subTest(prefix=prefix), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(prefix + events()), 'session-1')
        with self.assertRaises(ClaudeProtocolError):
            parse_claude_events(stream([notice] + events()[2:]), 'session-1')
        with self.assertRaises(ClaudeProtocolError):
            parse_claude_events(stream(events() + [notice]), 'session-1')

    def test_observed_refusal_fallback_is_rejected_with_specific_diagnostic(self):
        def launch(argv, *args):
            sid = argv[argv.index('--session-id') + 1]
            self.assertEqual(json.loads(argv[argv.index('--settings') + 1]),
                             {'switchModelsOnFlag': False})
            data = events(sid)
            data.insert(2, {'type':'system','subtype':'informational',
                            'session_id':sid,'content':'private diagnostic'})
            data.insert(-1, {'type':'system','subtype':'model_refusal_fallback',
                'session_id':sid,'trigger':'refusal','direction':'retry','scope':'session',
                'original_model':'original-model','fallback_model':'fallback-model'})
            data[-1]['num_turns'] = 2
            return 0, stream(data), 'private stderr'
        with patch('tacto.claude_result._run_claude', side_effect=launch):
            result = claude_result('owned fixture')
        self.assertEqual(result.status, 'error')
        self.assertIsNone(result.text)
        self.assertEqual(result.error.code, 'ClaudeProtocolError_model_fallback')
        self.assertNotIn('private diagnostic', json.dumps(result.to_dict()))
        data = events()
        data.insert(2, {'type':'system','subtype':'model_refusal_no_fallback','session_id':'session-1'})
        with self.assertRaises(ClaudeProtocolError) as caught:
            parse_claude_events(stream(data), 'session-1')
        self.assertEqual(caught.exception.code, 'ClaudeProtocolError_model_refusal')

    def test_protocol_failure_codes_distinguish_boundaries_without_raw_output(self):
        for variant, code in (
                (events()[:-1], 'missing_terminal'),
                (events() + [events()[1]], 'after_terminal'),
                ([{**events()[0], 'session_id': 'other'}], 'session_mismatch')):
            def launch(argv, *args):
                sid = argv[argv.index('--session-id') + 1]
                data = [{**item, 'session_id': sid if item['session_id'] == 'session-1'
                         else item['session_id']} for item in variant]
                return 0, stream(data), 'private provider stderr'
            with self.subTest(code=code), patch('tacto.claude_result._run_claude', side_effect=launch):
                result = claude_result('owned fixture prompt')
            self.assertEqual('ClaudeProtocolError_' + code, result.error.code)
            self.assertNotIn('private provider stderr', json.dumps(result.to_dict()))
        with patch('tacto.claude_result._run_claude',
                   side_effect=ClaudeProtocolError('private unknown diagnostic')):
            result = claude_result('q')
        self.assertEqual('ClaudeProtocolError', result.error.code)
        self.assertNotIn('private unknown diagnostic', json.dumps(result.to_dict()))

    def test_image_thinking_progress_respects_session_and_turn_boundaries(self):
        progress = {'type': 'system', 'subtype': 'thinking_tokens', 'session_id': 'session-1'}
        data = events()
        data.insert(2, progress)
        self.assertEqual('NLC-OK', parse_claude_events(stream(data), 'session-1')['text'])
        for invalid in ([progress] + events(), events() + [progress],
                        [events()[0], {**progress, 'session_id': 'other'}] + events()[1:]):
            with self.subTest(invalid=invalid), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(invalid), 'session-1')

    def test_uses_terminal_usage_actual_model_and_observed_cost(self):
        fields = parse_claude_events(stream(events()), 'session-1')
        self.assertEqual('observed-model', fields['model'])
        self.assertEqual(7, fields['output_tokens'])
        self.assertEqual(50, fields['cached_input_tokens'])
        self.assertEqual((0.003, 'USD'), (fields['cost'], fields['cost_currency']))
        self.assertEqual('cli_reported_list_estimate',fields['cost_basis'])

    def test_missing_cost_stays_unknown(self):
        data = events()
        data[-1].pop('total_cost_usd')
        fields = parse_claude_events(stream(data), 'session-1')
        self.assertIsNone(fields['cost'])
        self.assertIsNone(fields['cost_currency'])
        self.assertIsNone(fields['cost_basis'])
        data=events();data[-1].pop('modelUsage')
        self.assertIsNone(parse_claude_events(stream(data),'session-1')['cost_basis'])

    def test_partial_error_tool_and_multiple_models_rejected(self):
        variants = []
        variants.append(events()[:-1])
        data=events();data[-1]['stop_reason']='max_tokens';variants.append(data)
        data=events();data[-1]['subtype']='error_max_turns';variants.append(data)
        data=events();data[-1]['is_error']=True;variants.append(data)
        data=events();data[0]['tools']=['Read'];variants.append(data)
        data=events();data[0]['mcp_servers']=[{'name':'remote'}];variants.append(data)
        data=events();data[2]['message']['content']=[{'type':'tool_use','name':'Bash'}];variants.append(data)
        data=events();data[2]['message']['model']='first';data.insert(3,events()[2]);variants.append(data)
        data=events();data[-1]['result']='different';variants.append(data)
        variants.append(events()+[events()[-1]])
        for data in variants:
            with self.subTest(data=data), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(data),'session-1')

    def test_bad_usage_cost_and_session_cannot_pass(self):
        for key,value in [('input_tokens',True),('output_tokens',-1),('cache_read_input_tokens',1.2)]:
            data=events();data[-1]['usage'][key]=value
            with self.subTest(key=key), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(data),'session-1')
        for cost in (True,-1,float('nan'),float('inf')):
            data=events();data[-1]['total_cost_usd']=cost
            with self.subTest(cost=cost), self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(data),'session-1')
        with self.assertRaises(ClaudeProtocolError):
            parse_claude_events(stream(events()),'wrong-session')

    def test_stdin_image_shape_exact_bytes_and_mime_validation(self):
        png=b'\x89PNG\r\n\x1a\nfixture'
        payload=json.loads(input_message('read screenshot',png))
        blocks=payload['message']['content']
        self.assertEqual(['image','text'],[b['type'] for b in blocks])
        self.assertEqual(png,base64.b64decode(blocks[0]['source']['data']))
        self.assertEqual('image/png',blocks[0]['source']['media_type'])
        for value,mime in [(b'not image','image/png'),(png,'image/jpeg'),('', 'image/png'),(b'', 'image/png')]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                input_message('q',value,mime)

    def test_adapter_disables_tools_uses_temp_cwd_and_preserves_model_semantics(self):
        directories=[]
        def launch(argv,payload,timeout,cwd):
            directories.append(Path(cwd))
            self.assertTrue(Path(cwd).is_dir())
            self.assertEqual('',argv[argv.index('--tools')+1])
            self.assertIn('--safe-mode',argv)
            self.assertIn('--no-session-persistence',argv)
            self.assertIn('--strict-mcp-config',argv)
            self.assertEqual('requested-alias',argv[argv.index('--model')+1])
            self.assertNotIn('private prompt',argv)
            self.assertEqual('private prompt',json.loads(payload)['message']['content'][0]['text'])
            sid=argv[argv.index('--session-id')+1]
            return 0,stream(events(sid)),''
        with patch('tacto.claude_result._run_claude',side_effect=launch):
            result=claude_result('private prompt',model='requested-alias')
        self.assertEqual('ok',result.status)
        self.assertEqual('observed-model',result.model)
        self.assertNotEqual(result.request_id,result.provider_thread_id)
        self.assertIsNone(result.provider_request_id)
        self.assertFalse(result.cancellation_supported)
        self.assertFalse(directories[0].exists())

    def test_timeout_nonzero_and_invalid_deadline_are_structured(self):
        with patch('tacto.claude_result._run_claude',side_effect=subprocess.TimeoutExpired('SECRET',1)):
            result=claude_result('q')
        self.assertEqual('timeout',result.status)
        self.assertNotIn('SECRET',json.dumps(result.to_dict()))
        with patch('tacto.claude_result._run_claude',return_value=(1,'','')):
            self.assertEqual('error',claude_result('q').status)
        for timeout in (0,-1,True,float('nan'),float('inf')):
            with patch('tacto.claude_result._run_claude') as launch:
                self.assertEqual('error',claude_result('q',timeout=timeout).status)
                launch.assert_not_called()

    @unittest.skipUnless(sys.platform != 'win32','POSIX process group')
    def test_timeout_stops_descendant_without_delayed_write(self):
        with tempfile.TemporaryDirectory() as folder:
            marker=str(Path(folder)/'leaked')
            child='import pathlib,time;time.sleep(1.0);pathlib.Path('+repr(marker)+').write_text("alive")'
            parent='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);time.sleep(20)'
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_claude([sys.executable,'-c',parent],'stdin',0.2,folder)
            time.sleep(1.0)
            self.assertFalse(Path(marker).exists())

    def test_commands_changed_notice_is_not_a_turn(self):
        items = events()
        items.insert(1, {'type': 'system', 'subtype': 'commands_changed', 'session_id': 'session-1'})
        self.assertEqual('NLC-OK', parse_claude_events(stream(items), 'session-1')['text'])

    def test_failed_run_names_a_fixable_state_without_keeping_output(self):
        for stdout, stderr, reason in (
                ('Not logged in · Please run /login SECRET', '', 'not_logged_in'),
                ('', 'Claude AI usage limit reached SECRET', 'usage_limit')):
            with patch('tacto.claude_result._run_claude', return_value=(1, stdout, stderr)):
                result = claude_result('q')
            self.assertEqual(('error', reason), (result.status, result.error.code))
            self.assertNotIn('SECRET', json.dumps(result.to_dict()))

    def test_gemini_auth_diagnostic_in_failed_claude_output_is_not_gemini_refusal(self):
        output='Error authenticating: IneligibleTierError: SECRET UNSUPPORTED_CLIENT'
        with patch('tacto.claude_result._run_claude',return_value=(1,'',output)):
            result=claude_result('q')
        self.assertEqual('error',result.status)
        self.assertNotEqual('unsupported_client',result.error.code)
        self.assertNotIn('SECRET',json.dumps(result.to_dict()))

    def test_closed_unsupported_client_reason_preserves_error_envelope(self):
        with patch('tacto.claude_result._run_claude',return_value=(1,'','')), \
             patch('tacto.claude_result.classify_unavailable',return_value='unsupported_client'):
            result=claude_result('q')
        self.assertEqual(('error','unsupported_client'),(result.status,result.error.code))

    def test_an_answer_about_quotas_is_still_an_answer(self):
        def launch(argv, payload, timeout, cwd):
            sid = argv[argv.index('--session-id') + 1]
            items = events(sid)
            items[2]['message']['content'][0]['text'] = 'your usage limit and quota'
            items[3]['result'] = 'your usage limit and quota'
            return 0, stream(items), ''
        with patch('tacto.claude_result._run_claude', side_effect=launch):
            result = claude_result('q')
        self.assertEqual('ok', result.status)


class ClaudeInputTokenSemanticsTests(unittest.TestCase):
    def test_total_requires_all_three_components_including_explicit_zero(self):
        source = events()
        result = parse_claude_events(stream(source), 'session-1')
        self.assertEqual('uncached', result['input_tokens_basis'])
        self.assertIsNone(result['total_input_tokens'])
        for created, total in [(0,60),(40,100)]:
            source[-1]['usage']['cache_creation_input_tokens'] = created
            result = parse_claude_events(stream(source), 'session-1')
            self.assertEqual(10,result['input_tokens'])
            self.assertEqual(created,result['cache_creation_input_tokens'])
            self.assertEqual(total,result['total_input_tokens'])
        for invalid in (True,-1,1.5):
            source[-1]['usage']['cache_creation_input_tokens'] = invalid
            with self.assertRaises(ClaudeProtocolError):
                parse_claude_events(stream(source), 'session-1')


if __name__=='__main__':
    unittest.main()

class PreInitDiagnosticTests(unittest.TestCase):
    def test_protocol_stays_strict_and_only_closed_event_family_is_retained(self):
        from tacto.claude_result import ClaudeProtocolError, parse_claude_events
        for kind,subtype,expected in [('system','commands_changed','system_commands_changed'),
                ('system','private-content','system_other'),('assistant',None,'assistant'),
                ('rate_limit_event',None,'rate_limit_event'),('private-content',None,'unknown')]:
            event={'type':kind,'subtype':subtype,'session_id':'owned','secret':'private-content'}
            with self.assertRaises(ClaudeProtocolError) as caught:
                parse_claude_events(json.dumps(event),'owned')
            self.assertEqual('ClaudeProtocolError_before_init',caught.exception.code)
            self.assertEqual(expected,caught.exception.diagnostic)
            self.assertNotIn('private-content',str(caught.exception))
        self.assertIsNone(ClaudeProtocolError('event before initialization',diagnostic='private-content').diagnostic)

    def test_provider_error_keeps_safe_diagnostic_without_raw_stream_content(self):
        from tacto.claude_result import claude_result
        def failed(argv,payload,timeout,cwd):
            session=argv[argv.index('--session-id')+1]
            return 0,json.dumps({'type':'assistant','session_id':session,'secret':'private-value'}),''
        with patch('tacto.claude_result._run_claude',side_effect=failed):
            result=claude_result('owned test prompt')
        self.assertEqual('error',result.status)
        self.assertEqual('ClaudeProtocolError_before_init',result.error.code)
        self.assertIn('pre-init event: assistant',result.error.message)
        self.assertNotIn('private-value',result.error.message)
