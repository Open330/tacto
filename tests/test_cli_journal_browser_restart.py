"""Real Chromium click -> owned HTTP file write -> CLI worker restart.

The CLI's driver factory is explicitly injected with its optional BrowserDriver;
this does not establish a native helper or a production web CLI backend.
"""
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import playwright.sync_api
    HAS_BROWSER = True
except ImportError:
    HAS_BROWSER = False


@unittest.skipUnless(HAS_BROWSER, 'optional playwright dependency not installed')
class CLIJournalBrowserRestartTests(unittest.TestCase):
    def test_lost_cli_worker_after_received_file_write_cannot_click_save_again(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            received = []
            artifact = root / 'owned.txt'
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args): pass
                def do_GET(self):
                    if self.path == '/inbox':
                        body = json.dumps({'writes':len(received)}).encode()
                        self.send_response(200); self.end_headers(); self.wfile.write(body)
                        return
                    body = b'''<button onclick="fetch('/save',{method:'POST',body:'owned body'}).then(r=>{
                      if(r.ok)document.querySelector('output').textContent='Saved';})">Save</button><output>Draft</output>'''
                    self.send_response(200); self.send_header('Content-Type','text/html')
                    self.end_headers(); self.wfile.write(body)
                def do_POST(self):
                    body = self.rfile.read(int(self.headers['Content-Length']))
                    artifact.write_bytes(body)
                    received.append(body)
                    self.send_response(200); self.end_headers()
            server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
            try:
                origin = f'http://127.0.0.1:{server.server_port}'
                script = r'''
import contextlib,json,os,sys
from pathlib import Path
from urllib.request import urlopen
from unittest.mock import patch
from tacto import cli
from tacto.browser import BrowserDriver
from tacto.contract import ContextBinding,Destination,Resource,SafetyContract
from tacto.flow import Flow,Selector,Step
root=Path(sys.argv[1]);origin=sys.argv[2]
class CrashAfterReceivedWrite(BrowserDriver):
    def act(self,*args,**kwargs):
        reply=super().act(*args,**kwargs)
        if reply.get('type')=='acted' and args[2]['name']=='click':
            self._page.locator('output').filter(has_text='Saved').wait_for(timeout=3000)
            with urlopen(origin+'/inbox',timeout=2) as response:
                assert json.load(response)['writes']==1
            self.close()
            os._exit(17) # Before DurableDispatchDriver can append reply_received.
        return reply
flow=Flow('owned_http_save','page',[Step('click',Selector(role='Button',title='Save'),
    intent_text='save owned body to owned.txt on this local origin',
    safety=SafetyContract(effects=['write_persistent'],resource=Resource(scope='item',
        object_type='owned local text file',object_identity=origin+'/owned.txt'),
        reversibility='recoverable',destination=Destination(kind='same_origin',identity=origin)),
    binding=ContextBinding(bundle_id=origin,role='Button',title='Save',operation='click'))])
recipe=root/'flow.json';recipe.write_text(flow.to_json())
driver=CrashAfterReceivedWrite();driver.goto(origin)
try:
    with patch('tacto.driver.Driver',return_value=driver), \
         patch.object(cli,'_indicator_for',side_effect=lambda *args:contextlib.nullcontext()), \
         patch('tacto.session.sandboxed',return_value=False), \
         patch('tacto.session.KeepAwake',side_effect=contextlib.nullcontext):
        result=cli.main(['run',str(recipe),'--yes','--journal-dir',str(root/'journal')])
    assert result==1,result
    assert cli._execution_journal.get() is None
finally:driver.close()
'''
                command = [sys.executable,'-c',script,str(root),origin]
                def run_worker():
                    worker = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                              text=True,start_new_session=True)
                    try:
                        stdout,stderr = worker.communicate(timeout=20)
                    except BaseException:
                        try:os.killpg(worker.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        worker.wait(timeout=3)
                        raise
                    return subprocess.CompletedProcess(command,worker.returncode,stdout,stderr)
                first = run_worker()
                self.assertEqual(17,first.returncode,first.stdout+first.stderr)
                self.assertEqual([b'owned body'],received)
                second = run_worker()
                self.assertEqual(0,second.returncode,second.stdout+second.stderr)
                self.assertIn('dispatch_journal_requires_reconciliation',second.stdout)
                self.assertEqual([b'owned body'],received)
                self.assertEqual(b'owned body',artifact.read_bytes())
                lines=(root/'journal/00000.jsonl').read_text().splitlines()
                self.assertEqual(1,len(lines))
                self.assertEqual('reserved',json.loads(lines[0])['state'])
            finally:
                server.shutdown();server.server_close();thread.join(timeout=2)
