"""Host receipt for one confirmed Safari address-field navigation.

Safari's window reports the page it shows as its AXDocument, an http(s) URL.
Typing into the address field does not change it; loading a page does. So
the navigation is witnessed by the window, not by the key press:

- before the press (from the observation the press is addressed to): the
  front window shows some other page;
- after it (fresh observations): the front window shows the pinned
  destination.

What it certifies is that this browser window loaded that URL. It is not a
server-side receipt: what the site did with the request is not observed. A
redirect to another URL is reported as not shown, never adopted.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time
import uuid
from urllib.parse import urlsplit, urlunsplit

from .artifact_receipt import ArtifactReceiptError
from .commit_evidence import CommitEvidence, NavigationEvidence, action_digest, _event_binding_matches


class NavigationReceiptError(ArtifactReceiptError):
    """Sanitized refusal; an uncertain navigation must not be repeated."""


PUBLIC_CODES = frozenset({
    'already_prepared', 'not_a_bound_navigation', 'event_malformed', 'window_unreadable',
    'already_at_destination', 'already_used', 'commit_mismatch', 'reader_failed',
    'destination_not_shown',
})


def _refuse(code):
    assert code in PUBLIC_CODES, code
    error = NavigationReceiptError(code)
    error.public_code = code
    return error


def normalized_url(text):
    """Scheme and host lowercased, default port and an empty path made explicit.

    None for anything that is not an absolute http(s) URL without credentials.
    """
    if not isinstance(text, str) or not text or len(text) > 2048:
        return None
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    scheme = parts.scheme.lower()
    if scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        return None
    host = parts.hostname.lower()
    if port is not None and port != {'http': 80, 'https': 443}[scheme]:
        host = f'{host}:{port}'
    return urlunsplit((scheme, host, parts.path or '/', parts.query, parts.fragment))


def front_window_url(tree):
    """The normalized http(s) Document of the one window marked [focused]."""
    from .document_label import window_attributes, window_flag
    import re
    shown = [window_attributes(line) or {} for line in tree.splitlines()
             if re.match(r'^\d+\s+(?:AX)?Window\b', line) and window_flag(line, 'focused')]
    if len(shown) != 1:
        return None
    return normalized_url(shown[0].get('Document'))


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    action_digest: str
    before: str


class NavigationReceipt:
    """Witness one navigation in the front Safari window.

    `before_tree` is the observation the press is addressed to; reading the
    live window again before the press would replace that observation.
    `read_tree(target)` returns a fresh tree after the press.
    """

    def __init__(self, read_tree, *, target, destination, before_tree,
                 timeout_seconds=10.0, poll_interval=.25, clock=None, sleep=None):
        if not callable(read_tree):
            raise ValueError('read_tree must be callable')
        if not isinstance(target, str) or not target.strip():
            raise ValueError('target must be non-empty text')
        if normalized_url(destination) is None:
            raise ValueError('destination must be an absolute http(s) URL')
        if not isinstance(before_tree, str):
            raise ValueError('before_tree must be an observed tree')
        for name, number in (('timeout_seconds', timeout_seconds), ('poll_interval', poll_interval)):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError(f'{name} must be finite and positive')
        self._read_tree, self._target = read_tree, target
        self._destination = normalized_url(destination)
        self._before_tree = before_tree
        self._timeout, self._poll = float(timeout_seconds), float(poll_interval)
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._prepared = None
        self._prepare_attempted = False
        self._after_used = False

    @property
    def commit_expected(self):
        return {'이동할 주소': self._destination}

    def prepare_commit(self, event):
        if self._prepare_attempted:
            raise _refuse('already_prepared')
        self._prepare_attempted = True
        action, effects = event.get('action'), set(event.get('effects') or ())
        if (event.get('target') != self._target or not isinstance(action, dict)
                or action.get('name') != 'press_key' or action.get('key') != 'Return'
                or set(action) != {'name', 'key', 'element_index'}
                or 'transmit' not in effects or effects - {'transmit', 'read', 'write_ephemeral'}):
            raise _refuse('not_a_bound_navigation')
        digest = action_digest(action, event['effects'], self._target, obs_id=event.get('obs_id'))
        if event.get('action_digest') != digest or not isinstance(event.get('event_id'), str):
            raise _refuse('event_malformed')
        before = front_window_url(self._before_tree)
        if before is None:
            # A blank or start page has no http(s) document; any page differs from it.
            before = ''
        if before == self._destination:
            raise _refuse('already_at_destination')
        if not _event_binding_matches(event, event['event_id'], digest):
            raise _refuse('event_malformed')
        self._prepared = _Prepared(event['event_id'], digest, before)
        return self._prepared

    def after_commit(self, event, driver_result, context):
        if self._after_used:
            raise _refuse('already_used')
        self._after_used = True
        prepared = self._prepared
        if (type(context) is not _Prepared or context is not prepared
                or event.get('event_id') != prepared.event_id
                or event.get('action_digest') != prepared.action_digest):
            raise _refuse('commit_mismatch')
        deadline = self._clock() + self._timeout
        while True:
            try:
                tree = self._read_tree(self._target)
            except Exception:
                raise _refuse('reader_failed') from None
            if not isinstance(tree, str):
                raise _refuse('reader_failed')
            if front_window_url(tree) == self._destination:
                return CommitEvidence(prepared.event_id, prepared.action_digest,
                                      NavigationEvidence(uuid.uuid4().hex, self._destination))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _refuse('destination_not_shown')
            self._sleep(min(self._poll, remaining))
