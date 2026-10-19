"""Owned empty-document lifecycle stress; no model or user-window cleanup."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT / 'examples'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from comparison_fixture_worker import FixtureWorker
from focus_witness import FocusWitness
from product_comparison import _locked,source_manifest
from tacto.pin_diagnostic import diagnostic_lines


def run(launcher,cycles):
    if type(cycles) is not int or not 1<=cycles<=8:
        raise ValueError('cycles must be 1..8')
    report={'scope':'owned_empty_document_repeated_lifecycle_and_separate_signed_cli_pin',
            'model_requested':False,'comparison_eligible':False,'requested_cycles':cycles,'cycles':[]}
    before=source_manifest();start=time.monotonic()
    if _locked() is not False:
        return {**report,'status':'not_run','reason':'session_unavailable'}
    witness=None
    try:
        with FocusWitness() as witness:
            for index in range(cycles):
                record={'index':index,'reads':[],'cleanup_status':'not_requested'}
                report['cycles'].append(record)
                worker=None
                try:
                    if _locked() is not False:raise RuntimeError('session_unavailable')
                    worker=FixtureWorker(launcher)
                    record['prepared_fixture']=worker.request('prepare')
                    setup=worker.request('setup');record['setup']=setup['evidence']
                    path=setup['evidence']['path']
                    # Two fresh CLI channels, one immediately after fixture setup.
                    # No delay/retry substitutes for the first recorded outcome.
                    for read_index in range(2):
                        if _locked() is not False:raise RuntimeError('session_unavailable')
                        began=time.monotonic()
                        try:
                            result=subprocess.run([launcher,'observe','com.apple.TextEdit','--helper','--document',path],
                                capture_output=True,text=True,timeout=35,close_fds=True)
                        except subprocess.TimeoutExpired as error:
                            def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
                            record['reads'].append({'index':read_index,'exit':None,'timed_out':True,
                                'elapsed_seconds':time.monotonic()-began,'stdout':decoded(error.stdout),
                                'stderr':decoded(error.stderr),'reported_pin_diagnostics':[]})
                            record['error']='read_timeout'
                            break
                        record['reads'].append({'index':read_index,'exit':result.returncode,
                            'elapsed_seconds':time.monotonic()-began,'stdout':result.stdout,'stderr':result.stderr,
                            'reported_pin_diagnostics':diagnostic_lines(result.stdout)})
                        if any(d['reason']=='user_aborted' for d in record['reads'][-1]['reported_pin_diagnostics']):
                            record['error']='user_aborted'
                            break
                        if result.returncode!=0:
                            record['error']='read_failed_stop_unknown'
                            break
                    if not record.get('error'):record['empty_oracle']=worker.request('empty_oracle')
                except (KeyboardInterrupt,Exception) as error:
                    record['error']=type(error).__name__
                finally:
                    if worker is not None:
                        try:
                            if record.get('error')=='user_aborted':
                                record['cleanup_status']='user_aborted_left_as_is'
                            elif record.get('error'):
                                record['cleanup_status']='failure_or_interrupt_left_as_is'
                            elif not worker.poisoned and _locked() is False:
                                cleanup=worker.request('cleanup')
                                record['cleanup_status']=cleanup['status'];record['cleanup_evidence']=cleanup['evidence']
                            else:record['cleanup_status']='unavailable_effect_unknown'
                        except Exception as error:
                            record['cleanup_status']='unverified_effect_unknown';record['cleanup_error']=type(error).__name__
                        finally:
                            try:worker.close()
                            except Exception as error:record['worker_close_error']=type(error).__name__
                            record['worker_exit']=worker.process.returncode
                # Do not accumulate ambiguous owned windows or lose their evidence.
                if record['cleanup_status']!='closed' or record.get('error') or record.get('worker_close_error'):break
    except (KeyboardInterrupt,Exception) as error:
        report['error']=type(error).__name__
    report['focus']=witness.summary() if witness is not None else {'activations':None}
    report['elapsed_seconds']=time.monotonic()-start
    report['source_before']=before;report['source_after']=source_manifest()
    report['source_stable']=before==report['source_after']
    report['recorded_cycles']=len(report['cycles'])
    report['unverified_cleanup_cycles']=sum(c['cleanup_status']!='closed' for c in report['cycles'])
    report['completed_cycles']=sum(not c.get('error') and not c.get('worker_close_error')
        and len(c['reads'])==2 and all(r['exit']==0 for r in c['reads'])
        and c.get('empty_oracle')=={'observable':True,'exact_empty':True}
        and c['cleanup_status']=='closed' and c.get('cleanup_evidence',{}).get('artifact_removed') is True
        and c.get('worker_exit')==0 for c in report['cycles'])
    report['status']=('passed' if not report.get('error') and report['completed_cycles']==cycles and report['source_stable']
        and report['focus']['activations']==[] else 'failed')
    if report.get('error')=='KeyboardInterrupt' or any(c.get('error')=='KeyboardInterrupt' for c in report['cycles']):report['status']='interrupted'
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher',required=True)
    parser.add_argument('--cycles',type=int,default=6,choices=range(1,9))
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    launcher=Path(args.launcher)
    if not launcher.is_absolute() or not launcher.is_file() or not os.access(launcher,os.X_OK):
        parser.error('--launcher requires an absolute executable path')
    # Reserve before GUI work; never overwrite another report.
    fd=os.open(args.report,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w') as output:
        try:report=run(str(launcher),args.cycles)
        except (KeyboardInterrupt,Exception) as error:
            report={'status':'interrupted' if isinstance(error,KeyboardInterrupt) else 'failed','scope':'owned_pin_probe','error':type(error).__name__,
                    'comparison_eligible':False}
        json.dump(report,output,ensure_ascii=False)
        output.write('\n')
    if report.get('status')=='interrupted':raise KeyboardInterrupt
    print(json.dumps({k:report.get(k) for k in ('status','completed_cycles','recorded_cycles','unverified_cleanup_cycles','elapsed_seconds','source_stable')}))
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':raise SystemExit(main())
