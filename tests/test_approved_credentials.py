"""Approved ordinary field input must not require a second Vault setup."""
import json,tempfile,unittest
from pathlib import Path
from tacto.flow import Flow,Step,Selector,replay
from tacto.contract import SafetyContract,Resource
from tacto.helper import HelperDriver
from tacto.trace import TracingDriver
SECRET='owned-Synthetic-47!'
class Driver:
    capabilities={'action.approved_credential_input'}
    def __init__(self):self.calls=[]
    def identity(self,target):return target
    def observe(self,*a,**kw):return {'type':'observation','obs_id':'owned','tree':'0 Window\n  1 TextField  Subrole: AXSecureTextField  Identifier: password'}
    def act(self,*args,**kwargs):
        self.calls.append((args,kwargs))
        if not kwargs.get('approved'):return {'type':'confirm_request'}
        return {'type':'acted'}
def flow(declared=False):
    return Flow('owned','owned.app',steps=[Step('set_value',Selector(identifier='password'),params={'value':SECRET},
        safety=SafetyContract(effects=['credential'] if declared else ['write_ephemeral'],reversibility='reversible',resource=Resource(object_type='entry',scope='entry')),unverified_ok=True)])
class ApprovedCredentials(unittest.TestCase):
    def test_task_approval_handles_live_and_declared_password_once(self):
        for declared in (False,True):
            driver=Driver();result=replay(driver,flow(declared),confirm=lambda _:True)
            self.assertTrue(result.ok,result.log)
            self.assertEqual(1,len(driver.calls));args,kw=driver.calls[0]
            self.assertEqual(SECRET,args[2]['value']);self.assertTrue(kw['approved']);self.assertTrue(kw['sensitive_input'])
            self.assertNotIn(SECRET,str(result.log)+str(result.events))
    def test_no_approval_requests_confirmation_not_human_handoff(self):
        driver=Driver();result=replay(driver,flow(),confirm=lambda _:False)
        self.assertFalse(result.ok);self.assertEqual('confirm_request',result.log[-1]['gate'])
    def test_traced_input_is_forwarded_and_redacted(self):
        driver=Driver();traced=TracingDriver(driver);result=replay(traced,flow(),confirm=lambda _:True)
        self.assertTrue(result.ok,result.log);self.assertTrue(driver.calls[0][1]['sensitive_input'])
        self.assertNotIn(SECRET,traced.trace.to_json())
    def test_journal_contains_neither_password_nor_password_hash(self):
        from tacto.dispatch_journal import DurableDispatchDriver,operation_fingerprint
        driver=Driver()
        with tempfile.TemporaryDirectory() as d:
            with DurableDispatchDriver(driver,Path(d)) as durable:
                result=replay(durable,flow(),confirm=lambda _:True)
            self.assertTrue(result.ok,result.log)
            stored=''.join(p.read_text() for p in Path(d).glob('*.json'))
            self.assertNotIn(SECRET,stored)
            self.assertNotIn(operation_fingerprint('owned.app',{'name':'set_value','element_index':1,'value':SECRET}),stored)
    def test_general_mode_approved_input_needs_no_commit_receipt(self):
        from tacto.general import run
        from tacto.contract import Envelope
        driver=Driver()
        def plan(goal,candidates,history):
            if history:return {'done':True}
            return {'actions':[{'do':'set_value','candidate':candidates[0],'value':SECRET}]}
        result=run(driver,'owned.app','enter supplied password',plan,
            envelope=Envelope(quote='enter supplied password',allows=frozenset({'credential','write_ephemeral','read'})),approve=lambda _:True)
        self.assertTrue(result.ok,result.why);self.assertEqual(1,len(driver.calls));self.assertTrue(driver.calls[0][1]['sensitive_input'])
        self.assertNotIn(SECRET,str(result.visits)+str(result.events))
    def test_paste_requires_capability_before_any_input_and_never_retries_uncertain(self):
        from tacto.flow import _may_rerun
        f=flow();f.steps[0].params['input_method']='paste';driver=Driver()
        result=replay(driver,f,confirm=lambda _:True)
        self.assertFalse(result.ok);self.assertFalse(driver.calls);self.assertFalse(_may_rerun(f.steps[0]))
        driver.capabilities=driver.capabilities|{'action.credential_paste'}
        original=driver.act
        def paste(*a,**kw):
            r=original(*a,**kw);return {**r,'input_route':'native_paste','unverified':'observe_effect','clipboard_restored':True}
        driver.act=paste;result=replay(driver,f,confirm=lambda _:True)
        self.assertTrue(result.ok,result.log);self.assertEqual(1,len(driver.calls));self.assertNotIn(SECRET,str(result.events))
