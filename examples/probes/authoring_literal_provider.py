"""Opt-in real-provider authoring + saved replay on isolated headless Chromium.

This is conformance evidence, not a native-app or matched Sky benchmark.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from contextlib import nullcontext
from unittest.mock import patch

from tacto.authoring import author
from tacto.browser import BrowserDriver
from tacto.flow import replay
from tacto.model_budget import model_deadline
from tacto.provider_observation import capture_provider_calls, summarize_provider_calls
from tacto.store import FlowStore


def author_refusal(error):
    """Closed diagnostics only; never retain arbitrary provider/error text."""
    message=str(error)
    prefixes={'literal/template name collision:':'literal_ambiguity',
              'literal names conflict':'literal_names_conflict',
              'authoring requires Flow schema':'authoring_schema',
              'authored target differs':'target_mismatch',
              'binding.bundle_id contradicts':'binding_identity_mismatch',
              'authored step':'step_invalid'}
    return next((code for prefix,code in prefixes.items() if message.startswith(prefix)),
                'other_value_error' if isinstance(error,ValueError) else 'other_error')


def run(output, providers, *, repair_rounds=0, inject_initial_ambiguity=False):
    if inject_initial_ambiguity and repair_rounds != 1:
        raise ValueError('initial ambiguity injection requires one correction round')
    root=Path(__file__).resolve().parents[2]
    paths=sorted((root/'tacto').glob('*.py'))+[Path(__file__).resolve(),root/'pyproject.toml']
    def manifest():
        return {str(path.relative_to(root)):hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    output=Path(output)
    descriptor=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    before=manifest()
    report={'kind':'real-provider authoring and saved replay; isolated headless browser',
            'matched_sky_comparison':False,'native_apps_verified':False,
            'authoring_repair_rounds':repair_rounds,
            'injected_initial_ambiguity':inject_initial_ambiguity,
            'source_subset_before':before,'cases':[]}
    with os.fdopen(descriptor,'w') as stream:
        for provider in providers:
            case={'provider':provider,'passed':False}
            report['cases'].append(case)
            started=time.monotonic();driver=None;records=[]
            injected=0
            try:
                driver=BrowserDriver()
                driver._page.set_content('<label>{{field}}<input value="correct" readonly></label>'
                                         '<label>Different<input value="wrong" readonly></label>')
                import tacto.authoring as authoring_module
                real_reach=authoring_module.reach_model
                def injected_reach(*args,**kwargs):
                    nonlocal injected
                    if injected==0:
                        injected+=1
                        # Deterministic invalid recipe; this is NOT a provider response.
                        return json.dumps({'name':'injected_ambiguity','target':'page',
                            'params':{'field':'Different'},'steps':[{'action':'read',
                            'captures':[{'name':'result','field':{'role':'textbox','title':'{{field}}'}}],
                            'safety':{'effects':['read'],'data_classes':['none']}}]})
                    return real_reach(*args,**kwargs)
                instrument=(patch('tacto.authoring.reach_model',side_effect=injected_reach)
                            if inject_initial_ambiguity else nullcontext())
                with capture_provider_calls() as records, model_deadline(time.monotonic()+120), instrument:
                    flow=author('Read the Value of the textbox whose literal label is {{field}}. '
                                'Capture the value as result. Do not change the page.',
                                driver,target='page',provider=provider,verify=False,rounds=repair_rounds)
                case['author_elapsed_seconds']=time.monotonic()-started
                with tempfile.TemporaryDirectory() as directory:
                    store=FlowStore(directory);store.save(flow)
                    saved=store.load(flow.name)
                    case['saved_flow']=json.loads(saved.to_json())
                    case['schema']=json.loads(saved.to_json())['schema']
                    # A later runtime value must never reinterpret the observed label.
                    result=replay(driver,saved.bind(),_seed={'field':'Different'})
                captures=[event['captured'] for event in result.log
                          if isinstance(event.get('captured'),dict)]
                case['replay_ok']=result.ok
                case['captured_result_values']=[values['result'] for values in captures if 'result' in values]
                case['captured_exact_value']=any(values.get('result')=='correct' for values in captures)
                case['dom_unchanged']=driver._page.locator('input').evaluate_all(
                    '(nodes)=>nodes.map(node=>node.value)')==['correct','wrong']
                case['passed']=result.ok and case['captured_exact_value'] and case['dom_unchanged']
                case['replay_errors']=[event.get('error') for event in result.log if event.get('status')=='error']
            except Exception as error:
                case['error_class']=type(error).__name__
                case['refusal_code']=author_refusal(error)
            finally:
                case['elapsed_seconds']=time.monotonic()-started
                case['synthetic_initial_replies']=injected
                case['provider_metadata']=records
                case['usage']=summarize_provider_calls(records)
                if driver is not None:
                    driver.close()
            stream.seek(0);json.dump(report,stream,ensure_ascii=False,indent=2);stream.truncate();stream.flush();os.fsync(stream.fileno())
        report['source_subset_after']=manifest()
        report['source_subset_stable']=before==report['source_subset_after']
        report['passed']=bool(report['cases']) and all(case['passed'] for case in report['cases']) and report['source_subset_stable']
        stream.seek(0);json.dump(report,stream,ensure_ascii=False,indent=2);stream.truncate();stream.flush();os.fsync(stream.fileno())
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    parser.add_argument('--provider',action='append',choices=['codex','claude'],required=True)
    parser.add_argument('--repair-rounds',type=int,choices=[0,1],default=0)
    parser.add_argument('--inject-initial-ambiguity',action='store_true')
    args=parser.parse_args()
    result=run(args.output,args.provider,repair_rounds=args.repair_rounds,
               inject_initial_ambiguity=args.inject_initial_ambiguity)
    print(json.dumps({'passed':result['passed'],'cases':[
        {key:case.get(key) for key in ('provider','passed','error_class','elapsed_seconds')}
        for case in result['cases']]}))
    raise SystemExit(0 if result['passed'] else 1)
