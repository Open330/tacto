"""Reusable, vault-backed field filling and login on an explicitly delegated scope."""
from __future__ import annotations
from dataclasses import dataclass
import re
import time
from .credential_grants import origin, selector
from .credential_vault import CredentialError, vault_from


@dataclass(frozen=True)
class CredentialAuthorization:
    plan: dict
    field: str
    def authorizes(self,contract):
        return (self.field in {r['name'] for r in self.plan['fields']}
            and contract.resource.object_identity==self.field
            and contract.resource.scope=='entry'
            and set(contract.effects)<= {'credential','write_ephemeral','read'}
            and contract.destination.identity==(self.plan.get('origin') or self.plan['target']))


def _field(tree,selected):
    try:return selected.resolve(tree)[0]
    except (LookupError,ValueError):raise CredentialError('credential_target_missing') from None


def run_login(store,name,driver,*,fill_only=False,vault_factory=vault_from,clock=time.monotonic):
    """No plaintext fields, observations or provider diagnostics leave this function."""
    stage='grant';dispatched=0;submitted=False
    try:
        plan,configs=store.load(name)
        if not fill_only and plan['level']!='login':raise CredentialError('credential_level_requires_login')
        if 'action.credential_input' not in getattr(driver,'capabilities',()):raise CredentialError('credential_driver_upgrade_required')
        target=plan['target'];expected=plan.get('origin');vaults={k:vault_factory(v) for k,v in configs.items()}
        def still_delegated():
            current,current_configs=store.load(name)
            if current!=plan or current_configs!=configs:
                raise CredentialError('credential_scope_mismatch')
        from .flow import Postcondition
        success=Postcondition(field=selector(plan['success']['field']),attribute=plan['success']['attribute'],equals=plan['success']['equals']) if 'success' in plan else None
        def observed():
            o=driver.observe(target,'tree_full')
            if o.get('type')!='observation':raise CredentialError('credential_observation_unavailable')
            return o
        o=observed()
        if not fill_only and success is not None and success.primary_check(o['tree']):
            index=_field(o['tree'],success.field)
            check=driver.credential_input(o['obs_id'],target,index,None,expected_origin=plan.get('success_origin'),check_only=True)
            if check.get('type')!='credential_ready':raise CredentialError('credential_scope_mismatch')
            return {'ok':True,'status':'already_authenticated','target':target,'filled_fields':0,'submitted':False,'model_calls':0}
        for entry in plan['fields']:
            stage='fill';still_delegated()
            o=observed();sel=selector(entry['selector']);index=_field(o['tree'],sel)
            # The driver checks the live frame origin again at dispatch, before consuming the value.
            check=driver.credential_input(o['obs_id'],target,index,None,expected_origin=expected,check_only=True)
            if check.get('type')!='credential_ready':raise CredentialError('credential_scope_mismatch')
            ref=entry['secret']
            try:value=vaults[ref['vault']].get(ref['item'],ref['field'])
            except CredentialError:raise
            except Exception:raise CredentialError('vault_unavailable') from None
            if not isinstance(value,str) or not value or len(value.encode())>16384:raise CredentialError('vault_value_invalid')
            from .contract import SafetyContract,Resource,Destination,policy
            contract=SafetyContract(effects=['credential'],data_classes=['credential'],reversibility='reversible',
                resource=Resource(object_type='delegated login field',object_identity=entry['name'],scope='entry'),
                destination=Destination(kind='same_origin' if expected else 'same_app',identity=expected or target))
            if policy(contract,credential_authority=CredentialAuthorization(plan,entry['name'])).risk!='preapproved':raise CredentialError('credential_delegation_required')
            try:
                # An external vault may take time. Revocation or replacement
                # during that request must prevent dispatch of its result.
                still_delegated()
                answer=driver.credential_input(o['obs_id'],target,index,value,expected_origin=expected)
            finally:value=None
            if answer.get('type')!='acted':
                raise CredentialError('credential_fill_failed',effect=answer.get('effect','unknown') if answer.get('effect') in ('not_started','unknown') else 'unknown')
            dispatched+=1
        if fill_only:
            return {'ok':True,'status':'credential_fields_dispatched','target':target,'filled_fields':dispatched,'submitted':False,'model_calls':0}
        stage='submit';still_delegated();o=observed();index=_field(o['tree'],selector(plan['submit']))
        from .safety import effects_of
        line=next((v for v in o['tree'].splitlines() if re.match(rf'\s*{index}\s',v)), '')
        effects,_=effects_of({'name':'click','element_index':index},line,target=target)
        if effects & {'financial','delete','install','system_config'}:raise CredentialError('credential_submit_outside_scope')
        # Submit carries the same origin guard as the credential write.
        answer=driver.credential_submit(o['obs_id'],target,index,expected_origin=expected)
        if answer.get('type')!='acted':
            submitted=answer.get('effect')!='not_started'
            raise CredentialError('credential_submit_unverified',effect='unknown' if submitted else 'not_started')
        submitted=True
        stage='verify';end=clock()+10
        while True:
            o=observed()
            if success.primary_check(o['tree']):
                index=_field(o['tree'],success.field)
                check=driver.credential_input(o['obs_id'],target,index,None,
                    expected_origin=plan.get('success_origin'),check_only=True)
                if check.get('type')=='credential_ready':
                    return {'ok':True,'status':'login_verified','target':target,'filled_fields':dispatched,'submitted':True,'model_calls':0}
            if clock()>=end:raise CredentialError('credential_login_unverified',effect='unknown')
            time.sleep(.1)
    except CredentialError as e:
        return {'ok':False,'error':e.code,'stage':stage,'effect':e.effect if not dispatched and not submitted else 'unknown',
                'filled_fields':dispatched,'submitted':submitted,'model_calls':0}
    except Exception:
        # Exceptions from a vault or UI backend can contain credential values.
        return {'ok':False,'error':'credential_execution_failed','stage':stage,'effect':'unknown' if dispatched or submitted else 'not_started',
                'filled_fields':dispatched,'submitted':submitted,'model_calls':0}
