"""Open a web address in a browser, on a person's request, as a link click does.

LaunchServices hands the URL to the browser: no typing into an address bar, no
click, no accessibility input. The browser comes to the front, so this exists
for a person who asked for it, like `tacto activate`. With an observing driver
the page is confirmed by the URL its window's web area reports, the same
evidence the Safari navigation receipt uses.
"""
import re
import time

from .app_launch import NativeLaunchBackend, _session_error, _session_problem
from .navigation_receipt import _page_url, _windows, normalized_url

DEFAULT_BROWSER = 'com.apple.Safari'


class NativeURLBackend(NativeLaunchBackend):
    def open_url(self, url, app_path):
        from AppKit import NSWorkspaceOpenConfiguration
        from Foundation import NSURL
        config = NSWorkspaceOpenConfiguration.configuration()
        config.setActivates_(True)
        config.setAddsToRecentItems_(False)
        # LaunchServices answers later, on this thread's run loop.
        result = {'state': 'pending'}
        def completed(app, error):
            result['state'] = 'error' if error is not None else 'accepted'
        self.workspace.openURLs_withApplicationAtURL_configuration_completionHandler_(
            [NSURL.URLWithString_(url)], NSURL.fileURLWithPath_(app_path), config, completed)
        return result


def front_page(tree):
    """The page URL of the one [focused] window: where a link click's page shows."""
    from .document_label import window_flag
    focused = [block for line, block in _windows(tree) if window_flag(line, 'focused')]
    return _page_url(focused[0]) if len(focused) == 1 else None


def open_url(url, *, browser=DEFAULT_BROWSER, driver=None, backend=None,
             timeout=10, clock=time.monotonic, sleep=time.sleep):
    def error(code, effect='not_started', **extra):
        return {'type': 'error', 'error': code, 'url': url, 'browser': browser, 'effect': effect, **extra}
    target = normalized_url(url)
    if target is None:
        return error('invalid_url', detail='an absolute http(s) URL without credentials')
    if (not isinstance(browser, str) or len(browser) > 255
            or re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', browser) is None):
        return error('invalid_bundle_id')
    try:
        backend = backend if backend is not None else NativeURLBackend()
        problem = _session_problem(backend)
        if problem:
            return dict(error(problem), **_session_error(problem))
        path = backend.resolve(browser)
        if path is None:
            return error('application_not_installed')
        before = None
        if driver is not None:
            seen = driver.observe(browser)
            before = front_page(seen.get('tree', '')) if seen.get('type') == 'observation' else None
        request = backend.open_url(target, path)
    except Exception:
        return error('native_open_unavailable', 'unknown')
    result = {'type': 'url_opened', 'url': target, 'browser': browser}
    deadline = clock() + timeout
    # The hand-off first: LaunchServices must have accepted it before any
    # page counts, and a failure it reports later is never hidden by one.
    while request.get('state') == 'pending' and clock() < deadline:
        backend.pump(min(.05, max(0, deadline - clock())))
        sleep(0)
    if request.get('state') == 'error':
        return error('open_failed', 'unknown')
    if request.get('state') != 'accepted':
        return dict(result, type='error', error='open_unconfirmed', effect='unknown', loaded_verified=False)
    if driver is None:
        return dict(result, loaded_verified=False, note='handed to the browser; nothing observed it load')
    while True:
        obs = driver.observe(browser)
        shown = front_page(obs.get('tree', '')) if obs.get('type') == 'observation' else None
        if shown == target:
            # Already in front before: the page is there, not shown to have loaded anew.
            return dict(result, loaded_verified=before != target, already_open=before == target)
        if clock() >= deadline:
            # A redirect lands elsewhere and is not adopted; never opened twice.
            return dict(result, type='error', error='page_not_shown', effect='unknown', loaded_verified=False)
        backend.pump(min(.25, max(0, deadline - clock())))
        sleep(0)
