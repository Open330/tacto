"""Bounded read-only menu settling; readiness grants no input/completion authority."""
from copy import deepcopy
from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class MenuReadiness:
    state: str
    reason: str
    read_count: int
    enabled_samples: tuple[bool, ...]

    def to_dict(self):
        return {'type':'menu_readiness','state':self.state,'reason':self.reason,
                'read_count':self.read_count,'enabled_samples':list(self.enabled_samples)}


def wait_menu_enabled(driver, target, path, *, timeout_seconds=1, poll_interval=.05,
                      max_reads=64, cancelled=None, clock=None, sleep=None):
    """Poll trusted menu_state reads only; never prepare, press or retry input.

    A valid disabled response may settle. Errors/malformed responses are not
    absence and are never retried. Host cancellation/deadline are rechecked
    after every reader. In-flight calls cannot be forcibly interrupted.
    Callers still need fresh native binding, approval and task/receipt evidence.
    """
    text=lambda value:type(value) is str and bool(value.strip()) and len(value)<=1024
    if not text(target) or type(path) is not list or not 1<=len(path)<=8:
        raise ValueError('explicit target and bounded menu path required')
    if not all(text(part) or (type(part) is list and 1<=len(part)<=16 and all(text(v) for v in part)) for part in path):
        raise ValueError('invalid menu path')
    for value,zero in ((timeout_seconds,True),(poll_interval,False)):
        if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (not zero and value==0):
            raise ValueError('finite menu polling budgets required')
    if type(max_reads) is not int or not 1<=max_reads<=128:
        raise ValueError('bounded read count required')
    if cancelled is not None and not callable(cancelled):raise ValueError('invalid cancellation checker')
    clock=clock or time.monotonic;sleep=sleep or time.sleep
    held=deepcopy(path);deadline=clock()+timeout_seconds
    samples=[];reads=0
    def finish(state,reason):return MenuReadiness(state,reason,reads,tuple(samples))
    def stopped():
        if clock()>=deadline:return finish('timeout','deadline_exceeded')
        try:
            if cancelled is not None and cancelled():return finish('stopped','cancelled')
        except Exception:return finish('stopped','cancellation_check_failed')
        return None
    for _ in range(max_reads):
        if result:=stopped():return result
        reads+=1
        try:reply=driver.menu_state(target,deepcopy(held))
        except Exception:
            if result:=stopped():return result
            return finish('unavailable','menu_read_failed')
        valid=type(reply) is dict and reply.get('type')=='menu_state'
        enabled=reply.get('enabled') if valid else None
        valid=valid and type(enabled) is bool
        if valid:samples.append(enabled)
        if result:=stopped():return result
        if type(reply) is dict and reply.get('type')=='error' and reply.get('error')=='aborted_by_user':
            return finish('stopped','cancelled')
        if not valid:return finish('unavailable','menu_state_unavailable')
        if enabled:return finish('ready','menu_enabled')
        if reads==max_reads:return finish('timeout','read_limit')
        remaining=deadline-clock()
        if remaining<=0:return finish('timeout','deadline_exceeded')
        sleep(min(poll_interval,remaining))
    raise AssertionError('unreachable')
