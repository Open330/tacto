"""Stage-journalled signed Finder flow; no raw tree or label is persisted."""
import hashlib
import json
import os
import socket
import stat
from pathlib import Path
import runpy
import tempfile
import time
import threading

TARGET='com.apple.finder'


def background_precondition(*, target=TARGET, frontmost=None, lookup=None):
    """Read-only benchmark gate; not an input authorization or atomic witness."""
    from tacto.native_frontmost import frontmost_pid
    frontmost = frontmost or frontmost_pid
    if lookup is None:
        from AppKit import NSRunningApplication
        lookup=NSRunningApplication.runningApplicationWithProcessIdentifier_
    try:
        pid=frontmost()
        if type(pid) is not int or pid<=0:
            return {'ready':False,'reason':'foreground_unavailable'}
        app=lookup(pid)
        if app is None or app.isTerminated() or int(app.processIdentifier())!=pid:
            return {'ready':False,'reason':'foreground_unavailable'}
        bundle=app.bundleIdentifier()
        if not isinstance(bundle,str) or not bundle:
            return {'ready':False,'reason':'foreground_unavailable'}
        if frontmost()!=pid:
            return {'ready':False,'reason':'foreground_changed'}
        if bundle.lower()==target.lower():
            return {'ready':False,'reason':'target_already_foreground'}
        return {'ready':True,'reason':'background_target'}
    except Exception:
        return {'ready':False,'reason':'foreground_unavailable'}


def persist_owned_directory(folder,output):
    """Birth evidence survives worker exit; it grants no UI/cleanup authority."""
    folder=Path(folder)
    identity=folder.lstat()
    if not stat.S_ISDIR(identity.st_mode):
        raise RuntimeError('owned_directory_not_plain')
    path=Path(output).with_suffix('.ownership.json')
    record={'version':1,'directory':str(folder),'device':identity.st_dev,
            'inode':identity.st_ino,'uid':identity.st_uid,
            'authorizes_input':False,'window_ownership_verified':False}
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2)
        stream.write('\n');stream.flush();os.fsync(stream.fileno())
    # Persist the directory entry as well as its contents before any UI call.
    descriptor=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(descriptor)
    finally:os.close(descriptor)
    return path,record


class StageDriver:
    def __init__(self,driver,journal,*,preflight=False,background_check=None):
        self.driver=driver;self.journal=journal;self.sequence=0
        self.preflight=preflight;self.preflight_results=[]
        self.open_folder_attempted=False
        self.background_check=background_check;self.background_checks=[]
    def check_background(self):
        if self.background_check is None:return True
        checked=self.background_check()
        if (type(checked) is not dict or set(checked)!={'ready','reason'}
                or type(checked.get('ready')) is not bool
                or checked.get('reason') not in {'background_target','foreground_unavailable',
                                                'foreground_changed','target_already_foreground'}
                or checked['ready'] != (checked['reason']=='background_target')):
            checked={'ready':False,'reason':'foreground_unavailable'}
        self.background_checks.append(dict(checked))
        self.record('background_precondition','checked',**checked)
        return checked['ready']
    def __getattr__(self,name):return getattr(self.driver,name)
    def record(self,stage,phase,**values):
        self.sequence+=1
        json.dump({'sequence':self.sequence,'stage':stage,'phase':phase,
                   'at_monotonic':time.monotonic(),**values},self.journal)
        self.journal.write('\n');self.journal.flush();os.fsync(self.journal.fileno())
    def call(self,stage,method,*args,**kwargs):
        self.record(stage,'begin')
        try:result=method(*args,**kwargs)
        except BaseException as error:
            self.record(stage,'error',error_class=type(error).__name__);raise
        from tacto.preparation_diagnostic import preparation_diagnostic
        values={'response_type':result.get('type') if result.get('type') in ('observation','acted','error','readiness_preflight') else 'other'}
        # Persist closed diagnostic codes, never arbitrary UI/error detail.
        allowed_errors={'no_window_content','indicator_unavailable','window_list_unavailable',
                        'target_not_found','gui_session_unavailable','aborted_by_user',
                        'document_snapshot_unavailable','helper_unavailable'}
        allowed_reasons={'menu_item_not_on_screen','menu_item_unavailable','stream_unavailable',
                         'screen_locked','app_hidden','window_offscreen','no_visible_window','minimized',
                         'control_panel_unavailable','control_panel_not_visible','session_not_visible'}
        if result.get('error') in allowed_errors:values['error']=result['error']
        if result.get('reason') in allowed_reasons:values['reason']=result['reason']
        panel=result.get('control_panel_diagnostic')
        panel_keys={'created','visible','active_space','not_minimized','opaque','not_occluded',
                    'inside_screen','display_awake','target_frames_available','frame_available'}
        if isinstance(panel,dict) and panel and set(panel)<=panel_keys and all(type(v) is bool for v in panel.values()):
            values['control_panel_diagnostic']=panel
        if stage=='readiness_preflight':
            for key in ('read_only','authorizes_input'):
                values[key]=result.get(key) if type(result.get(key)) is bool else None
            if result.get('reason') in ('read_complete','invalid_or_stale_preflight',
                    'preflight_window_owner_unavailable','preflight_window_role_unavailable',
                    'preflight_deadline','preflight_nodes','preflight_unavailable'):
                values['reason']=result['reason']
        from tacto.window_listing_diagnostic import window_listing_diagnostic
        listing=window_listing_diagnostic(result.get('window_listing_diagnostic'))
        if listing is not None:values['window_listing_diagnostic']=listing
        if stage=='observe':
            count=result.get('element_count')
            if type(count) is int and 0<=count<=2500:values['element_count']=count
            values['truncated']=result.get('truncated') is True
            metrics=result.get('observation_metrics')
            keys={'packet_attempts','packet_successes','packet_fallbacks','secondary_action_reads'}
            if (type(metrics) is dict and set(metrics)==keys
                    and all(type(value) is int and 0<=value<=2500 for value in metrics.values())
                    and metrics['packet_successes']+metrics['packet_fallbacks']==metrics['packet_attempts']):
                values['observation_metrics']=dict(metrics)
        diagnostic=preparation_diagnostic(result.get('preparation_diagnostic'))
        if diagnostic is not None:values['preparation_diagnostic']=diagnostic
        if result.get('effect') in ('not_started','unknown'):values['effect']=result['effect']
        self.record(stage,'end',**values)
        return result
    def observe(self,*args,**kwargs):return self.call('observe',self.driver.observe,*args,**kwargs)
    def act(self,obs,target,action,**kwargs):
        if action.get('name') in ('open_folder','prepare_window') and not self.check_background():
            return {'type':'error','error':'background_precondition_failed','effect':'not_started'}
        if action.get('name')=='open_folder':self.open_folder_attempted=True
        if self.preflight and action.get('name')=='prepare_window':
            if 'diagnostic.readiness_preflight' not in self.driver.capabilities:
                return {'type':'error','error':'readiness_preflight_unavailable','effect':'not_started'}
            reply=self.call('readiness_preflight',self.driver._send,{
                'type':'readiness_preflight','target':target,'obs_id':obs,
                'element_index':action.get('element_index'),'titlebar_probe':True})
            self.preflight_results.append(reply)
            if (reply.get('type')!='readiness_preflight' or reply.get('read_only') is not True
                    or reply.get('authorizes_input') is not False):
                return {'type':'error','error':'readiness_preflight_failed','effect':'not_started'}
            if not self.check_background():
                return {'type':'error','error':'background_precondition_failed','effect':'not_started'}
            # No preflight binding/permit is passed. The original action takes
            # its own deadline and performs every existing fresh native guard.
        stage=action.get('name') if action.get('name') in ('open_folder','prepare_window','menu') else 'other_action'
        return self.call(stage,self.driver.act,obs,target,action,**kwargs)
    def wait_until(self,*args,**kwargs):return self.call('wait_until',self.driver.wait_until,*args,**kwargs)


def main(driver,output,*,preflight=False):
    if type(preflight) is not bool:raise ValueError("preflight must be boolean")
    root=Path(__file__).resolve().parents[2]
    output=Path(output);journal_path=output.with_suffix('.stages.jsonl')
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:journal_descriptor=os.open(journal_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except BaseException:os.close(descriptor);raise
    paths=list((root/'tacto').glob('*.py'))+list((root/'nlc').glob('*.py'))+list((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/eval.py',root/'examples/probes/finder-preparation-diagnostic.py',
        root/'launcher/tacto',root/'helper/tacto-helper',root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    def manifest():return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    report={'passed':False,'task_result_ok':None,'model_requests':0,'all_dependencies_frozen':False,'stage_journal':str(journal_path),'preflight_requested':preflight}
    before=None;interrupted=None;audit=None;timer=None;expired=threading.Event()
    with os.fdopen(journal_descriptor,'w') as journal:
        stages=StageDriver(driver,journal,preflight=preflight,background_check=background_precondition)
        try:
            before=manifest()
            if not stages.check_background():
                report['precondition_status']='not_run'
                return report
            created=Path(tempfile.mkdtemp(prefix='nlc-finder-staged-',dir=Path.home()/'Library/Caches'))
            # Normalize the parent without following a replaced fixture leaf.
            folder=created.parent.resolve()/created.name
            identity=folder.lstat();report['owned_directory']=str(folder);report['cleanup']='left_as_is'
            ownership_path,ownership=persist_owned_directory(folder,output)
            report['ownership_record']=str(ownership_path)
            report['owned_directory_identity']={key:ownership[key] for key in ('device','inode','uid')}
            stages.record('owned_fixture','ready',directory_device=ownership['device'],
                          directory_inode=ownership['inode'],directory_uid=ownership['uid'],
                          authorizes_input=False,window_ownership_verified=False)
            flow=runpy.run_path(str(root/'examples/probes/finder-preparation-diagnostic.py'))['preparation_flow'](str(folder))
            from tacto.agent import Agent
            from tacto.flow import replay
            approvals,blocked=Agent(driver=stages).authorize(flow,ask=lambda _:True)
            if blocked:raise RuntimeError('owned_flow_blocked')
            def expire():
                expired.set()
                driver._poisoned=True
                try:driver._sock.shutdown(socket.SHUT_RDWR)
                except (OSError,AttributeError):pass
            timer=threading.Timer(90,expire);timer.daemon=True;timer.start()
            from tacto.input_audit_process import ProcessInputAudit
            audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
            with audit:result=replay(stages,flow,approvals=approvals,budget=0)
            report.update(passed=result.ok,task_result_ok=result.ok,event_states=[e.get('state') for e in result.events],
                          preparation_diagnostics=result.preparation_diagnostics)
            errors={'postcondition_timeout','expect_not_met','contract_invalidated','target_missing',
                    'target_ambiguous','action_failed','target_seen_in_front','background_precondition_failed',
                    'aborted_by_user','postcondition_already_true','unverifiable_action'}
            report['task_error_codes']=[{
                'step':event.get('step') if type(event.get('step')) is int and 0<=event['step']<=2 else None,
                'error':event.get('error') if type(event.get('error')) is str and event['error'] in errors else 'other'}
                for event in getattr(result,'log',[]) if isinstance(event,dict) and event.get('status')=='error'][:3]
            now=folder.lstat()
            if stages.open_folder_attempted:
                # Removing the displayed directory can make Finder navigate
                # to its much larger parent. Window creation/close ownership
                # is not proved by this probe, so retain the owned directory.
                report['cleanup']='owned_directory_retained_without_window_close_proof'
            elif (not expired.is_set() and (now.st_dev,now.st_ino)==(identity.st_dev,identity.st_ino)
                    and not list(folder.iterdir())):
                folder.rmdir();report['cleanup']='owned_empty_directory_removed'
        except BaseException as error:
            report.update(passed=False,error_class=type(error).__name__)
            if not isinstance(error,Exception):interrupted=error
        finally:
            if timer is not None:timer.cancel()
            report['deadline_expired']=expired.is_set()
            report['preflight_results']=stages.preflight_results
            report['open_folder_attempted']=stages.open_folder_attempted
            report['background_precondition_checks']=stages.background_checks
            try:report['input_audit']=audit.report() if audit is not None else {'status':'NOT_STARTED'}
            except Exception as error:report['input_audit']={'status':'UNAVAILABLE','error_class':type(error).__name__}
            if expired.is_set() or report['input_audit'].get('status')!='UNCHANGED_SAMPLED':report['passed']=False
            try:after=manifest()
            except Exception:after=None
            stable=before is not None and after==before
            report.update(source_subset_before=before,source_subset_after=after,source_subset_stable=stable,
                          stage_count=stages.sequence)
            if not stable:report['passed']=False
            with os.fdopen(descriptor,'w') as stream:json.dump(report,stream,indent=2)
    if interrupted is not None:raise interrupted
    return report
