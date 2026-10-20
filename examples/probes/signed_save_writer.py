"""Controlled producer callback + fresh file receipt; no general app attribution."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import secrets
import stat
import subprocess
import tempfile
import time

TARGET='ai.callabo.tacto.save-writer-fixture'
PANEL_REASONS={'column browser absent or ambiguous','column container absent or ambiguous',
    'folder column chain unavailable','terminal selection present','selected folder absent or ambiguous',
    'selected folder URL absent or ambiguous','directory absent','save panel hierarchy changed',
    'explicit RTF format unavailable','observation map budget exceeded','save panel observation budget exceeded',
    'unsupported save browser','save sheet absent or ambiguous','unsupported setting role',
    'ContentTypesPopup absent or ambiguous','KeepChangesButton absent or ambiguous',
    'save settings or native identity changed','saveAsNameTextField absent or ambiguous'}
PANEL_REASONS |= {'save panel observation budget exceeded: nodes','save panel observation budget exceeded: deadline'}
PANEL_REASONS |= {'save panel observation budget exceeded: identities','save panel observation budget exceeded: array entries'}


def callback_verified(callbacks,nonce,path,body):
    expected={'callback':'written','nonce':nonce,'path':str(path),
              'sha256':hashlib.sha256(body.encode()).hexdigest(),'response':1}
    # One producer completion, not one good entry among conflicting results.
    completions=[row for row in callbacks if row.get('callback')!='ready']
    return (completions==[expected] and type(completions[0].get('response')) is int)


def preferences_exist():
    result=subprocess.run(['defaults','read',TARGET],capture_output=True,timeout=5)
    if result.returncode==0:
        # cfprefsd can retain a deleted domain as an empty dictionary.
        return result.stdout.strip() not in (b'{}',b'{\n}')
    if b'does not exist' in result.stderr or f"Domain '{TARGET}' not found.".encode() in result.stderr:return False
    raise RuntimeError('owned_preferences_status_unavailable')


def remove_empty_preferences(path):
    """Remove only the empty plist in the host-owned fixture domain."""
    try:descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    except FileNotFoundError:return
    try:
        before=os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid() or before.st_nlink!=1 or before.st_size>4096:
            raise RuntimeError('owned_preferences_cleanup_unverified')
        data=os.read(descriptor,4097)
        if plistlib.loads(data)!={} or os.fstat(descriptor)!=before or os.stat(path,follow_symlinks=False)!=before:
            raise RuntimeError('owned_preferences_cleanup_unverified')
        os.unlink(path)
    finally:os.close(descriptor)


def run(driver,*,sibling_count=0,journal_reply_loss=False,journal_worker_restart=False,journal_worker_reconcile=False):
    if type(journal_reply_loss) is not bool:raise ValueError('invalid reply loss mode')
    if type(journal_worker_restart) is not bool or journal_worker_restart and journal_reply_loss:
        raise ValueError('invalid worker restart mode')
    if type(journal_worker_reconcile) is not bool or journal_worker_reconcile and not journal_worker_restart:
        raise ValueError('reconciliation requires worker restart')
    if type(sibling_count) is not int or not 0<=sibling_count<=12000:
        raise ValueError('invalid owned sibling count')
    from examples.probes.focus_witness import FocusWitness
    from tacto.driver import Driver
    from tacto.producer_file_receipt import ProducerFileReceipt,ProducerObservation
    from tacto.native_process_identity import process_start_identity
    from tacto.commit_evidence import make_event,evidence_error
    required={'evidence.save_panel','action.click.save_panel_guard'}
    if not required <= set(driver.capabilities):
        raise RuntimeError('save_writer_capability_unavailable')
    # Read-only NSRunningApplication metadata; accessory fixtures are omitted
    # from the public regular-app list. No in-process GUI input is used.
    identity=Driver()
    if identity._resolve(TARGET,launch=False) is not None:
        raise RuntimeError('owned_producer_already_running')
    if preferences_exist():raise RuntimeError('owned_preferences_preexisting')
    root=Path(__file__).resolve().parents[2]
    nonce=secrets.token_hex(16);filename='fresh-'+nonce+'.rtf';body='{\\rtf1\\ansi TACTO owned save '+nonce+'}'
    report={'scope':'owned_native_save_producer_under_exclusive_directory_assumption',
            'passed':False,'model_requests':0,'general_app_producer_attribution_verified':False,
            'explicit_textedit_save_verified':False,'owned_sibling_count':sibling_count}
    report['journal_reply_loss_mode']=journal_reply_loss
    report['worker_process_restarted']=False
    report['worker_reconciliation_requested']=journal_worker_reconcile
    preference_file=Path.home()/'Library/Preferences'/f'{TARGET}.plist'
    report['owned_preferences_empty_before']=True
    report['owned_preference_file_present_before']=preference_file.exists()
    with tempfile.TemporaryDirectory(prefix='tacto-save-writer-',dir=Path.home()/'Library/Caches') as work:
        work=Path(work).resolve();destination=work/'output';destination.mkdir(mode=0o700)
        # Stress a selected ancestor's row membership without touching user files.
        for index in range(sibling_count):(work/f'sibling-{index:05d}').mkdir(mode=0o700)
        binary=work/'TACTO Save Writer.app/Contents/MacOS/NativeSaveWriterFixture'
        binary.parent.mkdir(parents=True)
        (binary.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,
            'CFBundleExecutable':binary.name,'CFBundlePackageType':'APPL','CFBundleName':'TACTO Owned Save Writer','LSUIElement':True}))
        subprocess.run(['xcrun','swiftc','-warnings-as-errors','-parse-as-library',
                        str(root/'tests/fixtures/NativeSaveWriterFixture.swift'),'-o',str(binary)],
                       check=True,capture_output=True,timeout=60)
        host=work/'callback.jsonl';fd=os.open(host,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as log,FocusWitness() as witness:
            child=subprocess.Popen([str(binary),str(destination),filename,nonce,
                '-NSNavPanelExpandedStateForSaveMode','YES','-NSNavLastListMode','2'],stdout=log,stderr=subprocess.DEVNULL,close_fds=True)
            callback_fd=None
            try:
                start_identity=process_start_identity(child.pid)
                callback_fd=os.open(host,os.O_RDONLY|os.O_NOFOLLOW)
                if (os.fstat(callback_fd).st_dev,os.fstat(callback_fd).st_ino)!=(os.fstat(log.fileno()).st_dev,os.fstat(log.fileno()).st_ino):
                    raise RuntimeError('producer_callback_channel_changed')
                def producer_observation():
                    data=os.pread(callback_fd,16385,0)
                    if len(data)>16384 or (data and not data.endswith(b'\n')):
                        raise RuntimeError('producer_callback_channel_unavailable')
                    rows=[json.loads(line) for line in data.splitlines()]
                    ready=[row for row in rows if row.get('callback')=='ready']
                    if ready!=[{'callback':'ready','nonce':nonce}]:
                        raise RuntimeError('producer_callback_not_ready')
                    completed=[row for row in rows if row.get('callback')!='ready']
                    if not completed and child.poll() is None:
                        return ProducerObservation(TARGET,child.pid,start_identity,ready[0]['nonce'],0,'ready')
                    if callback_verified(rows,nonce,destination/filename,body):
                        row=completed[0]
                        return ProducerObservation(TARGET,child.pid,start_identity,row['nonce'],1,'written',row['path'],row['sha256'])
                    return ProducerObservation(TARGET,child.pid,start_identity,nonce,len(completed),'failed')
                deadline=time.monotonic()+15
                prepared=False
                while time.monotonic()<deadline:
                    pid=identity._resolve(TARGET,launch=False)
                    if pid is None:
                        if child.poll() is not None:raise RuntimeError('owned_producer_exited_before_panel')
                        time.sleep(.1)
                        continue
                    if pid!=child.pid:
                        raise RuntimeError('owned_producer_identity_unverified')
                    obs=driver.observe(TARGET,'tree_full')
                    if obs.get('type')=='observation':
                        windows=[int(line.split()[0]) for line in obs['tree'].splitlines() if len(line.split())>1 and line.split()[1]=='Window']
                        report['preparation_controls']=[line.strip() for line in obs['tree'].splitlines()
                            if len(line.split())>1 and (line.split()[1]=='DisclosureTriangle'
                            or 'NSNav' in line or 'expand' in line.lower())]
                        disclosure=[line for line in obs['tree'].splitlines() if len(line.split())>1
                            and line.split()[1]=='DisclosureTriangle'
                            and 'Identifier: NS_OPEN_SAVE_DISCLOSURE_TRIANGLE' in line
                            and 'Value: 0,' in line]
                        if not prepared and len(disclosure)==1:
                            prepared=True
                            preparation=driver.act(obs['obs_id'],TARGET,
                                {'name':'click','element_index':int(disclosure[0].split()[0])},
                                expect={'role':'DisclosureTriangle','attribute':'Identifier','label':'NS_OPEN_SAVE_DISCLOSURE_TRIANGLE','enabled':True})
                            report['panel_expansion_dispatch']=preparation.get('type')
                            report['panel_expansion_error']=preparation.get('error')
                            if preparation.get('type')!='acted':raise RuntimeError('owned_save_panel_unavailable')
                            continue
                        if len(windows)>4:raise RuntimeError('owned_save_panel_unavailable')
                        panels=[driver.save_panel(obs['obs_id'],TARGET,index,require_format=True) for index in windows]
                        report['panel_readiness']=[{'type':candidate.get('type'),'error':candidate.get('error'),
                            'reason_code':(candidate.get('detail') if candidate.get('detail') in PANEL_REASONS else 'other'),
                            'observation_budget':candidate.get('observation_budget'),
                            'directory_matches':candidate.get('directory')==str(destination),
                            'filename_matches':candidate.get('filename')==filename,
                            'format':candidate.get('format'),'original_changes_setting':candidate.get('original_changes_setting')}
                            for candidate in panels]
                        matches=[candidate for candidate in panels if candidate.get('type')=='save_panel'
                            and candidate.get('directory')==str(destination) and candidate.get('filename')==filename
                            and candidate.get('format')=='rtf' and candidate.get('original_changes_setting')==0]
                        if len(matches)==1:
                            panel=matches[0];break
                    if child.poll() is not None:raise RuntimeError('owned_producer_exited_before_panel')
                    time.sleep(.1)
                else:raise RuntimeError('owned_save_panel_unavailable')
                action=panel['commit_control']['action']
                event=make_event(action,['write_persistent'],TARGET,obs_id=obs['obs_id'])
                receipt=ProducerFileReceipt(destination,filename,body,producer=TARGET,pid=child.pid,
                    start_time=start_identity,nonce=nonce,read_producer=producer_observation,timeout_seconds=3)
                context=None if journal_worker_restart else receipt.prepare_commit(event)
                report['destination_absent_before_dispatch']=not (destination/filename).exists()
                dispatch_kwargs={'save_panel_token':panel['panel_token'],'risk_class':'preapproved','approved':True,
                    'intent_text':'Save one evaluator-owned fresh fixture artifact'}
                if journal_worker_restart:
                    from examples.probes.signed_native_journal_worker import write_json,private_json
                    from tacto.commit_evidence import CommitEvidence,ProducerArtifactEvidence
                    from tacto.producer_file_receipt import ProducerObservation
                    import signal
                    config=work/'worker-config.json';callback_info=os.fstat(callback_fd)
                    write_json(config,{'work':str(work),'nonce':nonce,'pid':child.pid,'start':start_identity,
                        'callback_dev':callback_info.st_dev,'callback_ino':callback_info.st_ino})
                    exits=[]
                    for phase in ('dispatch','reconcile' if journal_worker_reconcile else 'restart'):
                        env=os.environ.copy();env.pop('TACTO_HELPER_FD',None)
                        fd=os.open(work/f'worker-{phase}.log',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                        with os.fdopen(fd,'w') as worker_log:
                            worker=subprocess.Popen([str(root/'launcher/tacto'),'eval','signed_native_journal_worker',
                                '--owned-worker-config',str(config),'--owned-worker-phase',phase],
                                env=env,stdout=worker_log,stderr=subprocess.STDOUT,close_fds=True,start_new_session=True)
                            try:exits.append(worker.wait(timeout=45 if phase=='reconcile' else 15))
                            except BaseException:
                                try:os.killpg(worker.pid,signal.SIGKILL)
                                except ProcessLookupError:pass
                                worker.wait(timeout=3);raise
                        report['worker_exit_codes']=list(exits)
                        with open(work/f'worker-{phase}.log','rb') as saved_log:
                            diagnostic=saved_log.read(16385)
                        report.setdefault('worker_diagnostics',{})[phase]={
                            'text':diagnostic[:16384].decode('utf-8',errors='replace'),
                            'truncated':len(diagnostic)>16384}
                        if exits[-1]!=(17 if phase=='dispatch' else 0):
                            raise RuntimeError('owned_signed_worker_failed')
                    original=private_json(work/'worker-proof.json');restart=private_json(work/'worker-restart.json')
                    event=original['event'];proof=CommitEvidence(event['event_id'],event['action_digest'],
                        ProducerArtifactEvidence(**original['evidence']))
                    # A fresh helper observation has its own volatile index.
                    # The worker independently pins directory/name/format and
                    # dispatches that panel's guarded commit_control. Validate
                    # its actual event digest below, not a parent-channel index.
                    operation=lambda payload:{key:value for key,value in payload.items() if key!='element_index'}
                    report['worker_dispatch_event']=dict(event)
                    if event['target']!=TARGET or operation(event['action'])!=operation(action):
                        raise RuntimeError('owned_worker_event_changed')
                    response={'type':'error','error':'owned_worker_terminated_after_receipt','effect':'unknown'}
                    report.update(worker_process_restarted=True,worker_exit_codes=exits,
                        worker_pids=[original['pid'],restart['pid']],restart_receiving_state=restart,
                        journal_lines=len((work/'dispatch-journal/00000.jsonl').read_text().splitlines()))
                elif journal_reply_loss:
                    from tacto.dispatch_journal import DurableDispatchDriver
                    calls=[]
                    class LoseReply:
                        def act(self,*args,**kwargs):
                            calls.append(1)
                            reply=driver.act(*args,**kwargs)
                            if reply.get('type')=='acted':raise TimeoutError('owned post-dispatch reply loss')
                            return reply
                    lossy=LoseReply();journal=work/'dispatch-journal'
                    with DurableDispatchDriver(lossy,journal) as guarded:
                        try:response=guarded.act(obs['obs_id'],TARGET,action,**dispatch_kwargs)
                        except TimeoutError:
                            response={'type':'error','error':'owned_injected_reply_loss','effect':'unknown'}
                    # Reconstruct only the barrier, not the native task/worker.
                    # This refusal must occur before consulting a stale native
                    # observation or the now-terminating producer app.
                    with DurableDispatchDriver(lossy,journal) as restarted:
                        refused=restarted.act('fresh-after-wrapper-reconstruction',TARGET,action,**dispatch_kwargs)
                    report['reconstructed_barrier_refusal']=refused
                    report['underlying_commit_dispatch_calls']=len(calls)
                    report['journal_lines']=len((journal/'00000.jsonl').read_text().splitlines())
                else:
                    response=driver.act(obs['obs_id'],TARGET,action,**dispatch_kwargs)
                report['dispatch_type']=response.get('type');report['dispatch_effect']=response.get('effect')
                report['dispatch_error']=response.get('error') if response.get('error') in ('save_panel_guard_unavailable','save_panel_unavailable','action_failed','precondition_failed','stale_observation','unverifiable_action','aborted_by_user') else None
                if not journal_worker_restart:proof=receipt.after_commit(event,response,context)
                event['commit_evidence']=proof
                report['evidence_error']=evidence_error(event,receipt.commit_expected)
                if proof is not None:
                    from dataclasses import asdict
                    report['producer_evidence']=asdict(proof.evidence)
                    report['producer_evidence_kind']=type(proof.evidence).__name__
                child.wait(timeout=5)
                callbacks=[json.loads(line) for line in host.read_text().splitlines()]
                report['producer_callback_verified']=callback_verified(callbacks,nonce,destination/filename,body)
                report['child_exit']=child.returncode
                report['fresh_bytes_match']=(destination/filename).read_bytes()==body.encode()
                dispatch_ok=(response.get('type')=='error' and response.get('error')=='owned_injected_reply_loss'
                    and report['underlying_commit_dispatch_calls']==1 and report['journal_lines']==1
                    and refused.get('error')=='dispatch_journal_requires_reconciliation'
                    and refused.get('effect')=='not_started') if journal_reply_loss else response.get('type')=='acted'
                if journal_worker_restart:
                    dispatch_ok=(exits==[17,0] and report['journal_lines']==1
                        and original['pid']!=restart['pid'] and restart['underlying_input_calls']==(1 if journal_worker_reconcile else 0)
                        and restart['receiving_sequence']==1 and restart['fresh_bytes_match'] is True)
                    if journal_worker_reconcile:
                        dispatch_ok=dispatch_ok and (restart.get('reconciled_slots')==1
                            and restart.get('journal_slots')==2 and restart.get('original_record_unchanged') is True
                            and restart.get('continuation',{}).get('after')=='count=1'
                            and restart.get('continuation',{}).get('owned_child_reaped') is True)
                        report['native_causal_continuation_verified']=dispatch_ok
                report['passed']=report['destination_absent_before_dispatch'] and dispatch_ok and report['evidence_error'] is None and report['producer_callback_verified'] and report['fresh_bytes_match'] and child.returncode==0
            except Exception as error:
                report['failure_class']=type(error).__name__
                reasons={'driver did not report dispatch':'driver_dispatch_unverified',
                         'expected destination already exists':'destination_already_exists',
                         'owned_producer_identity_unverified':'owned_producer_identity_unverified',
                         'owned_producer_exited_before_panel':'owned_producer_exited_before_panel',
                         'owned_save_panel_unavailable':'owned_save_panel_unavailable'}
                reasons['owned_signed_worker_failed']='owned_signed_worker_failed'
                reasons['owned_worker_event_changed']='owned_worker_event_changed'
                report['failure_code']=reasons.get(str(error),'probe_failed')
            finally:
                if callback_fd is not None:os.close(callback_fd)
                if child.poll() is None:child.terminate()
                try:child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill();child.wait(timeout=5)
                report['owned_child_reaped']=True
                try:
                    callbacks=[json.loads(line) for line in host.read_text().splitlines()]
                    report['callback_kinds']=[row.get('callback') for row in callbacks]
                    report['callback_refusals']=[row['reason'] for row in callbacks
                        if row.get('reason') in ('response','destination','format')]
                except (ValueError,AttributeError,TypeError):
                    report['failure_code']='callback_log_malformed';report['passed']=False
                report['owned_preferences_created']=preferences_exist()
                if report['owned_preferences_created']:
                    subprocess.run(['defaults','delete',TARGET],capture_output=True,check=True,timeout=5)
                report['owned_preferences_empty_after']=not preferences_exist()
                if report['owned_preferences_empty_after']:remove_empty_preferences(preference_file)
                report['owned_preference_file_absent_after']=not preference_file.exists()
                time.sleep(.2)
                report['owned_preference_file_absent_after_delay']=not preference_file.exists()
                report['passed']=report['passed'] and report['owned_preferences_empty_after'] and report['owned_preference_file_absent_after']
                report['passed']=report['passed'] and report['owned_preference_file_absent_after_delay']
        report['focus']=witness.summary()
    return report


def main(driver,output,*,sibling_count=0,journal_reply_loss=False,journal_worker_restart=False,journal_worker_reconcile=False):
    from tacto.input_audit_process import ProcessInputAudit
    root=Path(__file__).resolve().parents[2]
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'all_dependencies_frozen':False,'model_requests':0}
    paths=list((root/'tacto').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/eval.py',root/'tests/fixtures/NativeSaveWriterFixture.swift',
        root/'examples/probes/signed_native_journal_worker.py',
        root/'launcher/tacto',root/'helper/tacto-helper',root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    if journal_worker_reconcile:
        paths += [root/'examples/probes/signed_native_continuation.py',root/'tests/fixtures/NativeContinuationFixture.swift']
    def manifest():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    before=None;audit=None;interrupted=None
    try:
        before=manifest()
        audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
        with audit:report.update(run(driver,sibling_count=sibling_count,journal_reply_loss=journal_reply_loss,
                                     journal_worker_restart=journal_worker_restart,journal_worker_reconcile=journal_worker_reconcile))
    except BaseException as error:
        report.update(passed=False,error_class=type(error).__name__)
        if not isinstance(error,Exception):interrupted=error;report['interrupted']=True
    finally:
        try:report['input_audit']=audit.report() if audit is not None else {'status':'NOT_STARTED'}
        except Exception as error:
            report['input_audit']={'status':'UNAVAILABLE','error_class':type(error).__name__}
        if report['input_audit']['status']!='UNCHANGED_SAMPLED':report['passed']=False
        try:after=manifest()
        except Exception as error:
            after=None;report['source_manifest_error_class']=type(error).__name__
        stable=before is not None and after is not None and before==after
        report.update(source_subset_before=before,source_subset_after=after,source_subset_stable=stable)
        if not stable:report['passed']=False
        with os.fdopen(descriptor,'w') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    if interrupted is not None:raise interrupted
    return report
