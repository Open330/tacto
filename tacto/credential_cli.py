"""Small stage-specific CLI for credential stores and reusable login delegation."""
import argparse
import getpass
import json
import sys
from pathlib import Path
from .credential_grants import CredentialStore
from .credential_vault import CredentialError, KeychainVault

USAGE='''tacto auth vault <name> --keychain | --bitwarden SERVER [--session-file PATH] [--executable PATH]
tacto auth put <item>                 # hidden terminal input, or pipe value on stdin into Keychain
tacto auth grant <name> <plan.json> --yes
tacto auth fill <name> --helper       # stored fill/login delegation, zero model calls
tacto auth login <name> --helper      # stored login delegation, verifies the result
tacto auth list | revoke <name>'''


def main(argv,*,driver_factory,store=None,confirm=None):
    stage='config'
    try:
        store=store or CredentialStore()
        if not argv or argv==['--help']:
            print(USAGE);return 0
        command,*args=argv
        if command=='list' and not args:
            names=store.grants();scopes={}
            for name in names:
                try:
                    plan,_=store.load(name)
                    scopes[name]={k:plan[k] for k in ('target','origin','level')}
                except CredentialError as e:scopes[name]={'error':e.code}
            print(json.dumps({'grants':names,'scopes':scopes}));return 0
        if command=='revoke' and len(args)==1:
            store.revoke(args[0]);print(json.dumps({'status':'credential_grant_revoked'}));return 0
        if command=='put' and len(args)==1:
            # No password argument, environment variable, output or plain file.
            value=getpass.getpass('Credential: ') if sys.stdin.isatty() else sys.stdin.read(16385).removesuffix('\n')
            if not value or len(value.encode())>16384:raise CredentialError('vault_value_invalid')
            KeychainVault().put(args[0],value);value=None
            print(json.dumps({'status':'credential_stored','item':args[0]}));return 0
        if command=='vault':
            class Parser(argparse.ArgumentParser):
                def error(self,message):raise CredentialError('credential_arguments_invalid')
            p=Parser(add_help=False);p.add_argument('name');g=p.add_mutually_exclusive_group(required=True);g.add_argument('--keychain',action='store_true');g.add_argument('--bitwarden');p.add_argument('--session-file');p.add_argument('--executable');v=p.parse_args(args)
            config={'kind':'keychain'} if v.keychain else {'kind':'bitwarden','server':v.bitwarden}
            if v.session_file:config['session_file']=v.session_file
            if v.executable:config['executable']=v.executable
            store.add_vault(v.name,config);print(json.dumps({'status':'vault_registered','vault':v.name}));return 0
        if command=='grant':
            yes='--yes' in args;args=[a for a in args if a!='--yes']
            if len(args)!=2:raise CredentialError('credential_arguments_invalid')
            from .credential_grants import normalize_plan
            plan=normalize_plan(json.loads(Path(args[1]).read_text()))
            summary={k:plan[k] for k in ('target','origin','level','minutes')};summary['fields']=[f['name'] for f in plan['fields']];summary['submit']=plan.get('submit');summary['success']=plan.get('success')
            allowed=yes or (confirm(summary) if confirm else sys.stdin.isatty() and input(json.dumps(summary,ensure_ascii=False)+'\n위임할까요? [y/N] ').strip().lower() in ('y','yes'))
            if not allowed:raise CredentialError('credential_delegation_not_granted')
            store.grant(args[0],plan);print(json.dumps({'status':'credential_delegated','grant':args[0],'level':plan['level']}));return 0
        if command in ('fill','login'):
            stage='execute'
            if len(args)!=2 or args[1]!='--helper':raise CredentialError('credential_arguments_invalid')
            plan,_=store.load(args[0])
            if plan['target']=='page':raise CredentialError('credential_browser_host_required')
            from .credential_runtime import run_login
            result=run_login(store,args[0],driver_factory(['--helper']),fill_only=command=='fill')
            from .access_log import current
            if current.get() is not None:current.get().task_result(result)
            print(json.dumps(result,ensure_ascii=False));return 0 if result['ok'] else 1
        raise CredentialError('credential_arguments_invalid')
    except CredentialError as e:
        print(json.dumps({'ok':False,'error':e.code,'stage':stage,'effect':e.effect}));return 1
    except (Exception,SystemExit):
        print(json.dumps({'ok':False,'error':'credential_configuration_invalid','stage':stage,'effect':'not_started'}));return 2
