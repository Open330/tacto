"""Context-local model deadline shared by planner and witness callbacks."""
from contextlib import contextmanager
from contextvars import ContextVar
import math
import time

_DEADLINE = ContextVar('nlc_model_deadline', default=None)


@contextmanager
def model_deadline(deadline):
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise ValueError("model deadline must be finite")
    previous = _DEADLINE.get()
    effective = previous if deadline is None else deadline if previous is None else min(previous, deadline)
    token = _DEADLINE.set(effective)
    try:
        yield
    finally:
        _DEADLINE.reset(token)


def remaining_model_seconds(timeout_seconds=420):
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0):
        raise ValueError('model timeout must be finite and positive')
    remaining = min(timeout_seconds, 420)
    deadline = _DEADLINE.get()
    if deadline is not None:
        remaining = min(remaining, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError('model request deadline exceeded')
    return remaining
