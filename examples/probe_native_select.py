"""Owned AppKit popup probe. Does not launch or activate any application.

Requires ai.callabo.nlc.select-fixture already running in the background with
Alpha selected and selection count zero. No user app is used or restarted.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TARGET = 'ai.callabo.nlc.select-fixture'


def run_probe(driver):
    from nlc.driver import Driver, _attr, AXUIElementCreateApplication
    from nlc.flow import Selector
    from nlc.input_audit_process import ProcessInputAudit
    report = {'passed': False, 'target': TARGET, 'steps': [],
              'caret_preservation_verified': False, 'proves_no_intermediate_change': False}
    paths = [Path(__file__), ROOT/'tests/fixtures/NativeSelectFixture.swift',
             ROOT/'nlc/driver.py', ROOT/'nlc/input_audit.py', ROOT/'nlc/input_audit_process.py',
             ROOT/'helper/TactoHelper.swift', ROOT/'helper/nlc-helper', ROOT/'launcher/nlc']
    report['source_hashes'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in paths if p.is_file()}
    try:
        pid = Driver()._resolve(TARGET, launch=False)
        if pid is None:
            raise RuntimeError('owned_fixture_not_running')
        app = AXUIElementCreateApplication(pid)
        def background():
            if _attr(app, 'AXFrontmost') is not False:
                raise RuntimeError('fixture_not_observably_background')
        def observe():
            background()
            result = driver.observe(TARGET, 'tree_full')
            if result.get('type') != 'observation':
                raise RuntimeError('observation_failed:' + str(result.get('error')))
            return result
        def value(obs, identifier):
            index, _ = Selector(identifier=identifier).resolve(obs['tree'])
            lines = [line for line in obs['tree'].splitlines()
                     if line.strip().split(maxsplit=1)[0] == str(index)]
            if len(lines) != 1:
                raise RuntimeError('fixture_value_ambiguous')
            return Selector._attr(lines[0], 'Value')
        def control(obs):
            index, _ = Selector(role='PopUpButton',identifier='nlc-fixture-choice').resolve(obs['tree'])
            return index
        initial = observe()
        if value(initial,'nlc-fixture-result') != 'Alpha' or value(initial,'nlc-fixture-count') != '0':
            raise RuntimeError('fixture_initial_state_mismatch')
        report['initial_pid'] = pid
        report['initial_tree'] = initial['tree']
        report['initial_popup_count'] = len(initial.get('custom_selects', {}))
        # The refusal occurs AFTER observing the open menu. A valid choice then
        # uses that same menu after another observation, without opening again.
        specs = [('open','open_select',None), ('refuse','select_custom_option','Missing'),
                 ('choose_beta','select_custom_option','Beta'),
                 ('reopen','open_select',None), ('choose_alpha','select_custom_option','Alpha')]
        for label, name, option in specs:
            obs = observe()
            index = control(obs)
            metadata = obs.get('custom_selects',{}).get(str(index),{})
            expected_state = 'closed' if name == 'open_select' else 'open'
            if metadata.get('state') != expected_state:
                raise RuntimeError('unexpected_menu_state:' + label + ':' + str(metadata))
            if option and option != 'Missing':
                options = [o for o in metadata.get('options',[]) if o.get('label') == option]
                if len(options) != 1 or options[0].get('disabled'):
                    raise RuntimeError('expected_option_unavailable')
            action = {'name':name,'element_index':index}
            if option is not None:
                action['option_label'] = option
            step = {'label':label,'action':action}
            report['steps'].append(step)
            audit = ProcessInputAudit(strategy='application_frontmost',interval=.005,
                                      startup_timeout=10,stop_timeout=3)
            try:
                with audit:
                    background()
                    result = driver.act(obs['obs_id'],TARGET,action)
                    step['result'] = result
                    time.sleep(.15)
                    after = observe()
                    step['result_value'] = value(after,'nlc-fixture-result')
                    step['selection_count'] = value(after,'nlc-fixture-count')
                    step['menu'] = after.get('custom_selects',{}).get(str(control(after)))
            finally:
                step['audit'] = audit.report()
            if step['audit']['status'] != 'UNCHANGED_SAMPLED':
                raise RuntimeError('input_preservation_unverified:' + label)
            if label == 'refuse':
                if (result.get('type') != 'error' or result.get('effect') != 'not_started'
                        or step['selection_count'] != '0' or step['result_value'] != 'Alpha'
                        or (step['menu'] or {}).get('state') != 'open'):
                    raise RuntimeError('refusal_not_preserved')
            elif result.get('type') != 'acted' or result.get('unverified'):
                raise RuntimeError('action_not_verified:' + label)
            expected = ('Beta','1') if label in ('choose_beta','reopen') else ('Alpha','2') if label == 'choose_alpha' else ('Alpha','0')
            if (step['result_value'],step['selection_count']) != expected:
                raise RuntimeError('independent_fixture_readback_failed:' + label)
        report['passed'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__ + ':' + str(exc)
        report['cleanup'] = 'no further UI action; only owned fixture may exit separately'
    return report


def run_model_probe(driver, *, provider):
    from nlc.ask import ask_here_paged
    from nlc.driver import Driver, _attr, AXUIElementCreateApplication
    from nlc.flow import Selector
    from nlc.general import run
    from nlc.input_audit_process import ProcessInputAudit
    report={'passed':False,'target':TARGET,'provider':provider,'plans':[],
            'evaluation_kind':'real_model_owned_appkit','caret_preservation_verified':False}
    paths=sorted((ROOT/'nlc').glob('*.py'))+[Path(__file__),
        ROOT/'helper/TactoHelper.swift', ROOT/'helper/WindowInput.swift',
        ROOT/'helper/nlc-helper', ROOT/'helper/NLC Helper.app/Contents/MacOS/NLCHelper',
        ROOT/'launcher/nlc', ROOT/'tests/fixtures/NativeSelectFixture.swift']
    def sources():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report['source_before']=sources()
    audit=ProcessInputAudit(interval=.01,startup_timeout=10,stop_timeout=3,max_samples=20000)
    try:
        pid=Driver()._resolve(TARGET,launch=False)
        if pid is None: raise RuntimeError('owned_fixture_not_running')
        app=AXUIElementCreateApplication(pid)
        def background():
            if _attr(app,'AXFrontmost') is not False:
                raise RuntimeError('fixture_not_observably_background')
        def readback():
            background()
            obs=driver.observe(TARGET,'tree_full')
            if obs.get('type')!='observation': raise RuntimeError('observation_failed')
            values=[]
            for identifier in ('nlc-fixture-result','nlc-fixture-count'):
                index,_=Selector(identifier=identifier).resolve(obs['tree'])
                line=next(line for line in obs['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(index))
                values.append(Selector._attr(line,'Value'))
            return values
        if readback()!=['Alpha','0']: raise RuntimeError('fixture_initial_state_mismatch')
        def planner(goal,candidates,done):
            background()
            answer=ask_here_paged(goal,candidates,done,provider=provider,max_pages=2)
            report['plans'].append({'candidates':candidates,'answer':answer})
            background()
            return answer
        with audit:
            result=run(driver,TARGET,'선택 메뉴에서 Beta를 한 번 선택하고, 결과 표시의 값을 읽어 알려주세요.',
                       planner,max_screens=8,timeout_seconds=120)
            report.update(loop_ok=result.ok,why=result.why,said=result.said,
                          events=result.events,provider_calls=result.provider_calls,readback=readback())
        report['source_after']=sources()
        report['source_stable']=report['source_before']==report['source_after']
        report['passed']=(result.ok and report['readback']==['Beta','1'] and 'Beta' in result.said
                          and audit.report()['status']=='UNCHANGED_SAMPLED' and report['source_stable'])
    except Exception as exc:
        report['error']=type(exc).__name__+':'+str(exc)
    finally:
        report['audit']=audit.report()
    return report


def run_and_save(driver, *, output, transport, provider=None):
    with Path(output).open('x') as stream:
        result=run_model_probe(driver,provider=provider) if provider else run_probe(driver)
        result['transport']=transport
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    # Reserve first; a failed permission check also receives an artifact.
    with args.output.open('x') as stream:
        try:
            from nlc.driver import Driver
            result = run_probe(Driver())
        except Exception as exc:
            result = {'passed':False,'error':type(exc).__name__ + ':' + str(exc)}
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({'passed':result['passed'],'error':result.get('error'),'report':str(args.output)},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
