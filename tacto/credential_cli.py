"""Small stage-specific CLI for credential stores and reusable login delegation."""
import argparse
import getpass
import hashlib
import json
import sys
from pathlib import Path
from .credential_grants import CredentialStore
from .credential_vault import CredentialError, KeychainVault

USAGE='''tacto auth vault <name> --keychain | --bitwarden SERVER [--session-file PATH] [--executable PATH]
tacto auth put <item>                 # hidden terminal input, or pipe value on stdin into Keychain
tacto auth inspect <plan.json>         # review destination/level, never credential values
tacto auth grant <name> <plan.json> --yes [--fill-only]
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
        if command=='list' and args in ([],['--vaults']):
            names=store.grants();scopes={}
            for name in names:
                try:
                    plan,_=store.load(name)
                    scopes[name]={k:plan[k] for k in ('target','origin','level')}
                    scopes[name].update(stages=len(plan.get('stages') or [plan]),account_check=bool(plan.get('account')))
                except CredentialError as e:scopes[name]={'error':e.code}
            result={'grants':names,'scopes':scopes}
            if args:
                providers={}
                for path in sorted(store.root.glob('vault-*.json')):
                    name=path.name[6:-5]
                    try:
                        config=store._read('vault',name)
                        providers[name]={k:config[k] for k in ('kind','server','executable','session_file') if k in config and isinstance(config[k],str)}
                    except CredentialError as e:providers[name]={'error':e.code}
                result['vaults']=providers
            print(json.dumps(result));return 0
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
            if not v.keychain and not v.executable:
                from .credential_vault import find_bitwarden
                executable=find_bitwarden()
                if executable:config['executable']=executable
            if v.session_file:config['session_file']=v.session_file
            if v.executable:config['executable']=v.executable
            store.add_vault(v.name,config);print(json.dumps({'status':'vault_registered','vault':v.name}));return 0
        if command in ('grant','inspect'):
            expected_digest=None
            if '--review-digest' in args:
                pos=args.index('--review-digest')
                if pos+1>=len(args):raise CredentialError('credential_arguments_invalid')
                expected_digest=args[pos+1];args=args[:pos]+args[pos+2:]
            yes='--yes' in args;fill='--fill-only' in args;args=[a for a in args if a not in ('--yes','--fill-only')]
            if len(args)!=(2 if command=='grant' else 1):raise CredentialError('credential_arguments_invalid')
            from .credential_grants import normalize_plan
            plan=normalize_plan(json.loads(Path(args[-1]).read_text()))
            digest=hashlib.sha256(json.dumps(plan,sort_keys=True).encode()).hexdigest()
            if expected_digest is not None and expected_digest!=digest:raise CredentialError('credential_plan_changed')
            if fill:
                first=(plan.get('stages') or [plan])[0]
                plan=normalize_plan({k:v for k,v in first.items() if k in ('target','origin','fields','minutes','verify_seconds')} | {'level':'fill'})
            summary={k:plan[k] for k in ('target','origin','level','minutes')};summary['fields']=[f['name'] for f in plan['fields']]
            summary['stages']=len(plan.get('stages') or [plan]);summary['account_check']=bool(plan.get('account'))
            summary['review_digest']=digest
            if command=='inspect':print(json.dumps(summary,ensure_ascii=False));return 0
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
        from .credential_diagnostics import recovery
        print(json.dumps({'ok':False,'error':e.code,'stage':stage,'effect':e.effect,'repair':recovery(e.code)},ensure_ascii=False));return 1
    except (Exception,SystemExit):
        print(json.dumps({'ok':False,'error':'credential_configuration_invalid','stage':stage,'effect':'not_started'}));return 2
