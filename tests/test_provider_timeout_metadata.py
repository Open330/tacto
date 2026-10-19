"""Failure-only telemetry never authorizes a partial planning answer."""
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from tacto.claude_result import claude_result
from tacto.gemini_result import gemini_result
from tacto.opencode_result import opencode_result
from tacto.provider_io import bounded_output_text
from tests.test_claude_result import events,stream
from tests.test_gemini_result import frames as gemini_frames
from tests.test_opencode_result import frames as opencode_frames


class ProviderTimeoutMetadataTests(unittest.TestCase):
    def test_bounded_failure_decode_rejects_bad_utf8_wrong_types_and_oversize(self):
        with patch('tacto.provider_io.MAX_PROVIDER_OUTPUT_BYTES',4):
            self.assertEqual('한',bounded_output_text('한'.encode()))
            self.assertEqual('한',bounded_output_text('한'))
            for value in ('한한',b'12345',b'\xff',None,{},bytearray(b'ok'),'\ud800'):
                with self.subTest(value_type=type(value).__name__):self.assertIsNone(bounded_output_text(value))

    def test_claude_same_session_complete_telemetry_survives_timeout_without_text(self):
        for binary in (False,True):
            def launch(argv,*args):
                data=stream(events(argv[argv.index('--session-id')+1]))
                raise subprocess.TimeoutExpired('PRIVATE',1,output=data.encode() if binary else data)
            with self.subTest(binary=binary),patch('tacto.claude_result._run_claude',side_effect=launch):
                result=claude_result('q')
            self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
            self.assertEqual(10,result.input_tokens);self.assertEqual(7,result.output_tokens)
            self.assertEqual('observed-model',result.model)
            self.assertEqual(.003,result.cost);self.assertEqual('cli_reported_list_estimate',result.cost_basis)
            self.assertFalse(result.cancellation_supported)
            self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
        for mode in ('foreign','partial','duplicate','invalid'):
            def launch(argv,*args):
                sid=argv[argv.index('--session-id')+1];data=events('foreign' if mode=='foreign' else sid)
                if mode=='partial':data=data[:-1]
                if mode=='duplicate':data.append(data[-1])
                if mode=='invalid':data[-1]['usage']['input_tokens']=True
                raise subprocess.TimeoutExpired('PRIVATE',1,output=stream(data))
            with self.subTest(mode=mode),patch('tacto.claude_result._run_claude',side_effect=launch):result=claude_result('q')
            self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
            self.assertIsNone(result.input_tokens);self.assertIsNone(result.cost)

    def test_opencode_and_gemini_use_existing_partial_failure_metadata_contracts(self):
        for provider,frames,expected in ((opencode_result,opencode_frames,18),(gemini_result,gemini_frames,10)):
            for binary in (False,True):
                def launch(*args,**kwargs):
                    data=frames(kwargs['input_text']) if provider is gemini_result else frames()
                    output=stream(data)
                    raise subprocess.TimeoutExpired('PRIVATE',1,output=output.encode() if binary else output)
                with self.subTest(provider=provider.__name__,binary=binary),patch('tacto.authoring._run_provider',side_effect=launch):
                    result=provider('q')
                self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
                self.assertEqual(expected,result.total_input_tokens)
                self.assertEqual(4,result.output_tokens);self.assertFalse(result.cancellation_supported)
                self.assertNotIn('PRIVATE',json.dumps(result.to_dict()))
            for output in (b'\xff','not JSON',None,'['*1500):
                with self.subTest(provider=provider.__name__,bad=type(output).__name__),patch('tacto.authoring._run_provider',side_effect=subprocess.TimeoutExpired('PRIVATE',1,output=output)):
                    result=provider('q')
                self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
                self.assertIsNone(result.input_tokens)

    def test_owned_cli_process_timeout_preserves_opencode_completed_usage(self):
        from tacto.authoring import _run_provider
        script='import sys,time;sys.stdin.read();print('+repr(stream(opencode_frames()))+',flush=True);time.sleep(5)'
        def launch(*args,**kwargs):
            return _run_provider([sys.executable,'-c',script],.3,input_text='q')
        with patch('tacto.authoring._run_provider',side_effect=launch):result=opencode_result('q')
        self.assertEqual('timeout',result.status);self.assertIsNone(result.text)
        self.assertEqual(18,result.total_input_tokens)
        self.assertEqual('cli_reported_step_estimate',result.cost_basis)
        self.assertIsNone(result.cost_currency)
        self.assertFalse(result.cancellation_supported)
