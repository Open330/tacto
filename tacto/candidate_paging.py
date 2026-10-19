"""Lossless, budgeted pages of candidates with original pick identities.

The default counter charges one unit per UTF-8 byte: a conservative estimate
for byte-level tokenizers, not a claim about arbitrary provider tokenization.
Inject the provider's counter and a full-prompt renderer for exact accounting.
An oversized item is returned intact with ``overflow=True``; callers must not
silently submit that page as if it satisfied the budget.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Sequence


IndexedCandidates = tuple[tuple[int, dict], ...]


class StaleCursor(ValueError):
    """A page cursor belongs to different candidates or observation context."""


@dataclass(frozen=True)
class CandidatePage:
    items: IndexedCandidates
    text: str
    total: int
    next_cursor: str | None
    fingerprint: str
    token_count: int
    budget: int
    overflow: bool

    @property
    def picks(self) -> tuple[int, ...]:
        return tuple(pick for pick, _ in self.items)

    @property
    def candidates(self) -> tuple[dict, ...]:
        return tuple(candidate for _, candidate in self.items)


def utf8_token_upper_bound(text: str) -> int:
    """Byte-level-tokenizer estimate; excludes hidden provider framing."""
    return len(text.encode("utf-8"))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _render(items: IndexedCandidates) -> str:
    return _json([{"pick": pick, "candidate": candidate}
                  for pick, candidate in items])


def page_candidates(
    candidates: Sequence[dict], *, budget: int, cursor: str | None = None,
    token_counter: Callable[[str], int] | None = None,
    render: Callable[[IndexedCandidates], str] | None = None,
    context: str = "", order: Sequence[int] | None = None,
) -> CandidatePage:
    """Return the next ordered page, preserving every field and original pick.

    Candidates are JSON-serializable plain data. Their nested fields are copied
    for this page; a custom renderer must not mutate the candidate snapshot.

    ``render`` receives indexed candidates and may return the complete prompt,
    including instructions and goal. ``context`` should include the observation
    identity (and goal if relevant), so identical-looking new screens cannot
    reuse an old cursor. Budget changes between pages are allowed. Cursors are
    consistency markers, not credentials or authorization tokens.

    Empty pages occur only for an empty candidate collection. If the first
    candidate cannot fit, it is emitted alone with an explicit overflow flag;
    the next cursor still allows inspection of subsequent candidates.

    ``order`` optionally presents the same candidates in another sequence: a
    permutation of every index, so nothing is dropped or repeated. Items keep
    their original picks, and a cursor is bound to the order it was issued for.
    """
    if type(budget) is not int or budget <= 0:
        raise ValueError("budget must be a positive integer")
    if not isinstance(context, str):
        raise TypeError("context must be a string")
    rows = tuple(candidates)
    if any(not isinstance(candidate, dict) for candidate in rows):
        raise TypeError("candidates must contain dictionaries")
    _json(rows)  # Reject non-data values before invoking copy hooks.
    rows = deepcopy(rows)
    if order is None:
        sequence = tuple(range(len(rows)))
        bound = {"context": context, "candidates": rows}
    else:
        sequence = tuple(order)
        if (any(type(pick) is not int for pick in sequence)
                or sorted(sequence) != list(range(len(rows)))):
            raise ValueError("order must be a permutation of every candidate index")
        bound = {"context": context, "candidates": rows, "order": list(sequence)}
    fingerprint = hashlib.sha256(_json(bound).encode("utf-8")).hexdigest()
    start = 0
    if cursor is not None:
        if not isinstance(cursor, str):
            raise ValueError("invalid candidate cursor")
        parts = cursor.split(":")
        if (len(parts) != 3 or parts[0] != "v1" or
                not parts[2].isascii() or not parts[2].isdecimal()):
            raise ValueError("invalid candidate cursor")
        if parts[1] != fingerprint:
            raise StaleCursor("candidate cursor belongs to a different observation")
        start = int(parts[2])
        if not 0 <= start < len(rows):
            raise ValueError("candidate cursor is out of range")
    counter = token_counter or utf8_token_upper_bound
    renderer = render or _render

    def measure(items: IndexedCandidates) -> tuple[str, int]:
        before = _json(items) if render is not None else None
        text = renderer(items)
        if before is not None and _json(items) != before:
            raise ValueError("renderer modified candidates")
        if not isinstance(text, str):
            raise TypeError("renderer must return a string")
        count = counter(text)
        if type(count) is not int or count < 0:
            raise ValueError("token counter must return a nonnegative integer")
        return text, count

    accepted: IndexedCandidates = ()
    text, count = measure(accepted)
    for position in range(start, len(rows)):
        pick = sequence[position]
        trial = (*accepted, (pick, rows[pick]))
        trial_text, trial_count = measure(trial)
        if trial_count > budget and accepted:
            break
        accepted, text, count = trial, trial_text, trial_count
        if count > budget:
            break
    end = start + len(accepted)
    return CandidatePage(
        items=accepted, text=text, total=len(rows),
        next_cursor=f"v1:{fingerprint}:{end}" if end < len(rows) else None,
        fingerprint=fingerprint, token_count=count, budget=budget,
        overflow=count > budget,
    )
