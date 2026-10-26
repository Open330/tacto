import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tacto.credential_grants import CredentialStore,normalize_plan,origin
from tacto.credential_runtime import run_login,CredentialAuthorization
from tacto.credential_vault import CredentialError,BitwardenVault,private_read

SECRET='owned-credential-fixture-value'

def plan(level='login'):
    p={'target':'com.apple.Safari','origin':'https://login.example.test','level':level,'minutes':60,
       'fields':[{'name':'password','selector':{'role':'TextField','identifier':'password'},
       'secret':{'vault':'local','item':'owned-login','field':'password'}}]}
    if level=='login':p.update(submit={'role':'Button','title':'Sign in'},success={'field':{'role':'StaticText','title':'Signed in'},'attribute':'Title','equals':'Signed in'})
    return p

class Driver:
    capabilities={'action.credential_input'}
    def __init__(self):self.filled=[];self.submits=0;self.signed=False;self.origin_ok=True;self.fail_fill=False;self.fail_submit=False
    def observe(self,target,mode):
        return {'type':'observation','obs_id':'own','tree':'0 Window\n  1 TextField  Identifier: password\n  2 Button  Title: Sign in\n'+('  3 StaticText  Title: Signed in\n' if self.signed else '')}
    def credential_input(self,obs,target,index,value,*,expected_origin=None,check_only=False):
        if not self.origin_ok:return {'type':'error','error':'credential_origin_mismatch','effect':'not_started'}
        if check_only:return {'type':'credential_ready'}
        if self.fail_fill:return {'type':'error','detail':SECRET,'effect':'not_started'}
        self.filled.append(value);return {'type':'acted'}
    def credential_submit(self,*a,**kw):
        self.submits+=1
        if self.fail_submit:return {'type':'error','detail':SECRET,'effect':'not_started'}
        self.signed=True;return {'type':'acted'}

class Vault:
    def __init__(self):self.reads=[]
    def get(self,item,field):self.reads.append((item,field));return SECRET

class Delegation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CredentialStore(Path(self.tmp.name)/'Tacto'/'Credentials');self.store.add_vault('local',{'kind':'keychain'})
        self.store.grant('owned',plan());self.driver=Driver();self.vault=Vault()
    def run_login(self,**kw):return run_login(self.store,'owned',self.driver,vault_factory=lambda c:self.vault,**kw)
    def test_login_uses_vault_and_completes_without_handoff_or_prompt(self):
        result=self.run_login();self.assertTrue(result['ok'],result);self.assertEqual('login_verified',result['status'])
        self.assertEqual([SECRET],self.driver.filled);self.assertEqual(1,self.driver.submits)
        self.assertNotIn(SECRET,json.dumps(result));self.assertNotIn(SECRET,''.join(p.read_text() for p in self.store.root.glob('*.json')))
        self.assertEqual(0,result['model_calls'])
    def test_fill_only_does_not_submit(self):
        self.store.grant('owned',plan('fill'));r=self.run_login(fill_only=True);self.assertTrue(r['ok']);self.assertEqual(0,self.driver.submits)
    def test_fill_level_cannot_be_upgraded_to_login_by_caller(self):
        self.store.grant('owned',plan('fill'));r=self.run_login();self.assertEqual('credential_level_requires_login',r['error']);self.assertFalse(self.driver.filled)
    def test_wrong_live_origin_reads_no_secret_and_sends_no_input(self):
        self.driver.origin_ok=False;r=self.run_login();self.assertEqual('credential_scope_mismatch',r['error']);self.assertFalse(self.vault.reads);self.assertFalse(self.driver.filled)
    def test_expired_grant_starts_no_input(self):
        self.store.grant('owned',plan(),now=1);r=self.run_login();self.assertEqual('credential_grant_expired',r['error']);self.assertFalse(self.driver.filled)
    def test_delegation_is_reusable_until_revoked_without_arbitrary_default_expiry(self):
        p=plan();p.pop('minutes');self.store.grant('owned',p,now=1)
        loaded,_=self.store.load('owned',now=10**10)
        self.assertEqual(0,loaded['minutes'])
    def test_revoked_grant_starts_no_input(self):
        self.store.revoke('owned');r=self.run_login();self.assertFalse(r['ok']);self.assertFalse(self.driver.filled)
    def test_revocation_during_vault_fetch_prevents_dispatch(self):
        def get(*args):self.store.revoke('owned');return SECRET
        self.vault.get=get;r=self.run_login();self.assertFalse(r['ok']);self.assertFalse(self.driver.filled)
    def test_replacement_during_vault_fetch_prevents_dispatch(self):
        def get(*args):
            changed=plan();changed['origin']='https://other.example.test'
            self.store.grant('owned',changed);return SECRET
        self.vault.get=get;r=self.run_login();self.assertEqual('credential_scope_mismatch',r['error']);self.assertFalse(self.driver.filled)
    def test_vault_change_invalidates_old_delegation(self):
        self.store.add_vault('local',{'kind':'bitwarden','server':'https://vault.example.test'});r=self.run_login();self.assertEqual('credential_vault_changed',r['error'])
    def test_private_files_reject_world_readable_or_symlink(self):
        f=self.store.root/'grant-owned.json';f.chmod(0o644)
        with self.assertRaises(CredentialError):private_read(f)
        f.chmod(0o600);link=self.store.root/'link';link.symlink_to(f)
        with self.assertRaises(CredentialError):private_read(link)
    def test_only_refs_are_accepted_not_plain_passwords(self):
        p=plan();p['fields'][0]['secret']['value']=SECRET
        with self.assertRaises(CredentialError):normalize_plan(p)
    def test_no_blanket_allow_from_untrusted_json(self):
        from tacto.contract import policy,SafetyContract,Resource,Destination
        c=SafetyContract(effects=['credential'],data_classes=['credential'],reversibility='reversible',resource=Resource(object_identity='password',scope='entry'),destination=Destination(kind='same_origin',identity='https://login.example.test'))
        self.assertEqual('handoff',policy(c,credential_authority={'level':'login'}).risk)
        self.assertEqual('preapproved',policy(c,credential_authority=CredentialAuthorization(plan(),'password')).risk)
        c.effects.append('financial');self.assertNotEqual('preapproved',policy(c,credential_authority=CredentialAuthorization(plan(),'password')).risk)
    def test_backend_exceptions_and_responses_never_echo_secret(self):
        self.driver.fail_fill=True;r=self.run_login();self.assertNotIn(SECRET,json.dumps(r));self.assertEqual(0,self.driver.submits)
        def broken(*a):raise RuntimeError(SECRET)
        self.vault.get=broken;r=self.run_login();self.assertNotIn(SECRET,json.dumps(r))
        self.vault.get=lambda *a: (_ for _ in ()).throw(CredentialError(SECRET));r=self.run_login();self.assertNotIn(SECRET,json.dumps(r))
    def test_failed_submit_is_never_retried(self):
        self.driver.fail_submit=True;r=self.run_login();self.assertFalse(r['ok']);self.assertEqual(1,self.driver.submits);self.assertFalse(r['submitted']);self.assertNotIn(SECRET,json.dumps(r))
    def test_already_signed_in_still_checks_scope(self):
        self.driver.signed=True;self.driver.origin_ok=False;r=self.run_login();self.assertFalse(r['ok']);self.assertFalse(self.vault.reads)
    def test_trace_transport_cannot_receive_credentials(self):
        from tacto.trace import TracingDriver
        d=TracingDriver(self.driver);r=run_login(self.store,'owned',d,vault_factory=lambda c:self.vault)
        self.assertEqual('credential_driver_upgrade_required',r['error']);self.assertFalse(self.vault.reads)
    def test_config_creation_requires_explicit_user_delegation(self):
        from tacto.credential_cli import main
        f=Path(self.tmp.name)/'plan.json';f.write_text(json.dumps(plan()))
        with contextlib.redirect_stdout(io.StringIO()):code=main(['grant','new',str(f)],driver_factory=lambda a:self.driver,store=self.store,confirm=lambda v:False)
        self.assertEqual(1,code);self.assertNotIn('new',self.store.grants())
    def test_invalid_cli_arguments_are_not_echoed(self):
        from tacto.credential_cli import main
        out=io.StringIO();err=io.StringIO()
        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):code=main(['vault','local','--keychain',SECRET],driver_factory=None,store=self.store)
        self.assertNotEqual(0,code);self.assertNotIn(SECRET,out.getvalue()+err.getvalue())
    def test_list_exposes_matchable_scope_without_vault_item_or_values(self):
        from tacto.credential_cli import main
        out=io.StringIO()
        with contextlib.redirect_stdout(out):code=main(['list'],driver_factory=None,store=self.store)
        self.assertEqual(0,code);result=json.loads(out.getvalue())
        self.assertEqual('login',result['scopes']['owned']['level'])
        self.assertEqual('https://login.example.test',result['scopes']['owned']['origin'])
        self.assertNotIn('owned-login',out.getvalue());self.assertNotIn(SECRET,out.getvalue())

class ExternalVault(unittest.TestCase):
    def fake(self,status='unlocked',server='https://vault.example.test'):
        calls=[]
        def run(argv,**kw):
            calls.append((argv,kw))
            value={'status':status,'serverUrl':server} if argv[1]=='status' else {'id':'owned-id','login':{'username':'owned-user','password':SECRET},'fields':[]}
            return type('Result',(),{'returncode':0,'stdout':json.dumps(value)})()
        return run,calls
    def test_authenticated_exact_server_and_item_field_only(self):
        run,calls=self.fake();v=BitwardenVault({'server':'https://vault.example.test'},run=run)
        self.assertEqual(SECRET,v.get('owned-id','login.password'));self.assertEqual('owned-user',v.get('owned-id','login.username'))
        self.assertEqual(2,len(calls));self.assertNotIn(SECRET,str([a for a,k in calls]))
    def test_locked_and_wrong_server_never_get_an_item(self):
        for status,server in [('locked','https://vault.example.test'),('unlocked','https://evil.example.test')]:
            run,calls=self.fake(status,server);v=BitwardenVault({'server':'https://vault.example.test'},run=run)
            with self.assertRaises(CredentialError):v.get('owned-id','login.password')
            self.assertEqual(1,len(calls))
    def test_session_is_child_environment_not_process_arguments(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/'session';f.write_text('owned-session-token');f.chmod(0o600)
            run,calls=self.fake();v=BitwardenVault({'server':'https://vault.example.test','session_file':str(f)},run=run);v.get('owned-id','login.password')
            self.assertEqual('owned-session-token',calls[0][1]['env']['BW_SESSION']);self.assertNotIn('owned-session-token',str(calls[0][0]))
    def test_registered_npm_cli_finds_its_node_under_fixed_launcher_path(self):
        run,calls=self.fake();v=BitwardenVault({'server':'https://vault.example.test','executable':'/owned/node/bin/bw'},run=run)
        with patch.dict(os.environ,{'PATH':'/usr/bin:/bin'}):v.get('owned-id','login.password')
        self.assertEqual('/owned/node/bin/bw',calls[0][0][0])
        self.assertTrue(calls[0][1]['env']['PATH'].startswith('/owned/node/bin'+os.pathsep))
    def test_origin_canonicalization(self):
        self.assertEqual('https://example.test',origin('https://EXAMPLE.test:443/'))
        for v in ('https://example.test@evil.test','http://example.test','https://example.test/path','https://example.test/#fragment','javascript:alert(1)'):
            with self.assertRaises(CredentialError):origin(v)
