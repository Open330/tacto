"""Owned two-screen login, account check and post-login Flow receiving test."""
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs
from unittest.mock import patch
from test_credential_browser import AVAILABLE,SECRET
from tacto.credential_grants import CredentialStore
from tacto.credential_runtime import run_login
from tacto.flow import Flow,Step,Selector,Postcondition,replay
from tacto.contract import SafetyContract,Resource

class TwoScreens(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def response(self,html,cookie=None):
        self.send_response(200);self.send_header('Content-Type','text/html')
        if cookie:self.send_header('Set-Cookie',cookie+'; HttpOnly; SameSite=Strict')
        self.end_headers();self.wfile.write(html.encode())
    def dashboard(self,report=False):
        self.response('<h1>Signed in</h1><input aria-label="Account" readonly value="owned-user">'+('<h2>Report ready</h2>' if report else '<form method="post" action="/work"><button>Run report</button></form>'))
    def do_GET(self):
        self.response('<form method="post" action="/username"><input name="username" aria-label="Username"><button>Next</button></form>')
    def do_POST(self):
        data=parse_qs(self.rfile.read(int(self.headers.get('Content-Length',0))).decode());self.server.posts.append((self.path,data))
        if self.path=='/username' and data=={'username':['owned-user']}:
            self.response('<form method="post" action="/password"><input type="password" name="password" aria-label="Password"><button>Sign in</button></form>','owned_user=chosen')
        elif self.path=='/password' and data=={'password':[SECRET]} and 'owned_user=chosen' in self.headers.get('Cookie',''):self.dashboard()
        elif self.path=='/work':self.dashboard(True)
        else:self.response('<h1>Login failed</h1>')

@unittest.skipUnless(AVAILABLE,'optional Playwright required')
class MultiStageBrowser(unittest.TestCase):
    def test_flow_two_screens_then_work_reuse_and_wrong_account(self):
        from tacto.browser import BrowserDriver
        d=BrowserDriver();self.addCleanup(d.close)
        server=ThreadingHTTPServer(('127.0.0.1',0),TwoScreens);server.posts=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def stop():server.shutdown();server.server_close();thread.join()
        self.addCleanup(stop)
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        store=CredentialStore(Path(tmp.name)/'Tacto'/'Credentials');store.add_vault('local',{'kind':'keychain'})
        def field(name):return {'name':name.lower(),'selector':{'role':'textbox','title':name},'secret':{'vault':'local','item':name,'field':'password'}}
        plan={'target':'page','origin':f'http://127.0.0.1:{server.server_port}','stages':[
            {'fields':[field('Username')],'submit':{'role':'button','title':'Next'},'success':{'field':{'role':'textbox','title':'Password'},'attribute':'Title','equals':'Password'}},
            {'fields':[field('Password')],'submit':{'role':'button','title':'Sign in'},'success':{'field':{'role':'heading','title':'Signed in'},'attribute':'Title','equals':'Signed in'}}],
            'account':{'field':{'role':'textbox','title':'Account'},'attribute':'Value','secret':{'vault':'local','item':'Username','field':'password'}}}
        store.grant('owned',plan)
        reads=[]
        class Vault:
            def get(self,item,field):reads.append(item);return SECRET if item=='Password' else 'owned-user'
        factory=lambda config:Vault()
        d.goto(plan['origin'])
        f=Flow('signed-in-report','page',steps=[Step('authenticate',params={'grant':'owned'}),
            Step('click',selector=Selector(role='button',title='Run report'),postcondition=Postcondition(field=Selector(role='heading',title='Report ready'),attribute='Title',equals='Report ready'),safety=SafetyContract(effects=['write_ephemeral'],reversibility='reversible',resource=Resource(scope='entry',object_type='owned report button',object_identity='report')))])
        original=run_login
        with patch('tacto.credential_runtime.run_login',side_effect=lambda *a,**kw:original(*a,**kw,vault_factory=factory)):
            result=replay(d,f,credential_store=store)
        self.assertTrue(result.ok,result.log)
        self.assertEqual(['/username','/password','/work'],[p for p,_ in server.posts])
        self.assertNotIn(SECRET,json.dumps(result.log));self.assertNotIn(SECRET,f.to_json())
        reused=run_login(store,'owned',d,vault_factory=factory)
        self.assertTrue(reused['ok'],reused);self.assertTrue(reused['account_verified']);self.assertEqual(3,len(server.posts))
        d._page.locator('[aria-label=Account]').evaluate("el=>el.value='other-user'")
        wrong=run_login(store,'owned',d,vault_factory=factory)
        self.assertEqual('credential_account_mismatch',wrong['error']);self.assertEqual(3,len(server.posts))
        self.assertNotIn(SECRET,json.dumps(wrong))
