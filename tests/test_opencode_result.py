"""Versioned JSON framing, telemetry and bounded local transport; no model calls."""
import json
import subprocess
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from tacto.opencode_result import OpenCodeProtocolError, opencode_result, parse_opencode_events
from tacto.provider_result import parse_provider_result
from tacto.authoring import ask_model_result


def frames():
    def event(kind, part):
        return {'type':kind,'timestamp':100,'sessionID':'ses_owned','part':
            {'id':'part_'+kind,'sessionID':'ses_owned','messageID':'msg_owned',**part}}
    return [event('step_start',{'type':'step-start'}),
        event('text',{'type':'text','text':'{"ok":true}','time':{'start':100,'end':101}}),
        event('step_finish',{'type':'step-finish','reason':'stop','cost':.001,
            'tokens':{'input':10,'output':4,'reasoning':1,'cache':{'read':6,'write':2}}})]


def stream(events): return '\n'.join(json.dumps(event) for event in events)


class OpenCodeResultTests(unittest.TestCase):
    def test_complete_same_session_step_preserves_reported_usage(self):
        parsed = parse_opencode_events(stream(frames()))
        self.assertEqual('{"ok":true}',parsed['text'])
        self.assertEqual(18,parsed['total_input_tokens'])
        self.assertEqual('uncached',parsed['input_tokens_basis'])
        self.assertEqual(6,parsed['cached_input_tokens'])
        self.assertEqual(.001,parsed['cost'])
        self.assertNotIn('model',parsed); self.assertNotIn('cost_currency',parsed)

    def test_refuses_tools_cross_session_restart_truncation_and_invalid_counts(self):
        bad = [frames()[:-1], frames()+frames(), []]
        for change in ({'type':'tool_use'}, {'sessionID':'ses_foreign'}, {'timestamp':True}):
            events=frames(); events[1].update(change);bad.append(events)
        events=frames();events[1]['part']['messageID']='msg_foreign';bad.append(events)
        events=frames();events[-1]['part']['reason']='tool-calls';bad.append(events)
        for value in (True,-1,'10',float('inf')):
            events=frames();events[-1]['part']['tokens']['input']=value;bad.append(events)
            events=frames();events[-1]['part']['cost']=value;bad.append(events)
        for events in bad:
            with self.subTest(events=events), self.assertRaises(OpenCodeProtocolError):
                parse_opencode_events(stream(events))
        with self.assertRaises(OpenCodeProtocolError):parse_opencode_events('plain answer')

    def test_error_after_valid_step_keeps_usage_but_never_answer(self):
        events=frames()+[{'type':'error','timestamp':102,'sessionID':'ses_owned',
            'error':{'name':'UnknownError','data':{'message':'private error'}}}]
        with patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],0,stream(events),'')):
            result=opencode_result('private prompt')
        self.assertEqual('error',result.status);self.assertIsNone(result.text)
        self.assertEqual(18,result.total_input_tokens);self.assertEqual(.001,result.cost)
        self.assertNotIn('private error',str(result.to_dict()))
        parse_provider_result(result.to_dict())
        with patch('tacto.authoring.shutil.which',return_value='/owned/opencode'), \
             patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],0,stream(events),'')):
            logical=ask_model_result('private prompt','opencode')
        self.assertEqual('error',logical.status)
        self.assertEqual(18,logical.attempts[-1].total_input_tokens)
        self.assertEqual(.001,logical.attempts[-1].cost)

    def test_length_limited_finish_retains_usage_without_accepting_answer(self):
        events=frames();events[-1]['part']['reason']='length'
        with patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],0,stream(events),'')):
            result=opencode_result('q')
        self.assertEqual('error',result.status);self.assertIsNone(result.text)
        self.assertEqual(18,result.total_input_tokens)
        self.assertEqual(.001,result.cost)

    def test_framed_answer_can_discuss_authentication_and_quotas(self):
        events=frames();events[1]['part']['text']='Error: Token refresh failed: 401; quota explanation'
        with patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],0,stream(events),'')):
            result=opencode_result('q')
        self.assertEqual('ok',result.status)
        self.assertEqual(events[1]['part']['text'],result.text)

    def test_failed_framing_does_not_classify_assistant_discussion_as_diagnostics(self):
        for variant in ('truncated','late_error','nonzero'):
            events=frames();events[1]['part']['text']='quota; not logged in; please run /login'
            if variant=='truncated':events=events[:-1]
            elif variant=='late_error':
                events.append({'type':'error','timestamp':102,'sessionID':'ses_owned',
                    'error':{'name':'UnknownError','data':{'message':'unrelated failure'}}})
            with self.subTest(variant=variant), patch('tacto.authoring._run_provider',
                    return_value=subprocess.CompletedProcess([],1 if variant=='nonzero' else 0,stream(events),'')):
                result=opencode_result('q')
            self.assertEqual('error',result.status);self.assertIsNone(result.text)
            self.assertEqual('OpenCodeProtocolError',result.error.code)
            self.assertEqual(None if variant=='truncated' else 18,result.total_input_tokens)

    def test_only_diagnostic_channels_classify_service_unavailability(self):
        from tacto.opencode_result import diagnostic_unavailable
        auth={'type':'error','timestamp':102,'sessionID':'ses_owned',
            'error':{'name':'UnknownError','data':{'message':'Error: Token refresh failed: 401'}}}
        self.assertEqual('authentication_failed',diagnostic_unavailable(json.dumps(auth),''))
        self.assertEqual('usage_limit',diagnostic_unavailable(stream(frames()),'rate limit exceeded'))
        self.assertEqual('not_logged_in',diagnostic_unavailable('', 'please run /login'))
        for change in ({'timestamp':True},{'sessionID':'foreign'},{'extra':'field'}):
            with self.subTest(change=change):
                self.assertIsNone(diagnostic_unavailable(json.dumps({**auth,**change}),''))
        self.assertIsNone(diagnostic_unavailable('Error: Token refresh failed: 401',''))

    def test_route_stdin_isolated_config_and_metadata_attempts(self):
        def run(argv, timeout, **kwargs):
            self.assertEqual(['opencode','run','--format','json','--agent','NLCPlanner'],argv)
            self.assertEqual('private prompt',kwargs['input_text'])
            self.assertNotIn('private prompt',argv)
            env=kwargs['env'];config=json.loads(env['OPENCODE_CONFIG_CONTENT'])
            self.assertEqual({'*':'deny'},config['permission'])
            self.assertEqual('disabled',config['share'])
            self.assertEqual([],config['plugin'])
            self.assertNotIn('OPENCODE_CONFIG',env)
            self.assertEqual('true',env['OPENCODE_DISABLE_PROJECT_CONFIG'])
            return subprocess.CompletedProcess(argv,0,stream(frames()),'')
        with patch('tacto.authoring.shutil.which',return_value='/owned/opencode'), \
             patch('tacto.authoring._run_provider',side_effect=run):
            result=ask_model_result('private prompt','opencode',expect_json=True)
        self.assertEqual('ok',result.status);self.assertEqual('cli-json',result.transport)
        self.assertEqual(18,result.attempts[-1].total_input_tokens)
        self.assertIsNone(result.model);self.assertIsNone(result.cost_currency)
        self.assertFalse(result.cancellation_supported)

    def test_timeout_and_nonzero_exit_keep_failure_closed(self):
        for response in (subprocess.TimeoutExpired('owned',1),
                         subprocess.CompletedProcess([],1,stream(frames()),'private error')):
            runner = ({'side_effect':response} if isinstance(response,Exception) else {'return_value':response})
            with patch('tacto.authoring._run_provider',**runner):result=opencode_result('q')
            self.assertIn(result.status,('error','timeout'));self.assertIsNone(result.text)
            self.assertNotIn('private error',str(result.to_dict()))

    def test_real_local_process_receives_stdin_and_isolated_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'opencode'
            executable.write_text('#!'+sys.executable+'\n'+'''import json,os,sys
assert sys.argv[1:]==['run','--format','json','--agent','NLCPlanner']
assert sys.stdin.read()=='private stdin prompt'
assert os.getcwd()!=os.environ['TACTO_TEST_CALLER_CWD']
assert json.loads(os.environ['OPENCODE_CONFIG_CONTENT'])['permission']=={'*':'deny'}
assert 'OPENCODE_CONFIG' not in os.environ
print('''+repr(stream(frames()))+''')
''')
            executable.chmod(0o700)
            with patch.dict(os.environ,{'PATH':folder+os.pathsep+os.environ['PATH'],
                    'OPENCODE_CONFIG':'/foreign/config','TACTO_TEST_CALLER_CWD':os.getcwd()}):
                result=opencode_result('private stdin prompt',timeout=3)
            self.assertEqual('ok',result.status,result.error)
            self.assertEqual('{"ok":true}',result.text)
