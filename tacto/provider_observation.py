"""Opt-in, context-local model metadata collection without additional calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_COLLECTORS = ContextVar('tacto_provider_collectors', default=())


@contextmanager
def capture_provider_calls():
    """Collect per-call metadata; prompts and model response text are excluded."""
    records = []
    token = _COLLECTORS.set((*_COLLECTORS.get(), records))
    try:
        yield records
    finally:
        _COLLECTORS.reset(token)


def collecting_provider_calls():
    return bool(_COLLECTORS.get())


def record_provider_result(result):
    if _COLLECTORS.get():
        metadata = result.to_dict()
        metadata.pop('text', None)
        for records in _COLLECTORS.get():
            records.append(deepcopy(metadata))
    return result


def provider_failure_reason(records):
    """Friendly closed failure text for the latest failed logical request.

    Ignore old attempts if a later request succeeded, and never surface the
    provider's message or infer refusal from its answer text.
    """
    messages = {
        'ClaudeProtocolError_model_refusal': '모델이 요청을 거절하여 작업을 중단했습니다',
        'ClaudeProtocolError_model_fallback': '요청한 모델의 응답을 검증하지 못하여 작업을 중단했습니다',
        'usage_limit': '모델 사용량 한도에 도달하여 작업을 중단했습니다',
        'not_logged_in': '모델 로그인이 필요하여 작업을 중단했습니다',
        'unsupported_client': '모델 서비스가 현재 클라이언트를 지원하지 않아 작업을 중단했습니다',
        'authentication_failed': '모델 인증에 실패하여 작업을 중단했습니다',
        'organization_access_disabled': '조직에서 Claude Code 구독 사용을 제한하여 작업을 중단했습니다',
    }
    if not records or not isinstance(records[-1], dict) or records[-1].get('status') != 'error':
        return None
    latest = records[-1]
    error = latest.get('error')
    code = error.get('code') if isinstance(error, dict) else None
    attempts = latest.get('attempts')
    if isinstance(attempts, list) and attempts and isinstance(attempts[-1], dict):
        code = attempts[-1].get('error_code')
    return messages.get(code) if isinstance(code, str) else None


def summarize_provider_calls(records):
    """Sum observed attempts without double-counting the logical final result.

    Route invocations need not be remote requests (a CLI may be absent), and
    unknown usage is never treated as zero. Costs retain currency AND basis.
    This summarizes observed telemetry, not invoices or remote cancellation.
    """
    from .provider_result import parse_provider_result, finite_nonnegative_number
    # Collectors omit text; reconstruct only to validate metadata, not content.
    observed = []
    positions = []
    logical_without_attempts = 0
    local_route_rejections = 0
    for request_index, record in enumerate(records):
        data = deepcopy(record)
        data['text'] = 'metadata-only' if data.get('status') == 'ok' else None
        result = parse_provider_result(data)
        if result.attempts:
            local_route_rejections += sum(
                attempt.transport == 'muxa' and attempt.status == 'error'
                and attempt.error_code == 'muxa_isolated_ask_unavailable'
                and all(getattr(attempt,key) is None for key in (
                    'model','provider_request_id','provider_thread_id','input_tokens','output_tokens',
                    'cached_input_tokens','cache_creation_input_tokens','total_input_tokens',
                    'input_tokens_basis','cost','cost_currency','cost_basis'))
                for attempt in result.attempts)
            observed.extend(result.attempts)
            positions.extend((request_index, index) for index in range(len(result.attempts)))
        else:
            logical_without_attempts += 1
            observed.append(result)
            positions.append((request_index, None))
    token_totals = {}
    for name in ('total_input_tokens', 'output_tokens'):
        known = [getattr(item, name) for item in observed if getattr(item, name) is not None]
        token_totals[name] = {'observed_sum': sum(known) if known else None,
                             'unknown_records': len(observed) - len(known)}
    costs = {}
    unknown_cost_records = 0
    unaggregated_costs = []
    cost_sum_errors = set()
    for item, (request_index, attempt_index) in zip(observed, positions):
        if item.cost is None or item.cost_currency is None or item.cost_basis is None:
            unknown_cost_records += 1
            if item.cost is not None:
                unaggregated_costs.append({'logical_request_index':request_index,
                    'attempt_index':attempt_index, 'provider':item.provider,
                    'transport':item.transport, 'status':item.status,
                    'observed_amount':item.cost, 'currency':item.cost_currency,
                    'basis':item.cost_basis,
                    'missing_fields':[name for name,value in (
                        ('currency',item.cost_currency),('basis',item.cost_basis)) if value is None]})
            continue
        key = (item.cost_currency, item.cost_basis)
        if key in cost_sum_errors:continue
        amount=costs.get(key,0)+item.cost
        if finite_nonnegative_number(amount):costs[key]=amount
        else:
            costs[key]=None
            cost_sum_errors.add(key)
    # Do not combine total-basis and uncached-basis input counts. Components
    # remain separate observations, not values to add to total_input_tokens.
    component_groups = {}
    for item in observed:
        component_groups.setdefault(item.input_tokens_basis, []).append(item)
    components = []
    for basis, items in sorted(component_groups.items(), key=lambda row:row[0] or ''):
        totals = {}
        for name in ('input_tokens','cached_input_tokens','cache_creation_input_tokens'):
            known = [getattr(item,name) for item in items if getattr(item,name) is not None]
            totals[name] = {'observed_sum':sum(known) if known else None,
                           'unknown_records':len(items)-len(known)}
        components.append({'input_tokens_basis':basis, 'records':len(items), 'tokens':totals})
    return {'logical_requests': len(records),
            'route_invocations': sum(len(record.get('attempts', ())) for record in records),
            'reported_local_route_rejections': local_route_rejections,
            'logical_requests_without_attempt_details': logical_without_attempts,
            'tokens': token_totals,
            'input_token_components': components,
            'costs': [{'currency': currency, 'basis': basis, 'observed_sum': amount,
                       **({'aggregation_error':'cost_sum_out_of_range'} if (currency,basis) in cost_sum_errors else {})}
                      for (currency, basis), amount in sorted(costs.items())],
            'unknown_cost_records': unknown_cost_records,
            'unaggregated_costs': unaggregated_costs}
