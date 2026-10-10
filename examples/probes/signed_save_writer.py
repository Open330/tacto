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

TARGET='ai.callabo.nlc.save-writer-fixture'
PANEL_REASONS={'column browser absent or ambiguous','column container absent or ambiguous',
    'folder column chain unavailable','terminal selection present','selected folder absent or ambiguous',
    'selected folder URL absent or ambiguous','directory absent','save panel hierarchy changed',
    'explicit RTF format unavailable','observation map budget exceeded','save panel observation budget exceeded',
    'unsupported save browser','save sheet absent or ambiguous','unsupported setting role',
    'ContentTypesPopup absent or ambiguous','KeepChangesButton absent or ambiguous',
    'save settings or native identity changed','saveAsNameTextField absent or ambiguous'}


def callback_verified(callbacks,nonce,path,body):
    expected={'callback':'written','nonce':nonce,'path':str(path),
              'sha256':hashlib.sha256(body.encode()).hexdigest(),'response':1}
    # One producer completion, not one good entry among conflicting results.
    completions=[row for row in callbacks if row.get('callback')!='ready']
    return completions==[expected]


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


def run(driver):
    from focus_witness import FocusWitness
    from nlc.driver import Driver
    from nlc.artifact_receipt import FreshFileReceipt
    from nlc.commit_evidence import make_event,evidence_error
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
    nonce=secrets.token_hex(16);filename='fresh-'+nonce+'.rtf';body='{\\rtf1\\ansi NLC owned save '+nonce+'}'
    report={'scope':'owned_native_save_producer_under_exclusive_directory_assumption',
            'passed':False,'model_requests':0,'general_app_producer_attribution_verified':False,
            'explicit_textedit_save_verified':False}
    preference_file=Path.home()/'Library/Preferences'/f'{TARGET}.plist'
    report['owned_preferences_empty_before']=True
    report['owned_preference_file_present_before']=preference_file.exists()
    with tempfile.TemporaryDirectory(prefix='nlc-save-writer-',dir=Path.home()/'Library/Caches') as work:
        work=Path(work).resolve();destination=work/'output';destination.mkdir(mode=0o700)
        binary=work/'NLC Save Writer.app/Contents/MacOS/NativeSaveWriterFixture'
        binary.parent.mkdir(parents=True)
        (binary.parent.parent/'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':TARGET,
            'CFBundleExecutable':binary.name,'CFBundlePackageType':'APPL','CFBundleName':'NLC Owned Save Writer','LSUIElement':True}))
        subprocess.run(['xcrun','swiftc','-warnings-as-errors','-parse-as-library',
                        str(root/'tests/fixtures/NativeSaveWriterFixture.swift'),'-o',str(binary)],
                       check=True,capture_output=True,timeout=60)
        host=work/'callback.jsonl';fd=os.open(host,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as log,FocusWitness() as witness:
            child=subprocess.Popen([str(binary),str(destination),filename,nonce,
                '-NSNavPanelExpandedStateForSaveMode','YES','-NSNavLastListMode','2'],stdout=log,stderr=subprocess.DEVNULL,close_fds=True)
            try:
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
                receipt=FreshFileReceipt(destination,filename,body,timeout_seconds=3)
                context=receipt.prepare_commit(event)
                report['destination_absent_before_dispatch']=not (destination/filename).exists()
                response=driver.act(obs['obs_id'],TARGET,action,save_panel_token=panel['panel_token'],risk_class='preapproved',approved=True,
                    intent_text='Save one evaluator-owned fresh fixture artifact')
                report['dispatch_type']=response.get('type');report['dispatch_effect']=response.get('effect')
                report['dispatch_error']=response.get('error') if response.get('error') in ('save_panel_guard_unavailable','save_panel_unavailable','action_failed','precondition_failed','stale_observation','unverifiable_action','aborted_by_user') else None
                proof=receipt.after_commit(event,response,context)
                event['commit_evidence']=proof
                report['evidence_error']=evidence_error(event,receipt.commit_expected)
                child.wait(timeout=5)
                callbacks=[json.loads(line) for line in host.read_text().splitlines()]
                report['producer_callback_verified']=callback_verified(callbacks,nonce,destination/filename,body)
                report['child_exit']=child.returncode
                report['fresh_bytes_match']=(destination/filename).read_bytes()==body.encode()
                report['passed']=report['destination_absent_before_dispatch'] and response.get('type')=='acted' and report['evidence_error'] is None and report['producer_callback_verified'] and report['fresh_bytes_match'] and child.returncode==0
            except Exception as error:
                report['failure_class']=type(error).__name__
                reasons={'driver did not report dispatch':'driver_dispatch_unverified',
                         'expected destination already exists':'destination_already_exists',
                         'owned_producer_identity_unverified':'owned_producer_identity_unverified',
                         'owned_producer_exited_before_panel':'owned_producer_exited_before_panel',
                         'owned_save_panel_unavailable':'owned_save_panel_unavailable'}
                report['failure_code']=reasons.get(str(error),'probe_failed')
            finally:
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
