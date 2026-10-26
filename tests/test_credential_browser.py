"""An owned HTTP login server verifies real DOM input, POST and session state."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import parse_qs
from tacto.credential_grants import CredentialStore
from tacto.credential_runtime import run_login

SECRET='owned-browser-fixture-password'
try:
    import playwright.sync_api
    AVAILABLE=True
except ImportError:
    AVAILABLE=False

class Page(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
        self.wfile.write(b'''<form method="post"><input aria-label="Username" name="username" autocomplete="username"><input aria-label="Password" name="password" type="password" autocomplete="current-password"><button>Sign in</button></form>''')
    def do_POST(self):
        data=parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
        self.server.received.append(data)
        ok=data=={'username':['owned-user'],'password':[SECRET]}
        self.send_response(200 if ok else 403)
        self.send_header('Content-Type','text/html')
        if ok:self.send_header('Set-Cookie','owned_session=authenticated; HttpOnly; SameSite=Strict')
        self.end_headers();self.wfile.write(b'<h1>Signed in</h1>' if ok else b'<h1>Login failed</h1>')

@unittest.skipUnless(AVAILABLE,'optional Playwright dependency not installed')
class BrowserLogin(unittest.TestCase):
    def setUp(self):
        from tacto.browser import BrowserDriver
        self.driver=BrowserDriver();self.addCleanup(self.driver.close)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Page);self.server.received=[]
        thread=threading.Thread(target=self.server.serve_forever,daemon=True);thread.start()
        def cleanup():self.server.shutdown();self.server.server_close();thread.join()
        self.addCleanup(cleanup)
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CredentialStore(Path(self.tmp.name)/'Tacto'/'Credentials');self.store.add_vault('local',{'kind':'keychain'})
        self.plan={'target':'page','origin':self.url,'level':'login','fields':[
            {'name':'username','selector':{'role':'textbox','title':'Username'},'secret':{'vault':'local','item':'owned-user','field':'password'}},
            {'name':'password','selector':{'role':'textbox','title':'Password'},'secret':{'vault':'local','item':'owned-password','field':'password'}}],
            'submit':{'role':'button','title':'Sign in'},'success':{'field':{'role':'heading','title':'Signed in'},'attribute':'Title','equals':'Signed in'}}
        self.store.grant('owned',self.plan)
        self.reads=[]
        owner=self
        class Vault:
            def get(self,item,field):
                owner.reads.append(item);return 'owned-user' if item=='owned-user' else SECRET
        self.factory=lambda config:Vault()
    def test_real_input_post_cookie_and_reuse_without_another_secret_read(self):
        self.driver.goto(self.url)
        result=run_login(self.store,'owned',self.driver,vault_factory=self.factory)
        self.assertEqual('login_verified',result.get('status'),result)
        self.assertEqual(1,len(self.server.received));self.assertEqual(2,len(self.reads))
        self.assertTrue(any(c['name']=='owned_session' for c in self.driver._page.context.cookies()))
        self.assertNotIn(SECRET,json.dumps(result));self.assertEqual(0,result['model_calls'])
        again=run_login(self.store,'owned',self.driver,vault_factory=self.factory)
        self.assertEqual('already_authenticated',again.get('status'),again)
        self.assertEqual(1,len(self.server.received));self.assertEqual(2,len(self.reads))
        self.assertNotIn(SECRET,''.join(p.read_text() for p in self.store.root.glob('*.json')))
    def test_wrong_origin_before_fetch_and_navigation_after_fetch_never_fill(self):
        self.driver.goto(self.url)
        changed=dict(self.plan,origin='https://unrelated.example.test');self.store.grant('owned',changed)
        r=run_login(self.store,'owned',self.driver,vault_factory=self.factory)
        self.assertFalse(r['ok']);self.assertEqual([],self.reads)
        self.store.grant('owned',self.plan)
        driver=self.driver
        class NavigatingVault:
            def get(self,item,field):driver.goto('data:text/html,<input aria-label="Username">');return SECRET
        r=run_login(self.store,'owned',driver,vault_factory=lambda c:NavigatingVault())
        self.assertFalse(r['ok']);self.assertEqual([],self.server.received)
        self.assertEqual('',driver._page.locator('input').input_value())
