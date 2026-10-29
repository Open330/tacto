"""Owned controlled form: validate receiving events, replacement and clipboard bytes."""
import json,os,subprocess,sys,tempfile,threading,time,unittest
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'launcher/tacto'
@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_CREDENTIAL_TESTS')=='1','opt-in native controlled credential paste')
class CredentialPasteNative(unittest.TestCase):
 def test_paste_updates_controlled_state_and_restores_all_clipboard_formats(self):
  from AppKit import NSPasteboard
  def clipboard():
   return [[(str(t),bytes(item.dataForType_(t)) if item.dataForType_(t) is not None else None) for t in item.types()] for item in NSPasteboard.generalPasteboard().pasteboardItems() or []]
  class Page(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):
    if self.path.startswith('/proof?'):
     from urllib.parse import urlparse,parse_qs
     self.server.proof=parse_qs(urlparse(self.path).query);self.send_response(204);self.end_headers();return
    self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
    self.wfile.write(b'''<title>Tacto owned controlled password</title><input type="password" aria-label="Owned password" id="p"><script>let state='',inputs=0;p.addEventListener('input',()=>{state=p.value;inputs++});setInterval(()=>fetch('/proof?dom='+String(p.value==='owned-Synthetic-47!')+'&state='+String(state==='owned-Synthetic-47!')+'&inputs='+inputs),100)</script>''')
  server=ThreadingHTTPServer(('127.0.0.1',0),Page);server.proof={};thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  def command(*args):return subprocess.run([str(LAUNCHER),*args],capture_output=True,text=True,timeout=40,env=dict(os.environ,TACTO_HELPER_MODE='warm'))
  try:
   opened=command('open-url',f'http://127.0.0.1:{server.server_port}','--browser','com.apple.Safari','--helper')
   self.assertEqual(0,opened.returncode,opened.stdout+opened.stderr)
   before=clipboard()
   with tempfile.TemporaryDirectory() as d:
    f=Path(d)/'flow.json';trace=Path(d)/'trace.json'
    step={'action':'set_value','selector':{'role':'TextField','title':'Owned password','ancestor':'Tacto owned controlled password'},'params':{'value':'owned-Synthetic-47!','input_method':'paste'},'safety':{'effects':['credential'],'reversibility':'reversible','resource':{'scope':'entry'}},'unverified_ok':True}
    f.write_text(json.dumps({'name':'owned native credential paste','target':'com.apple.Safari','steps':[step,step]}))
    r=command('run',str(f),'--helper','--yes','--trace',str(trace))
    self.assertEqual(0,r.returncode,r.stdout+r.stderr)
    deadline=time.monotonic()+3
    while server.proof.get('inputs')!=['2']:
     self.assertLess(time.monotonic(),deadline);time.sleep(.05)
    self.assertEqual(['true'],server.proof.get('dom'));self.assertEqual(['true'],server.proof.get('state'))
    self.assertTrue(before==clipboard(),'Clipboard item/type bytes were not preserved')
    self.assertNotIn('owned-Synthetic-47!',r.stdout+r.stderr+trace.read_text())
    # The same route must work without exposing a Vault value to the agent.
    from tacto.credential_grants import CredentialStore
    from tacto.credential_vault import KeychainVault
    import uuid
    name='owned-paste-'+uuid.uuid4().hex;store=CredentialStore();vault=KeychainVault()
    try:
     vault.put(name,'owned-Synthetic-47!');store.add_vault(name,{'kind':'keychain'})
     store.grant(name,{'target':'com.apple.Safari','origin':f'http://127.0.0.1:{server.server_port}','level':'fill','fields':[{'name':'password','selector':{'role':'TextField','title':'Owned password','ancestor':'Tacto owned controlled password'},'secret':{'vault':name,'item':name,'field':'password'},'input_method':'paste'}]})
     filled=command('auth','fill',name,'--helper')
     self.assertEqual(0,filled.returncode,filled.stdout+filled.stderr)
     self.assertNotIn('owned-Synthetic-47!',filled.stdout+filled.stderr)
     deadline=time.monotonic()+3
     while server.proof.get('inputs')!=['3']:
      self.assertLess(time.monotonic(),deadline);time.sleep(.05)
     self.assertEqual(['true'],server.proof.get('state'));self.assertTrue(before==clipboard(),'Vault paste changed clipboard bytes')
    finally:
     store.revoke(name);store._path('vault',name).unlink(missing_ok=True);vault.delete(name)
    print('Native paste: two replacement inputs received by controlled form; clipboard formats restored; no secret in output/trace',flush=True)
  finally:server.shutdown();server.server_close();thread.join()
