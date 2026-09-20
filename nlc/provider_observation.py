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
