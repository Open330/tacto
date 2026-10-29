"""Opt-in actual secure-field receiving through the signed launcher/helper."""
import json,os,plistlib,subprocess,sys,tempfile,time,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LAUNCHER=ROOT/'launcher/tacto'
@unittest.skipUnless(sys.platform=='darwin' and os.environ.get('TACTO_RUN_NATIVE_CREDENTIAL_TESTS')=='1','opt-in signed secure-field receiving test')
class ApprovedCredentialsNative(unittest.TestCase):
    def test_approved_password_and_confirmation_reach_secure_fields(self):
        with tempfile.TemporaryDirectory(prefix='tacto-credentials-') as directory:
            base=Path(directory);app=base/'OwnedCredentials.app';binary=app/'Contents/MacOS/OwnedCredentials';binary.parent.mkdir(parents=True)
            subprocess.run(['swiftc',str(ROOT/'tests/fixtures/NativeCredentialFixture.swift'),'-o',str(binary)],check=True,capture_output=True)
            target='ai.callabo.tacto.credentialfixture'
            (app/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':target,'CFBundleExecutable':'OwnedCredentials','CFBundleName':'Tacto Credential Fixture','CFBundlePackageType':'APPL','LSUIElement':True}))
            subprocess.run(['codesign','--force','--sign','-',str(app)],check=True,capture_output=True)
            state=base/'received.json';process=subprocess.Popen([str(binary),str(state)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+10
                while not state.exists():
                    self.assertLess(time.monotonic(),deadline);time.sleep(.05)
                secret='owned-Synthetic-47!'
                safety={'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'owned input','scope':'entry'}}
                steps=[{'action':'set_value','selector':{'identifier':field},'params':{'value':secret},'safety':safety,'unverified_ok':True} for field in ('password','confirm-password')]
                steps.append({'action':'click','selector':{'identifier':'check-fields'},'safety':safety,'unverified_ok':True})
                flow=base/'flow.json';flow.write_text(json.dumps({'name':'owned credential input','target':target,'steps':steps}))
                env=dict(os.environ,TACTO_HELPER_MODE='warm')
                out=subprocess.run([str(LAUNCHER),'run',str(flow),'--helper','--yes','--trace',str(base/'trace.json')],env=env,capture_output=True,text=True,timeout=30)
                self.assertEqual(0,out.returncode,out.stdout+out.stderr)
                deadline=time.monotonic()+3
                while json.loads(state.read_text())['checks']!=1:
                    self.assertLess(time.monotonic(),deadline);time.sleep(.05)
                received=json.loads(state.read_text())
                self.assertEqual({'password_matches':True,'confirmation_matches':True,'checks':1},received)
                self.assertNotIn(secret,out.stdout+out.stderr+(base/'trace.json').read_text())
                print('Actual NSSecureTextField password + confirmation received; one check; no credential output/trace leakage',flush=True)
            finally:
                process.terminate();process.wait(timeout=5)

    def test_safari_password_values_persist_after_focus_changes(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        class Page(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path.startswith('/proof'):
                    self.server.matched=True;self.send_response(204);self.end_headers();return
                self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers()
                self.wfile.write(b'''<title>Tacto owned approval credential fixture</title><input type="password" aria-label="Owned password" id="p"><input type="password" aria-label="Owned confirmation" id="c"><input aria-label="Owned ordinary field"><script>setInterval(()=>{if(p.value==='owned-Synthetic-47!'&&c.value==='owned-Synthetic-47!')fetch('/proof?both=matched')},100)</script>''')
        server=ThreadingHTTPServer(('127.0.0.1',0),Page);server.matched=False
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def command(*args):
            return subprocess.run([str(LAUNCHER),*args],capture_output=True,text=True,
                env=dict(os.environ,TACTO_HELPER_MODE='warm'),timeout=40)
        try:
            opened=command('open-url',f'http://127.0.0.1:{server.server_port}','--browser','com.apple.Safari','--helper')
            self.assertEqual(0,opened.returncode,opened.stdout+opened.stderr)
            safety={'effects':['write_ephemeral'],'reversibility':'reversible','resource':{'object_type':'owned input','scope':'entry'}}
            steps=[{'action':'set_value','selector':{'role':'TextField','title':label,'ancestor':'Tacto owned approval credential fixture'},'params':{'value':value},'safety':safety,'unverified_ok':True}
                for label,value in [('Owned password','owned-Synthetic-47!'),('Owned confirmation','owned-Synthetic-47!'),('Owned ordinary field','blur-check')]]
            with tempfile.TemporaryDirectory() as directory:
                base=Path(directory);flow=base/'flow.json';flow.write_text(json.dumps({'name':'owned-approved-passwords','target':'com.apple.Safari','steps':steps}))
                out=command('run',str(flow),'--helper','--yes','--trace',str(base/'trace.json'))
                self.assertEqual(0,out.returncode,out.stdout+out.stderr)
                end=time.monotonic()+3
                while not server.matched:
                    self.assertLess(time.monotonic(),end);time.sleep(.05)
                self.assertNotIn('owned-Synthetic-47!',out.stdout+out.stderr+(base/'trace.json').read_text())
                print('Safari page verified password + confirmation persist after blur; no Vault setup; no secret in trace',flush=True)
        finally:
            server.shutdown();server.server_close();thread.join()
