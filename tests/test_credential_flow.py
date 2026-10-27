import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tacto.flow import Flow,Step,Selector,Postcondition,replay
from tacto.contract import SafetyContract
from tacto.credential_runtime import run_login
from tacto.credential_grants import CredentialStore
from test_credential_delegation import Driver,Vault,plan,SECRET

class FlowDriver(Driver):
    def __init__(self):super().__init__();self.reads=0
    def observe(self,target,mode,**kwargs):return super().observe(target,mode)
    def act(self,*args,**kwargs):self.reads+=1;return {'type':'acted'}

class CredentialFlow(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=CredentialStore(Path(self.tmp.name)/'Tacto'/'Credentials');self.store.add_vault('local',{'kind':'keychain'});self.store.grant('owned',plan())
        self.driver=FlowDriver();self.vault=Vault()
        original=run_login
        self.patch=patch('tacto.credential_runtime.run_login',side_effect=lambda *a,**kw:original(*a,**kw,vault_factory=lambda c:self.vault));self.patch.start();self.addCleanup(self.patch.stop)
    def flow(self):
        return Flow('with-login','com.apple.Safari',steps=[Step('authenticate',params={'grant':'owned'}),Step('read',selector=Selector(role='StaticText',title='Signed in'),safety=SafetyContract(effects=['read'],reversibility='reversible'))])
    def test_flow_logs_in_then_continues_and_reuses_authenticated_state(self):
        flow=self.flow();r=replay(self.driver,flow,credential_store=self.store)
        self.assertTrue(r.ok,r.log);self.assertEqual(1,self.driver.submits);self.assertEqual(1,self.driver.reads)
        r=replay(self.driver,flow,credential_store=self.store)
        self.assertTrue(r.ok,r.log);self.assertEqual(1,self.driver.submits)
        self.assertNotIn(SECRET,json.dumps(r.log));self.assertNotIn(SECRET,flow.to_json())
    def test_invalid_later_grant_is_found_before_prefix_input(self):
        f=self.flow();f.steps.reverse();f.steps[-1].params['grant']='absent'
        r=replay(self.driver,f,credential_store=self.store)
        self.assertFalse(r.ok);self.assertEqual(0,self.driver.reads);self.assertFalse(self.vault.reads)
    def test_wrong_app_and_fill_only_grants_cannot_run(self):
        for change in ({'target':'com.apple.TextEdit'}, {'level':'fill'}):
            p=plan('fill') if change.get('level')=='fill' else plan();p.update(change);self.store.grant('owned',p)
            r=replay(self.driver,self.flow(),credential_store=self.store)
            self.assertFalse(r.ok);self.assertFalse(self.vault.reads)
    def test_parser_avoids_inheriting_plaintext_safety_for_named_auth(self):
        f=Flow.from_json(json.dumps({'name':'a','target':'com.apple.Safari','safety_defaults':{'effects':['read'],'reversibility':'reversible'},'steps':[{'action':'authenticate','params':{'grant':'owned'}}]}))
        self.assertIsNone(f.steps[0].safety)
        self.assertTrue(replay(self.driver,f,credential_store=self.store).ok)
    def test_missing_field_diagnostic_is_bounded_and_has_no_secret(self):
        p=plan();p['fields'][0]['selector']['identifier']='missing';self.store.grant('owned',p)
        r=run_login(self.store,'owned',self.driver,vault_factory=lambda c:self.vault)
        self.assertEqual('password',r['repair']['field']);self.assertFalse(r['repair']['submit_repeated']);self.assertFalse(self.vault.reads)
    def test_journal_reserves_credential_submit_without_secret_values(self):
        from tacto.dispatch_journal import DurableDispatchDriver
        path=Path(self.tmp.name)/'journal'
        with DurableDispatchDriver(self.driver,path) as d:
            r=run_login(self.store,'owned',d,vault_factory=lambda c:self.vault);self.assertTrue(r['ok'],r)
        self.driver.signed=False
        with DurableDispatchDriver(self.driver,path) as d:
            r=run_login(self.store,'owned',d,vault_factory=lambda c:self.vault);self.assertFalse(r['ok'])
        self.assertEqual(1,self.driver.submits)
        self.assertEqual([SECRET],self.driver.filled)
        self.assertNotIn(SECRET,''.join(p.read_text() for p in path.glob('*.jsonl')))
    def test_account_check_prevents_reusing_a_different_account(self):
        p=plan();p['account']={'field':{'role':'StaticText','title':'Signed in'},'attribute':'Title','secret':{'vault':'local','item':'account-name','field':'password'}};self.store.grant('owned',p)
        self.driver.signed=True
        r=run_login(self.store,'owned',self.driver,vault_factory=lambda c:self.vault)
        self.assertEqual('credential_account_mismatch',r['error']);self.assertEqual(0,self.driver.submits);self.assertNotIn(SECRET,json.dumps(r))
    def test_review_digest_rejects_file_change_before_grant_save(self):
        import io,contextlib
        from tacto.credential_cli import main
        path=Path(self.tmp.name)/'plan.json';path.write_text(json.dumps(plan()))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):code=main(['inspect',str(path)],driver_factory=None,store=self.store)
        self.assertEqual(0,code);summary=json.loads(output.getvalue());self.assertNotIn('owned-login',output.getvalue())
        changed=plan();changed['origin']='https://changed.example.test';path.write_text(json.dumps(changed))
        output=io.StringIO()
        with contextlib.redirect_stdout(output):code=main(['grant','reviewed',str(path),'--yes','--review-digest',summary['review_digest']],driver_factory=None,store=self.store)
        self.assertEqual(1,code);self.assertEqual('credential_plan_changed',json.loads(output.getvalue())['error'])
        self.assertNotIn('reviewed',self.store.grants())
    def test_fill_downgrade_and_explicit_provider_metadata_listing(self):
        import io,contextlib
        from tacto.credential_cli import main
        path=Path(self.tmp.name)/'plan.json';path.write_text(json.dumps(plan()))
        with contextlib.redirect_stdout(io.StringIO()):code=main(['grant','fill',str(path),'--yes','--fill-only'],driver_factory=None,store=self.store)
        self.assertEqual(0,code);loaded,_=self.store.load('fill');self.assertEqual('fill',loaded['level']);self.assertNotIn('submit',loaded)
        output=io.StringIO()
        with contextlib.redirect_stdout(output):code=main(['list','--vaults'],driver_factory=None,store=self.store)
        self.assertEqual(0,code);self.assertEqual({'kind':'keychain'},json.loads(output.getvalue())['vaults']['local'])
        self.assertNotIn(SECRET,output.getvalue())
    def test_multi_stage_skips_verified_username_stage_and_completes_password(self):
        original_observe=self.driver.observe
        def observed(*args,**kwargs):
            o=original_observe(*args,**kwargs);o['tree']=o['tree'].replace('Identifier: password','Title: Password, Identifier: password');return o
        self.driver.observe=observed
        p=plan();stage1={'fields':[dict(p['fields'][0],name='username')], 'submit':p['submit'],'success':{'field':p['fields'][0]['selector'],'attribute':'Title','equals':'Password'}}
        self.store.grant('owned',{'target':p['target'],'origin':p['origin'],'stages':[stage1,{k:p[k] for k in ('fields','submit','success')}]})
        r=run_login(self.store,'owned',self.driver,vault_factory=lambda c:self.vault)
        self.assertTrue(r['ok'],r);self.assertEqual(1,self.driver.submits);self.assertEqual(2,r['completed_stages'])
        self.assertEqual(1,len(self.driver.filled))
