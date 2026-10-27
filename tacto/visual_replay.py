"""Host-scoped dynamic native image steps in a Flow; no serialized pixels."""
from .contract import Envelope, policy
from .flow import visual_problem
from .general_visual import NativeVisualScope
from .model_budget import model_deadline, remaining_model_seconds


def scope_problem(step, scopes, driver):
    problem=visual_problem(step.params)
    if problem:return problem
    if (not isinstance(scopes,dict) or len(scopes)>32
            or type(scopes.get(step.params['scope'])) is not NativeVisualScope):
        return 'the host must register this NativeVisualScope before replay'
    scope=scopes[step.params['scope']]
    if (step.selector is not None or step.for_each is not None or step.captures
            or step.skip_if is not None or step.postcondition is not None or step.expect is not None
            or step.unverified_ok or step.risk_class!='none'
            or not isinstance(step.intent_text,str) or not step.intent_text.strip()
            or len(step.intent_text)>500 or step.safety is None
            or set(step.safety.effects)!=set(scope.effects) or policy(step.safety).risk!='none'
            or scope.when is not None):
        return 'visual steps require a reversible host-verified contract and explicit goal'
    from .native_visual_actions import native_visual_capabilities
    if not native_visual_capabilities(scope.input_actions) <= set(getattr(driver,'capabilities',()) or ()):
        return 'driver lacks observation-bound authorized native input'
    return None


def run_step(driver,target,step,index,result,scopes):
    from .general import run
    import time
    # Recheck the rendered step and current capabilities immediately before
    # entering the image loop, including changes during an earlier branch.
    problem = scope_problem(step, scopes, driver)
    if problem:
        result.record(status='error', step=index, action='visual',
                      error='visual_scope_required', detail=problem)
        return False
    scope=scopes[step.params['scope']]
    result.visual_used=True
    try:
        remaining=remaining_model_seconds(step.params.get('timeout_seconds',10))
        deadline=time.monotonic()+remaining
        def no_ax(*args):
            raise ValueError('a visual Flow step cannot fall back to AX planning')
        with model_deadline(deadline):
            child=run(driver,target,result._redact(step.intent_text),no_ax,visual_scope=scope,
                envelope=Envelope(quote=scope.label,allows=scope.effects,scope='entry'),
                max_screens=step.params.get('max_screens',2),timeout_seconds=remaining)
    except Exception as error:
        result.record(status='error',step=index,action='visual',error='visual_step_failed',
                      detail=type(error).__name__)
        return False
    for event in child.events:
        event['visual_flow_scope']=step.params['scope']
    result.events.extend(child.events)
    result.visual_history.extend(child.visual_history)
    result.provider_calls.extend(child.provider_calls)
    if not (child.ok and child.verified and child.certainty=='confirmed'
            and all(event['state']=='confirmed' for event in child.events)):
        for event in child.events:
            if event['state'] == 'dispatched':
                event['state'] = 'unknown'
        result.record(status='unverified',step=index,action='visual',error='visual_step_unverified',
                      detail=child.why,certainty=child.certainty)
        return False
    result.record(status='passed',step=index,action='visual',scope=step.params['scope'],
                  receiving_host_verified=True,events=len(child.events),planner_calls=child.asked)
    return True
