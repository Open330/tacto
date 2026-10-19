"""Gemini planning framing/configuration; fixtures never contact a model."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tacto.gemini_result import GeminiProtocolError,gemini_result,parse_gemini_events,wire_prompt,_settings
from tacto.provider_result import parse_provider_result
from tacto.authoring import ask_model_result

STAMP='2026-10-11T01:02:03.004Z'
SESSION='12345678-1234-1234-1234-123456789abc'


def frames(prompt='owned'):
    def event(kind,**fields):return {'type':kind,'timestamp':STAMP,**fields}
    usage={'total_tokens':19,'input_tokens':10,'output_tokens':4,'cached':6,'input':4}
    return [event('init',session_id=SESSION,model='auto'),event('message',role='user',content=prompt),
        event('message',role='assistant',content='{"ok":true}',delta=True),
        event('result',status='success',stats={**usage,'duration_ms':30,'tool_calls':0,
            'models':{'gemini-observed':usage.copy()}})]


def stream(events):return '\n'.join(json.dumps(event) for event in events)


class GeminiResultTests(unittest.TestCase):
    def test_single_run_usage_uses_reported_model_and_total_input_not_selector(self):
        fields=parse_gemini_events(stream(frames()),'owned')
        self.assertEqual('gemini-observed',fields['model'])
        self.assertEqual(10,fields['total_input_tokens']);self.assertEqual(6,fields['cached_input_tokens'])
        self.assertEqual('total',fields['input_tokens_basis']);self.assertEqual(SESSION,fields['provider_thread_id'])
        self.assertEqual('{"ok":true}',fields['text']);self.assertNotIn('cost',fields)

    def test_error_or_tools_retains_valid_counters_without_answer(self):
        for extra in ({'type':'tool_use','timestamp':STAMP,'tool_name':'owned'},
                      {'type':'error','timestamp':STAMP,'message':'PRIVATE'}):
            data=frames();data.insert(2,extra)
            with self.assertRaises(GeminiProtocolError) as rejected:parse_gemini_events(stream(data),'owned')
            self.assertEqual(10,rejected.exception.fields['input_tokens'])
            self.assertNotIn('text',rejected.exception.fields)
        data=frames();data[-1]['status']='error'
        with self.assertRaises(GeminiProtocolError) as rejected:parse_gemini_events(stream(data),'owned')
        self.assertEqual(4,rejected.exception.fields['output_tokens'])

    def test_wrong_run_missing_completion_bad_counters_and_framing_refused(self):
        bad=[frames()[:-1],frames()+frames(),frames()[1:],frames()+[frames()[2]],[]]
        for value in (True,-1,'10',2**53,float('inf')):
            data=frames();data[-1]['stats']['input_tokens']=value;bad.append(data)
        for key,value in [('model',[]),('session_id','foreign'),('timestamp',True)]:
            data=frames();data[0][key]=value;bad.append(data)
        data=frames();data[1]['content']='foreign prompt';bad.append(data)
        data=frames();data[2]['delta']=False;bad.append(data)
        data=frames();data[-1]['stats']['models']['gemini-observed']['input_tokens']=11;bad.append(data)
        data=frames();data[-1]['stats']['tool_calls']=1;bad.append(data)
        for data in bad:
            with self.subTest(data=data),self.assertRaises(GeminiProtocolError):parse_gemini_events(stream(data),'owned')
        with self.assertRaises(GeminiProtocolError):parse_gemini_events('plain answer','owned')

    def test_multiple_reported_models_aggregate_usage_without_fake_single_model(self):
        data=frames();stats=data[-1]['stats'];stats['models']['second']=stats['models']['gemini-observed'].copy()
        for key in ('total_tokens','input_tokens','output_tokens','cached','input'):stats[key]*=2
        fields=parse_gemini_events(stream(data),'owned')
        self.assertNotIn('model',fields);self.assertEqual(20,fields['input_tokens'])

    def test_wire_payload_roundtrip_disables_at_and_slash_macros(self):
        prompt='/shell\n@/private/owned.txt foo@example.com \\@name "한글"'
        wire=wire_prompt(prompt)
        self.assertNotIn('@',wire);self.assertFalse(wire.startswith('/'))
        self.assertEqual(prompt,json.loads(wire.split('\n',1)[1])['planning_prompt'])

    def test_clamped_system_settings_preserve_auth_and_refuse_foreign_file_shapes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'system.json';path.write_text(json.dumps({'security':{'auth':{'selectedType':'owned'}},
                'tools':{'core':['ShellTool'],'discoveryCommand':'owned discovery'},'hooksConfig':{'enabled':True}}))
            result=_settings({'GEMINI_CLI_SYSTEM_SETTINGS_PATH':str(path)})
            self.assertEqual('owned',result['security']['auth']['selectedType'])
            self.assertEqual([],result['tools']['core']);self.assertEqual('',result['tools']['discoveryCommand'])
            self.assertFalse(result['hooksConfig']['enabled']);self.assertFalse(result['admin']['mcp']['enabled'])
            alias=Path(folder)/'alias';alias.symlink_to(path)
            with self.assertRaises(OSError):_settings({'GEMINI_CLI_SYSTEM_SETTINGS_PATH':str(alias)})
            path.write_text('[]')
            with self.assertRaises(ValueError):_settings({'GEMINI_CLI_SYSTEM_SETTINGS_PATH':str(path)})

    def test_actual_local_process_uses_stdin_private_cwd_and_clamps_session_config(self):
        with tempfile.TemporaryDirectory() as folder:
            executable=Path(folder)/'gemini'
            executable.write_text('#!'+sys.executable+'\n'+r'''
import json,os,sys
from pathlib import Path
prompt=sys.stdin.read()
assert '@' not in prompt
assert sys.argv[1:]==['--output-format','stream-json','--extensions','none','--approval-mode','plan']
config=Path(os.environ['GEMINI_CLI_SYSTEM_SETTINGS_PATH'])
assert config.parent.resolve()==Path.cwd().resolve() and config.stat().st_mode&0o777==0o600
settings=json.loads(config.read_text())
assert settings['tools']['core']==[] and settings['tools']['discoveryCommand']==''
assert not settings['admin']['mcp']['enabled'] and not settings['hooksConfig']['enabled']
assert 'TACTO_HELPER_FD' not in os.environ and os.environ['NO_BROWSER']=='1'
sys.path.insert(0,sys.argv[0].rsplit('/',1)[0])
from fixture import frames,stream
print(stream(frames(prompt)))
''')
            executable.chmod(0o700)
            (Path(folder)/'fixture.py').write_text('from tests.test_gemini_result import frames,stream\n')
            env=os.environ.copy();env.update(PATH=folder+os.pathsep+env['PATH'],TACTO_HELPER_FD='999',
                GEMINI_CLI_SYSTEM_SETTINGS_PATH=str(Path(folder)/'absent-system.json'))
            # The child fixture import is explicit; no user CLI/config is invoked.
            env['PYTHONPATH']=str(Path.cwd())
            from tacto.authoring import _run_provider
            diagnostics=[]
            def execute(*args,**kwargs):
                response=_run_provider(*args,**kwargs);diagnostics.append(response.stderr);return response
            with patch.dict(os.environ,env,clear=True),patch('tacto.authoring._run_provider',side_effect=execute):
                result=gemini_result('owned @text',timeout=5)
            self.assertEqual('ok',result.status,(result.error,diagnostics))
            self.assertEqual(10,result.input_tokens);self.assertIsNone(result.cost)
            self.assertFalse(result.cancellation_supported)
            self.assertEqual(result,parse_provider_result(result.to_dict()))

    def test_unsupported_client_and_timeout_remain_failed_provider_attempts(self):
        error='Error authenticating: IneligibleTierError: unsupported_client'
        with patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],1,'',error)), \
             patch('tacto.authoring.shutil.which',return_value='/owned/gemini'):
            result=ask_model_result('owned','gemini',timeout=5)
        self.assertEqual('error',result.status);self.assertEqual('unsupported_client',result.error.code)
        self.assertEqual('cli-json',result.attempts[-1].transport);self.assertIsNone(result.text)
        with patch('tacto.authoring._run_provider',side_effect=subprocess.TimeoutExpired('owned',1)):
            result=gemini_result('owned',timeout=1)
        self.assertEqual('timeout',result.status);self.assertIsNone(result.text)

    def test_assistant_stdout_cannot_spoof_authentication_failure(self):
        data=frames(wire_prompt('owned'));data[2]['content']='Error authenticating: IneligibleTierError: unsupported_client'
        data[-1]['status']='error'
        with patch('tacto.authoring._run_provider',return_value=subprocess.CompletedProcess([],1,stream(data),'')):
            result=gemini_result('owned',timeout=5)
        self.assertEqual('GeminiProtocolError',result.error.code)
        self.assertEqual(10,result.input_tokens);self.assertIsNone(result.text)
