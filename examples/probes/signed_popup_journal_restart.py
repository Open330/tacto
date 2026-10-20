"""Two fresh signed CLI workers share one journal; owned popup input only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess

from examples.probes.signed_popup_flow_replay import ROOT,TARGET,owned_flow,reserve_report


def journal_contents(directory):
    paths=sorted(directory.iterdir())
    if [p.name for p in paths]!=[f'{n:05d}.jsonl' for n in range(4)]:
        raise ValueError('four_dispatch_slots_required')
    rows={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    for path in paths:
        records=[json.loads(line) for line in path.read_text().splitlines()]
        if len(records)!=2 or records[0].get('state')!='reserved' or (records[1].get('state')!='reply_received' or records[1].get('category')!='acted'):
            raise ValueError('completed_dispatch_slots_required')
    return rows


def dispatch_worker(driver,config):
    """Internal explicit fixture worker; metadata-only config is not consent."""
    import stat
    path=Path(config)
    info=path.lstat();parent=path.parent;parent_info=parent.lstat()
    if (not path.is_absolute() or path.resolve()!=path or not stat.S_ISREG(info.st_mode)
            or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600
            or info.st_size>1024 or parent.resolve()!=parent
            or not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid!=os.getuid()
            or stat.S_IMODE(parent_info.st_mode)!=0o700):
        raise ValueError('private_worker_config_required')
    data=json.loads(path.read_text())
    if type(data) is not dict or set(data)!={'expected_pid'} or type(data['expected_pid']) is not int or data['expected_pid']<=0:
        raise ValueError('invalid_worker_config')
    from examples.probes.signed_popup_flow_replay import main as dispatch
    result=dispatch(driver,parent/'dispatch-worker-report.json',data['expected_pid'],journal_directory=parent/'journal')
    if not result['passed']:raise ValueError('audited_worker_failed')
    return result


def main(driver,output,expected_pid):
    with reserve_report(output) as stream:
        report={'passed':False,'scope':'two fresh signed CLI processes with one durable journal',
                'actual_human_typing_verified':False,'comparison_eligible':False,
                'crash_recovery_verified':False,'automatic_resume_verified':False,'whole_goal_complete':False}
        names=['examples/probes/signed_popup_journal_restart.py','examples/probes/signed_popup_flow_replay.py',
               'examples/eval.py','tacto/dispatch_journal.py','tacto/cli.py','tacto/flow.py',
               'launcher/tacto','helper/Tacto Helper.app/Contents/MacOS/TactoHelper']
        hashes=lambda:{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in names}
        report['source_before']=hashes()
        try:
            if type(expected_pid) is not int or expected_pid<=0:raise ValueError('owned_pid_required')
            from tacto.helper import InheritedHelperDriver
            if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
                raise ValueError('signed_channel_required')
            if 'action.open_select.explicit_ax' not in driver.capabilities:raise ValueError('explicit_opening_required')
            from tacto.driver import Driver,_attr,AXUIElementCreateApplication
            from tacto.flow import Selector
            from tacto.input_audit_process import ProcessInputAudit
            from examples.focus_diagnostics import FocusDiagnosticReader
            metadata=Driver();base=Path(output).parent
            def receiving():
                if metadata._resolve(TARGET,launch=False)!=expected_pid:raise ValueError('owned_pid_changed')
                if _attr(AXUIElementCreateApplication(expected_pid),'AXFrontmost') is not False:
                    raise ValueError('owned_fixture_not_background')
                observation=driver.observe(TARGET,'tree_full')
                if observation.get('type')!='observation':raise ValueError('receiving_unavailable')
                def value(identifier):
                    index,_=Selector(identifier=identifier).resolve(observation['tree'])
                    lines=[line for line in observation['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(index)]
                    if len(lines)!=1:raise ValueError('receiving_ambiguous')
                    return Selector._attr(lines[0],'Value')
                index,_=Selector(identifier='tacto-fixture-choice').resolve(observation['tree'])
                return {'value':value('tacto-fixture-result'),'count':value('tacto-fixture-count'),
                        'menu_state':observation.get('custom_selects',{}).get(str(index),{}).get('state')}
            report['initial']=receiving()
            if report['initial']!={'value':'Alpha','count':'0','menu_state':'closed'}:raise ValueError('initial_mismatch')
            flow_path=base/'flow.json'
            with os.fdopen(os.open(flow_path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'w') as file:
                file.write(owned_flow().to_json())
            journal=base/'journal';journal.mkdir(mode=0o700)
            # Both fresh workers must use this measured checkout, regardless
            # of which older release the user's global alias points to.
            launcher=(ROOT/'launcher/tacto').resolve(strict=True)
            report['worker_launcher']=str(launcher)
            report['worker_launcher_sha256']=hashlib.sha256(launcher.read_bytes()).hexdigest()
            config=base/'worker-config.json'
            with os.fdopen(os.open(config,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'w') as file:
                json.dump({'expected_pid':expected_pid},file)
            dispatch_argv=[str(launcher),'eval','signed_native_select_journal_dispatch_worker','--select-journal-worker-config',str(config)]
            restart_argv=[str(launcher),'run',str(flow_path),'--helper','--yes','--think','0','--no-indicator','--journal-dir',str(journal)]
            report['workers']=[]
            for ordinal in range(2):
                focus=FocusDiagnosticReader(base/f'worker-{ordinal}-focus.jsonl')
                audit=ProcessInputAudit(reader=focus,interval=.005,startup_timeout=10,stop_timeout=3)
                row={};report['workers'].append(row)
                try:
                    with audit:
                        receiving()
                        from tacto.provider_io import require_provider_child_ownership,communicate_owned_provider
                        require_provider_child_ownership()
                        with subprocess.Popen(dispatch_argv if ordinal==0 else restart_argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True) as process:
                            row['pid']=process.pid
                            stdout,stderr=communicate_owned_provider(process,timeout=60)
                            row['exit_code']=process.returncode
                        row['receiving']=receiving()
                finally:row['input_audit']=audit.report()
                for kind,data in (('stdout',stdout),('stderr',stderr)):
                    with os.fdopen(os.open(base/f'worker-{ordinal}-{kind}.txt',os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600),'w') as file:file.write(data)
                if row['input_audit']['status']!='UNCHANGED_SAMPLED':raise ValueError('worker_input_audit_unverified')
                if row['receiving']!={'value':'Alpha','count':'2','menu_state':'closed'}:raise ValueError('worker_receiving_mismatch')
                snapshot=journal_contents(journal)
                if ordinal==0:
                    if row['exit_code']!=0:raise ValueError('initial_worker_failed')
                    worker_report=json.loads((base/'dispatch-worker-report.json').read_text())
                    if not worker_report.get('passed') or worker_report.get('model_requests')!=0 or not worker_report.get('durable_journal_used'):
                        raise ValueError('audited_worker_report_unverified')
                    report['first_worker_each_dispatch_checked']=True
                    report['first_worker_model_requests']=worker_report['model_requests']
                    report['journal_before_restart']=snapshot
                else:
                    if row['exit_code']!=1 or 'dispatch_journal_requires_reconciliation' not in stdout:
                        raise ValueError('restart_refusal_unverified')
                    if snapshot!=report['journal_before_restart']:raise ValueError('journal_changed_after_restart')
                    report['journal_after_restart']=snapshot
            if report['workers'][0]['pid']==report['workers'][1]['pid']:raise ValueError('fresh_process_identity_unverified')
            report.update(passed=True,restart_refused=True,receiving_count_unchanged_after_restart=True,
                          journal_slots_unchanged_after_restart=True)
        except Exception as error:report.update(passed=False,error_class=type(error).__name__,error=str(error))
        finally:
            report['source_after']=hashes();report['source_stable']=report['source_after']==report['source_before']
            if not report['source_stable']:report['passed']=False
            json.dump(report,stream,indent=2);stream.write('\n')
        return report
