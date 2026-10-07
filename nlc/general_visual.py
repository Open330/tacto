"""Explicit trusted native image scope for the general screen loop.

An inaccessible control cannot explain its effect. The host must know that the
approved region only performs reversible navigation/ephemeral input. A model
never chooses this scope, its callbacks, its effects, or an approval result.
"""
from copy import deepcopy
from dataclasses import dataclass
import time

from .contract import policy
from .flow import Selector, _same_role
from .commit_evidence import make_event
from .model_budget import model_deadline
from .native_visual_actions import capture_window, NativeVisualClickExecutor, native_visual_capabilities
from .provider_observation import capture_provider_calls


@dataclass(frozen=True)
class NativeVisualScope:
    window_selector: Selector
    planner: object
    authorize: object
    verify: object
    effects: frozenset
    when: object = None
    label: str = 'native visual navigation'
    verify_step: object = None
    input_actions: frozenset = frozenset({'click'})

    def __post_init__(self):
        native_visual_capabilities(self.input_actions)
        if not isinstance(self.window_selector, Selector) or not _same_role(self.window_selector.role or '', 'Window'):
            raise ValueError('visual scope requires an explicit Window selector')
        if not all(callable(callback) for callback in (self.planner, self.authorize, self.verify)):
            raise ValueError('visual planner, authorization and verification must be host callbacks')
        if self.verify_step is not None and not callable(self.verify_step):
            raise ValueError('visual step verification must be a host callback')
        if self.when is not None and not callable(self.when):
            raise ValueError('visual scope predicate must be a host callback')
        if (type(self.effects) is not frozenset or 'write_ephemeral' not in self.effects
                or not self.effects <= {'read', 'write_ephemeral'}):
            raise ValueError('visual navigation scope requires known reversible ephemeral effects; commit effects need a separate receipt path')
        if not isinstance(self.label, str) or not self.label.strip() or len(self.label) > 120:
            raise ValueError('visual scope requires a bounded host label')


class NativeVisualSession:
    def __init__(self, scope, driver, target, goal, envelope, deadline, stopped):
        self.scope, self.driver, self.target, self.goal = scope, driver, target, goal
        self.envelope, self.deadline, self.stopped = envelope, deadline, stopped
        self.identity, self.history, self.pending = None, [], []
        self.executor = NativeVisualClickExecutor(driver, target)

    def remaining(self):
        return min(5, self.deadline-time.monotonic()) if self.deadline is not None else 5

    def step(self, obs, res, *, allow_plan=True):
        """One visual screen, at most one pair. Return whether general stops."""
        from .general import Visit
        scope = self.scope
        required = native_visual_capabilities(scope.input_actions)
        if not required <= set(getattr(self.driver, 'capabilities', ()) or ()):
            res.why = '관측한 창에 결합한 시각 입력 capability가 없습니다'
            return True
        if not self.envelope.admits(scope.effects):
            res.why = '시각 입력이 부탁하신 효과 범위를 벗어납니다'
            return True
        decision = policy(self.envelope.contract_for(set(scope.effects), scope.label))
        if decision.risk not in ('none', 'preapproved'):
            res.why = '시각 입력 범위를 승인할 수 없습니다: ' + decision.reason
            return True
        try:
            index, _ = scope.window_selector.resolve(obs['tree'])
            if type(index) is not int or index < 0:
                res.why = '시각 입력 대상 창을 유일하게 찾지 못했습니다'
                return True
            if self.stopped():
                return True
            capture = capture_window(self.driver, obs['obs_id'], self.target,
                window_index=index, timeout_seconds=self.remaining())
            identity = (capture.pid, capture.window_id)
            if self.identity is not None and identity != self.identity:
                res.identity_changed = True
                res.why = '시각 입력 대상의 native 창 또는 프로세스가 바뀌었습니다'
                return True
            self.identity = identity
            visit = Visit(screen='visual:' + capture.sha256)
            if res.visits and res.visits[-1].actions:
                res.visits[-1].next_screen = visit.screen
            res.visits.append(visit)
            if self.stopped():
                return True
            # The host checks the receiving operation and the requested goal,
            # independently of image interpretation or a model's done flag.
            verified = scope.verify(capture, deepcopy(self.history)) is True
            if self.stopped():
                return True
            if verified:
                for event in self.pending:
                    event['state'] = 'confirmed'
                    event['visual_host_verified'] = True
                res.certainty = 'confirmed' if all(e['state']=='confirmed' for e in res.events) else 'unknown'
                visit.verified = True
                res.ok = res.verified = True
                res.why = '시각 입력 결과를 독립 호스트 검증으로 확인했습니다'
                return True
            if self.pending:
                if scope.verify_step is None or scope.verify_step(capture, deepcopy(self.history)) is not True:
                    res.why = '이전 시각 입력의 결과를 확인하지 못해 다음 입력을 중단했습니다'
                    return True
                if self.stopped():
                    return True
                for event in self.pending:
                    event['state'] = 'confirmed'
                    event['visual_step_verified'] = True
                self.pending.clear()
                res.certainty = 'confirmed' if all(e['state']=='confirmed' for e in res.events) else 'unknown'
            if not allow_plan:
                return False  # Receiving evidence was checked before returning to AX.
            calls = []
            try:
                res.asked += 1
                with capture_provider_calls() as calls, model_deadline(self.deadline):
                    proposal = scope.planner(capture, self.goal, deepcopy(self.history))
            finally:
                res.provider_calls.extend(calls)
            if self.stopped():
                return True
            if proposal == {'action':'done'}:
                res.why = '모델은 완료라고 했지만 독립 호스트 결과 증거가 없습니다'
                return True
            if proposal == {'action':'stop'}:
                res.why = '시각 판별기가 중단을 제안했습니다'
                return True
            proposed_kind = proposal.get('action') if isinstance(proposal, dict) else None
            if proposed_kind not in scope.input_actions:
                res.why = '호스트가 허용하지 않은 시각 입력 종류입니다'
                return True
            execute = self.executor.input if proposed_kind in ('key', 'text') else self.executor.click
            outcome = execute(capture, proposal, scope.authorize,
                cancelled=self.stopped, timeout_seconds=self.remaining())
            self.history.append(deepcopy(outcome))
            res.visual_history.append(deepcopy(outcome))
            record = {'do':outcome['action'], 'label':scope.label, 'role':'Window',
                      'attr':'Title', 'match':scope.window_selector.title or scope.label,
                      'visual_capture':capture.to_dict(), 'visual_effect':outcome['effect'],
                      'image_point':outcome.get('image_point'), 'point':outcome.get('window_point'),
                      'window_input_payload':outcome.get('window_input_payload')}
            visit.actions.append(record)
            if outcome['effect'] != 'not_started':
                action = outcome['dispatch_action']
                event = make_event(action, set(scope.effects), self.target,
                    state='dispatched' if outcome['effect']=='dispatched' else 'unknown', obs_id=capture.obs_id)
                event.update(visual_capture_id=capture.capture_id, visual_capture_sha256=capture.sha256,
                             observation_required=True)
                res.events.append(event); self.pending.append(event)
                res.effects |= set(scope.effects)
                res.certainty = 'unknown'
            if self.stopped():
                return True
            if outcome['effect'] != 'dispatched':
                res.why = '시각 입력을 확인하지 못했습니다: ' + outcome.get('reason','unknown')
                return True
            return False
        except TimeoutError:
            if not self.stopped():
                res.stop_reason = 'model_timeout'
                self.stopped()
            return True
        except Exception as error:
            if not self.stopped():
                res.why = '시각 실행 실패: ' + type(error).__name__ + ':' + str(error)[:160]
            return True
