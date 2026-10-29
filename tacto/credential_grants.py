"""Host-owned reusable login delegation. No credential values are stored here."""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from .credential_vault import CredentialError, private_read
from .storage import write_private

_NAME=re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}$')
_BUNDLE=re.compile(r'^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$')


def origin(url,*,observed=False):
    try:
        if not isinstance(url,str) or any(ord(c)<33 or c=='\\' for c in url):raise ValueError()
        from ada_url import URL
        u=URL(url)
        host=u.hostname
        import ipaddress
        loopback=host=='localhost'
        try:loopback=loopback or ipaddress.ip_address(host.strip('[]')).is_loopback
        except ValueError:pass
        if (u.protocol!='https:' and not (u.protocol=='http:' and loopback)
                or u.username or u.password or not host):raise ValueError()
        if not observed:
            parts=urlsplit(url)
            if parts.path not in ('','/') or parts.query or parts.fragment:raise ValueError()
        return u.origin
    except Exception:raise CredentialError('credential_origin_invalid') from None


def selector(value):
    from .flow import Selector
    if not isinstance(value,dict) or set(value)-{'role','identifier','title','desc','subrole','ancestor'}:
        raise CredentialError('credential_selector_invalid')
    if not isinstance(value.get('role'),str) or not any(value.get(k) for k in ('identifier','title','desc')):
        raise CredentialError('credential_selector_invalid')
    if any(not isinstance(v,str) or not v or len(v)>2048 for v in value.values()):
        raise CredentialError('credential_selector_invalid')
    return Selector(**value)


def normalize_plan(value):
    try:
        if not isinstance(value,dict) or set(value)-{'target','origin','level','fields','submit','success','success_origin','minutes','stages','account','verify_seconds'}:raise ValueError()
        if 'stages' in value:
            rows=value['stages']
            if not isinstance(rows,list) or not 1<=len(rows)<=8 or value.get('level','login')!='login':raise ValueError()
            stages=[]
            for row in rows:
                if not isinstance(row,dict) or set(row)-{'fields','submit','success','origin','success_origin','target','level','minutes','verify_seconds'}:raise ValueError()
                fixed={'target':value['target'],'level':'login','minutes':value.get('minutes',0),'verify_seconds':value.get('verify_seconds',10)}
                if any(k in row and row[k]!=v for k,v in fixed.items()):raise ValueError()
                common={k:value[k] for k in ('target','origin','minutes','verify_seconds') if k in value}
                stages.append(normalize_plan({**common,**row,'level':'login'}))
            result={**stages[-1],'origin':stages[0]['origin'],'fields':[f for row in stages for f in row['fields']],'stages':stages}
            if 'fields' in value and value['fields']!=result['fields']:raise ValueError()
            if 'account' in value:result['account']=normalize_account(value['account'])
            return result
        target=value['target'];level=value.get('level','login')
        if not isinstance(target,str) or not (target=='page' or _BUNDLE.fullmatch(target)) or level not in ('fill','login'):raise ValueError()
        website=origin(value['origin']) if value.get('origin') else None
        if target=='page' and website is None:raise ValueError()
        minutes=value.get('minutes',0)
        if type(minutes) not in (int,float) or not math.isfinite(minutes) or minutes<0:raise ValueError()
        seconds=value.get('verify_seconds',10)
        if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=120:raise ValueError()
        fields=value['fields']
        if not isinstance(fields,list) or not 1<=len(fields)<=16:raise ValueError()
        seen=set()
        for f in fields:
            if not isinstance(f,dict) or (not {'name','selector','secret'} <= set(f) or set(f)-{'name','selector','secret','input_method'}) or not _NAME.fullmatch(f['name']) or f['name'] in seen:raise ValueError()
            if 'input_method' in f and f['input_method']!='paste':raise ValueError()
            seen.add(f['name']);sel=selector(f['selector'])
            if sel.role not in ('TextField','TextArea','SearchField','ComboBox','textbox'):raise ValueError()
            ref=f['secret']
            if (not isinstance(ref,dict) or set(ref)!={'vault','item','field'}
                    or not _NAME.fullmatch(ref['vault']) or any(not isinstance(ref[k],str) or not ref[k] or len(ref[k])>512 for k in ('item','field'))):raise ValueError()
        if level=='login':
            sel=selector(value['submit'])
            if sel.role not in ('Button','button','Link','link'):raise ValueError()
            success=value['success']
            if not isinstance(success,dict) or set(success)!={'field','attribute','equals'} or success['attribute'] not in ('Title','Value','Description'):raise ValueError()
            selector(success['field'])
            if not isinstance(success['equals'],str) or not success['equals'] or len(success['equals'])>2048:raise ValueError()
        elif 'submit' in value or 'success' in value:raise ValueError()
        result=json.loads(json.dumps(value));result.update(target=target,level=level,origin=website,minutes=minutes)
        result['verify_seconds']=seconds
        result['success_origin']=origin(value.get('success_origin') or website) if website else None
        if 'account' in value:result['account']=normalize_account(value['account'])
        return result
    except CredentialError:raise
    except Exception:raise CredentialError('credential_plan_invalid') from None


def normalize_account(value):
    if (not isinstance(value,dict) or set(value)!={'field','attribute','secret'}
            or value['attribute'] not in ('Title','Value','Description')):
        raise CredentialError('credential_plan_invalid')
    selector(value['field']);ref=value['secret']
    if (not isinstance(ref,dict) or set(ref)!={'vault','item','field'}
            or not isinstance(ref.get('vault'),str) or not _NAME.fullmatch(ref['vault'])
            or any(not isinstance(ref[k],str) or not ref[k] or len(ref[k])>512 for k in ('item','field'))):
        raise CredentialError('credential_plan_invalid')
    return json.loads(json.dumps(value))


def vault_names(plan):
    return {f['secret']['vault'] for f in plan['fields']} | ({plan['account']['secret']['vault']} if plan.get('account') else set())


class CredentialStore:
    def __init__(self,root=None):
        if root is None:
            from .access_log import log_directory
            root=log_directory().parent/'Credentials'
        self.root=Path(root)
        from .access_log import private_directory
        private_directory(self.root)
    def _path(self,kind,name):
        if not isinstance(name,str) or not _NAME.fullmatch(name):raise CredentialError('credential_name_invalid')
        return self.root/(kind+'-'+name+'.json')
    def _write(self,kind,name,data):
        write_private(str(self._path(kind,name)),json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    def _read(self,kind,name):
        try:return json.loads(private_read(self._path(kind,name)))
        except CredentialError:raise
        except Exception:raise CredentialError('credential_registration_invalid') from None
    def add_vault(self,name,config):
        if not isinstance(config,dict) or config.get('kind') not in ('keychain','bitwarden'):
            raise CredentialError('vault_provider_invalid')
        if config['kind']=='bitwarden':
            if set(config)-{'kind','server','executable','session_file'}:raise CredentialError('vault_provider_invalid')
            config=dict(config);config['server']=origin(config['server'])
            for k in ('executable','session_file'):
                if k in config and (not isinstance(config[k],str) or not os.path.isabs(config[k])):raise CredentialError('vault_provider_invalid')
        elif set(config)!={'kind'}:raise CredentialError('vault_provider_invalid')
        self._write('vault',name,config)
    def grant(self,name,plan,*,now=None):
        plan=normalize_plan(plan);configs={name:self._read('vault',name) for name in vault_names(plan)}
        now=time.time() if now is None else now
        payload={'schema':1,'plan':plan,'created':now,'expires':now+plan['minutes']*60 if plan['minutes'] else None,
                 'vault_digest':hashlib.sha256(json.dumps(configs,sort_keys=True).encode()).hexdigest()}
        self._write('grant',name,payload)
    def load(self,name,*,now=None):
        value=self._read('grant',name)
        if value.get('schema')!=1:raise CredentialError('credential_registration_invalid')
        plan=normalize_plan(value['plan']);now=time.time() if now is None else now
        if value['expires'] is not None and now>=value['expires']:raise CredentialError('credential_grant_expired')
        configs={name:self._read('vault',name) for name in vault_names(plan)}
        if value['vault_digest']!=hashlib.sha256(json.dumps(configs,sort_keys=True).encode()).hexdigest():raise CredentialError('credential_vault_changed')
        return plan,configs
    def revoke(self,name):
        try:self._path('grant',name).unlink()
        except FileNotFoundError:pass
    def grants(self):
        return [p.name[6:-5] for p in sorted(self.root.glob('grant-*.json')) if not p.is_symlink()]
