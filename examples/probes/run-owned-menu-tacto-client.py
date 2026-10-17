"""Tacto equivalent of the five Sky menu comparison operations, owned fixture only."""
import json
import re
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def main(driver=None, control_dir=None, coordinate_only=False, test_abort=False, model_provider=None, prepare_window=False, general_prepare=False):
    from nlc.driver import Driver
    control = Path(control_dir if control_dir is not None else sys.argv[1]).resolve()
    target = 'ai.callabo.nlc.menu-fixture'
    driver = driver if driver is not None else Driver()
    resolver = Driver()
    def live():
        if (control/'finished.json').exists():
            raise RuntimeError('supervisor_finished')
    def read(name):
        for _ in range(200):
            path = control/name
            if path.exists(): return json.loads(path.read_text())
            live()
            time.sleep(.05)
        raise TimeoutError('control_signal_timeout')
    def publish(name, data):
        temporary = control/('.'+name+'.tmp')
        with temporary.open('x') as stream: json.dump(data, stream)
        temporary.rename(control/name)
    session = read('session.json')
    paths = [None, ['Probe'], ['Probe','Direct Command'], ['Probe'], ['Probe','Responder Command']]
    if '--activate' in sys.argv[2:]:
        paths.insert(1, 'experimental_activation')
    if '--focus-window' in sys.argv[2:]:
        paths = [None, 'experimental_activation', 'experimental_window_focus',
                 ['Probe','Direct Command'], ['Probe','Responder Command']]
    if '--make-key' in sys.argv[2:]:
        paths = [None, 'experimental_activation', 'experimental_make_key',
                 ['Probe','Direct Command'], ['Probe','Responder Command']]
    if '--titlebar-click' in sys.argv[2:] or '--cg-titlebar-click' in sys.argv[2:]:
        paths = [None, 'experimental_activation', 'experimental_titlebar_click',
                 ['Probe','Direct Command'], ['Probe','Responder Command']]
    if '--coordinate-target' in sys.argv[2:]:
        paths = ['coordinate_metadata', 'driver_coordinate_click']
    if '--coordinate-metadata' in sys.argv[2:]:
        paths = ['coordinate_metadata']
    if coordinate_only:
        paths = ["driver_coordinate_click"]
    if model_provider is not None:
        if model_provider not in ("claude", "codex") or test_abort:
            raise ValueError("unsupported model probe configuration")
        paths = ["model_coordinate"]
    if prepare_window:
        if test_abort or (model_provider and not general_prepare): raise ValueError('incompatible preparation probe')
        coordinate_only = False
        paths = [None, 'driver_prepare_window', ['Probe','Responder Command']]
    hardening = '--menu-hardening' in sys.argv
    if hardening:
        coordinate_only = False
        paths = [['Hardening', title] for title in ('Duplicate', 'Disabled', 'Enabled')]
    discovery = '--menu-discovery' in sys.argv
    if discovery:
        coordinate_only = False
        paths = [[], ['Probe'], ['Hardening']]
    general_menu = '--general-menu' in sys.argv
    if general_menu:
        coordinate_only = False
        paths = ['general_menu']
    for number, path in enumerate(paths, 1):
        live()
        if number > 1 and read(f'result-{number-1}.json')['status'] != 'ok':
            raise RuntimeError('previous_step_failed')
        publish(f'request-{number}.json', {'label': 'NLC '+str(path or 'observe')})
        ready = read(f'ready-{number}.json')
        live()
        if (ready.get('status') != 'ready' or ready.get('target') != target
                or ready.get('fixture_pid') != session['fixture_pid']
                or resolver._resolve(target, launch=False) != session['fixture_pid']):
            raise RuntimeError('owned_fixture_identity_mismatch')
        started = time.monotonic()
        result = {}
        try:
            obs = driver.observe(target, 'tree_full')
            if obs.get('type') != 'observation': raise RuntimeError(str(obs))
            result = {'before': obs}
            if path == 'general_menu':
                from nlc.general import run
                from nlc.flow import Selector
                plans=[]
                stages=[('inspect_menu','메뉴 목록 조회'),('inspect_menu','Probe'),('menu','Direct Command'),('read',None)]
                def planner(goal,candidates,history):
                    if model_provider:
                        from nlc.ask import ask_here_paged
                        answer=ask_here_paged(goal,candidates,history,provider=model_provider,max_pages=3)
                    elif len(plans)>=len(stages):answer={'done':True}
                    else:
                        do,label=stages[len(plans)]
                        chosen=next(c for c in candidates if do in c['can'] and
                            (c['label']==label if label else 'nlc-fixture-direct-count' in c['line']))
                        answer={'actions':[{'do':do,'candidate':chosen}]}
                    plans.append({'answer':answer,'candidates':candidates})
                    return answer
                general=run(driver,target,'Probe 메뉴의 Direct Command를 정확히 한 번 실행하고 nlc-fixture-direct-count 값을 읽으세요.',
                            planner,max_screens=8,timeout_seconds=65)
                result['general']={'ok':general.ok,'why':general.why,'said':general.said,'events':general.events,
                                   'certainty':general.certainty,'plans':plans,'provider_calls':general.provider_calls}
                direct_reads = [a for v in general.visits for a in v.actions if a.get('do')=='read'
                                and 'nlc-fixture-direct-count' in a.get('line','')
                                and Selector._attr(a['line'],'Value')=='1']
                menu_events = [e for e in general.events if e.get('action',{}).get('name')=='menu']
                if (not general.ok or not direct_reads or len(menu_events)!=1
                        or menu_events[0]['action'].get('path')!=['Probe','Direct Command']):
                    raise RuntimeError('general_menu_readback_failed')
                result['after_general']=driver.observe(target,'tree_full')
                index,_=Selector(identifier='nlc-fixture-direct-count').resolve(result['after_general']['tree'])
                line=next(l for l in result['after_general']['tree'].splitlines() if l.strip().split(maxsplit=1)[0]==str(index))
                if Selector._attr(line,'Value')!='1':raise RuntimeError('general_menu_counter_mismatch')
                if '--menu-promotion' in sys.argv:
                    from nlc.promote import candidate_from, deployable
                    from nlc.flow import Postcondition, replay
                    candidate=candidate_from(general,target,'increment direct counter once and read')
                    if candidate is None:raise RuntimeError('menu_candidate_unavailable')
                    menu_indices=[i for i,a in enumerate(candidate.actions) if a['do']=='menu']
                    if len(menu_indices)!=1:raise RuntimeError('unexpected_menu_candidate_actions')
                    flow,reasons=deployable(candidate,'owned-menu-replay',candidate.goal,
                        menu_postconditions={menu_indices[0]:Postcondition(
                            field=Selector(identifier='nlc-fixture-direct-count'),equals='2',attribute='Value')})
                    if flow is None:raise RuntimeError('menu_flow_refused:'+str(reasons))
                    replayed=replay(driver,flow)
                    result['promotion']={'ok':replayed.ok,'events':replayed.events,
                        'postcondition_source':'explicit test-host expected counter2 after separate replay',
                        'menu_path':flow.steps[menu_indices[0]].params['path']}
                    if not replayed.ok:raise RuntimeError('menu_replay_failed')
                    replay_after=driver.observe(target,'tree_full')
                    index,_=Selector(identifier='nlc-fixture-direct-count').resolve(replay_after['tree'])
                    line=next(l for l in replay_after['tree'].splitlines() if l.strip().split(maxsplit=1)[0]==str(index))
                    if Selector._attr(line,'Value')!='2':raise RuntimeError('menu_replay_counter_mismatch')
                    result['promotion']['after']=replay_after

            elif path == 'driver_prepare_window':
                from nlc.flow import Selector
                index,_ = Selector(role='Window',title='NLC Native Menu Fixture').resolve(obs['tree'])
                if general_prepare:
                    from nlc.general import run
                    replies=[]
                    class RecordingDriver:
                        def __getattr__(self,name):return getattr(driver,name)
                        def act(self,*args,**kwargs):
                            reply=driver.act(*args,**kwargs);replies.append(reply);return reply
                    plans=[]
                    def planner(goal,cands,history):
                        if model_provider:
                            from nlc.ask import ask_here_paged
                            answer=ask_here_paged(goal,cands,history,provider=model_provider,max_pages=2)
                            plans.append({'candidates':cands,'answer':answer})
                            return answer
                        plans.append([dict(c) for c in cands])
                        if len(plans)==1:
                            chosen=next(c for c in cands if c['role']=='Window')
                            return {'actions':[{'do':'prepare_window','candidate':chosen}]}
                        if len(plans)==2:
                            chosen=next(c for c in cands if 'nlc-fixture-state' in c['line'])
                            return {'actions':[{'do':'read','candidate':chosen}]}
                        return {'done':True}
                    general=run(RecordingDriver(),target,
                        'NLC Native Menu Fixture 창을 prepare_window로 준비하고, 새 관측에서 nlc-fixture-state의 실제 상태를 읽어주세요. 다른 버튼은 누르지 마세요.',
                        planner,max_screens=5,timeout_seconds=65)
                    result['general']={'ok':general.ok,'why':general.why,'said':general.said,
                        'events':general.events,'certainty':general.certainty,'plans':plans,
                        'requested_provider':model_provider,'provider_calls':getattr(general,'provider_calls',[])}
                    if not general.ok or not any('isKeyWindow=true' in text for text in general.said):
                        raise RuntimeError('general_preparation_readback_failed')
                    result['action']=next(reply for reply in replies if reply.get('action')=='prepare_window')
                else:
                    result['action'] = driver.act(obs['obs_id'],target,{'name':'prepare_window','element_index':index})
                if result['action'].get('type')!='acted' or result['action'].get('input_route')!='window_prepare':
                    raise RuntimeError('window_preparation_not_dispatched')
                time.sleep(.2)
                result['after_prepare']=driver.observe(target,'tree_full')
                result['menu_after_prepare']=driver.menu_state(target,['Probe','Responder Command'])
                if result['menu_after_prepare'].get('enabled') is not True:
                    raise RuntimeError('responder_menu_not_enabled_after_preparation')
                if '--replay-prepared-window' in sys.argv:
                    if not general_prepare:raise RuntimeError('general preparation required')
                    from nlc.promote import candidate_from, deployable
                    from nlc.flow import Postcondition, replay
                    state_index,_=Selector(identifier='nlc-fixture-state').resolve(result['after_prepare']['tree'])
                    state_line=next(l for l in result['after_prepare']['tree'].splitlines() if l.strip().split(maxsplit=1)[0]==str(state_index))
                    ready_state=Selector._attr(state_line,'Value')
                    if not ready_state or 'isKeyWindow=true' not in ready_state:
                        raise RuntimeError('prepared_state_not_observed')
                    candidate=candidate_from(general,target,'prepare window and read state')
                    if candidate is None:raise RuntimeError('prepare_candidate_unavailable')
                    flow,reasons=deployable(candidate,'owned-prepare-replay',candidate.goal,
                        preparation_postconditions={0:Postcondition(field=Selector(identifier='nlc-fixture-state'),
                                                                   equals=ready_state,attribute='Value')})
                    if flow is None:raise RuntimeError('prepare_flow_refused:'+str(reasons))
                    repeated=replay(driver,flow)
                    result['prepare_replay']={'ok':repeated.ok,'events':repeated.events,'log':repeated.log,
                        'postcondition_source':'host-pinned already-prepared exact fixture state; not a fresh transition claim'}
                    if not repeated.ok:raise RuntimeError('prepare_replay_failed')

            elif path == 'model_coordinate':
                from nlc.ask import ask_here_paged
                from nlc.general import run
                from nlc.flow import Selector
                result['plans'] = []
                def planner(goal, candidates, done):
                    live()
                    answer = ask_here_paged(goal, candidates, done,
                                           provider=model_provider, max_pages=2)
                    result['plans'].append({'candidates':candidates, 'answer':answer})
                    return answer
                loop = run(driver, target,
                    'Coordinate target 버튼을 정확히 한 번 클릭한 뒤, 클릭 카운터 값을 읽어 알려주세요.',
                    planner, max_screens=5, timeout_seconds=60)
                result['model'] = {'provider':model_provider, 'ok':loop.ok,
                    'verified':loop.verified, 'why':loop.why, 'certainty':loop.certainty,
                    'said':loop.said, 'events':loop.events, 'asked':loop.asked,
                    'provider_calls':loop.provider_calls}
            elif path == 'coordinate_metadata':
                from nlc.flow import Selector
                from nlc.native_window_binding import capture_binding, validate_binding
                from ApplicationServices import AXUIElementCopyActionNames
                index, _ = Selector(identifier='nlc-coordinate-button').resolve(obs['tree'])
                snapshot = driver._latest[target.lower()]
                element = snapshot.elements[index]
                error, names = AXUIElementCopyActionNames(element, None)
                result['action_names'] = {'error':error,'names':list(names) if names is not None else None}
                publish('coordinate-action-names.json', result['action_names'])
                binding = capture_binding(element, snapshot.pid,
                                          launch_identity=snapshot.process_launch_identity)
                validate_binding(binding)
                result['binding'] = {'pid':binding.pid,'window_id':binding.window_id,
                                     'bounds':binding.window_bounds,'point':binding.point}
            elif path == 'driver_coordinate_click':
                from nlc.flow import Selector
                index, _ = Selector(identifier='nlc-coordinate-button').resolve(obs['tree'])
                if coordinate_only:
                    result['refusals'] = []
                    for token, expectation in [('invalid-owned-observation', {'role':'Button'}),
                            (obs['obs_id'], {'role':'TextField'})]:
                        refused = driver.act(token, target, {'name':'click','element_index':index},
                                             expect=expectation)
                        result['refusals'].append(refused)
                        if refused.get('type') != 'error' or refused.get('effect') != 'not_started':
                            raise RuntimeError('invalid_coordinate_request_not_refused')
                if coordinate_only:
                    for deadline in [time.monotonic() - 1, True]:
                        refused = driver._send({'type':'act','obs_id':obs['obs_id'],'target':target,
                            'action':{'name':'click','element_index':index},
                            'click_deadline_uptime':deadline})
                        result['refusals'].append(refused)
                        if refused.get('type') != 'error' or refused.get('effect') != 'not_started':
                            raise RuntimeError('invalid_deadline_not_refused')
                result['action'] = driver.act(obs['obs_id'], target,
                    {'name':'click','element_index':index},
                    expect={'role':'Button','enabled':True})
            elif path == 'experimental_activation':
                # Owned-fixture experiment only, using public NSEvent/CGEvent
                # construction. Values were observed at this fixture's AppKit
                # boundary; no private service or global HID posting is used.
                import AppKit
                import Quartz
                matches = re.findall(r'fixtureWindow=(\d+)', obs['tree'])
                if len(matches) != 1: raise RuntimeError('fixture_window_ambiguous')
                window_id = int(matches[0])
                windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll,
                                                            Quartz.kCGNullWindowID)
                hits = [w for w in windows if w.get(Quartz.kCGWindowNumber) == window_id]
                if len(hits) != 1 or hits[0].get(Quartz.kCGWindowOwnerPID) != session['fixture_pid']:
                    raise RuntimeError('owned_window_pid_mismatch')
                event = AppKit.NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                    13, (0, 0), 0xC0000, time.monotonic(), window_id, None, 1, 0, 0)
                cg = event.CGEvent() if event is not None else None
                if cg is None or Quartz.CGEventGetType(cg) != 13:
                    raise RuntimeError('activation_event_unavailable')
                live()
                if resolver._resolve(target, launch=False) != session['fixture_pid']:
                    raise RuntimeError('owned_process_changed')
                Quartz.CGEventPostToPid(session['fixture_pid'], cg)
                result['activation_post'] = {'type':13,'subtype':1,'flags':0xC0000,
                    'window_id':window_id,'pid':session['fixture_pid'],
                    'post_return_is_not_effect_verification':True}
                time.sleep(.2)
            elif path == 'experimental_titlebar_click':
                import AppKit
                import Quartz
                matches = re.findall(r'fixtureWindow=(\d+)', obs['tree'])
                if len(matches) != 1: raise RuntimeError('fixture_window_ambiguous')
                window_id = int(matches[0])
                windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll,
                                                            Quartz.kCGNullWindowID)
                hits = [w for w in windows if w.get(Quartz.kCGWindowNumber) == window_id]
                if len(hits) != 1 or hits[0].get(Quartz.kCGWindowOwnerPID) != session['fixture_pid']:
                    raise RuntimeError('owned_window_pid_mismatch')
                bounds = hits[0][Quartz.kCGWindowBounds]
                # This exact fixture's observed titlebar point, not a generic
                # safe location for arbitrary windows or application controls.
                point = (bounds['X'] + 30, bounds['Y'] + 16)
                local = (30, bounds['Height'] - 16)
                if '--content-click' in sys.argv[2:]:
                    from nlc.flow import Selector
                    from nlc.driver import _elem_frame, _attr
                    from ApplicationServices import AXUIElementGetPid
                    index, _ = Selector(identifier='nlc-fixture-make-key').resolve(obs['tree'])
                    element = driver._latest[target].elements[index]
                    error, pid = AXUIElementGetPid(element, None)
                    frame = _elem_frame(element)
                    if (error != 0 or pid != session['fixture_pid'] or frame is None
                            or _attr(element, 'AXIdentifier') != 'nlc-fixture-make-key'):
                        raise RuntimeError('owned_button_geometry_unavailable')
                    x, y, width, height = frame
                    point = (x + width / 2, y + height / 2)
                    if not (bounds['X'] < point[0] < bounds['X'] + bounds['Width']
                            and bounds['Y'] < point[1] < bounds['Y'] + bounds['Height']):
                        raise RuntimeError('owned_button_outside_window')
                    local = (point[0] - bounds['X'], bounds['Height'] - (point[1] - bounds['Y']))
                set_window_location = get_window_location = None
                window_locations = []
                if '--window-location' in sys.argv[2:]:
                    # Experimental OS-private API, declared in WebKit's SPI.
                    # Resolve before dispatch; never replace a missing symbol
                    # with a global input path or guessed numeric fields.
                    import ctypes
                    import objc
                    class Point(ctypes.Structure):
                        _fields_ = [('x', ctypes.c_double), ('y', ctypes.c_double)]
                    cg_library = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
                    set_window_location = cg_library.CGEventSetWindowLocation
                    set_window_location.argtypes = [ctypes.c_void_p, Point]
                    set_window_location.restype = None
                    get_window_location = cg_library.CGEventGetWindowLocation
                    get_window_location.argtypes = [ctypes.c_void_p]
                    get_window_location.restype = Point
                events = []
                for event_type in (1, 2):
                    if '--cg-titlebar-click' in sys.argv[2:]:
                        cg = Quartz.CGEventCreateMouseEvent(None, event_type, point,
                                                         Quartz.kCGMouseButtonLeft)
                        if cg is not None:
                            Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventClickState, 1)
                            Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventNumber, 1)
                    else:
                        native = AppKit.NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                            event_type, local, 0, time.monotonic(), window_id, None, event_type, 1, 1.0)
                        cg = native.CGEvent() if native is not None else None
                    if cg is None or Quartz.CGEventGetType(cg) != event_type:
                        raise RuntimeError('mouse_event_unavailable')
                    Quartz.CGEventSetLocation(cg, point)
                    Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventWindowUnderMousePointer, window_id)
                    Quartz.CGEventSetIntegerValueField(cg, Quartz.kCGMouseEventWindowUnderMousePointerThatCanHandleThisEvent, window_id)
                    if set_window_location is not None:
                        # SetLocation clears this field to (-1,-1), so this
                        # setter must follow all global coordinate updates.
                        relative = Point(point[0] - bounds['X'], point[1] - bounds['Y'])
                        set_window_location(objc.pyobjc_id(cg), relative)
                        actual = get_window_location(objc.pyobjc_id(cg))
                        if (actual.x, actual.y) != (relative.x, relative.y):
                            raise RuntimeError('window_location_not_preserved')
                        window_locations.append([actual.x, actual.y])
                    events.append(cg)
                live()
                if resolver._resolve(target, launch=False) != session['fixture_pid']:
                    raise RuntimeError('owned_process_changed')
                for cg in events:
                    Quartz.CGEventPostToPid(session['fixture_pid'], cg)
                result['titlebar_post'] = {'window_id':window_id,'pid':session['fixture_pid'],
                    'point':point,'local_point':local,'types':[1,2],
                    'constructor':'CGEventCreateMouseEvent' if '--cg-titlebar-click' in sys.argv[2:] else 'NSEvent',
                    'private_window_location':window_locations,
                    'post_return_is_not_effect_verification':True}
                time.sleep(.2)
            elif path == 'experimental_make_key':
                from nlc.flow import Selector
                index, _ = Selector(identifier='nlc-fixture-make-key').resolve(obs['tree'])
                result['make_key'] = driver.act(obs['obs_id'], target,
                    {'name':'click','element_index':index})
                time.sleep(.2)
            elif path == 'experimental_window_focus':
                from ApplicationServices import AXUIElementSetAttributeValue, AXUIElementGetPid
                from nlc.driver import _attr
                element = driver._latest[target].elements[0]
                error, pid = AXUIElementGetPid(element, None)
                if (error != 0 or pid != session['fixture_pid']
                        or _attr(element, 'AXRole') != 'AXWindow'
                        or _attr(element, 'AXTitle') != 'NLC Native Menu Fixture'):
                    raise RuntimeError('owned_window_identity_unavailable')
                live()
                result['window_focus'] = {'attribute':'AXFocused', 'value':True,
                    'return_code':AXUIElementSetAttributeValue(element, 'AXFocused', True),
                    'return_code_is_not_effect_verification':True}
                time.sleep(.2)
            elif discovery:
                result['menu_items'] = driver.menu_items(target, path)
                listed = result['menu_items']
                if path == ['Hardening']:
                    if listed.get('error') != 'menu_ambiguous' or 'items' in listed:
                        raise RuntimeError('duplicate_listing_not_refused')
                else:
                    if listed.get('type') != 'menu_items' or listed.get('complete') is not True:
                        raise RuntimeError('menu_listing_not_complete')
                    titles = {item['title'] for item in listed['items']}
                    required = {'Probe','Hardening'} if not path else {'Direct Command','Responder Command'}
                    if not required <= titles:
                        raise RuntimeError('expected_menu_missing')
            elif hardening:
                result['menu_before'] = driver.menu_state(target, path)
                result['action'] = driver.act(obs['obs_id'], target, {'name':'menu','path':path})
                expected_enabled = path[-1] == 'Enabled'
                if expected_enabled:
                    if result['action'].get('type') != 'acted':
                        raise RuntimeError('enabled_menu_not_dispatched')
                elif (result['action'].get('type') != 'error'
                      or result['action'].get('effect') != 'not_started'):
                    raise RuntimeError('invalid_menu_not_refused_before_dispatch')
            elif path is not None:
                menu = driver.menu_state(target, path)
                result['menu_before'] = menu
                if menu.get('enabled') is not True:
                    result['not_invoked'] = 'not_observably_enabled'
                else:
                    result['action'] = driver.act(obs['obs_id'], target, {'name':'menu','path':path})
            result['after'] = driver.observe(target, 'tree_full')
            if hardening or discovery:
                from nlc.flow import Selector
                def direct_count(observation):
                    index,_=Selector(identifier='nlc-fixture-direct-count').resolve(observation['tree'])
                    line=next(l for l in observation['tree'].splitlines() if l.strip().split(maxsplit=1)[0]==str(index))
                    return Selector._attr(line,'Value')
                if direct_count(obs) != '0' or direct_count(result['after']) != ('1' if hardening and expected_enabled else '0'):
                    raise RuntimeError('menu_counter_mismatch')
            if prepare_window and isinstance(path,list):
                from nlc.flow import Selector
                index,_=Selector(identifier='nlc-fixture-responder-count').resolve(result['after']['tree'])
                line=next(l for l in result['after']['tree'].splitlines() if l.strip().split(maxsplit=1)[0]==str(index))
                if Selector._attr(line,'Value')!='1':raise RuntimeError('responder_command_not_verified')
            if coordinate_only:
                def count(observation):
                    index, _ = Selector(identifier='nlc-coordinate-count').resolve(observation['tree'])
                    line = next(line for line in observation['tree'].splitlines()
                                if line.strip().split(maxsplit=1)[0] == str(index))
                    return Selector._attr(line, 'Value')
                if count(obs) != '0' or count(result['after']) != '1':
                    raise RuntimeError('coordinate_effect_not_verified')
                if model_provider is not None:
                    if not loop.ok or not loop.verified or '1' not in loop.said:
                        raise RuntimeError('model_coordinate_completion_not_verified')
                elif (result['action'].get('type') != 'acted'
                      or result['action'].get('input_route') != 'window_bound_mouse'):
                    raise RuntimeError('coordinate_route_not_verified')
            if coordinate_only and test_abort:
                import Quartz
                live()
                if resolver._resolve(target, launch=False) != session['fixture_pid']:
                    raise RuntimeError('owned_fixture_identity_changed')
                keys = [Quartz.CGEventCreateKeyboardEvent(None, 53, down) for down in (True, False)]
                if any(key is None for key in keys):
                    raise RuntimeError('escape_event_construction_failed')
                for key in keys:
                    Quartz.CGEventSetFlags(key, 0)
                    Quartz.CGEventPostToPid(session['fixture_pid'], key)
                result['abort_injection'] = 'owned fixture PID only; no global HID post'
                for _ in range(10):
                    stopped = driver._send({'type':'selection','target':target})
                    if stopped.get('error') == 'aborted_by_user':
                        break
                    time.sleep(.05)
                result['abort_read_probe'] = stopped
                if stopped.get('error') != 'aborted_by_user':
                    raise RuntimeError('directed_escape_not_observed_no_further_click')
                result['abort_ping'] = driver._send({'type':'ping'})
                result['after_abort_click'] = driver.act(result['after']['obs_id'], target,
                    {'name':'click','element_index':index})
                refused = result['after_abort_click']
                if refused.get('error') != 'aborted_by_user' or refused.get('effect') != 'not_started':
                    raise RuntimeError('escape_latch_did_not_refuse_click')
                result['independent_after_abort'] = resolver.observe(target, 'tree_full')
                if count(result['independent_after_abort']) != '1':
                    raise RuntimeError('counter_changed_after_abort')
            done = {'status':'ok', 'duration_seconds':time.monotonic()-started, 'result':result}
        except Exception as exc:
            done = {'status':'error','error':type(exc).__name__+':'+str(exc),
                    'partial_result':result if 'result' in locals() else None}
        publish(f'done-{number}.json', done)
        measured = read(f'result-{number}.json')
        print(json.dumps({'step':number,'status':measured['status'],
                          'audit':measured['audit']['status'],'result':done}),flush=True)
        if measured['status'] != 'ok': raise RuntimeError('step_failed_stop')
    publish('stop.json', {})

if __name__ == '__main__': main()
