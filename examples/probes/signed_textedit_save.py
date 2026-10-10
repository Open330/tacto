"""Actual owned TextEdit preparation/Save; saved state is not writer attribution."""
import secrets

from signed_owned_textedit import SignedOwnedTextEdit, TARGET


def run(driver,*,can_cleanup):
    from focus_witness import FocusWitness
    from nlc.commit_evidence import make_event,evidence_error
    from nlc.existing_file_receipt import ExistingTextFileReceipt,SAVE_PATH,_file_state
    report={'scope':'owned_textedit_in_place_save_dispatch_and_saved_state',
            'passed':False,'model_requests':0,'producer_attribution_verified':False}
    fixture=SignedOwnedTextEdit(driver,'NLC 저장 검증 '+secrets.token_hex(8)+'.txt')
    body='NLC 실제 저장 검증 '+secrets.token_hex(16)+'\n두 번째 줄\n'
    with FocusWitness() as witness:
        try:
            if witness.initial==TARGET:raise RuntimeError('target_initially_frontmost')
            fixture.setup()
            report['owned_directory']=str(fixture.directory)
            report['owned_directory_identity']=fixture.directory_identity
            if not fixture.write(body):raise RuntimeError('owned_text_readback_failed')
            report['exact_document_text']=True
            report['disk_matched_before_prepare']=_file_state(str(fixture.path))[5]==body.encode()
            report['menu_enabled_before_prepare']=driver.menu_state(TARGET,SAVE_PATH).get('enabled')
            if report['menu_enabled_before_prepare'] is not True:
                obs=driver.observe(TARGET,document_token=fixture.token)
                if obs.get('type')!='observation' or obs.get('document_token')!=fixture.token or obs.get('truncated') is not False:
                    raise RuntimeError('scoped_window_unavailable')
                windows=[int(line.split()[0]) for line in obs['tree'].splitlines()
                         if len(line.split())>1 and line.split()[1]=='Window']
                if len(windows)!=1:raise RuntimeError('scoped_window_unavailable')
                preparation=driver.act(obs['obs_id'],TARGET,{'name':'prepare_window','element_index':windows[0]},expect={'role':'Window'})
                report['preparation_type']=preparation.get('type')
                report['preparation_effect']=preparation.get('effect')
                if preparation.get('type')!='acted':raise RuntimeError('window_preparation_unverified')
            else:report['preparation_type']='not_needed'
            # Preparation is only dispatched, not assumed to have enabled Save.
            state=driver.menu_state(TARGET,SAVE_PATH)
            report['menu_enabled_after_prepare']=state.get('enabled')
            if state.get('type')!='menu_state' or state.get('enabled') is not True:
                raise RuntimeError('save_menu_not_enabled_after_prepare')
            # Menu actions cannot use a document-scoped observation. The native
            # focused_document guard independently requires this exact file.
            obs=driver.observe(TARGET)
            if obs.get('type')!='observation':raise RuntimeError('save_observation_unavailable')
            action={'name':'menu','path':SAVE_PATH,'focused_document':str(fixture.path)}
            event=make_event(action,['write_persistent'],TARGET,obs_id=obs['obs_id'])
            receipt=ExistingTextFileReceipt(driver,document_path=str(fixture.path),timeout_seconds=5)
            context=receipt.prepare_commit(event)
            report['disk_matched_before_save']=_file_state(str(fixture.path))[5]==body.encode()
            response=driver.act(obs['obs_id'],TARGET,action,risk_class='preapproved',approved=True,
                intent_text='Save only the evaluator-created temporary TextEdit document')
            report['save_dispatch_type']=response.get('type');report['save_dispatch_effect']=response.get('effect')
            event['commit_evidence']=receipt.after_commit(event,response,context)
            report['evidence_error']=evidence_error(event,receipt.commit_expected)
            report['exact_saved_text']=_file_state(str(fixture.path))[5]==body.encode()
            report['passed']=response.get('type')=='acted' and report['evidence_error'] is None and report['exact_saved_text']
        except Exception as error:
            report['failure_class']=type(error).__name__
            codes={'target_initially_frontmost','owned_text_readback_failed','scoped_window_unavailable',
                   'window_preparation_unverified','save_menu_not_enabled_after_prepare','save_observation_unavailable'}
            report['failure_code']=getattr(error,'public_code',None) or (str(error) if str(error) in codes else 'probe_unavailable')
        finally:
            if fixture.token is not None:
                report['cleanup_status']=fixture.cleanup() if can_cleanup() else 'environment_changed_left_as_is'
            else:report['cleanup_status']='not_bound_left_as_is'
            report['closed_observed']=fixture.closed_observed
            report['artifact_removed']=fixture.directory is not None and not fixture.directory.exists()
    report['focus']=witness.summary()
    report['passed']=report['passed'] and report['cleanup_status']=='closed' and not report['focus']['activations']
    return report
