"""Actual owned TextEdit preparation/Save; saved state is not writer attribution."""
import secrets
from copy import deepcopy

from signed_owned_textedit import SignedOwnedTextEdit, TARGET


def run(driver,*,can_cleanup,scoped_save=False,cli_save=False):
    from focus_witness import FocusWitness
    from tacto.commit_evidence import make_event,evidence_error
    from tacto.existing_file_receipt import ExistingTextFileReceipt,SAVE_PATH,_file_state
    report={'scope':'installed_cli_scoped_textedit_save_and_saved_state' if cli_save else 'owned_textedit_scoped_save_dispatch_and_saved_state' if scoped_save else 'owned_textedit_in_place_save_dispatch_and_saved_state',
            'passed':False,'model_requests':0,'producer_attribution_verified':False}
    fixture=SignedOwnedTextEdit(driver,'Tacto 저장 검증 '+secrets.token_hex(8)+'.txt')
    body='Tacto 실제 저장 검증 '+secrets.token_hex(16)+'\n두 번째 줄\n'
    with FocusWitness() as witness:
        try:
            if witness.initial==TARGET:raise RuntimeError('target_initially_frontmost')
            fixture.setup()
            report['owned_directory']=str(fixture.directory)
            report['owned_directory_identity']=fixture.directory_identity
            if not can_cleanup():raise RuntimeError('environment_changed_before_write')
            if not fixture.write(body):raise RuntimeError('owned_text_readback_failed')
            report['exact_document_text']=True
            menu_driver=driver
            if scoped_save:
                from tacto.document_scope import DocumentScopedDriver
                driver=DocumentScopedDriver(driver,TARGET,str(fixture.path))
            report['disk_matched_before_prepare']=_file_state(str(fixture.path))[5]==body.encode()
            report['menu_enabled_before_prepare']=menu_driver.menu_state(TARGET,SAVE_PATH).get('enabled')
            if report['menu_enabled_before_prepare'] is not True:
                obs=(driver.observe(TARGET) if scoped_save else driver.observe(TARGET,document_token=fixture.token))
                if obs.get('type')!='observation' or (not scoped_save and obs.get('document_token')!=fixture.token) or obs.get('truncated') is not False:
                    raise RuntimeError('scoped_window_unavailable')
                windows=[int(line.split()[0]) for line in obs['tree'].splitlines()
                         if len(line.split())>1 and line.split()[1]=='Window']
                if len(windows)!=1:raise RuntimeError('scoped_window_unavailable')
                preparation=driver.act(obs['obs_id'],TARGET,{'name':'prepare_window','element_index':windows[0]},expect={'role':'Window'})
                report['preparation_type']=preparation.get('type')
                report['preparation_effect']=preparation.get('effect')
                from tacto.preparation_diagnostic import preparation_diagnostic
                diagnostic=preparation_diagnostic(preparation.get('preparation_diagnostic'))
                if diagnostic is not None:report['preparation_diagnostic']=diagnostic
                if preparation.get('type')!='acted':raise RuntimeError('window_preparation_unverified')
            else:report['preparation_type']='not_needed'
            # Preparation is only dispatched, not assumed to have enabled Save.
            if report['preparation_type']=='acted':
                from tacto.menu_readiness import wait_menu_enabled
                readiness=wait_menu_enabled(menu_driver,TARGET,SAVE_PATH,timeout_seconds=1,
                    cancelled=lambda:not can_cleanup())
                report['menu_readiness']=readiness.to_dict()
                if readiness.state!='ready':
                    raise RuntimeError({'timeout':'save_menu_read_timeout',
                        'unavailable':'save_menu_read_unavailable','stopped':'save_menu_read_stopped'}[readiness.state])
                state={'type':'menu_state','enabled':True}
            else:state=menu_driver.menu_state(TARGET,SAVE_PATH)
            report['menu_enabled_after_prepare']=state.get('enabled')
            if state.get('type')!='menu_state' or state.get('enabled') is not True:
                raise RuntimeError('save_menu_not_enabled_after_prepare')
            if cli_save:
                import os,subprocess
                from pathlib import Path
                env={key:value for key,value in os.environ.items()
                     if key not in ('TACTO_HELPER_FD','TACTO_HELPER_TOKEN','TACTO_LAUNCHER')}
                report['disk_matched_before_save']=_file_state(str(fixture.path))[5]==body.encode()
                result=subprocess.run([str(Path.home()/'.local/bin/tacto'),'replay','textedit_save',
                    '-p','document='+str(fixture.path),'-p','name='+fixture.name,
                    '--helper','--document',str(fixture.path),'--yes','--no-indicator'],
                    env=env,close_fds=True,capture_output=True,text=True,timeout=25)
                report['cli_exit']=result.returncode
                report['cli_reported_done']=any(line=='done' or line.startswith('done —') for line in result.stdout.splitlines())
                report['exact_saved_text']=_file_state(str(fixture.path))[5]==body.encode()
                report['exact_document_text_after_save']=fixture.read()==body
                report['passed']=result.returncode==0 and report['cli_reported_done'] and report['exact_saved_text'] and report['exact_document_text_after_save']
            else:
                # Scoped Save retains the native document token. Both modes also
                # require this exact file in focused/main windows at the press.
                obs=driver.observe(TARGET)
                if obs.get('type')!='observation':raise RuntimeError('save_observation_unavailable')
                action={'name':'menu','path':SAVE_PATH,'focused_document':str(fixture.path)}
                event=make_event(action,['write_persistent'],TARGET,obs_id=obs['obs_id'])
                receipt=ExistingTextFileReceipt(driver,document_path=str(fixture.path),timeout_seconds=5)
                context=receipt.prepare_commit(event)
                report['disk_matched_before_save']=_file_state(str(fixture.path))[5]==body.encode()
                if not can_cleanup():raise RuntimeError('environment_changed_before_save')
                response=driver.act(obs['obs_id'],TARGET,action,risk_class='preapproved',approved=True,
                    intent_text='Save only the evaluator-created temporary TextEdit document')
                report['save_dispatch_type']=response.get('type');report['save_dispatch_effect']=response.get('effect')
                if type(response.get('activated_target')) is bool:
                    report['save_observed_activation']=response['activated_target']
                event['commit_evidence']=receipt.after_commit(event,response,context)
                report['evidence_error']=evidence_error(event,receipt.commit_expected)
                report['exact_saved_text']=_file_state(str(fixture.path))[5]==body.encode()
                report['passed']=response.get('type')=='acted' and report['evidence_error'] is None and report['exact_saved_text']
        except Exception as error:
            report['failure_class']=type(error).__name__
            codes={'target_initially_frontmost','owned_text_readback_failed','scoped_window_unavailable',
                   'window_preparation_unverified','save_menu_not_enabled_after_prepare','save_observation_unavailable',
                   'save_menu_read_timeout','save_menu_read_unavailable','save_menu_read_stopped',
                   'environment_changed_before_write','environment_changed_before_save'}
            report['failure_code']=getattr(error,'public_code',None) or (str(error) if str(error) in codes else 'probe_unavailable')
        finally:
            if fixture.token is not None:
                if report.get('save_observed_activation') is True:
                    report['cleanup_status']='activation_observed_left_as_is'
                else:
                    report['cleanup_status']=fixture.cleanup() if can_cleanup() else 'environment_changed_left_as_is'
            else:report['cleanup_status']='not_bound_left_as_is'
            report['closed_observed']=fixture.closed_observed
            report['close_requested']=fixture.close_requested is True
            observations=fixture.status_observations
            report['status_observations']=deepcopy(observations) if isinstance(observations,list) else []
            report['status_observations_truncated']=fixture.status_observations_truncated is True
            report['artifact_removed']=fixture.directory is not None and not fixture.directory.exists()
    report['focus']=witness.summary()
    report['passed']=(report['passed'] and report['cleanup_status']=='closed'
                      and not report['focus']['activations']
                      and report.get('save_observed_activation') is not True)
    return report
