"""Owned two-window navigation fixture; no user app, global input or save."""
from pathlib import Path
import json
import plistlib
import subprocess
import tempfile
import time
import hashlib

TARGET = 'ai.callabo.nlc.navigation-fixture'


def main(driver, output, *, model_provider=None, require_selection=False):
    from nlc.driver import Driver, _attr
    from nlc.flow import Selector
    from nlc.model_budget import model_deadline
    from nlc.input_audit_process import ProcessInputAudit
    from ApplicationServices import AXUIElementCreateApplication
    root = Path(__file__).resolve().parents[2]
    if type(require_selection) is not bool:
        raise ValueError('require_selection must be boolean')
    host_driver = Driver()
    assert model_provider in (None, 'claude', 'codex'), 'unsupported model provider'
    audit = ProcessInputAudit(interval=.005, startup_timeout=10, stop_timeout=3,
                             max_samples=30000 if model_provider else 10000, require_selection=require_selection)
    report = {'passed': False, 'scope': 'owned two-window custom navigation controls',
              'transport': type(driver).__name__, 'actions': [], 'refusals': [],
              'model_provider_requested': model_provider, 'require_selection': require_selection}
    sources = ['tests/fixtures/NativeNavigationFixture.swift', 'examples/probes/native-navigation.py',
        'nlc/driver.py', 'nlc/native_key_events.py', 'nlc/native_window_binding.py',
        'nlc/native_window_dispatch.py', 'nlc/native_window_ready.py', 'nlc/general.py', 'nlc/flow.py', 'nlc/protocol.py',
        'nlc/input_audit.py', 'nlc/input_audit_process.py', 'nlc/native_frontmost.py',
        'helper/WindowInput.swift', 'helper/WindowDispatch.swift', 'helper/TactoHelper.swift',
        'launcher/tacto.swift', 'nlc/helper.py', 'nlc/promote.py', 'nlc/authoring.py']
    def source_hashes():
        return {name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in sources}
    report['source_hashes_before'] = source_hashes()
    child = None
    try:
        assert host_driver._resolve(TARGET, launch=False) is None, 'owned fixture already running'
        with tempfile.TemporaryDirectory(prefix='nlc-native-navigation-') as directory:
            bundle = Path(directory) / 'Navigation.app'
            binaries = bundle / 'Contents/MacOS'; binaries.mkdir(parents=True)
            binary = binaries / 'Navigation'
            (bundle / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': TARGET, 'CFBundleName': 'NLC Navigation Fixture',
                'CFBundleExecutable': 'Navigation', 'CFBundlePackageType': 'APPL'}))
            subprocess.run(['swiftc', '-parse-as-library', '-O', '-framework', 'AppKit',
                str(root / 'tests/fixtures/NativeNavigationFixture.swift'), '-o', str(binary)],
                check=True, capture_output=True, timeout=60)
            with audit:
                try:
                    child = subprocess.Popen([str(binary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    report['fixture_pid'] = child.pid
                    until = time.monotonic() + 15
                    while host_driver._resolve(TARGET, launch=False) != child.pid:
                        assert child.poll() is None and time.monotonic() < until
                        time.sleep(.1)
                    time.sleep(.3)

                    def state():
                        found, stack, seen = {}, [AXUIElementCreateApplication(child.pid)], 0
                        while stack:
                            seen += 1; assert seen <= 150, 'host AX read budget exceeded'
                            node = stack.pop(); identifier = _attr(node, 'AXIdentifier')
                            if isinstance(identifier, str) and identifier.startswith('nav-'):
                                assert identifier not in found
                                found[identifier] = _attr(node, 'AXValue')
                            stack.extend(_attr(node, 'AXChildren') or [])
                        assert len(found) == 6, found
                        return found

                    def observe():
                        value = driver.observe(TARGET, 'tree_full')
                        assert value.get('type') == 'observation', value
                        return value

                    def index(observation, identifier):
                        return Selector(identifier=identifier).resolve(observation['tree'])[0]

                    def refused(observation, identifier, key, *, stale=False, expired=False):
                        action = {'name': 'press_key', 'element_index': index(observation, identifier), 'key': key}
                        if expired and not isinstance(driver, Driver):
                            result = driver._send({'type':'act','target':TARGET,'obs_id':observation['obs_id'],
                                'action':action,'click_deadline_uptime':time.monotonic()-1})
                        else:
                            with model_deadline(time.monotonic() - 1 if expired else time.monotonic() + 5):
                                result = driver.act('foreign' if stale else observation['obs_id'], TARGET, action)
                        report['refusals'].append(result)
                        assert result.get('type') == 'error' and result.get('effect') == 'not_started', result

                    initial = state(); report['initial'] = initial
                    assert all('downs=0 ups=0 routeErrors=0 key=false' in initial[f'nav-{s}-state'] for s in ('A', 'B'))
                    observation = observe()
                    refused(observation, 'nav-A-first', 'cmd+Delete')
                    refused(observation, 'nav-A-first', 'Escape')
                    refused(observation, 'nav-A-first', 'Right', stale=True)
                    refused(observation, 'nav-A-first', 'Right', expired=True)
                    assert state() == initial
                    window, _ = Selector(role='Window', title='NLC Navigation Fixture A').resolve(observation['tree'])
                    prepared = driver.act(observation['obs_id'], TARGET, {'name': 'prepare_window', 'element_index': window})
                    report['preparation'] = prepared
                    assert prepared.get('type') == 'acted', prepared
                    time.sleep(.2)
                    report['prepared_state'] = state()
                    observation = observe(); refused(observation, 'nav-B-first', 'Right')
                    for key, identifier, position, focused in [
                        ('Right', 'nav-A-first', 1, 'nav-A-first'),
                        ('Left', 'nav-A-first', 0, 'nav-A-first'),
                        ('Up', 'nav-A-first', 10, 'nav-A-first'),
                        ('Down', 'nav-A-first', 0, 'nav-A-first'),
                        ('Tab', 'nav-A-first', 0, 'nav-A-second'),
                        ('shift+Tab', 'nav-A-second', 0, 'nav-A-first'),
                    ]:
                        observation = observe()
                        result = driver.act(observation['obs_id'], TARGET, {'name': 'press_key',
                            'element_index': index(observation, identifier), 'key': key})
                        report['actions'].append({'key': key, 'result': result})
                        assert result.get('type') == 'acted' and result.get('posted_events') == 2, result
                        assert result.get('unverified') == 'observe_effect' and result.get('reobserve_required') is True
                        time.sleep(.12)
                        after = state(); report['actions'][-1]['host_after'] = after
                        assert after['nav-A-first'] == f'position={position}', after
                        assert f'focus={focused}' in after['nav-A-state'], after
                        assert 'downs=0 ups=0 routeErrors=0' in after['nav-B-state'], after
                        assert 'routeErrors=0' in after['nav-A-state'], after
                        if key == 'Tab':
                            refused(observe(), 'nav-A-first', 'Tab')
                    report['final'] = state()
                    assert 'downs=6 ups=6 routeErrors=0' in report['final']['nav-A-state']
                    from nlc.general import run
                    plans = []
                    class OwnedNavigationDriver:
                        """Closed model probe: one Right on A-first, plus reads."""
                        def __init__(self): self.observation = None; self.key_attempts = 0
                        def __getattr__(self, name): return getattr(driver, name)
                        def observe(self, *args, **kwargs):
                            assert args[0] == TARGET
                            self.observation = driver.observe(*args, **kwargs)
                            return self.observation
                        def act(self, obs_id, target, action, **kwargs):
                            if target != TARGET or self.observation is None or obs_id != self.observation['obs_id']:
                                return {'type':'error','effect':'not_started','error':'owned_observation_required'}
                            if action.get('name') == 'press_key':
                                expected = index(self.observation, 'nav-A-first')
                                if action.get('key') != 'Right' or action.get('element_index') != expected or self.key_attempts:
                                    return {'type':'error','effect':'not_started','error':'owned_one_right_required'}
                                self.key_attempts += 1
                            elif action.get('name') != 'read':
                                return {'type':'error','effect':'not_started','error':'owned_navigation_or_read_only'}
                            return driver.act(obs_id, target, action, **kwargs)
                    general_driver = OwnedNavigationDriver() if model_provider else driver
                    def planner(goal, candidates, history):
                        if model_provider:
                            from nlc.ask import ask_here_paged
                            for candidate in candidates:
                                candidate['can'] = [value for value in candidate['can'] if value in ('press_key','read')]
                            answer = ask_here_paged(goal, candidates, history, provider=model_provider, max_pages=2)
                            plans.append({'candidates': candidates, 'answer': answer})
                            return answer
                        plans.append(candidates)
                        if len(plans) > 2: return {'done': True}
                        candidate = next(c for c in candidates if c.get('match') == 'nav-A-first')
                        action = {'do': 'press_key' if len(plans)==1 else 'read', 'candidate': candidate}
                        if len(plans)==1: action['key'] = 'Right'
                        return {'actions': [action]}
                    general = run(general_driver, TARGET,
                        'nav-A-first에 Right 탐색 키를 정확히 한 번 보내고, 새 관측에서 그 요소의 실제 값을 읽어주세요. 다른 요소의 입력이나 창 준비·버튼 클릭은 하지 마세요.',
                        planner, max_screens=6 if model_provider else 4,
                        timeout_seconds=65 if model_provider else 10)
                    report['general'] = {'ok': general.ok, 'why': general.why, 'said': general.said,
                        'events': general.events, 'certainty': general.certainty,
                        'plans': plans, 'provider_calls': general.provider_calls}
                    assert general.ok and any('position=1' in value for value in general.said), report['general']
                    time.sleep(.12)
                    report['final'] = state()
                    assert 'downs=7 ups=7 routeErrors=0' in report['final']['nav-A-state']
                    assert 'downs=0 ups=0 routeErrors=0' in report['final']['nav-B-state']
                    from nlc.flow import Flow, Step, Postcondition, replay, pin_params
                    from nlc.contract import Envelope, ContextBinding
                    field = Selector(role='TextArea', identifier='nav-A-first')
                    # Explicit owned-fixture reset, then replay the observed
                    # Right/read candidate. This is no retry or model step.
                    observed = observe()
                    reset = driver.act(observed['obs_id'], TARGET, {'name':'press_key',
                        'element_index':index(observed, 'nav-A-first'), 'key':'Left'})
                    assert reset.get('type') == 'acted' and reset.get('posted_events') == 2, reset
                    time.sleep(.12)
                    assert state()['nav-A-first'] == 'position=0'
                    from nlc.promote import candidate_from, deployable
                    candidate = candidate_from(general, TARGET, 'navigate once and read')
                    assert candidate is not None
                    promoted, reasons = deployable(candidate, 'owned-promoted-navigation', candidate.goal,
                        navigation_postconditions={0:Postcondition(field=field, attribute='Value', equals='position=1')})
                    assert promoted is not None and not reasons, reasons
                    promoted_result = replay(driver, promoted)
                    report['promotion'] = {'ok':promoted_result.ok, 'events':promoted_result.events,
                        'log':promoted_result.log, 'flow':json.loads(promoted.to_json()),
                        'reset':reset, 'model_calls_during_replay':0}
                    assert promoted_result.ok, report['promotion']
                    time.sleep(.12)
                    assert state()['nav-A-first'] == 'position=1'
                    params = {'key': 'Up'}
                    step = Step('press_key', field, params=params,
                        safety=Envelope.minimal().contract_for({'write_ephemeral'}, 'navigate'),
                        binding=ContextBinding(bundle_id=TARGET, role='TextArea', identifier='nav-A-first',
                            operation='press_key', params_digest=pin_params(params)),
                        postcondition=Postcondition(field=field, attribute='Value', equals='position=11'))
                    replayed = replay(driver, Flow('owned-navigation', TARGET, [step], schema=2))
                    report['replay'] = {'ok': replayed.ok, 'events': replayed.events, 'log': replayed.log}
                    assert replayed.ok, report['replay']
                    time.sleep(.12)
                    report['final'] = state()
                    assert report['final']['nav-A-first'] == 'position=11'
                    assert 'downs=10 ups=10 routeErrors=0' in report['final']['nav-A-state']
                    assert 'downs=0 ups=0 routeErrors=0' in report['final']['nav-B-state']
                    report['passed'] = True
                finally:
                    if child is not None:
                        if child.poll() is None:
                            child.terminate()
                            try: child.wait(timeout=3)
                            except subprocess.TimeoutExpired: child.kill(); child.wait(timeout=3)
                        report['owned_child_reaped'] = child.poll() is not None
    except Exception as error:
        report['passed'] = False
        report['error'] = type(error).__name__ + ':' + str(error)
    report['input_audit'] = audit.report()
    report['source_hashes_after'] = source_hashes()
    report['source_stable_during_run'] = report['source_hashes_before'] == report['source_hashes_after']
    if not report['source_stable_during_run']:
        report['passed'] = False
        report.setdefault('error', 'listed source files changed during run')
    if report['input_audit'].get('status') != 'UNCHANGED_SAMPLED':
        report['passed'] = False
        report.setdefault('error', 'input changed or inconclusive; cause not attributed')
    with Path(output).open('x') as stream:
        import os
        os.chmod(output, 0o600)
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report
