"""Owned AppKit popup probe. Does not launch or activate any application.

Requires ai.callabo.nlc.select-fixture already running in the background with
Alpha selected and selection count zero. No user app is used or restarted.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TARGET = 'ai.callabo.nlc.select-fixture'


def _record_native_call(receipts,operation,obs_id,target,action,*args,**kwargs):
    # Reserve an attempt before invoking the real driver: a transport exception
    # may follow input, and must never look like zero attempted actions.
    entry={'obs_id':obs_id,'target':target,'action':dict(action),'status':'attempted'}
    receipts.append(entry)
    try:
        result=operation(obs_id,target,action,*args,**kwargs)
    except BaseException as exc:
        entry['exception_class']=type(exc).__name__
        raise
    entry.update(status='returned',result=result)
    return result


def _record_receiving_check(entry,check):
    """A failed host read must not erase a returned backend input receipt."""
    try:check()
    except BaseException as exc:
        entry['receiving_check_error_class']=type(exc).__name__
        raise


def _combo_model_receipt_valid(receipts, *, allow_scrolling=False, option="Beta"):
    changes=[entry for entry in receipts if entry.get('action',{}).get('name')!='read']
    scrolls=[entry for entry in changes if entry.get('action',{}).get('name')=='scroll']
    if scrolls:
        if not allow_scrolling or len(scrolls)>12:return False
        for entry in scrolls:
            proof=entry.get('scroll_evidence',{})
            action=entry.get('action',{});result=entry.get('result',{})
            if (entry.get('target')!=TARGET or not _combo_scroll_receiving_valid(proof,action.get('direction'))
                    or result.get('type')!='acted' or result.get('unverified')):return False
        names=[entry.get('action',{}).get('name') for entry in changes]
        if names!=['open_select']+['scroll']*len(scrolls)+['select_custom_option']:return False
        changes=[entry for entry in changes if entry.get('action',{}).get('name')!='scroll']
    if [entry.get('action',{}).get('name') for entry in changes]!=['open_select','select_custom_option']:
        return False
    opened=changes[0];opening=opened.get('result',{})
    if (opened.get('target')!=TARGET or opening.get('type')!='acted'
            or opening.get('opened') is not True or opening.get('unverified')):
        return False
    selections=[entry for entry in receipts if entry.get('action',{}).get('name')=='select_custom_option']
    if len(selections)!=1:
        return False
    entry=selections[0];result=entry.get('result',{})
    return (entry.get('target')==TARGET and entry.get('action',{}).get('option_label')==option
            and result.get('type')=='acted' and result.get('selected')==option
            and result.get('input_route')=='combo_popup_bound_click'
            and result.get('selection_transition_verified') is True
            and type(result.get('posted_events')) is int and result['posted_events']==2
            and bool(entry.get('obs_id')) and result.get('obs_id')==entry['obs_id']
            and not result.get('unverified'))


def _model_probe_failure_reasons(report):
    failures=[]
    if report.get('loop_ok') is not True:failures.append('loop_not_completed')
    if report.get('readback_error_class'):failures.append('final_readback_unavailable')
    option=report['requested_option']
    if report.get('readback')!=[option,'1']:failures.append('fixture_readback_mismatch')
    if option not in report.get('said',[]):failures.append('model_answer_missing_value')
    if report.get('audit',{}).get('status')!='UNCHANGED_SAMPLED':failures.append('input_audit_not_unchanged')
    if report.get('source_stable') is not True:failures.append('source_changed_or_unverified')
    metrics=report.get('prompt_metrics',[])
    if not metrics or not all(type(row.get('tokens')) is int and 0<=row['tokens']<=report['prompt_budget'] for row in metrics):
        failures.append('prompt_budget_unverified')
    if report.get('combo'):
        if report.get('combo_readback')!={'control_value':option,'selected_index':str(report.get('requested_index',1)),'state':'closed'}:
            failures.append('combo_readback_mismatch')
        if not _combo_model_receipt_valid(report.get('native_receipts',[]),allow_scrolling=report.get('visible_items',0)>0,option=option):failures.append('combo_route_unverified')
    return failures


def _owned_combo_window_metadata(elements, pid):
    """Read only this fixture's AX/CG routing; never infer a dispatch target."""
    from tacto.native_window_binding import _MacBackend
    backend = _MacBackend()
    def read(operation):
        try:
            return {'value': operation()}
        except Exception as exc:
            return {'error': type(exc).__name__ + ':' + str(exc)[:200]}
    rows = []
    for element in elements:
        if backend.pid(element) != pid:
            raise RuntimeError('owned_combo_metadata_owner_mismatch')
        role = backend.attr(element, 'AXRole')
        if role not in ('AXComboBox', 'AXScrollArea', 'AXList', 'AXGroup', 'AXTextField'):
            continue
        def ax_window():
            window = backend.attr(element, 'AXWindow')
            if backend.pid(window) != pid:
                raise RuntimeError('owned_combo_window_owner_mismatch')
            return {'role': backend.attr(window, 'AXRole'),
                    'id': backend.window_id(window), 'bounds': backend.frame(window)}
        rows.append({'role': role, 'element_window_id': read(lambda: backend.window_id(element)),
                     'bounds': read(lambda: backend.frame(element)), 'ax_window': read(ax_window)})
        if len(rows) > 256:
            raise RuntimeError('owned_combo_metadata_limit')
    return {'elements': rows, 'cg_windows': [entry for entry in backend.windows() if entry['pid'] == pid],
            'input_sent': False, 'receiving_binding_verified': False}


def _combo_row_scroll_direction(viewport, row):
    import math
    if (len(viewport)!=4 or len(row)!=4 or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in (*viewport,*row))):
        raise ValueError('combo_geometry_unavailable')
    x,y,w,h=viewport;rx,ry,rw,rh=row
    if min(w,h,rw,rh)<=0 or rw>w or rh>h or rx<x or rx+rw>x+w:
        raise ValueError('combo_row_cannot_fit')
    if ry<y:return 'up'
    if ry+rh>y+h:return 'down'
    return None


def _combo_scroll_progress(before, after, direction):
    """A moving rectangle alone does not prove progress toward this option."""
    try:
        popup=before['popup_id']
        if type(popup) is not int or popup<=0 or type(after['popup_id']) is not int or after['popup_id']!=popup:
            return False
        viewport=tuple(before['viewport']);other=tuple(after['viewport'])
        row=tuple(before['row']);moved=tuple(after['row'])
        wanted=_combo_row_scroll_direction(viewport,row)
        actual=_combo_row_scroll_direction(other,moved)
        if (viewport!=other or direction not in ('up','down') or wanted!=direction
                or before.get('direction')!=wanted or after.get('direction')!=actual
                or (row[0],row[2],row[3])!=(moved[0],moved[2],moved[3])):
            return False
        if (direction=='down' and moved[1]>=row[1]) or (direction=='up' and moved[1]<=row[1]):
            return False
        def gap(rect):
            return max(viewport[1]-rect[1],0)+max(rect[1]+rect[3]-(viewport[1]+viewport[3]),0)
        return gap(moved)<gap(row)
    except (KeyError,TypeError,ValueError,OverflowError):
        return False


def _combo_scroll_receiving_valid(proof,direction):
    return (proof.get('scoped') is True and proof.get('readback_before')==['Alpha','0']
            and proof.get('readback_after')==['Alpha','0']
            and _combo_scroll_progress(proof.get('before',{}),proof.get('after',{}),direction))


def _require_combo_scroll_receiving(proof,direction):
    if not _combo_scroll_receiving_valid(proof,direction):
        raise RuntimeError('model_scroll_receiving_unverified')


def _expected_scroll_receiving_failure(report,planner_calls):
    """Prove this injected failure stopped; never relabel the underlying task."""
    receipts=report.get('native_receipts',[])
    if (type(planner_calls) is not int or planner_calls!=2 or report.get('passed') is not False
            or report.get('loop_ok') is not False or report.get('source_stable') is not True
            or report.get('provider_calls')!=[] or report.get('audit',{}).get('status')!='UNCHANGED_SAMPLED'
            or report.get('readback')!=['Alpha','0'] or report.get('combo_readback')!=
                {'control_value':'Alpha','selected_index':'0','state':'open'}
            or len(receipts)!=2 or [r.get('action',{}).get('name') for r in receipts]!=['open_select','scroll']):
        return False
    opened,scroll=receipts;proof=scroll.get('scroll_evidence',{})
    before,after=proof.get('before',{}),proof.get('after',{})
    try:
        geometry_ok=(type(before['popup_id']) is int and before['popup_id']>0
            and type(after['popup_id']) is int and before['popup_id']==after['popup_id']
            and before['viewport']==after['viewport']
            and _combo_row_scroll_direction(before['viewport'],before['row'])=='down'
            and _combo_row_scroll_direction(after['viewport'],after['row'])=='down')
    except (KeyError,TypeError,ValueError,OverflowError):return False
    events=report.get('events',[])
    return (geometry_ok and bool(events) and events[-1].get('state')=='unknown'
        and all(r.get('target')==TARGET and r.get('status')=='returned' for r in receipts)
        and opened.get('result',{}).get('type')=='acted' and opened.get('result',{}).get('opened') is True
        and scroll.get('result',{}).get('type')=='acted' and scroll.get('action',{}).get('direction')=='up'
        and type(scroll.get('action',{}).get('pages')) is int and scroll['action']['pages']==1
        and scroll.get('receiving_check_error_class')=='RuntimeError'
        and proof.get('scoped') is True and proof.get('readback_before')==['Alpha','0']
        and proof.get('readback_after')==['Alpha','0'] and not _combo_scroll_receiving_valid(proof,'up'))


def _owned_combo_scroll_geometry(pid, option):
    """Read an owned fixture only; all input remains on the signed channel."""
    from tacto.driver import Driver, _attr
    from tacto.native_window_binding import _MacBackend
    reader=Driver();reader.observe(TARGET,'tree_full')
    backend=_MacBackend()
    controls=[el for el in reader._latest[TARGET.lower()].elements
              if _attr(el,'AXRole')=='AXComboBox' and _attr(el,'AXIdentifier')=='nlc-fixture-choice']
    if len(controls)!=1:raise RuntimeError('owned_combo_control_ambiguous')
    def children(parent,role):
        result=[el for el in (_attr(parent,'AXChildren') or []) if _attr(el,'AXRole')==role]
        for el in result:
            if backend.pid(el)!=pid or not backend.equal(_attr(el,'AXParent'),parent):
                raise RuntimeError('owned_combo_scroll_parent_mismatch')
        return result
    if backend.pid(controls[0])!=pid:raise RuntimeError('owned_combo_scroll_owner_mismatch')
    scrolls=children(controls[0],'AXScrollArea')
    if len(scrolls)!=1:raise RuntimeError('owned_combo_scroll_ambiguous')
    scroll=scrolls[0];lists=children(scroll,'AXList');bars=children(scroll,'AXScrollBar')
    if len(lists)!=1 or len(bars)!=1:raise RuntimeError('owned_combo_scroll_structure_unavailable')
    rows=[]
    for row in children(lists[0],'AXGroup'):
        texts=children(row,'AXTextField')
        if len(texts)==1 and _attr(texts[0],'AXValue')==option:rows.append(row)
    if len(rows)!=1:raise RuntimeError('owned_combo_scroll_option_ambiguous')
    viewport=backend.frame(scroll);row=backend.frame(rows[0]);popup_id=backend.window_id(scroll)
    windows=[w for w in backend.windows() if w['id']==popup_id and w['pid']==pid and w.get('onscreen')]
    if len(windows)!=1 or tuple(windows[0]['bounds'])!=viewport:
        raise RuntimeError('owned_combo_scroll_popup_unavailable')
    return {'viewport':viewport,'row':row,'popup_id':popup_id,
            'direction':_combo_row_scroll_direction(viewport,row)}


def run_probe(driver, *, duplicates=False, combo=False, visible_items=0,focus_diagnostics_dir=None,opening_only=False,expected_pid=None,opening_action=None):
    if opening_action is not None and (opening_action!='AXPress' or focus_diagnostics_dir is None
            or type(expected_pid) is not int or expected_pid<=0):
        raise ValueError('explicit_opening_requires_owned_pid_and_focus_directory')
    if type(opening_only) is not bool or (opening_only and
            (focus_diagnostics_dir is None or type(expected_pid) is not int or expected_pid<=0)):
        raise ValueError('opening_diagnostic_requires_owned_pid_and_focus_directory')
    if type(visible_items) is not int or not 0<=visible_items<=20 or (visible_items and not combo):
        raise ValueError('visible items requires a ComboBox configuration')
    if focus_diagnostics_dir is not None:
        import stat
        directory=Path(focus_diagnostics_dir)
        info=directory.lstat()
        if (not directory.is_absolute() or directory.resolve()!=directory
                or not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid()
                or stat.S_IMODE(info.st_mode)!=0o700):
            raise ValueError('private_focus_diagnostics_directory_required')
    from tacto.driver import Driver, _attr, AXUIElementCreateApplication
    from tacto.flow import Selector
    from tacto.input_audit_process import ProcessInputAudit
    report = {'passed': False, 'target': TARGET, 'steps': [],
              'duplicate_options':duplicates,'combo':combo,'visible_items':visible_items,'comparison_eligible':False,
              'caret_preservation_verified': False, 'proves_no_intermediate_change': False}
    paths = [Path(__file__), ROOT/'tests/fixtures/NativeSelectFixture.swift',
             ROOT/'tacto/driver.py', ROOT/'tacto/input_audit.py', ROOT/'tacto/input_audit_process.py',
             ROOT/'tacto/native_window_binding.py', ROOT/'examples/focus_diagnostics.py',
             ROOT/'helper/TactoHelper.swift', ROOT/'helper/WindowInput.swift', ROOT/'helper/WindowDispatch.swift',
             ROOT/'helper/tacto-helper', ROOT/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper', ROOT/'launcher/tacto']
    report['source_hashes'] = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in paths if p.is_file()}
    try:
        pid = Driver()._resolve(TARGET, launch=False)
        if pid is None:
            raise RuntimeError('owned_fixture_not_running')
        if expected_pid is not None and pid!=expected_pid:
            raise RuntimeError('owned_fixture_pid_mismatch')
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
            index, _ = Selector(role='ComboBox' if combo else 'PopUpButton',identifier='nlc-fixture-choice').resolve(obs['tree'])
            return index
        initial = observe()
        if value(initial,'nlc-fixture-result') != 'Alpha' or value(initial,'nlc-fixture-count') != '0':
            raise RuntimeError('fixture_initial_state_mismatch')
        report['initial_pid'] = pid
        report['initial_tree'] = initial['tree']
        report['initial_popup_count'] = len(initial.get('custom_selects', {}))
        if combo and value(initial,'nlc-fixture-selected-index')!='0':
            raise RuntimeError('fixture_initial_index_mismatch')
        # The refusal occurs AFTER observing the open menu. A valid choice then
        # uses that same menu after another observation, without opening again.
        specs = [('open','open_select',None), ('refuse','select_custom_option','Missing'),
                 ('refuse_disabled','select_custom_option','Disabled'),
                 ('choose_beta','select_custom_option','Beta'),
                 ('reopen','open_select',None), ('choose_alpha','select_custom_option','Alpha')]
        if duplicates:specs.insert(3,('refuse_duplicate','select_custom_option','Duplicate'))
        if combo:
            specs=[step for step in specs if step[0]!='refuse_disabled']
            specs.insert(1,('refuse_already','select_custom_option','Alpha'))
        if visible_items:
            specs.insert(3,('refuse_clipped','select_custom_option','Beta'))
        if opening_only:
            specs=specs[:1]
            report['scope']='owned_popup_single_opening_focus_diagnostic'
            report['selection_task_complete']=False
        for label, name, option in specs:
            if visible_items and label in ('choose_beta','choose_alpha'):
                expected=('Alpha','0') if label=='choose_beta' else ('Beta','1')
                popup_id=None
                for attempt in range(12):
                    geometry=_owned_combo_scroll_geometry(pid,option)
                    if popup_id is not None and geometry['popup_id']!=popup_id:
                        raise RuntimeError('owned_combo_scroll_popup_changed')
                    popup_id=geometry['popup_id']
                    if geometry['direction'] is None:break
                    obs=observe()
                    indices=Selector(role='ScrollArea',ancestor='nlc-fixture-choice').resolve_all(obs['tree'])
                    if len(indices)!=1:raise RuntimeError('owned_combo_scroll_target_ambiguous')
                    index=indices[0]
                    action={'name':'scroll','element_index':index,'direction':geometry['direction'],'pages':1}
                    step={'label':'scroll_'+option+'_'+str(attempt),'action':action,'geometry_before':geometry}
                    report['steps'].append(step)
                    audit=ProcessInputAudit(strategy='application_frontmost',interval=.005,startup_timeout=10,stop_timeout=3)
                    try:
                        with audit:
                            background();step['result']=driver.act(obs['obs_id'],TARGET,action)
                            time.sleep(.15);after=observe()
                            step['readback']=[value(after,'nlc-fixture-result'),value(after,'nlc-fixture-count')]
                            step['geometry_after']=_owned_combo_scroll_geometry(pid,option)
                    finally:step['audit']=audit.report()
                    if (step['result'].get('type')!='acted' or step['result'].get('unverified')
                            or step['audit']['status']!='UNCHANGED_SAMPLED' or step['readback']!=list(expected)):
                        raise RuntimeError('owned_combo_scroll_unverified')
                    if step['geometry_after']['popup_id']!=popup_id:
                        raise RuntimeError('owned_combo_scroll_popup_changed')
                    if not _combo_scroll_progress(geometry,step['geometry_after'],action['direction']):
                        raise RuntimeError('owned_combo_scroll_no_progress')
                else:raise RuntimeError('owned_combo_scroll_limit')
            obs = observe()
            index = control(obs)
            metadata = obs.get('custom_selects',{}).get(str(index),{})
            expected_state = 'closed' if name == 'open_select' else 'open'
            if metadata.get('state') != expected_state:
                raise RuntimeError('unexpected_menu_state:' + label + ':' + str(metadata))
            if option and option not in ('Missing','Disabled','Duplicate'):
                options = [o for o in metadata.get('options',[]) if o.get('label') == option]
                if len(options) != 1 or options[0].get('disabled'):
                    raise RuntimeError('expected_option_unavailable')
            if option == 'Disabled':
                options = [o for o in metadata.get('options',[]) if o.get('label') == option]
                if len(options) != 1 or options[0].get('disabled') is not True:
                    raise RuntimeError('disabled_option_not_observed')
            if option=='Duplicate':
                options=[o for o in metadata.get('options',[]) if o.get('label')==option]
                if len(options)!=2 or any(o.get('disabled') is not False for o in options):
                    raise RuntimeError('duplicate_options_not_observed')
            action = {'name':name,'element_index':index}
            if opening_action is not None and name=='open_select':action['opening_action']=opening_action
            if option is not None:
                action['option_label'] = option
            step = {'label':label,'action':action}
            report['steps'].append(step)
            reader=None
            if focus_diagnostics_dir is not None:
                from examples.focus_diagnostics import FocusDiagnosticReader
                sidecar=Path(focus_diagnostics_dir)/('focus-'+str(len(report['steps']))+'.jsonl')
                reader=FocusDiagnosticReader(sidecar)
                step['focus_diagnostics_path']=str(sidecar)
                step['focus_diagnostics_authorizes_input']=False
            audit = ProcessInputAudit(reader=reader,strategy='application_frontmost',interval=.005,
                                      startup_timeout=10,stop_timeout=3)
            try:
                with audit:
                    background()
                    if reader is not None:step['action_started_at']=time.monotonic()
                    result = driver.act(obs['obs_id'],TARGET,action)
                    if reader is not None:step['action_finished_at']=time.monotonic()
                    step['result'] = result
                    time.sleep(.15)
                    after = observe()
                    step['result_value'] = value(after,'nlc-fixture-result')
                    step['selection_count'] = value(after,'nlc-fixture-count')
                    step['menu'] = after.get('custom_selects',{}).get(str(control(after)))
                    if combo:
                        step['selected_index']=value(after,'nlc-fixture-selected-index')
                        step['owned_tree_after']=after['tree']
                        from ApplicationServices import AXUIElementCopyActionNames,AXUIElementIsAttributeSettable
                        own_reader=Driver();own_reader.observe(TARGET,'tree_full')
                        observed=own_reader._latest[TARGET.lower()]
                        step['owned_control_action_metadata']=[]
                        readable=list(observed.elements)
                        for parent in observed.elements:
                            readable.extend(c for c in (_attr(parent,'AXChildren') or []) if _attr(c,'AXRole')=='AXList')
                        step['owned_window_metadata'] = _owned_combo_window_metadata(readable, pid)
                        for el in readable:
                            role=_attr(el,'AXRole')
                            if role in ('AXComboBox','AXScrollArea','AXList','AXGroup','AXTextField'):
                                error,names=AXUIElementCopyActionNames(el,None)
                                step['owned_control_action_metadata'].append({'role':role,
                                    'action_error':error,'actions':list(names or []),
                                    'selected_children_settable':AXUIElementIsAttributeSettable(el,'AXSelectedChildren',None),
                                    'selected_settable':AXUIElementIsAttributeSettable(el,'AXSelected',None),
                                    'children_roles':[_attr(c,'AXRole') for c in (_attr(el,'AXChildren') or [])]})
            finally:
                step['audit'] = audit.report()
            if step['audit']['status'] != 'UNCHANGED_SAMPLED':
                raise RuntimeError('input_preservation_unverified:' + label)
            if label in ('refuse','refuse_disabled','refuse_duplicate','refuse_already','refuse_clipped'):
                if (result.get('type') != 'error' or result.get('effect') != 'not_started'
                        or step['selection_count'] != '0' or step['result_value'] != 'Alpha'
                        or (step['menu'] or {}).get('state') != 'open'):
                    raise RuntimeError('refusal_not_preserved')
                if label=='refuse_already' and result.get('error')!='already_selected':
                    raise RuntimeError('already_selected_refusal_unclassified')
            elif result.get('type') != 'acted' or result.get('unverified'):
                raise RuntimeError('action_not_verified:' + label)
            if opening_only and (result.get('opened') is not True or (step['menu'] or {}).get('state')!='open'):
                raise RuntimeError('opening_state_unverified')
            expected = ('Beta','1') if label in ('choose_beta','reopen') else ('Alpha','2') if label == 'choose_alpha' else ('Alpha','0')
            if (step['result_value'],step['selection_count']) != expected:
                raise RuntimeError('independent_fixture_readback_failed:' + label)
            if combo and step['selected_index']!=('1' if expected[0]=='Beta' else '0'):
                raise RuntimeError('independent_fixture_index_failed:' + label)
        report['passed'] = True
    except Exception as exc:
        report['error'] = type(exc).__name__ + ':' + str(exc)
        report['cleanup'] = 'no further UI action; only owned fixture may exit separately'
    report['source_hashes_after']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in paths if p.is_file()}
    report['source_stable']=report['source_hashes']==report['source_hashes_after']
    if not report['source_stable']:report['passed']=False
    return report


def run_process_exit_probe(driver, fixture_factory, *,replacement=False):
    """Owned app exit then a fresh popup, through one signed helper channel."""
    if type(replacement) is not bool:raise ValueError('replacement must be boolean')
    from dataclasses import asdict
    from ApplicationServices import AXUIElementCopyAttributeValue
    from tacto.driver import Driver, _attr, AXUIElementCreateApplication
    from tacto.flow import Selector
    from tacto.native_window_binding import _MacBackend
    from tacto.native_process_identity import process_start_identity
    from tacto.helper_identity import parse_helper_identity, helper_instance_status
    report={'passed':False,'target':TARGET,'comparison_eligible':False,
            'caret_preservation_verified':False,'input_preservation_verified':False,
            'evaluation_kind':'owned_view_replacement_same_helper' if replacement else 'owned_process_exit_same_helper','actions':[]}
    paths=sorted((ROOT/'helper').glob('*.swift'))+sorted((ROOT/'tacto').glob('*.py'))+[
        Path(__file__),ROOT/'examples/eval.py',ROOT/'tests/fixtures/NativeSelectFixture.swift',
        ROOT/'helper/tacto-helper',ROOT/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',ROOT/'launcher/tacto']
    def sources():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    report['source_before']=sources();owned=[]
    try:
        identity=driver.helper_process_identity
        if identity is None or helper_instance_status(identity)!='same_instance':
            raise RuntimeError('signed_helper_identity_unavailable')
        report['helper_before']=asdict(identity)
        def start(combo,**config):
            process=fixture_factory(combo=combo,**config)
            if process is None:raise RuntimeError('owned_fixture_required')
            owned.append(process)
            return process
        def observe(process):
            reader=Driver()
            if reader._resolve(TARGET,launch=False)!=process.pid:
                raise RuntimeError('owned_fixture_process_mismatch')
            app=AXUIElementCreateApplication(process.pid)
            if _attr(app,'AXFrontmost') is not False:raise RuntimeError('fixture_not_background')
            obs=driver.observe(TARGET,'tree_full')
            if obs.get('type')!='observation':raise RuntimeError('observation_failed')
            return obs
        def value(obs,identifier):
            index,_=Selector(identifier=identifier).resolve(obs['tree'])
            line=next(line for line in obs['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(index))
            return Selector._attr(line,'Value')
        def act(process,role,operation,option=None,identifier='nlc-fixture-choice'):
            obs=observe(process)
            index,_=Selector(role=role,identifier=identifier).resolve(obs['tree'])
            action={'name':operation,'element_index':index}
            if option is not None:action['option_label']=option
            result=_record_native_call(report['actions'],driver.act,obs['obs_id'],TARGET,action)
            if result.get('type')!='acted' or result.get('unverified'):raise RuntimeError('action_unverified:'+operation)
            return observe(process)
        first=start(True,**({'replacement':True} if replacement else {}));report['retired_pid']=first.pid;report['retired_start']=process_start_identity(first.pid)
        opened=act(first,'ComboBox','open_select')
        reader=Driver();reader.observe(TARGET,'tree_full')
        nodes=reader._latest[TARGET.lower()].elements
        controls=[el for el in nodes if _attr(el,'AXRole')=='AXComboBox']
        if len(controls)!=1:raise RuntimeError('combo_ambiguous')
        control=controls[0]
        scrolls=[el for el in (_attr(control,'AXChildren') or []) if _attr(el,'AXRole')=='AXScrollArea']
        if len(scrolls)!=1:raise RuntimeError('popup_missing')
        backend=_MacBackend();report['retired_popup_id']=backend.window_id(scrolls[0])
        if replacement:
            act(first,'Button','click',identifier='nlc-fixture-replace')
            report['fixture_same_instance']=first.poll() is None and process_start_identity(first.pid)==report['retired_start']
            if not report['fixture_same_instance']:raise RuntimeError('fixture_instance_changed')
        else:
            first.terminate();report['retired_exit_code']=first.wait(timeout=5)
        report['retired_role_error']=AXUIElementCopyAttributeValue(control,'AXRole',None)[0]
        report['retired_parent_error']=AXUIElementCopyAttributeValue(control,'AXParent',None)[0]
        if not replacement:
            try:os.kill(first.pid,0);raise RuntimeError('retired_pid_still_exists')
            except ProcessLookupError:report['retired_pid_missing']=True
        onscreen=backend.q.CGWindowListCopyWindowInfo(backend.q.kCGWindowListOptionOnScreenOnly,0)
        if onscreen is None:raise RuntimeError('popup_visibility_unavailable')
        if any(int(e[backend.q.kCGWindowNumber])==report['retired_popup_id'] for e in onscreen):
            raise RuntimeError('retired_popup_still_visible')
        report['retired_popup_absent']=True
        second=first if replacement else start(False);report['successor_pid']=second.pid
        current=observe(second)
        if [value(current,k) for k in ('nlc-fixture-result','nlc-fixture-count')]!=['Alpha','0']:
            raise RuntimeError('successor_initial_state_mismatch')
        if replacement and value(current,'nlc-fixture-replacement-count')!='1':
            raise RuntimeError('view_replacement_not_received')
        act(second,'PopUpButton','open_select')
        final=act(second,'PopUpButton','select_custom_option','Beta')
        report['successor_readback']=[value(final,k) for k in ('nlc-fixture-result','nlc-fixture-count')]
        pong=driver._send({'type':'ping'})
        after=parse_helper_identity(pong.get('helper_process'),pong.get('helper_peer'))
        report['helper_after']=asdict(after) if after else None
        report['helper_same_instance']=after==identity and helper_instance_status(identity)=='same_instance'
        report['passed']=report['helper_same_instance'] and report['successor_readback']==['Beta','1']
    except Exception as exc:report['error']=type(exc).__name__+':'+str(exc)[:250]
    finally:
        for process in owned:
            if process.poll() is None:process.terminate();process.wait(timeout=5)
        report['source_after']=sources();report['source_stable']=report['source_before']==report['source_after']
        if not report['source_stable']:report['passed']=False
    return report


def run_model_probe(driver, *, provider, option='Beta', budget=16000, tokenizer='utf8_bytes', combo=False,visible_items=0,combo_option_index=1,max_pages=8):
    if type(max_pages) is not int or not 1<=max_pages<=32:
        raise ValueError('model probe planner request limit must be 1..32')
    from tacto.ask import ask_here_paged
    from tacto.driver import Driver, _attr, AXUIElementCreateApplication
    from tacto.flow import Selector
    from tacto.general import run
    from tacto.provider_observation import capture_provider_calls, summarize_provider_calls
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.candidate_paging import utf8_token_upper_bound
    from unittest.mock import patch
    import tacto.ask as ask_module
    if tokenizer=='o200k_base':
        import tiktoken
        encoding=tiktoken.get_encoding('o200k_base')
        counter=lambda text:len(encoding.encode(text,disallowed_special=()))
    elif tokenizer=='utf8_bytes':counter=utf8_token_upper_bound
    else:raise ValueError('unsupported host-selected tokenizer')
    if type(combo) is not bool or (combo and (not isinstance(option,str) or not option or type(combo_option_index) is not int or not 1<=combo_option_index<200)):
        raise ValueError('combo model probe requires an exact option and selected index')
    if type(visible_items) is not int or not 0<=visible_items<=20 or (visible_items and not combo):
        raise ValueError('clipped model probe requires 0..20 visible ComboBox rows')
    report={'passed':False,'target':TARGET,'provider':provider,'plans':[],'planner_queries':[],'combo':combo,
            'native_receipts':[],'visible_items':visible_items,
            'requested_option':option,'requested_index':combo_option_index if combo else None,'prompt_budget':budget,'prompt_metrics':[],
            'planner_request_limit':max_pages,
            'prompt_counter':tokenizer,'serving_tokenizer_verified':False,
            'comparison_eligible':False,
            'evaluation_kind':'real_model_owned_appkit','caret_preservation_verified':False}
    paths=sorted((ROOT/'tacto').glob('*.py'))+[Path(__file__),
        ROOT/'examples/eval.py',ROOT/'helper/TactoHelper.swift', ROOT/'helper/WindowInput.swift',ROOT/'helper/WindowDispatch.swift',
        ROOT/'helper/tacto-helper', ROOT/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper',
        ROOT/'launcher/tacto', ROOT/'tests/fixtures/NativeSelectFixture.swift']
    def sources():
        return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    audit=None
    provider_calls=[]
    try:
        try:report['source_before']=sources()
        except Exception as exc:
            report['source_before_error_class']=type(exc).__name__
            raise
        audit=ProcessInputAudit(interval=.01,startup_timeout=10,stop_timeout=3,max_samples=20000)
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
            if combo:
                index,_=Selector(role='ComboBox',identifier='nlc-fixture-choice').resolve(obs['tree'])
                line=next(line for line in obs['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(index))
                selected,_=Selector(identifier='nlc-fixture-selected-index').resolve(obs['tree'])
                index_line=next(line for line in obs['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(selected))
                report['combo_readback']={'control_value':Selector._attr(line,'Value'),
                    'selected_index':Selector._attr(index_line,'Value'),
                    'state':obs.get('custom_selects',{}).get(str(index),{}).get('state')}
            return values
        if readback()!=['Alpha','0']: raise RuntimeError('fixture_initial_state_mismatch')
        if combo and report.get('combo_readback')!={'control_value':'Alpha','selected_index':'0','state':'closed'}:
            raise RuntimeError('fixture_initial_combo_state_mismatch')
        def planner(goal,candidates,done):
            background()
            original_ask=ask_module.ask
            def measured_ask(question,*args,**kwargs):
                guard=kwargs['_request_guard']
                def measured_guard(prompt,attempt):
                    guard(prompt,attempt)  # Only count accepted submission boundaries.
                    report['prompt_metrics'].append({'tokens':counter(prompt),'validation_attempt':attempt,
                        'planner_call':len(report['plans'])+1,
                        'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest()})
                kwargs['_request_guard']=measured_guard
                answer=original_ask(question,*args,**kwargs)
                if isinstance(answer,dict):
                    if 'read_context' in answer:
                        from copy import deepcopy
                        report['planner_queries'].append({'planner_call':len(report['plans'])+1,
                            'kind':'read_context','request':deepcopy(answer['read_context'])})
                    elif answer.get('next_page') is True:
                        report['planner_queries'].append({'planner_call':len(report['plans'])+1,'kind':'next_page'})
                return answer
            with patch.object(ask_module,'ask',side_effect=measured_ask):
                answer=ask_here_paged(goal,candidates,done,provider=provider,max_pages=max_pages,budget=budget,token_counter=counter)
            report['plans'].append({'candidates':candidates,'answer':answer})
            background()
            return answer
        original_act=driver.act
        original_observe=driver.observe
        latest={}
        def recorded_observe(*args,**kwargs):
            result=original_observe(*args,**kwargs)
            if result.get('type')=='observation':latest.clear();latest.update(result)
            return result
        def recorded_act(obs_id,target,action,*args,**kwargs):
            proof={}
            def operation(*call_args,**call_kwargs):
                if visible_items and action.get('name')=='scroll':
                    indices=[]
                    for role in ('ScrollArea','TextField'):
                        indices+=Selector(role=role,ancestor='nlc-fixture-choice').resolve_all(latest.get('tree',''))
                    proof['scoped']=(target==TARGET and latest.get('obs_id')==obs_id and action.get('element_index') in indices)
                    if not proof['scoped']:raise RuntimeError('model_scroll_target_not_owned_popup')
                    proof['before']=_owned_combo_scroll_geometry(pid,option)
                    # Read from the current signed observation without replacing its ID.
                    proof['readback_before']=[Selector._attr(next(line for line in latest['tree'].splitlines()
                        if 'Identifier: '+identifier in line),'Value') for identifier in ('nlc-fixture-result','nlc-fixture-count')]
                return original_act(*call_args,**call_kwargs)
            try:
                result=_record_native_call(report['native_receipts'],operation,obs_id,target,action,*args,**kwargs)
                if proof:
                    def check():
                        proof['after']=_owned_combo_scroll_geometry(pid,option)
                        proof['readback_after']=readback()
                        _require_combo_scroll_receiving(proof,action.get('direction'))
                    _record_receiving_check(report['native_receipts'][-1],check)
                return result
            finally:
                if proof:report['native_receipts'][-1]['scroll_evidence']=proof
        with capture_provider_calls() as provider_calls:
            with audit:
                with patch.object(driver,'act',side_effect=recorded_act),patch.object(driver,'observe',side_effect=recorded_observe):
                    result=run(driver,TARGET,f'선택 메뉴에서 {option}를 한 번 선택하고, 결과 표시의 값을 읽어 알려주세요.',
                               planner,max_screens=16 if combo_option_index>2 else 8,timeout_seconds=180 if combo_option_index>2 else 120)
                # A final observation may fail after a deadline or disconnect.
                # Preserve loop evidence before requesting it; never reconnect
                # or replay an action to manufacture a successful readback.
                report.update(loop_ok=result.ok,why=result.why,said=result.said,
                              events=result.events)
                try:report['readback']=readback()
                except Exception as exc:
                    report['readback_error_class']=type(exc).__name__
    except Exception as exc:
        report['error']=type(exc).__name__+':'+str(exc)
        report['failure_reasons']=['probe_exception']
    finally:
        report['provider_calls']=provider_calls
        report['usage']=summarize_provider_calls(provider_calls)
        try:
            report['source_after']=sources()
            report['source_stable']='source_before' in report and report['source_before']==report['source_after']
        except Exception as exc:
            report['source_stable']=False
            report['source_after_error_class']=type(exc).__name__
        if audit is not None:
            try:report['audit']=audit.report()
            except Exception as exc:report['audit_report_error_class']=type(exc).__name__
        if 'error' not in report:
            report['failure_reasons']=_model_probe_failure_reasons(report)
        elif not report['source_stable']:
            report['failure_reasons'].append('source_changed_or_unverified')
        report['passed']=not report['failure_reasons']
    return report


def owned_popup_capabilities(app, expected_pid):
    """Read only the evaluator's uniquely identified closed popup."""
    from CoreFoundation import CFEqual
    from ApplicationServices import (AXUIElementGetPid, AXUIElementCopyActionNames,
        AXUIElementIsAttributeSettable, AXUIElementCopyAttributeValue)
    def read(node,name):
        error,value=AXUIElementCopyAttributeValue(node,name,None)
        if error==0:return value
        if error in (-25205,-25212):return None
        raise RuntimeError('owned_metadata_read_unavailable')
    queue=[app];seen=[];matches=[]
    while queue:
        node=queue.pop(0)
        if any(CFEqual(node,old) for old in seen):continue
        if len(seen)>=256:raise RuntimeError('owned_metadata_traversal_incomplete')
        seen.append(node)
        role=read(node,'AXRole')
        if not isinstance(role,str) or not role:raise RuntimeError('owned_metadata_role_unavailable')
        if (read(node,'AXIdentifier')=='nlc-fixture-choice' and role=='AXPopUpButton'):
            matches.append(node)
        children=read(node,'AXChildren')
        if children is not None:queue.extend(list(children))
    if len(matches)!=1:raise RuntimeError('owned_popup_metadata_ambiguous')
    node=matches[0]
    error,pid=AXUIElementGetPid(node,None)
    if error!=0 or pid!=expected_pid:raise RuntimeError('owned_popup_metadata_owner_changed')
    error,actions=AXUIElementCopyActionNames(node,None)
    result={'owner_verified':True,'action_error':int(error),
            'actions':list(actions or []) if error==0 else None,'attributes':{},
            'authorizes_input':False}
    for name in ('AXValue','AXSelectedChildren','AXSelected','AXFocused'):
        error,value=AXUIElementIsAttributeSettable(node,name,None)
        result['attributes'][name]={'error':int(error),
            'settable':value if error==0 and type(value) is bool else None}
    return result


def run_observe_control(driver, *, output, focus_diagnostics_dir,expected_pid):
    """Owned closed popup, signed observation only; no action request."""
    from examples.focus_diagnostics import FocusDiagnosticReader
    from tacto.input_audit_process import ProcessInputAudit
    from tacto.flow import Selector
    from tacto.driver import Driver, _attr, AXUIElementCreateApplication
    fd=os.open(output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    report={'passed':False,'scope':'owned_popup_read_only_observation_control',
            'GUI_input_requests':0,'model_requests':0,'comparison_eligible':False,
            'popup_cause_verified':False,'observations':[]}
    audit=None
    files=[Path(__file__),ROOT/'examples/focus_diagnostics.py',ROOT/'tacto/helper.py',
           ROOT/'tacto/input_audit.py',ROOT/'tacto/input_audit_process.py',ROOT/'helper/TactoHelper.swift']
    def hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    before=hashes()
    try:
        pid=Driver()._resolve(TARGET,launch=False)
        if type(expected_pid) is not int or expected_pid<=0 or pid!=expected_pid:
            raise RuntimeError('owned_fixture_identity_unverified')
        app=AXUIElementCreateApplication(pid)
        reader=FocusDiagnosticReader(Path(focus_diagnostics_dir)/'observe-control.jsonl')
        report['focus_diagnostics_path']=reader.output
        audit=ProcessInputAudit(reader=reader,interval=.01,startup_timeout=10,stop_timeout=3)
        with audit:
            for _ in range(2):
                if _attr(app,'AXFrontmost') is not False:raise RuntimeError('fixture_not_background')
                started=time.monotonic()
                obs=driver.observe(TARGET,'tree_full')
                if obs.get('type')!='observation':raise RuntimeError('owned_observation_unavailable')
                values={}
                for name in ('nlc-fixture-result','nlc-fixture-count'):
                    index,_=Selector(identifier=name).resolve(obs['tree'])
                    lines=[line for line in obs['tree'].splitlines() if line.strip().split(maxsplit=1)[0]==str(index)]
                    if len(lines)!=1:raise RuntimeError('owned_receiving_ambiguous')
                    values[name]=Selector._attr(lines[0],'Value')
                report['observations'].append({'started_at':started,'finished_at':time.monotonic(),
                    'values':values,'custom_selects':obs.get('custom_selects')})
                if values!={'nlc-fixture-result':'Alpha','nlc-fixture-count':'0'}:
                    raise RuntimeError('owned_receiving_changed')
                index,_=Selector(role='PopUpButton',identifier='nlc-fixture-choice').resolve(obs['tree'])
                if obs.get('custom_selects',{}).get(str(index),{}).get('state')!='closed':
                    raise RuntimeError('owned_menu_not_closed')
                if 'owned_popup_capabilities' not in report:
                    report['owned_popup_capabilities']=owned_popup_capabilities(app,pid)
                time.sleep(.2)
        report['passed']=True
    except Exception as error:report['error_class']=type(error).__name__
    finally:
        report['input_audit']=audit.report() if audit is not None else {'status':'NOT_STARTED'}
        report['source_before']=before;report['source_after']=hashes()
        report['source_stable']=before==report['source_after']
        report['passed']=report['passed'] and report['source_stable'] and report['input_audit']['status']=='UNCHANGED_SAMPLED'
        with os.fdopen(fd,'w') as stream:json.dump(report,stream,indent=2)
    return report


def run_and_save(driver, *, output, transport, provider=None, option='Beta',budget=16000,tokenizer='utf8_bytes',duplicates=False,combo=False,visible_items=0,combo_option_index=1,max_pages=8,focus_diagnostics_dir=None,opening_only=False,expected_pid=None,opening_action=None):
    if provider is not None and (focus_diagnostics_dir is not None or opening_only or opening_action is not None):
        raise ValueError('focus_diagnostics_require_nonmodel_probe')
    with os.fdopen(os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as stream:
        result=run_model_probe(driver,provider=provider,option=option,budget=budget,tokenizer=tokenizer,combo=combo,visible_items=visible_items,combo_option_index=combo_option_index,max_pages=max_pages) if provider else run_probe(driver,duplicates=duplicates,combo=combo,visible_items=visible_items,focus_diagnostics_dir=focus_diagnostics_dir,opening_only=opening_only,expected_pid=expected_pid,opening_action=opening_action)
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
            from tacto.driver import Driver
            result = run_probe(Driver())
        except Exception as exc:
            result = {'passed':False,'error':type(exc).__name__ + ':' + str(exc)}
        json.dump(result,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    print(json.dumps({'passed':result['passed'],'error':result.get('error'),'report':str(args.output)},ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
