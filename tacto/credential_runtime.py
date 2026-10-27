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
    """Run delegated stages; only closed diagnostics leave this boundary."""
    stage='grant';dispatched=0;submitted=False;pending_submit=False;submissions=0;stage_index=0;completed=0;field=None
    try:
        root,configs=store.load(name)
        if not fill_only and root['level']!='login':raise CredentialError('credential_level_requires_login')
        if fill_only and root.get('stages'):raise CredentialError('credential_level_requires_login')
        if 'action.credential_input' not in getattr(driver,'capabilities',()):raise CredentialError('credential_driver_upgrade_required')
        target=root['target'];vaults={key:vault_factory(config) for key,config in configs.items()}
        def still_delegated():
            current,current_configs=store.load(name)
            if current!=root or current_configs!=configs:raise CredentialError('credential_scope_mismatch')
        from .flow import Postcondition,Selector
        def observed():
            observation=driver.observe(target,'tree_full')
            if observation.get('type')!='observation':raise CredentialError('credential_observation_unavailable')
            return observation
        def secret(ref):
            try:value=vaults[ref['vault']].get(ref['item'],ref['field'])
            except CredentialError:raise
            except Exception:raise CredentialError('vault_unavailable') from None
            if not isinstance(value,str) or not value or len(value.encode())>16384:raise CredentialError('vault_value_invalid')
            still_delegated()
            return value
        def scoped(observation,index,expected):
            check=driver.credential_input(observation['obs_id'],target,index,None,expected_origin=expected,check_only=True)
            if check.get('type')!='credential_ready':raise CredentialError('credential_scope_mismatch')
        def success(plan,observation):
            if 'success' not in plan:return False
            condition=Postcondition(field=selector(plan['success']['field']),attribute=plan['success']['attribute'],equals=plan['success']['equals'])
            if not condition.primary_check(observation['tree']):return False
            scoped(observation,_field(observation['tree'],condition.field),plan.get('success_origin'))
            return True
        def account(observation):
            nonlocal field
            if not root.get('account'):return False
            field='account';check=root['account'];index=_field(observation['tree'],selector(check['field']))
            scoped(observation,index,root.get('success_origin'))
            expected=secret(check['secret'])
            try:
                # Vault access may be slow; confirm the current account again
                # after it returns rather than accepting a pre-fetch snapshot.
                observation=observed();index=_field(observation['tree'],selector(check['field']))
                scoped(observation,index,root.get('success_origin'))
                line=next((line for line in observation['tree'].splitlines() if re.match(rf'\s*{index}\s',line)), '')
                matches=Selector._attr(line,check['attribute'])==expected
            finally:expected=None
            if not matches:raise CredentialError('credential_account_mismatch')
            field=None;return True
        observation=observed()
        if not fill_only and success(root,observation):
            verified=account(observation)
            return {'ok':True,'status':'already_authenticated','target':target,'filled_fields':0,'submitted':False,'account_verified':verified,'model_calls':0}
        stages=root.get('stages') or [root]
        for stage_index,plan in enumerate(stages):
            stage='stage_check';field=None;still_delegated()
            if stage_index:observation=observed()
            if not fill_only and success(plan,observation):
                completed+=1;continue
            for field_index,entry in enumerate(plan['fields']):
                stage='fill';field=entry['name'];still_delegated()
                if field_index:observation=observed()
                index=_field(observation['tree'],selector(entry['selector']))
                scoped(observation,index,plan.get('origin'))
                value=secret(entry['secret'])
                from .contract import SafetyContract,Resource,Destination,policy
                contract=SafetyContract(effects=['credential'],data_classes=['credential'],reversibility='reversible',
                    resource=Resource(object_type='delegated login field',object_identity=entry['name'],scope='entry'),
                    destination=Destination(kind='same_origin' if plan.get('origin') else 'same_app',identity=plan.get('origin') or target))
                if policy(contract,credential_authority=CredentialAuthorization(plan,entry['name'])).risk!='preapproved':raise CredentialError('credential_delegation_required')
                try:
                    still_delegated()
                    answer=driver.credential_input(observation['obs_id'],target,index,value,expected_origin=plan.get('origin'))
                finally:value=None
                if answer.get('type')!='acted':raise CredentialError('credential_fill_failed',effect=answer.get('effect','unknown'))
                dispatched+=1
            field=None
            if fill_only:return {'ok':True,'status':'credential_fields_dispatched','target':target,'filled_fields':dispatched,'submitted':False,'model_calls':0}
            stage='submit';still_delegated();observation=observed();index=_field(observation['tree'],selector(plan['submit']))
            from .safety import effects_of
            line=next((line for line in observation['tree'].splitlines() if re.match(rf'\s*{index}\s',line)), '')
            effects,_=effects_of({'name':'click','element_index':index},line,target=target)
            if effects & {'financial','delete','install','system_config'}:raise CredentialError('credential_submit_outside_scope')
            answer=driver.credential_submit(observation['obs_id'],target,index,expected_origin=plan.get('origin'))
            if answer.get('type')!='acted':
                uncertain=answer.get('effect')!='not_started';submitted|=uncertain;pending_submit=uncertain
                raise CredentialError('credential_submit_unverified',effect='unknown' if uncertain else 'not_started')
            submitted=True;pending_submit=True;submissions+=1;stage='verify';end=clock()+plan.get('verify_seconds',10)
            while True:
                observation=observed()
                if success(plan,observation):break
                if clock()>=end:raise CredentialError('credential_login_unverified',effect='unknown')
                time.sleep(.1)
            completed+=1;pending_submit=False
        verified=account(observation)
        return {'ok':True,'status':'login_verified','target':target,'filled_fields':dispatched,'submitted':submitted,
                'submissions':submissions,'completed_stages':completed,'account_verified':verified,'model_calls':0}
    except Exception as exc:
        from .credential_diagnostics import recovery
        code=exc.code if isinstance(exc,CredentialError) else 'credential_execution_failed'
        effect=(exc.effect if isinstance(exc,CredentialError) else 'not_started') if not dispatched and not submitted else 'unknown'
        return {'ok':False,'error':code,'stage':stage,'effect':effect,'filled_fields':dispatched,
                'submitted':submitted,'model_calls':0,
                'repair':recovery(code,field=field,stage_index=stage_index,submitted=pending_submit,completed_stages=completed)}
