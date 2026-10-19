"""Bounded replay of one immutable saved Flow on one signed connection."""
import json
import os
import select
import time
from pathlib import Path

from .flow import Flow, every_step
from .flow_errors import feedback
from .flow_session import flow_input_mode

MAX_REQUEST_BYTES = 65536


class RequestLines:
    def __init__(self, fd):
        self.fd = fd
        self.buffer = bytearray()

    def read(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if b'\n' in self.buffer:
                line, tail = self.buffer.split(b'\n', 1)
                if len(line) > MAX_REQUEST_BYTES:
                    raise ValueError('request_over_budget')
                self.buffer = bytearray(tail)
                return line.decode('utf-8')
            if len(self.buffer) > MAX_REQUEST_BYTES:
                raise ValueError('request_over_budget')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise TimeoutError('idle_timeout')
            chunk = os.read(self.fd, 65536)
            if not chunk:
                if self.buffer:
                    raise ValueError('request_requires_newline')
                return None
            self.buffer.extend(chunk)


class BatchDriver:
    """Preserve the foreground latch across individual ReplayResults."""
    def __init__(self, inner):
        self.inner = inner
        self.activated = False
        self.verified = False
        self.first_observation = False

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def begin_job(self):
        self.first_observation = True

    def observe(self, *args, **kwargs):
        if self.first_observation:
            self.first_observation = False
            if self.verified:
                # The last dispatched action already passed its result check.
                # Still request a new tree; do not reuse an observation ID.
                kwargs['_settle_after_action'] = False
        reply=self.inner.observe(*args, **kwargs)
        if isinstance(reply,dict) and reply.get('activated_target') is True:
            self.activated=True
        return reply

    def wait_until(self, target, predicate, *args, **kwargs):
        reply = self.inner.wait_until(target, predicate, *args, **kwargs)
        if isinstance(reply,dict) and (reply.get('activated_target') is True or
                isinstance(reply.get('last_obs'),dict) and reply['last_obs'].get('activated_target') is True):
            self.activated=True
        if isinstance(reply,dict) and reply.get('type')=='observation' and predicate(reply.get('tree','')):
            self.verified = True
        return reply

    def act(self, *args, **kwargs):
        action=kwargs.get('action',args[2] if len(args)>2 else {})
        if self.activated and action.get('name')!='read':
            return {'type':'error','error':'target_seen_in_front','effect':'not_started'}
        self.verified = False
        reply = self.inner.act(*args, **kwargs)
        if isinstance(reply, dict) and reply.get('activated_target') is True:
            self.activated = True
        return reply


def run_batch(args, *, make_driver, run_flow, stdin, emit):
    from .auto_repair import parse_options, RepairRefused
    try:args,auto_repair=parse_options(args)
    except RepairRefused as exc:
        emit(str(exc));return 2
    timeout, maximum = 90, 10
    repair_path = None
    if '--repair-packet' in args:
        i = args.index('--repair-packet')
        if (args.count('--repair-packet') != 1 or i+1 >= len(args)
                or not os.path.isabs(args[i+1]) or os.path.lexists(args[i+1])
                or not Path(args[i+1]).parent.is_dir()):
            emit('repair_packet: use one absolute new output path in an existing directory');return 2
        repair_path = args[i+1];del args[i:i+2]
    for flag, low, high in (('--idle-timeout',1,600),('--max-jobs',1,1000)):
        if flag in args:
            i=args.index(flag)
            if args.count(flag)!=1 or i+1>=len(args) or not args[i+1].isdigit() or not low<=int(args[i+1])<=high:
                emit('batch_invalid_limit: '+flag);return 2
            value=int(args[i+1]);del args[i:i+2]
            if flag=='--idle-timeout':timeout=value
            else:maximum=value
    files=[v for v in args if v not in ('--helper','--timings')]
    if len(files)!=1 or files[0].startswith('-') or args.count('--helper')!=1 or args.count('--timings')>1:
        emit('usage: batch <flow.json> --helper [--max-jobs 1..1000] [--idle-timeout 1..600] [--timings] [--repair-packet /absolute/new.json]');return 2
    try:
        recipe=Flow.from_json(Path(files[0]).read_text())
    except (OSError,ValueError,TypeError,KeyError,RecursionError) as exc:
        emit('invalid_flow: '+feedback(exc));return 2
    if '{{' in recipe.target or any(s.action in ('decide','visual') for s in every_step(recipe.steps)):
        emit('batch_requires_fixed_target_deterministic_flow; use run');return 2
    driver=None;completed=0;started=time.monotonic()
    try:
        driver=BatchDriver(make_driver(['--helper']))
        with flow_input_mode(stdin.fileno()):
            reader=RequestLines(stdin.fileno())
            emit('TACTO_BATCH_READY '+json.dumps({'target':recipe.target,'max_jobs':maximum},separators=(',',':')))
            for number in range(1,maximum+1):
                try:
                    line=reader.read(timeout)
                    if line is None:
                        return 0 if completed else 2
                    record=json.loads(line)
                    if (not isinstance(record,dict) or set(record)!={'params'} or not isinstance(record['params'],dict)
                            or not all(isinstance(k,str) and isinstance(v,str) for k,v in record['params'].items())):
                        raise ValueError('request_shape')
                    bound=recipe.bind(**record['params'])
                except (ValueError,TypeError,KeyError,ArithmeticError,TimeoutError,RecursionError):
                    emit('TACTO_BATCH_STOP invalid_request_or_parameters; use one {"params":{"name":"value"}} JSON line; no request was replayed')
                    return 2
                if bound.target!=recipe.target:
                    emit('TACTO_BATCH_STOP target_mismatch');return 2
                flags=['--helper','--think','0','--no-indicator']
                if '--timings' in args:flags.append('--timings')
                if repair_path is not None:
                    flags.extend(['--repair-packet', repair_path])
                driver.begin_job()
                run_started=time.monotonic()
                calls_before=auto_repair.calls if auto_repair is not None else 0
                try:
                    if auto_repair is None:
                        code=run_flow(bound,flags,_driver=driver,_confirm=lambda description:False)
                    else:
                        code,learned=auto_repair.run(recipe,record['params'],flags,
                            driver=driver,run_flow=run_flow,emit=emit)
                        if code==0:recipe=learned
                except Exception:
                    # Transport/unknown completion is not a retry instruction.
                    emit('TACTO_BATCH_STOP runtime_error; inspect current state, do not resend')
                    return 1
                if type(code) is not int or code not in (0,1,2,3):
                    emit('TACTO_BATCH_STOP invalid_runtime_result');return 1
                result = {'job':number,'exit':code,
                    'seconds':round(time.monotonic()-run_started,6),
                    'model_requests':auto_repair.calls-calls_before if auto_repair is not None else 0}
                if code != 0 and repair_path is not None and Path(repair_path).is_file():
                    result['repair_packet'] = repair_path
                emit('TACTO_BATCH_RESULT '+json.dumps(result,separators=(',',':')))
                if code!=0:return code
                completed+=1
                if driver.activated:
                    emit('TACTO_BATCH_STOP activation_observed; completed job recorded, next job not started');return 1
            return 0
    finally:
        if driver is not None:driver.close()
        emit('TACTO_BATCH_DONE '+json.dumps({'completed':completed,'seconds':round(time.monotonic()-started,6)},separators=(',',':')))
