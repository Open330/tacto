"""Signed real-app saved-state check; never claims writer attribution."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys
import threading

from examples.probes.signed_finder_preparation import background_precondition

TARGET='com.apple.TextEdit'


def main(driver,output):
    from tacto.helper import InheritedHelperDriver
    if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
        raise RuntimeError('signed_helper_required')
    root=Path(__file__).resolve().parents[2]
    inputs=sorted((root/'tacto').glob('*.py'))+sorted((root/'helper').glob('*.swift'))+[
        Path(__file__).resolve(),root/'examples/eval.py',root/'examples/probes/signed_textedit_save.py',
        root/'examples/probes/signed_owned_textedit.py',root/'examples/probes/focus_witness.py',
        root/'examples/probes/signed_finder_preparation.py',root/'launcher/tacto',
        root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
    def manifest():
        return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    report={'passed':False,'run_status':'not_started','scope':'real TextEdit scoped Save and exact saved state',
            'producer_attribution_verified':False,'model_requests':0,
            'proves_no_intermediate_input_change':False,'private_window_titles_in_final_report':False}
    before=None;audit=None;timer=None;interrupted=None;expired=threading.Event()
    try:
        before=manifest()
        checked=background_precondition(target=TARGET)
        report['background_precondition']=checked
        if checked['ready']:
            from tacto.session import preflight
            def can_cleanup():
                return (not expired.is_set() and preflight().get('verdict')=='ready'
                        and background_precondition(target=TARGET)['ready'])
            def expire():
                expired.set();driver._poisoned=True
                try:driver._sock.shutdown(socket.SHUT_RDWR)
                except (OSError,AttributeError):pass
            timer=threading.Timer(90,expire);timer.daemon=True;timer.start()
            from tacto.input_audit_process import ProcessInputAudit
            audit=ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3)
            probes=str(root/'examples/probes');sys.path.insert(0,probes)
            try:
                module=importlib.import_module('signed_textedit_save')
                if Path(module.__file__).resolve()!=root/'examples/probes/signed_textedit_save.py':
                    raise RuntimeError('probe_source_unavailable')
                report['run_status']='attempted'
                with audit:
                    child=module.run(driver,can_cleanup=can_cleanup,scoped_save=True)
                # The old activation witness also samples window titles. Keep
                # only counts/booleans in this report, never those title strings.
                focus=child.pop('focus',{})
                child['focus']={'initial_target_frontmost':focus.get('initial')==TARGET,
                                'activation_count':len(focus.get('activations',[])),
                                'window_sample_count':len(focus.get('windows',[]))}
                report['saved_state_result']=child
                report['passed']=child.get('passed') is True
            finally:
                sys.path.remove(probes)
        else:
            report['run_status']='precondition_unmet'
    except BaseException as error:
        report['error_class']=type(error).__name__
        if not isinstance(error,Exception):interrupted=error
    finally:
        if timer is not None:timer.cancel()
        report['deadline_expired']=expired.is_set()
        try:report['input_audit']=audit.report() if audit is not None else {'status':'NOT_STARTED'}
        except Exception as error:report['input_audit']={'status':'UNAVAILABLE','error_class':type(error).__name__}
        if report['input_audit'].get('status')!='UNCHANGED_SAMPLED' or expired.is_set():report['passed']=False
        try:after=manifest() if before is not None else None
        except Exception:after=None
        report.update(source_subset_before=before,source_subset_after=after,
                      source_subset_stable=before is not None and before==after)
        if not report['source_subset_stable']:report['passed']=False
        with os.fdopen(descriptor,'w') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    if interrupted is not None:raise interrupted
    return report
