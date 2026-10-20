"""Actual owned popup Flow IR replay; no model, activation, or retry."""
import hashlib
import json
import os
from pathlib import Path
import stat

TARGET='ai.callabo.tacto.select-fixture'
ROOT=Path(__file__).resolve().parents[2]


def reserve_report(output):
    path=Path(output);parent=path.parent;info=parent.lstat()
    if (not path.is_absolute() or parent.resolve()!=parent or not stat.S_ISDIR(info.st_mode)
            or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o700):
        raise ValueError('private_report_directory_required')
    return os.fdopen(os.open(path,os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_WRONLY,0o600),'w')


def owned_flow():
    from tacto.flow import Flow,Step,Selector,Postcondition
    from tacto.contract import SafetyContract,Resource
    choice=Selector(role='PopUpButton',identifier='tacto-fixture-choice')
    result=Selector(identifier='tacto-fixture-result')
    def safety():
        return SafetyContract(effects=['write_ephemeral'],resource=Resource(scope='entry'),reversibility='reversible')
    steps=[]
    for label in ('Beta','Alpha'):
        steps.append(Step('open_select',choice,params={'opening_action':'AXPress'},safety=safety()))
        steps.append(Step('select_custom_option',choice,params={'option_label':label},safety=safety(),
                          postcondition=Postcondition(field=result,attribute='Value',equals=label)))
    # Exercise serialized IR acceptance, not just hand-built Step objects.
    return Flow.from_json(Flow('owned explicit popup replay',TARGET,steps).to_json())


EXPECTED_TRANSITIONS=(('Alpha','0','open'),('Beta','1','closed'),
                      ('Beta','1','open'),('Alpha','2','closed'))


def checked_transition_response(response,receiving,ordinal,audit_status):
    if type(ordinal) is not int or not 0<=ordinal<len(EXPECTED_TRANSITIONS):
        return {'type':'error','error':'owned_dispatch_limit','effect':'unknown','reobserve_required':True}
    actual=tuple(receiving.get(key) for key in ('value','count','menu_state'))
    if audit_status!='UNCHANGED_SAMPLED' or actual!=EXPECTED_TRANSITIONS[ordinal]:
        return {'type':'error','error':'owned_transition_unverified','effect':'unknown','reobserve_required':True}
    return response


def main(driver,output,expected_pid,*,journal_directory=None):
    with reserve_report(output) as stream:
        sources=['examples/probes/signed_popup_flow_replay.py','examples/eval.py','tacto/flow.py',
                 'tacto/protocol.py','tacto/helper.py','helper/TactoHelper.swift','launcher/tacto.swift',
                 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper','launcher/tacto','tests/fixtures/NativeSelectFixture.swift']
        hashes=lambda:{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in sources}
        report={'passed':False,'scope':'one new owned background popup serialized Flow IR replay',
                'actual_human_typing_verified':False,'proves_no_intermediate_change':False,
                'comparison_eligible':False,'whole_goal_complete':False,'source_before':hashes(),'dispatches':[]}
        try:
            if type(expected_pid) is not int or expected_pid<=0:raise ValueError('owned_pid_required')
            if 'action.open_select.explicit_ax' not in getattr(driver,'capabilities',()):
                raise ValueError('explicit_opening_capability_required')
            from tacto.helper import InheritedHelperDriver
            if not isinstance(driver,InheritedHelperDriver) or not InheritedHelperDriver.available():
                raise ValueError('signed_channel_required')
            report['transport']='inherited-helper'
            from tacto.driver import Driver,_attr,AXUIElementCreateApplication
            from tacto.flow import Selector,replay
            from tacto.input_audit_process import ProcessInputAudit
            from examples.focus_diagnostics import FocusDiagnosticReader
            from tacto.provider_observation import capture_provider_calls
            metadata=Driver()
            def background():
                if metadata._resolve(TARGET,launch=False)!=expected_pid:
                    raise ValueError('owned_fixture_pid_changed')
                if _attr(AXUIElementCreateApplication(expected_pid),'AXFrontmost') is not False:
                    raise ValueError('owned_fixture_not_background')
            def receiving(obs):
                if obs.get('type')!='observation':raise ValueError('owned_observation_unavailable')
                def value(identifier):
                    index,_=Selector(identifier=identifier).resolve(obs['tree'])
                    lines=[s for s in obs['tree'].splitlines() if s.strip().split(maxsplit=1)[0]==str(index)]
                    if len(lines)!=1:raise ValueError('owned_receiving_ambiguous')
                    return Selector._attr(lines[0],'Value')
                choice,_=Selector(role='PopUpButton',identifier='tacto-fixture-choice').resolve(obs['tree'])
                return {'value':value('tacto-fixture-result'),'count':value('tacto-fixture-count'),
                        'menu_state':obs.get('custom_selects',{}).get(str(choice),{}).get('state')}
            background();report['initial']=receiving(driver.observe(TARGET,'tree_full'))
            if report['initial']!={'value':'Alpha','count':'0','menu_state':'closed'}:
                raise ValueError('owned_initial_state_mismatch')
            class AuditedDriver:
                def __getattr__(self,name):return getattr(driver,name)
                def act(self,obs_id,target,action,**kwargs):
                    if target!=TARGET or len(report['dispatches'])>=4:
                        return {'type':'error','error':'owned_dispatch_limit','effect':'not_started'}
                    row={'action':dict(action)};report['dispatches'].append(row)
                    focus=FocusDiagnosticReader(Path(output).parent/('flow-focus-'+str(len(report['dispatches']))+'.jsonl'))
                    audit=ProcessInputAudit(reader=focus,interval=.005,startup_timeout=10,stop_timeout=3)
                    try:
                        with audit:
                            background()
                            response=driver.act(obs_id,target,action,**kwargs);row['response']=response
                            background();row['receiving']=receiving(driver.observe(TARGET,'tree_full'))
                    finally:row['input_audit']=audit.report()
                    checked=checked_transition_response(response,row['receiving'],len(report['dispatches'])-1,
                                                        row['input_audit']['status'])
                    row['returned_response']=checked
                    return checked
            flow=owned_flow();report['serialized_flow']=json.loads(flow.to_json())
            from contextlib import ExitStack
            with ExitStack() as stack, capture_provider_calls() as calls:
                replay_driver=AuditedDriver()
                if journal_directory is not None:
                    from tacto.dispatch_journal import DurableDispatchDriver
                    replay_driver=stack.enter_context(DurableDispatchDriver(replay_driver,journal_directory))
                    report['durable_journal_used']=True
                result=replay(replay_driver,flow,budget=0)
            report['provider_calls']=calls;report['model_requests']=len(calls)
            report['replay']={'ok':result.ok,'log':result.log,'events':result.events}
            background();report['final']=receiving(driver.observe(TARGET,'tree_full'))
            expected=list(EXPECTED_TRANSITIONS)
            actual=[(s['receiving']['value'],s['receiving']['count'],s['receiving']['menu_state']) for s in report['dispatches']]
            if not result.ok or calls or actual!=expected or report['final']!={'value':'Alpha','count':'2','menu_state':'closed'}:
                raise ValueError('owned_replay_or_receiving_unverified')
            report['passed']=True
        except Exception as error:
            report.update(passed=False,error_class=type(error).__name__,error=str(error))
        finally:
            report['source_after']=hashes();report['source_stable']=report['source_before']==report['source_after']
            if not report['source_stable']:report['passed']=False
            json.dump(report,stream,indent=2);stream.write('\n')
        return report
