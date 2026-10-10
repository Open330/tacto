"""Opt-in, context-local model metadata collection without additional calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

_COLLECTORS = ContextVar('nlc_provider_collectors', default=())


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
    from .provider_result import parse_provider_result
    # Collectors omit text; reconstruct only to validate metadata, not content.
    observed = []
    logical_without_attempts = 0
    for record in records:
        data = deepcopy(record)
        data['text'] = 'metadata-only' if data.get('status') == 'ok' else None
        result = parse_provider_result(data)
        if result.attempts:
            observed.extend(result.attempts)
        else:
            logical_without_attempts += 1
            observed.append(result)
    token_totals = {}
    for name in ('total_input_tokens', 'output_tokens'):
        known = [getattr(item, name) for item in observed if getattr(item, name) is not None]
        token_totals[name] = {'observed_sum': sum(known) if known else None,
                             'unknown_records': len(observed) - len(known)}
    costs = {}
    unknown_cost_records = 0
    for item in observed:
        if item.cost is None or item.cost_currency is None or item.cost_basis is None:
            unknown_cost_records += 1
            continue
        key = (item.cost_currency, item.cost_basis)
        costs[key] = costs.get(key, 0) + item.cost
    return {'logical_requests': len(records),
            'route_invocations': sum(len(record.get('attempts', ())) for record in records),
            'logical_requests_without_attempt_details': logical_without_attempts,
            'tokens': token_totals,
            'costs': [{'currency': currency, 'basis': basis, 'observed_sum': amount}
                      for (currency, basis), amount in sorted(costs.items())],
            'unknown_cost_records': unknown_cost_records}
