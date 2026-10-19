"""Finite, nondecreasing host time at receipt acceptance boundaries."""
import math
import time

from .artifact_receipt import ArtifactReceiptError

PUBLIC_CODES = frozenset({'invalid_receipt_clock'})


class ReceiptClockError(ArtifactReceiptError):
    public_code = 'invalid_receipt_clock'

    def __init__(self):
        super().__init__(self.public_code)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def checked_receipt_clock(clock=None):
    source = time.monotonic if clock is None else clock
    if not callable(source):
        raise ValueError('receipt clock must be callable')
    previous = None

    def read():
        nonlocal previous
        try:
            value = source()
        except Exception:
            raise ReceiptClockError() from None
        if not _finite(value) or (previous is not None and value < previous):
            raise ReceiptClockError()
        previous = value
        return value

    return read


def receipt_deadline(clock, seconds):
    try:
        deadline = clock() + seconds
    except ReceiptClockError:
        raise
    except Exception:
        raise ReceiptClockError() from None
    if not _finite(deadline):
        raise ReceiptClockError()
    return deadline
