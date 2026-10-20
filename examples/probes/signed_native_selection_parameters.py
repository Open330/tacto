"""Signed-channel refusal matrix; no valid observed control is ever addressed."""
import hashlib
import json
import os
from pathlib import Path
import uuid


def main(driver, output):
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    report = {'passed':False, 'model_requests':0, 'cases':[],
              'selection_task_success_verified':False,
              'scope':'invalid parameters or deliberately stale observation; no selected control'}
    error = None
    with os.fdopen(fd, 'w') as stream:
        try:
            from tacto.helper import InheritedHelperDriver
            from tacto.input_audit_process import ProcessInputAudit
            if not isinstance(driver, InheritedHelperDriver) or not InheritedHelperDriver.available():
                raise RuntimeError('signed_helper_required')
            if 'select_option.strict_parameters' not in driver.capabilities:
                raise RuntimeError('strict_native_selection_capability_required')
            root = Path(__file__).resolve().parents[2]
            paths = list((root/'tacto').glob('*.py')) + list((root/'helper').glob('*.swift')) + [
                Path(__file__), root/'examples/eval.py', root/'launcher/tacto', root/'launcher/tacto.swift',
                root/'helper/tacto-helper', root/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
            def manifest():
                return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
            report['source_before'] = manifest()
            invalid = [('value_'+name, {'option_label':'Beta', 'value':value}) for name,value in (
                ('null',None),('number',1),('boolean',True),('array',[]),('object',{}),
                ('different_string','other'),('empty_string',''))]
            invalid += [('label_'+name, {'option_label':value}) for name,value in (
                ('null',None),('number',1),('boolean',False),('array',[]),('object',{}),('empty_string',''))]
            invalid.append(('missing_label',{}))
            invalid += [('accent_normalization_mismatch',{'option_label':'é','value':'e\u0301'}),
                ('hangul_normalization_mismatch',{'option_label':'가','value':'\u1100\u1161'})]
            valid = [('omitted_value',{'option_label':'Beta'}),
                ('matching_value',{'option_label':'Beta','value':'Beta'}),
                ('unicode_value',{'option_label':'한글 🧪','value':'한글 🧪'}),
                ('space_value',{'option_label':' ','value':' '})]
            observation = 'never-observed-'+uuid.uuid4().hex
            with ProcessInputAudit(interval=.005,startup_timeout=10,stop_timeout=3) as audit:
                for name in ('select_option', 'select_custom_option'):
                    for case,params in invalid+valid:
                        response = driver._send({'type':'act','target':'com.apple.finder','obs_id':observation,
                            'action':{'name':name,'element_index':0,**params}})
                        expected = {'stale_observation'} if (case,params) in valid else {
                            'rejected_by_launcher','invalid_selection_parameters'}
                        report['cases'].append({'action':name,'case':case,
                            'error':response.get('error') if response.get('error') in expected else 'unexpected',
                            'not_started':response.get('effect') == 'not_started',
                            'verified':response.get('type') == 'error' and response.get('error') in expected
                                       and response.get('effect') == 'not_started'})
            report['input_audit'] = audit.report()
            report['source_after'] = manifest()
            report['source_subset_stable'] = report['source_before'] == report['source_after']
            report['passed'] = (all(row['verified'] for row in report['cases'])
                and len(report['cases']) == 40 and report['source_subset_stable']
                and report['input_audit']['status'] == 'UNCHANGED_SAMPLED')
        except BaseException as caught:
            report['error_class'] = type(caught).__name__; error = caught
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    if error is not None:
        raise error
    return report
