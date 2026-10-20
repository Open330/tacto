"""Fresh tool-using agent runs and provider-specific aggregate usage.

This deliberately does not use the tool-free planning parsers. Token counters
are reported telemetry, not invoices or interchangeable tokenizer units.
"""
import json
import os
from pathlib import Path
import subprocess
import time
import tempfile


def usage_from_stream(provider, stream):
    events=[json.loads(line) for line in stream.splitlines() if line.strip()]
    if provider=='codex':
        terminal=[e for e in events if e.get('type')=='turn.completed']
        if len(terminal)!=1:return {'status':'missing_or_ambiguous'}
        u=terminal[0].get('usage',{});total=u.get('input_tokens');read=u.get('cached_input_tokens')
        created=None;new=None if total is None or read is None else total-read
        output=u.get('output_tokens');ok=True;models=[]
    else:
        terminal=[e for e in events if e.get('type')=='result']
        if len(terminal)!=1:return {'status':'missing_or_ambiguous'}
        r=terminal[0];u=r.get('usage',{});uncached=u.get('input_tokens')
        read=u.get('cache_read_input_tokens');created=u.get('cache_creation_input_tokens')
        if any(v is not None and (type(v) is not int or v<0) for v in (uncached,read,created)):
            return {'status':'invalid'}
        new=None if uncached is None or created is None else uncached+created
        total=None if new is None or read is None else new+read
        output=u.get('output_tokens');ok=r.get('is_error') is False
        models=list(r.get('modelUsage',{}))
    values=[total,read,created,new,output]
    if any(v is not None and (type(v) is not int or v<0) for v in values):
        return {'status':'invalid'}
    return {'status':'reported' if ok else 'reported_failed','total_input_tokens':total,
            'cache_read_tokens':read,'cache_creation_tokens':created,'new_input_tokens':new,
            'new_input_basis':'total_input_tokens - cache_read_tokens',
            'output_tokens':output,'observed_models':models,'billing_verified':False,
            'raw_usage':u}


def codex_turn_delta(usage, previous=None):
    """CLI0.161.0 resume reports session totals (verified against own rollout).

    Never subtract a fresh-session record or infer a reset as a cheap turn.
    Keep the original aggregate and reject nonmonotonic counters.
    """
    result=dict(usage);result['scope']='session total' if previous is None else 'difference of same-session totals'
    if previous is None:return result
    result['reported_session_total']=dict(usage)
    for key in ('total_input_tokens','cache_read_tokens','new_input_tokens','output_tokens'):
        now,before=usage.get(key),previous.get(key)
        if type(now) is not int or type(before) is not int or now<before:
            result['status']='counter_scope_unverified'
            for name in ('total_input_tokens','cache_read_tokens','new_input_tokens','output_tokens'):result[name]=None
            return result
        result[key]=now-before
    return result


def run_agent(provider, prompt, directory, timeout=240, *, resume=None, workspace=None, retain=False, model=None, reasoning_effort=None, profile=False):
    from tacto.provider_io import communicate_owned_provider, require_provider_child_ownership
    directory=Path(directory);directory.mkdir(mode=0o700)
    temporary=None if workspace else tempfile.TemporaryDirectory(prefix='tacto-repeat-agent-')
    work=Path(workspace) if workspace else Path(temporary.name)
    work.mkdir(mode=0o700,exist_ok=True)
    env=os.environ.copy()
    for key in ('TACTO_HELPER_FD','TACTO_HELPER_TOKEN','TACTO_LAUNCHER'):
        env.pop(key,None)
    if provider=='codex':
        if resume:
            argv=[str(Path.home()/'.local/bin/codex'),'exec','resume',resume,'--skip-git-repo-check','--json','-']
        else:
            argv=[str(Path.home()/'.local/bin/codex'),'exec','--skip-git-repo-check',
                  '--sandbox','danger-full-access','-C',str(work),'--json','-']
            if not retain:argv.insert(2,'--ephemeral')
    elif provider=='claude':
        argv=[str(Path.home()/'.local/bin/claude'),'--print','--verbose','--output-format','stream-json',
              '--permission-mode','dontAsk',
              '--allowedTools','Bash,Read,Write,Edit','--strict-mcp-config',
              '--mcp-config','{"mcpServers":{}}']
        if resume:argv.extend(['--resume',resume])
        elif not retain:argv.append('--no-session-persistence')
    else:raise ValueError('unknown agent provider')
    if model is not None or reasoning_effort is not None:
        if provider!='codex':raise ValueError('fixed model settings supported for codex only')
        if model is not None:
            if not isinstance(model,str) or not model.strip():raise ValueError('invalid model')
            argv[-1:-1]=['--model',model]
        if reasoning_effort is not None:
            if reasoning_effort not in ('low','medium','high','xhigh'):raise ValueError('invalid reasoning effort')
            argv[-1:-1]=['-c','model_reasoning_effort='+json.dumps(reasoning_effort)]
    start=time.monotonic();error=None;stdout=stderr=''
    timeline=None
    if profile:
        from examples.agent_timeline import AgentTimeline
        timeline=AgentTimeline(start)
    try:
        require_provider_child_ownership()
        with subprocess.Popen(argv,cwd=work,env=env,stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,start_new_session=True) as p:
            try:stdout,stderr=communicate_owned_provider(p,input=prompt,timeout=timeout,
                **({'stdout_observer':timeline.feed} if timeline else {}))
            except subprocess.TimeoutExpired as exc:
                stdout=exc.stdout or '';stderr=exc.stderr or '';error='timeout'
            code=p.returncode
    except OSError as exc:code=None;error=type(exc).__name__
    for name,data in [('stdout.jsonl',stdout),('stderr.txt',stderr),('prompt.txt',prompt)]:
        if isinstance(data,bytes):data=data.decode(errors='replace')
        path=directory/name;path.write_text(data);path.chmod(0o600)
    try:usage=usage_from_stream(provider,stdout)
    except (ValueError,TypeError):usage={'status':'malformed_stream'}
    identity=None
    try:
        for event in map(json.loads,stdout.splitlines()):
            if event.get('type')=='thread.started':identity=event.get('thread_id')
            if event.get('type')=='system' and event.get('subtype')=='init':identity=event.get('session_id')
    except (ValueError,TypeError):pass
    result={'provider':provider,'seconds':time.monotonic()-start,'exit':code,'error':error,
            'usage':usage,'trace_directory':str(directory),'fresh_workspace':not bool(resume),
            'history_supplied':bool(resume),'session_id':identity,'requested_model':model or 'CLI default','requested_reasoning_effort':reasoning_effort,
            'environment_parity_verified':False}
    if timeline is not None:
        result['profile']=timeline.finish(result['seconds'],stdout)
    path=directory/'agent.json';path.write_text(json.dumps(result,indent=2));path.chmod(0o600)
    if temporary:temporary.cleanup()
    return result
