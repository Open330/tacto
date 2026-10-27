"""Credential values stay in an OS/authenticated vault, never in flow JSON."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


_PUBLIC_ERRORS=frozenset(['credential_plan_changed', 'credential_account_mismatch', 'credential_arguments_invalid', 'credential_browser_host_required', 'credential_delegation_not_granted', 'credential_delegation_required', 'credential_driver_upgrade_required', 'credential_execution_failed', 'credential_file_permissions', 'credential_file_too_large', 'credential_file_unavailable', 'credential_fill_failed', 'credential_grant_expired', 'credential_level_requires_login', 'credential_login_unverified', 'credential_name_invalid', 'credential_observation_unavailable', 'credential_origin_invalid', 'credential_plan_invalid', 'credential_registration_invalid', 'credential_scope_mismatch', 'credential_selector_invalid', 'credential_submit_outside_scope', 'credential_submit_unverified', 'credential_target_missing', 'credential_vault_changed', 'keychain_delete_failed', 'keychain_field_invalid', 'keychain_item_unavailable', 'keychain_requires_macos', 'keychain_write_failed', 'vault_command_failed', 'vault_field_unavailable', 'vault_item_invalid', 'vault_item_mismatch', 'vault_locked', 'vault_provider_invalid', 'vault_provider_unknown', 'vault_server_mismatch', 'vault_status_invalid', 'vault_unavailable', 'vault_value_invalid', 'vault_value_not_text'])


class CredentialError(Exception):
    def __init__(self, code, *, effect='not_started'):
        self.code=code if code in _PUBLIC_ERRORS else 'credential_execution_failed'
        self.effect=effect if effect in ('not_started','unknown') else 'unknown'
        super().__init__(self.code)


def find_bitwarden():
    """Resolve installed CLI locations without executing or changing a session."""
    import shutil
    found=shutil.which('bw')
    if found:return found
    candidates=[Path('/opt/homebrew/bin/bw'),Path('/usr/local/bin/bw'),Path.home()/'.local/bin/bw']
    def version(path):
        try:return tuple(int(v) for v in path.parts[-3].lstrip('v').split('.'))
        except ValueError:return ()
    candidates+=sorted((Path.home()/'.nvm/versions/node').glob('*/bin/bw'),key=version,reverse=True)
    return next((str(p) for p in candidates if p.is_file() and os.access(p,os.X_OK)),None)


def private_read(path, maximum=1024*1024):
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd,'rb') as f:
            st=os.fstat(f.fileno())
            if (not stat.S_ISREG(st.st_mode) or st.st_mode & 0o077
                    or hasattr(os,'getuid') and st.st_uid!=os.getuid() or st.st_size>maximum):
                raise CredentialError('credential_file_permissions')
            data=f.read(maximum+1)
            if len(data)>maximum:raise CredentialError('credential_file_too_large')
            return data.decode('utf-8')
    except CredentialError:raise
    except (OSError,UnicodeError):raise CredentialError('credential_file_unavailable') from None


class KeychainVault:
    service=b'ai.callabo.tacto.credentials'
    def __init__(self):
        if sys.platform!='darwin':raise CredentialError('keychain_requires_macos')
        self.api=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
        self.cf=ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        P=ctypes.c_void_p;U=ctypes.c_uint32;C=ctypes.c_char_p
        self.api.SecKeychainFindGenericPassword.argtypes=[P,U,C,U,C,ctypes.POINTER(U),ctypes.POINTER(P),ctypes.POINTER(P)]
        self.api.SecKeychainAddGenericPassword.argtypes=[P,U,C,U,C,U,P,ctypes.POINTER(P)]
        self.api.SecKeychainItemModifyAttributesAndData.argtypes=[P,P,U,P]
        self.api.SecKeychainItemFreeContent.argtypes=[P,P]
        self.api.SecKeychainItemDelete.argtypes=[P]
        self.cf.CFRelease.argtypes=[P]
    def _find(self,item):
        key=item.encode();length=ctypes.c_uint32();data=ctypes.c_void_p();ref=ctypes.c_void_p()
        status=self.api.SecKeychainFindGenericPassword(None,len(self.service),self.service,len(key),key,
            ctypes.byref(length),ctypes.byref(data),ctypes.byref(ref))
        if status:raise CredentialError('keychain_item_unavailable')
        try:return ctypes.string_at(data,length.value).decode('utf-8'),ref
        except UnicodeError:
            self.cf.CFRelease(ref);raise CredentialError('vault_value_not_text') from None
        finally:self.api.SecKeychainItemFreeContent(None,data)
    def get(self,item,field='password'):
        if field!='password':raise CredentialError('keychain_field_invalid')
        value,ref=self._find(item)
        try:return value
        finally:self.cf.CFRelease(ref)
    def put(self,item,value):
        key=item.encode();raw=value.encode();buffer=ctypes.create_string_buffer(raw)
        status=self.api.SecKeychainAddGenericPassword(None,len(self.service),self.service,len(key),key,len(raw),buffer,None)
        if status==-25299:
            _,ref=self._find(item)
            try:status=self.api.SecKeychainItemModifyAttributesAndData(ref,None,len(raw),buffer)
            finally:self.cf.CFRelease(ref)
        if status:raise CredentialError('keychain_write_failed')
    def delete(self,item):
        _,ref=self._find(item)
        try:
            if self.api.SecKeychainItemDelete(ref):raise CredentialError('keychain_delete_failed')
        finally:self.cf.CFRelease(ref)


class BitwardenVault:
    """Exact server, existing unlocked session; values captured only by this consumer."""
    def __init__(self,config,*,run=subprocess.run):
        self.config,self.run=config,run
        self._authenticated=False;self._items={}
    def _call(self,args):
        env=dict(os.environ)
        executable=self.config.get('executable','bw')
        if os.path.isabs(executable):
            # npm's bw uses /usr/bin/env node. Its registered installation
            # directory supplies node even under the launcher's fixed PATH.
            env['PATH']=str(Path(executable).parent)+os.pathsep+os.defpath
        if self.config.get('session_file'):
            env['BW_SESSION']=private_read(self.config['session_file'],65536).strip()
        try:
            r=self.run([executable,*args],capture_output=True,text=True,
                       env=env,timeout=30)
            if r.returncode:raise CredentialError('vault_command_failed')
            return r.stdout
        except CredentialError:raise
        except Exception:raise CredentialError('vault_unavailable') from None
    def authenticate(self):
        if self._authenticated:return
        try:status=json.loads(self._call(['status']))
        except CredentialError:raise
        except Exception:raise CredentialError('vault_status_invalid') from None
        server=status.get('serverUrl') or self._call(['config','server']).strip()
        if server.rstrip('/')!=self.config['server'].rstrip('/'):
            raise CredentialError('vault_server_mismatch')
        if status.get('status')!='unlocked':raise CredentialError('vault_locked')
        self._authenticated=True
    def get(self,item,field):
        self.authenticate()
        try:
            if item not in self._items:self._items[item]=json.loads(self._call(['get','item',item]))
            data=self._items[item]
            if data.get('id')!=item:raise CredentialError('vault_item_mismatch')
            if field in ('login.username','login.password'):
                value=(data.get('login') or {}).get(field.split('.')[1])
            else:
                values=[r.get('value') for r in data.get('fields',[]) if r.get('name')==field]
                if len(values)!=1:raise CredentialError('vault_field_unavailable')
                value=values[0]
            if not isinstance(value,str) or not value:raise CredentialError('vault_field_unavailable')
            return value
        except CredentialError:raise
        except Exception:raise CredentialError('vault_item_invalid') from None


def vault_from(config):
    if config['kind']=='keychain':return KeychainVault()
    if config['kind']=='bitwarden':return BitwardenVault(config)
    raise CredentialError('vault_provider_unknown')
