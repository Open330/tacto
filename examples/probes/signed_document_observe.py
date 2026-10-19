"""Installed CLI scope against two owned documents; no model or user edits."""
import os
from pathlib import Path
import secrets
import subprocess
import tempfile

from signed_owned_textedit import SignedOwnedTextEdit,TARGET


def run(driver,*,can_cleanup,escaped_name=False,run_read=False):
    from focus_witness import FocusWitness
    nonce=secrets.token_hex(8)
    wanted='NLC owned selected '+nonce;decoy='NLC owned unselected '+nonce
    # Same basename in different private directories: titles cannot select it.
    name='NLC scoped '+nonce+(' [focused], Title: "quoted"' if escaped_name else '')+'.txt'
    fixtures=[SignedOwnedTextEdit(driver,name),SignedOwnedTextEdit(driver,name)]
    report={'passed':False,'scope':'installed_signed_cli_replay_two_owned_documents' if run_read else 'installed_signed_cli_observation_two_owned_documents',
            'model_requests':0,'paths':[],'cleanup_statuses':[]}
    with FocusWitness() as witness:
        try:
            if witness.initial==TARGET:raise RuntimeError('target_initially_frontmost')
            for fixture,text in zip(fixtures,[wanted,decoy]):
                fixture.setup();report['paths'].append(str(fixture.path))
                if not fixture.write(text):raise RuntimeError('owned_document_write_unverified')
            executable=Path.home()/'.local/bin/tacto'
            env={key:value for key,value in os.environ.items()
                 if key not in ('TACTO_HELPER_FD','TACTO_HELPER_TOKEN','TACTO_LAUNCHER')}
            with tempfile.TemporaryDirectory(prefix='nlc-scoped-replay-') as flow_dir:
                if run_read:
                    from tacto.flow import Flow,Step,Capture,Selector
                    from tacto.contract import Envelope
                    field=Selector(role='TextArea',identifier='First Text View',ancestor=str(fixtures[0].path))
                    flow=Flow('owned_document_read',TARGET,[Step('read',
                        safety=Envelope.minimal().contract_for({'read'},'read selected owned document'),
                        captures=[Capture('body',field=field)])],schema=2)
                    flow_path=Path(flow_dir)/'read.json'
                    fd=os.open(flow_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w') as stream:stream.write(flow.to_json())
                    command=[str(executable),'run',str(flow_path),'--helper','--document',str(fixtures[0].path),'--no-indicator']
                else:
                    command=[str(executable),'observe',TARGET,'--helper','--document',str(fixtures[0].path)]
                result=subprocess.run(command,env=env,close_fds=True,capture_output=True,text=True,timeout=25)
            report['cli_exit']=result.returncode
            report['selected_body_seen']=wanted in result.stdout
            report['other_owned_body_seen']=decoy in result.stdout
            from tacto.document_label import document_attribute
            paths=[document_attribute(line) for line in result.stdout.splitlines()]
            report['document_path_in_tree_matches']=str(fixtures[0].path) in paths
            report['document_path_json_quoted']='Document: "' in result.stdout
            if run_read:
                report['owned_bodies_unchanged']=[fixture.read()==body
                    for fixture,body in zip(fixtures,[wanted,decoy])]
                report['passed']=result.returncode==0 and wanted in result.stdout and decoy not in result.stdout and all(report['owned_bodies_unchanged'])
            else:
                report['passed']=result.returncode==0 and wanted in result.stdout and decoy not in result.stdout and report['document_path_in_tree_matches'] and report['document_path_json_quoted']
        except Exception as error:
            report['failure_class']=type(error).__name__
        finally:
            for fixture in reversed(fixtures):
                status=fixture.cleanup() if fixture.token is not None and can_cleanup() else 'not_cleaned_left_as_is'
                report['cleanup_statuses'].append(status)
            report['artifacts_removed']=[fixture.directory is not None and not fixture.directory.exists() for fixture in fixtures]
    report['focus']=witness.summary()
    report['closed_observed']=[fixture.closed_observed for fixture in fixtures]
    report['passed']=report['passed'] and all(report['artifacts_removed']) and all(report['closed_observed']) and all(status=='closed' for status in report['cleanup_statuses']) and not report['focus']['activations']
    return report
