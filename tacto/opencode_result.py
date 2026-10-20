"""OpenCode 1.2.22 JSON-event adapter; CLI telemetry is not an invoice.

One fresh planning step only. Config isolation and deny permissions are
requested, not a sandbox or proof against plugin/CLI side effects. Observed
step usage can survive a later error; it is not complete remote-call billing.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import uuid
from .provider_result import (ProviderError, ProviderResult, ProviderUnavailable,
                              classify_unavailable, finite_nonnegative_number)


class OpenCodeProtocolError(ValueError):
    def __init__(self, fields=None):
        super().__init__('OpenCode structured response rejected')
        self.fields = dict(fields or {})


def parse_opencode_events(stdout):
    fields = {}
    def reject(): raise OpenCodeProtocolError(fields)
    if type(stdout) is not str or len(stdout.encode()) > 8*1024*1024: reject()
    session = message = None
    started = finished = False
    texts = []; ids = set(); events = 0
    for line in stdout.splitlines():
        if not line.strip(): continue
        events += 1
        try: event = json.loads(line)
        except (ValueError,RecursionError): reject()
        if (events > 1000 or type(event) is not dict
                or type(event.get('timestamp')) is not int or event['timestamp'] < 0
                or type(event.get('sessionID')) is not str
                or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,128}',event['sessionID'])): reject()
        if session is None: session = event['sessionID']
        if event['sessionID'] != session: reject()
        kind = event.get('type')
        if kind == 'error': reject()
        if finished or kind not in ('step_start','text','step_finish'): reject()
        if set(event) != {'type','timestamp','sessionID','part'}: reject()
        part = event['part']
        if (type(part) is not dict or part.get('sessionID') != session
                or type(part.get('id')) is not str or not 0 < len(part['id']) <= 128
                or type(part.get('messageID')) is not str
                or not re.fullmatch(r'msg_[A-Za-z0-9_-]{1,128}',part['messageID'])
                or part['id'] in ids): reject()
        ids.add(part['id'])
        if message is None: message = part['messageID']
        if part['messageID'] != message: reject()
        if kind == 'step_start':
            if started or part.get('type') != 'step-start': reject()
            started = True
        elif kind == 'text':
            stamp = part.get('time')
            if (not started or part.get('type') != 'text' or type(part.get('text')) is not str
                    or part.get('synthetic',False) is not False or part.get('ignored',False) is not False
                    or type(stamp) is not dict or set(stamp) != {'start','end'}
                    or any(type(stamp[k]) is not int or stamp[k] < 0 for k in stamp)
                    or stamp['end'] < stamp['start']): reject()
            texts.append(part['text'])
        else:
            tokens = part.get('tokens'); cache = tokens.get('cache') if type(tokens) is dict else None
            if (not started or part.get('type') != 'step-finish'
                    or type(part.get('reason')) is not str or not 0 < len(part['reason']) <= 128
                    or type(tokens) is not dict or set(tokens) - {'input','output','reasoning','cache','total'}
                    or type(cache) is not dict or set(cache) != {'read','write'}
                    or any(type(tokens.get(k)) is not int or tokens[k] < 0 for k in ('input','output','reasoning'))
                    or any(type(cache[k]) is not int or cache[k] < 0 for k in cache)
                    or ('total' in tokens and (type(tokens['total']) is not int or tokens['total'] < 0))
                    or not finite_nonnegative_number(part.get('cost'))): reject()
            fields = {'provider_thread_id':session,'input_tokens':tokens['input'],
                'output_tokens':tokens['output'],'cached_input_tokens':cache['read'],
                'cache_creation_input_tokens':cache['write'],'input_tokens_basis':'uncached',
                'total_input_tokens':tokens['input']+cache['read']+cache['write'],
                'cost':part['cost'],'cost_basis':'cli_reported_step_estimate'}
            # The event has no currency or serving-model field. Keep them unknown.
            if part['reason'] != 'stop': reject()
            finished = True
    if not finished or not ''.join(texts).strip(): reject()
    return {'text':''.join(texts), **fields}


def diagnostic_unavailable(stdout, stderr):
    """Classify CLI diagnostics without searching assistant text frames."""
    reason = classify_unavailable(stderr, provider='opencode')
    if reason:
        return reason
    if type(stdout) is not str or len(stdout.encode()) > 8*1024*1024:
        return None
    # stdout is a JSON-event channel. Plain text and assistant parts are not
    # service diagnostics, even when framing later fails or is truncated.
    for line in stdout.splitlines():
        try: event = json.loads(line)
        except (ValueError, RecursionError): continue
        if (type(event) is not dict or set(event) != {'type','timestamp','sessionID','error'}
                or event.get('type') != 'error' or type(event.get('timestamp')) is not int
                or event['timestamp'] < 0 or type(event.get('sessionID')) is not str
                or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,128}', event['sessionID'])
                or type(event.get('error')) is not dict):
            continue
        error = event['error']
        if (set(error) != {'name','data'} or error.get('name') != 'UnknownError'
                or type(error.get('data')) is not dict or set(error['data']) != {'message'}
                or error['data']['message'] != 'Error: Token refresh failed: 401'):
            continue
        return 'authentication_failed'
    return None


def opencode_result(prompt, *, timeout=420):
    from .authoring import _provider_env, _run_provider
    from .model_budget import remaining_model_seconds
    began = time.monotonic(); fields = {}; status = 'ok'; error = None
    try:
        timeout = remaining_model_seconds(timeout)
        with tempfile.TemporaryDirectory(prefix='tacto-opencode-result-') as folder:
            config = Path(folder)/'config'; config.mkdir()
            env = _provider_env()
            for key in ('OPENCODE_CONFIG','OPENCODE_CONFIG_DIR','OPENCODE_CONFIG_CONTENT','OPENCODE_PERMISSION'):
                env.pop(key,None)
            env.update(XDG_CONFIG_HOME=str(config), OPENCODE_CONFIG_DIR=str(config),
                OPENCODE_DISABLE_PROJECT_CONFIG='true', OPENCODE_DISABLE_EXTERNAL_SKILLS='true',
                OPENCODE_DISABLE_AUTOUPDATE='true', OPENCODE_AUTO_SHARE='false',
                OPENCODE_CONFIG_CONTENT=json.dumps({'plugin':[], 'autoupdate':False,
                    'share':'disabled', 'permission':{'*':'deny'},
                    'agent':{'TactoPlanner':{'mode':'primary','permission':{'*':'deny'},
                        'description':'Return a planning answer without tools'}}}))
            remaining = timeout-(time.monotonic()-began)
            if remaining <= 0: raise TimeoutError()
            response = _run_provider(['opencode','run','--format','json','--agent','TactoPlanner'],
                remaining, env=env, cwd=folder, input_text=prompt)
            try: fields = parse_opencode_events(response.stdout)
            except OpenCodeProtocolError as exc:
                fields = exc.fields
                reason = diagnostic_unavailable(response.stdout,response.stderr)
                if reason: raise ProviderUnavailable(reason) from None
                raise
            if response.returncode != 0: raise OpenCodeProtocolError(fields)
    except Exception as exc:
        if isinstance(exc,subprocess.TimeoutExpired):
            from .provider_io import bounded_output_text
            partial=bounded_output_text(exc.output)
            if partial is not None:
                try:fields=parse_opencode_events(partial)
                except OpenCodeProtocolError as rejected:fields=rejected.fields
        status = 'timeout' if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else 'error'
        fields.pop('text',None)
        error = ProviderError(getattr(exc,'reason',type(exc).__name__),
            'OpenCode request exceeded deadline' if status == 'timeout' else 'OpenCode CLI response failed')
    return ProviderResult(str(uuid.uuid4()),'opencode','opencode','cli-json',status,
        fields.pop('text',None),error,max(0,time.monotonic()-began),**fields)
