"""Complete AX text witnesses without native imports or UI mutations.

Callbacks supply raw AX results, preserving failure separately from empty text.
This does not identify a document or declare any driver capability. Callers must
pin and validate the target element's identity separately.
"""
from dataclasses import dataclass


class TextSnapshotError(ValueError):
    """No complete, internally consistent text witness could be obtained."""


@dataclass(frozen=True, slots=True)
class CompleteText:
    text: str
    utf16_units: int


def _read(callback, *args):
    try:
        result = callback(*args)
    except Exception as exc:
        raise TextSnapshotError('AX read raised an exception') from exc
    if (not isinstance(result, tuple) or len(result) != 2
            or not isinstance(result[0], int) or isinstance(result[0], bool)
            or result[0] != 0):
        raise TextSnapshotError('AX read failed or returned an invalid result')
    return result[1]


def _count(value, budget):
    # PyObjC returns OC_PythonLong (an int subclass), not always a built-in int.
    if not isinstance(value, int) or isinstance(value, bool):
        raise TextSnapshotError('AXNumberOfCharacters must be a nonnegative integer')
    value = int(value)
    if value < 0:
        raise TextSnapshotError('AXNumberOfCharacters must be a nonnegative integer')
    if value > budget:
        raise TextSnapshotError('AX text exceeds the UTF-16 budget')
    return value


def _text(value, count):
    if not isinstance(value, str):
        raise TextSnapshotError('AX text value is not a string')
    # Bound encoding work before allocating UTF-16 bytes. Every code point takes
    # at least one UTF-16 unit; lone surrogates are rejected by strict encoding.
    if len(value) > count:
        raise TextSnapshotError('AX text length differs from AXNumberOfCharacters')
    try:
        units = len(value.encode('utf-16-le')) // 2
    except UnicodeEncodeError as exc:
        raise TextSnapshotError('AX text contains invalid Unicode') from exc
    if units != count:
        raise TextSnapshotError('AX text length differs from AXNumberOfCharacters')
    return value


def read_complete_text(read_attribute, read_range, *, max_utf16_units):
    """Cross-check AXValue against a full range, then reread count and AXValue.

    ``read_attribute(name)`` and ``read_range(start, length)`` must return
    ``(AXError, value)`` with integer error zero on success. Range offsets and
    the required explicit budget use UTF-16 code units, not Python characters.
    A successful empty document requires a successful range read at (0, 0).
    Sequential reads detect disagreement, but cannot rule out an ABA edit that
    changes and returns to the same contents between observations.
    """
    if type(max_utf16_units) is not int or max_utf16_units < 0:
        raise ValueError('max_utf16_units must be a nonnegative integer')
    count = _count(_read(read_attribute, 'AXNumberOfCharacters'), max_utf16_units)
    value = _text(_read(read_attribute, 'AXValue'), count)
    ranged = _text(_read(read_range, 0, count), count)
    if ranged != value:
        raise TextSnapshotError('AXValue and AXStringForRange disagree')
    after_count = _count(_read(read_attribute, 'AXNumberOfCharacters'), max_utf16_units)
    after_value = _text(_read(read_attribute, 'AXValue'), after_count)
    if after_count != count or after_value != value:
        raise TextSnapshotError('AX text changed while taking the snapshot')
    return CompleteText(value, count)
