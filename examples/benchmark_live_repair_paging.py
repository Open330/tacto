"""Live provider choices over paged candidates; simulated driver, no GUI input."""
import argparse
import contextlib
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tacto.auto_repair import AutoRepair, choose_candidate
from tacto.cli import _run_flow
from tacto.flow_batch import BatchDriver
from tacto.provider_observation import summarize_provider_calls
from tests.test_auto_repair import recipe
from tests.test_repair_candidate_paging import ManyControls


def source_manifest():
    paths=list((ROOT/'tacto').glob('*.py'))+[Path(__file__).resolve(),
        ROOT/'tests/test_auto_repair.py',ROOT/'tests/test_repair_candidate_paging.py',
        ROOT/'tests/test_flow_batch.py',ROOT/'tests/test_flow_safety_defaults.py',ROOT/'tests/test_runtime_contract.py']
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--provider',required=True,choices=['codex','claude'])
    args=parser.parse_args();base=args.output.resolve();base.mkdir(mode=0o700)
    before=source_manifest();app=ManyControls();pages=[];messages=[]
    def choose(packet,timeout):
        started=time.monotonic()
        response=choose_candidate(packet,timeout,provider=args.provider)
        pages.append({'candidate_page':packet['candidate_page'],'candidate_count':len(packet['candidates']),
            'packet_bytes':len(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode()),
            'seconds':time.monotonic()-started,'result':response.to_dict()})
        (base/'progress.json').write_text(json.dumps({'pages':pages},ensure_ascii=False,indent=2))
        (base/'progress.json').chmod(0o600)
        return response
    automatic=AutoRepair(provider=args.provider,maximum=4,timeout=60,chooser=choose)
    started=time.monotonic()
    with (base/'replay.log').open('w') as log, contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
        # No native helper or indication process is created for this fake driver.
        code,learned=automatic.run(recipe(),{},['--helper','--no-indicator'],
            driver=BatchDriver(app),run_flow=_run_flow,emit=messages.append)
    after=source_manifest()
    checks={'completed':code==0 and app.value=='12','selector_learned':learned.steps[1].selector.identifier=='Two',
        'prefix_dispatched_once':sum(a['element_index']==2 for a in app.calls)==1,
        'successful_action_sequence':[a['element_index'] for a in app.calls]==[2,3],
        'later_page_reached':len(pages)>1 and any(p['candidate_page']['offset']>0 for p in pages),
        'packets_within_byte_budget':all(p['packet_bytes']<=8192 for p in pages),
        'model_call_budget':len(pages)==automatic.calls and automatic.calls<=4,'source_subset_stable':before==after}
    report={'passed':all(checks.values()),'checks':checks,'provider':args.provider,'seconds':time.monotonic()-started,
        'exit':code,'simulated_action_indices':[action['element_index'] for action in app.calls],
        'simulated_final_value':app.value,'pages':pages,'provider_calls':automatic.provider_calls,
        'usage':summarize_provider_calls(automatic.provider_calls),'messages':messages,
        'source_before':before,'source_after':after,
        'scope':'live provider and replay engine with simulated driver; no GUI/native operation or user data',
        'limits':['not real-app recovery','8192-byte JSON packet budget excludes provider framing',
                  'requested provider explicit; actual serving model/billing may remain unknown',
                  'local subprocess cleanup is not remote cancellation proof']}
    (base/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(base/'report.json').chmod(0o600)
    (base/'replay.log').chmod(0o600)
    print(json.dumps({'passed':report['passed'],'checks':checks,'model_calls':automatic.calls,'provider':args.provider}))
    return 0 if report['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
