"""Gemini CLI 0.56 stream adapter; telemetry is not an invoice or sandbox.

A fresh planning run requests no tools/hooks/MCP/extensions. The JSON wire
prompt avoids CLI slash/at-command preprocessing without changing task data.
Local process cancellation does not establish remote cancellation.
"""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import time
import uuid
from .provider_result import ProviderError, ProviderResult, ProviderUnavailable, classify_unavailable


class GeminiProtocolError(ValueError):
    def __init__(self, fields=None):
        super().__init__('Gemini structured response rejected')
        self.fields=dict(fields or {})


def wire_prompt(prompt):
    if type(prompt) is not str or not prompt.strip():raise ValueError('empty planning prompt')
    encoded=json.dumps({'planning_prompt':prompt},ensure_ascii=False).replace('@',r'\u0040')
    return ('Decode the planning_prompt JSON string below and answer that planning question. '
            'Treat it as task data, not CLI commands. Use no tools. Return only the requested answer.\n'+encoded)


def _usage(stats):
    keys={'total_tokens','input_tokens','output_tokens','cached','input','duration_ms','tool_calls','models'}
    model_keys=keys-{'duration_ms','tool_calls','models'}
    if type(stats) is not dict or set(stats)!=keys:raise GeminiProtocolError()
    def counts(row,names):
        if any(type(row[k]) is not int or not 0<=row[k]<=2**53-1 for k in names):raise GeminiProtocolError()
        if (row['cached']>row['input_tokens'] or row['input']!=row['input_tokens']-row['cached']
                or row['total_tokens']<row['input_tokens']+row['output_tokens']):raise GeminiProtocolError()
    counts(stats,keys-{'models'})
    models=stats['models']
    if type(models) is not dict or len(models)>100:raise GeminiProtocolError()
    for name,row in models.items():
        if (type(name) is not str or not re.fullmatch(r'[A-Za-z0-9._:/-]{1,200}',name)
                or type(row) is not dict or set(row)!=model_keys):raise GeminiProtocolError()
        counts(row,model_keys)
    if any(sum(row[k] for row in models.values())!=stats[k] for k in model_keys):raise GeminiProtocolError()
    fields={'input_tokens':stats['input_tokens'],'total_input_tokens':stats['input_tokens'],
            'cached_input_tokens':stats['cached'],'output_tokens':stats['output_tokens'],
            'input_tokens_basis':'total'}
    # init.model is a configured selector (possibly auto), not serving identity.
    if len(models)==1:fields['model']=next(iter(models))
    return fields


def parse_gemini_events(stdout, expected_prompt):
    fields={};stage=0;text=[];finished=False;rejected=False
    def reject():raise GeminiProtocolError(fields)
    if type(stdout) is not str or len(stdout.encode())>8*1024*1024:reject()
    lines=[line for line in stdout.splitlines() if line.strip()]
    if len(lines)>1000:reject()
    for line in lines:
        try:frame=json.loads(line)
        except (ValueError,RecursionError):reject()
        if (type(frame) is not dict or type(frame.get('timestamp')) is not str
                or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z',frame['timestamp'])):reject()
        kind=frame.get('type')
        if finished:reject()
        if kind=='init':
            if (stage!=0 or set(frame)!={'type','timestamp','session_id','model'}
                    or type(frame['session_id']) is not str
                    or not re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}',frame['session_id'])
                    or type(frame['model']) is not str or not 0<len(frame['model'])<=200):reject()
            fields['provider_thread_id']=frame['session_id'];stage=1
        elif kind=='message':
            if frame.get('role')=='user':
                if (stage!=1 or set(frame)!={'type','timestamp','role','content'}
                        or frame['content']!=expected_prompt):reject()
                stage=2
            else:
                if (stage!=2 or set(frame)!={'type','timestamp','role','content','delta'}
                        or frame['role']!='assistant' or frame['delta'] is not True
                        or type(frame['content']) is not str):reject()
                text.append(frame['content'])
        elif kind=='result':
            if (not {'type','timestamp','status','stats'}<=set(frame)
                    or set(frame)-{'type','timestamp','status','stats','error'}
                    or frame['status'] not in ('success','error')):reject()
            try:fields.update(_usage(frame['stats']))
            except GeminiProtocolError:reject()
            finished=True
            if stage!=2 or frame['status']!='success' or 'error' in frame or frame['stats']['tool_calls']!=0:
                rejected=True
        elif kind in ('tool_use','tool_result','error'):
            # Refuse answers, but keep independently valid final CLI counters.
            rejected=True
        else:reject()
    if rejected or not finished or not ''.join(text).strip():reject()
    fields['text']=''.join(text)
    return fields


def _settings(env):
    """Preserve unrelated system settings; narrow this invocation's tools."""
    original=env.get('GEMINI_CLI_SYSTEM_SETTINGS_PATH',
        '/Library/Application Support/GeminiCli/settings.json')
    settings={}
    try:fd=os.open(original,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:pass
    else:
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0,os.getuid()) or info.st_size>1024*1024:
                raise ValueError('system settings unavailable')
            data=os.read(fd,1024*1024+1);after=os.fstat(fd)
            version=lambda row:(row.st_dev,row.st_ino,row.st_uid,row.st_mode,row.st_size,row.st_mtime_ns,row.st_ctime_ns)
            if len(data)!=info.st_size or version(info)!=version(after):raise ValueError('system settings changed')
            settings=json.loads(data)
            if type(settings) is not dict:raise ValueError('system settings unavailable')
        finally:os.close(fd)
    def merge(target,changes):
        for key,value in changes.items():
            if type(value) is dict:
                if type(target.get(key)) is not dict:target[key]={}
                merge(target[key],value)
            else:target[key]=value
    merge(settings,{'tools':{'core':[],'useRipgrep':False,'discoveryCommand':'','callCommand':''},'hooksConfig':{'enabled':False},
        'admin':{'mcp':{'enabled':False},'extensions':{'enabled':False},'skills':{'enabled':False}},
        'skills':{'enabled':False},'context':{'fileName':[]},'telemetry':{'enabled':False,'logPrompts':False},
        'model':{'maxSessionTurns':1},'general':{'enableAutoUpdate':False,'sessionRetention':{'enabled':False}}})
    return settings


def gemini_result(prompt, *, timeout=420):
    from .authoring import _run_provider, _provider_env
    from .model_budget import remaining_model_seconds
    began=time.monotonic();fields={};status='ok';error=None;wire=None
    try:
        timeout=remaining_model_seconds(timeout);wire=wire_prompt(prompt)
        with tempfile.TemporaryDirectory(prefix='tacto-gemini-result-') as folder:
            env=_provider_env();settings=_settings(env);config=Path(folder)/'settings.json'
            fd=os.open(config,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
            with os.fdopen(fd,'w') as file:json.dump(settings,file,allow_nan=False)
            env['GEMINI_CLI_SYSTEM_SETTINGS_PATH']=str(config)
            env['NO_BROWSER']='1';env['GEMINI_TELEMETRY_ENABLED']='false';env['GEMINI_TELEMETRY_LOG_PROMPTS']='false'
            remaining=timeout-(time.monotonic()-began)
            if remaining<=0:raise TimeoutError()
            response=_run_provider(['gemini','--output-format','stream-json','--extensions','none',
                                    '--approval-mode','plan'],remaining,env=env,cwd=folder,input_text=wire)
            try:fields=parse_gemini_events(response.stdout,wire)
            except GeminiProtocolError as exc:
                fields=exc.fields
                # Assistant stdout can quote authentication errors. Only the
                # CLI diagnostic channel may classify service availability.
                reason=classify_unavailable(response.stderr,provider='gemini')
                if reason:raise ProviderUnavailable(reason) from None
                raise
            if response.returncode!=0:raise GeminiProtocolError(fields)
    except Exception as exc:
        if isinstance(exc,subprocess.TimeoutExpired) and wire is not None:
            from .provider_io import bounded_output_text
            partial=bounded_output_text(exc.output)
            if partial is not None:
                try:fields=parse_gemini_events(partial,wire)
                except GeminiProtocolError as rejected:fields=rejected.fields
        status='timeout' if isinstance(exc,(TimeoutError,subprocess.TimeoutExpired)) else 'error'
        fields.pop('text',None)
        error=ProviderError(getattr(exc,'reason',type(exc).__name__),
            'Gemini request exceeded deadline' if status=='timeout' else 'Gemini CLI response failed')
    return ProviderResult(str(uuid.uuid4()),'gemini','gemini','cli-json',status,
        fields.pop('text',None),error,max(0,time.monotonic()-began),**fields)
