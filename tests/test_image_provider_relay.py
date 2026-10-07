import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from nlc import ask_provider
from nlc.helper import InheritedHelperDriver
from nlc.provider_images import encode_image, decode_image
from nlc.provider_observation import capture_provider_calls
from nlc.provider_result import ProviderResult, ProviderError
from nlc.visual import VisualCapture, VisualError, query_visual

PNG=b'\x89PNG\r\n\x1a\nprivate own pixels'
ROOT=Path(__file__).resolve().parents[1]


def result(status='ok'):
    return ProviderResult('request','claude','claude','cli-json',status,
        '{"answer":"green"}' if status=='ok' else None,
        None if status=='ok' else ProviderError('missing_cli','provider unavailable'),.1,
        model='observed',input_tokens=10,output_tokens=2)


class ImageRelayTests(unittest.TestCase):
    def test_strict_png_base64_size_and_no_paths(self):
        self.assertEqual(PNG,decode_image(encode_image(PNG)))
        invalid=[{'media_type':'image/png','data':'notbase64'},
                 {'media_type':'image/jpeg','data':base64.b64encode(PNG).decode()},
                 {'media_type':'image/png','data':base64.b64encode(b'text').decode()},
                 {**encode_image(PNG),'path':'/private/file'},
                 {'media_type':'image/png','data':base64.b64encode(PNG).decode()+'\n'}]
        for envelope in invalid:
            with self.assertRaises(ValueError):decode_image(envelope)
        for png in (b'',b'text',PNG+b'x'*(5*1024*1024)):
            with self.assertRaises(ValueError):encode_image(png)

    def test_signed_request_uses_same_channel_and_preserves_observed_usage(self):
        d=object.__new__(InheritedHelperDriver)
        d._send=Mock(return_value={'type':'answer','text':result().text,'provider_result_v1':result().to_dict()})
        observed=d.ask_launcher_result('host question',True,provider='claude',timeout_seconds=.5,
            image=encode_image(PNG),model='requested')
        request=d._send.call_args.args[0]
        self.assertEqual(('ask','claude','requested',.5),(request['type'],request['provider'],request['model'],request['timeout_seconds']))
        self.assertEqual(PNG,decode_image(request['image']))
        self.assertEqual(('launcher','observed',10,2),(observed.transport,observed.model,observed.input_tokens,observed.output_tokens))
        d._send.assert_called_once()
        for provider in ('auto','opencode','/bin/tool'):
            with self.assertRaises(ValueError):d.ask_launcher_result('q',provider=provider,image=encode_image(PNG))
        with self.assertRaises(ValueError):d.ask_launcher_result('q',model='requested')
        self.assertEqual(1,d._send.call_count)

    def test_visual_image_goes_through_launcher_once_and_errors_are_counted(self):
        capture=VisualCapture('v','o','own','hash','state',{'width':10,'height':10},1,PNG)
        driver=Mock()
        for status in ('ok','error','timeout'):
            driver.ask_launcher_result.return_value=result(status)
            driver.ask_launcher_result.side_effect=TimeoutError('private failure') if status=='timeout' else None
            with patch.object(InheritedHelperDriver,'available',return_value=True), \
                 patch.object(InheritedHelperDriver,'__new__',return_value=driver), \
                 patch('nlc.claude_result.claude_result') as direct, capture_provider_calls() as calls:
                if status=='ok':
                    answer=query_visual(capture,'green?',provider='claude',model='requested')
                    self.assertEqual('green',answer['answer'])
                else:
                    with self.assertRaisesRegex(VisualError,'TimeoutError' if status=='timeout' else 'missing_cli'):
                        query_visual(capture,'green?',provider='claude')
            self.assertEqual(1,len(calls));self.assertEqual(status,calls[0]['status'])
            self.assertNotIn('private own pixels',json.dumps(calls));self.assertNotIn('text',calls[0])
            direct.assert_not_called()
        self.assertEqual(3,driver.ask_launcher_result.call_count)

    def test_provider_wrapper_decodes_image_and_pins_provider_without_text_fallback(self):
        body=json.dumps({'prompt':'own image question','image':encode_image(PNG),'model':'requested'})
        stdout=io.StringIO()
        with patch('sys.argv',['ask_provider','--image-envelope','--result-envelope','--provider','claude','--timeout','2']), \
             patch('sys.stdin',io.StringIO(body)),patch('sys.stdout',stdout), \
             patch('nlc.claude_result.claude_result',return_value=result()) as claude, \
             patch('nlc.authoring.ask_model_result') as text:
            self.assertEqual(0,ask_provider.main())
        claude.assert_called_once_with('own image question',model='requested',timeout=2,image_bytes=PNG)
        text.assert_not_called();self.assertEqual(10,json.loads(stdout.getvalue())['input_tokens'])

    def test_malformed_image_wrapper_cannot_invoke_provider(self):
        for provider,body in [('auto',{'prompt':'q','image':encode_image(PNG),'model':None}),
                              ('claude',{'prompt':'q','image':{'path':'private'},'model':None}),
                              ('claude',{'prompt':'q','image':encode_image(PNG),'model':'--tool'})]:
            with patch('sys.argv',['ask_provider','--image-envelope','--result-envelope','--provider',provider]), \
                 patch('sys.stdin',io.StringIO(json.dumps(body))),patch('sys.stdout',io.StringIO()), \
                 patch('sys.stderr',io.StringIO()),patch('nlc.claude_result.claude_result') as call:
                self.assertEqual(1,ask_provider.main());call.assert_not_called()

    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swift'),'Swift required')
    def test_actual_launcher_rejects_noncanonical_images_and_wrong_routes(self):
        source=(ROOT/'launcher/nlc.swift').read_text()
        schema=source[source.index('let allowedTypes:'):source.index('// MARK: - Socket plumbing')]
        requests=[{'type':'ask','prompt':'own question','provider':'claude','image':encode_image(PNG),'model':'requested'}]
        for provider in ('auto','opencode','/bin/tool'):
            requests.append({**requests[0],'provider':provider})
        for image in ({'media_type':'image/png','data':'!'}, {**encode_image(PNG),'path':'private'},
                      {'media_type':'image/jpeg','data':encode_image(PNG)['data']}):
            requests.append({**requests[0],'image':image})
        requests += [{**requests[0],'type':'observe','target':'own'},
                     {**requests[0],'model':'--tool'}, {**requests[0],'model':'bad\nmodel'},
                     {key:value for key,value in requests[0].items() if key!='image'}]
        payload=json.dumps(requests)
        harness='''
let requests=try! JSONSerialization.jsonObject(with:Data(#"PAYLOAD"#.utf8)) as! [[String:Any]]
print(String(data:try! JSONSerialization.data(withJSONObject:requests.map { vetted($0)==nil }),encoding:.utf8)!)
'''.replace('PAYLOAD',payload)
        with tempfile.TemporaryDirectory() as folder:
            script=Path(folder)/'main.swift'
            script.write_text('import Foundation\nimport CoreFoundation\nfunc modelTimeout(_ raw:Any?)->Double? { 1 }\n'+schema+harness)
            run=subprocess.run(['swift',str(script)],capture_output=True,text=True,timeout=30)
        self.assertEqual(0,run.returncode,run.stderr)
        self.assertEqual([True]+[False]*(len(requests)-1),json.loads(run.stdout))
