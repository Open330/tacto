"""Host receipt for one confirmed Safari address-field navigation.

Safari's page is the window's web area, whose AXURL is the loaded page's
URL. Typing into the address field does not change it; loading a page does.
So the navigation is witnessed by the window, not by the key press:

- before the press (from the observation the press is addressed to): the
  window holding the pressed address field shows some other page;
- after it (fresh observations): that same window, found by its identifier,
  shows the pinned destination.

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


def _windows(tree):
    """[(window line, its lines)] for each top-level window of a tree."""
    import re
    lines = tree.splitlines()
    tops = [i for i, line in enumerate(lines) if re.match(r'^\d+\s', line)]
    return [(lines[i], lines[i:next((j for j in tops if j > i), len(lines))])
            for i in tops if re.match(r'^\d+\s+(?:AX)?Window\b', lines[i])]


def _page_url(block):
    """The page of one window: its shallowest WebArea, unique, with a URL.

    Safari leaves a window's AXDocument empty; the page is its AXWebArea, whose
    AXURL the helper reports on a fully quoted WebArea line (a page title is
    the site's text and must not be able to spell a URL field). Frames are
    deeper WebAreas. A start page has none: ''.
    """
    import re
    from .document_label import web_area_attributes
    areas = [(len(line) - len(line.lstrip(' ')), line) for line in block[1:]
             if re.match(r'^\s*\d+\s+(?:AX)?WebArea\b', line)]
    if not areas:
        return ''
    shallowest = min(depth for depth, _ in areas)
    pages = [line for depth, line in areas if depth == shallowest]
    if len(pages) != 1:
        return None
    fields = web_area_attributes(pages[0])
    return normalized_url(fields.get('URL')) if fields else None


def window_of(tree, element_index):
    """The Identifier of the one window whose lines hold `element_index`."""
    import re
    from .document_label import window_attributes
    found = [line for line, block in _windows(tree)
             if any(re.match(rf'^\s*{element_index}\s', item) for item in block)]
    if len(found) != 1:
        return None
    identifier = (window_attributes(found[0]) or {}).get('Identifier')
    return identifier if isinstance(identifier, str) and identifier else None


def page_url(tree, window):
    """The page URL of the window with Identifier `window`; '' for none, None unknown."""
    from .document_label import window_attributes
    blocks = [block for line, block in _windows(tree)
              if (window_attributes(line) or {}).get('Identifier') == window]
    return _page_url(blocks[0]) if len(blocks) == 1 else None


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    action_digest: str
    before: str
    window: str


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
        # The window holding the pressed address field, by its own identifier:
        # not whichever window is in front, which may be another tab or a start page.
        window = window_of(self._before_tree, action['element_index'])
        before = page_url(self._before_tree, window) if window is not None else None
        if before is None:
            raise _refuse('window_unreadable')
        if before == self._destination:
            raise _refuse('already_at_destination')
        if not _event_binding_matches(event, event['event_id'], digest):
            raise _refuse('event_malformed')
        self._prepared = _Prepared(event['event_id'], digest, before, window)
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
            if page_url(tree, prepared.window) == self._destination:
                return CommitEvidence(prepared.event_id, prepared.action_digest,
                                      NavigationEvidence(uuid.uuid4().hex, self._destination))
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _refuse('destination_not_shown')
            self._sleep(min(self._poll, remaining))
