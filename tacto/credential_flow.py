"""A Flow refers to an existing delegation; credentials never enter its IR."""
import re
from .credential_vault import CredentialError


def problem(step):
    if (not isinstance(step.params,dict) or set(step.params)!={'grant'}
            or not isinstance(step.params.get('grant'),str)
            or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}',step.params['grant'])
            or step.selector is not None or step.safety is not None or step.binding is not None
            or step.for_each or step.captures or step.skip_if or step.postcondition or step.expect
            or step.unverified_ok or step.on_uncertain or step.risk_class!='none'):
        return 'authenticate uses only params.grant naming a registered login delegation'


def preflight(step,target,driver,store):
    issue=problem(step)
    if issue:raise CredentialError('credential_plan_invalid')
    plan,_=store.load(step.params['grant'])
    if plan['target']!=target:raise CredentialError('credential_scope_mismatch')
    if plan['level']!='login':raise CredentialError('credential_level_requires_login')
    if 'action.credential_input' not in getattr(driver,'capabilities',()):
        raise CredentialError('credential_driver_upgrade_required')


def run_step(step,target,driver,store,result,index):
    from .credential_runtime import run_login
    preflight(step,target,driver,store)
    outcome=run_login(store,step.params['grant'],driver)
    # Only the closed runtime summary is attached, never its observations/values.
    result.record(status='passed' if outcome['ok'] else 'error',step=index,
                  action='authenticate',stage='authenticate',credential_result=outcome,
                  **({'error':outcome['error'],'effect':outcome['effect']} if not outcome['ok'] else {}))
    return outcome['ok']
