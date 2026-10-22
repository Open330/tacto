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
        result = {'error': None}
        def completed(app, error):
            if error is not None:
                result['error'] = 'open_failed'
        self.workspace.openURLs_withApplicationAtURL_configuration_completionHandler_(
            [NSURL.URLWithString_(url)], NSURL.fileURLWithPath_(app_path), config, completed)
        return result


def loaded_somewhere(tree, url):
    """Whether any window of this observation shows `url` as its page."""
    return any(_page_url(block) == url for _, block in _windows(tree))


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
        request = backend.open_url(target, path)
    except Exception:
        return error('native_open_unavailable', 'unknown')
    result = {'type': 'url_opened', 'url': target, 'browser': browser}
    if driver is None:
        return dict(result, loaded_verified=False, note='dispatched; nothing observed it load')
    deadline = clock() + timeout
    while True:
        if request.get('error'):
            return error('open_failed', 'unknown')
        obs = driver.observe(browser)
        if obs.get('type') == 'observation' and loaded_somewhere(obs.get('tree', ''), target):
            return dict(result, loaded_verified=True)
        if clock() >= deadline:
            # A redirect lands elsewhere and is not adopted; never opened twice.
            return dict(result, type='error', error='page_not_shown', effect='unknown', loaded_verified=False)
        backend.pump(min(.25, max(0, deadline - clock())))
        sleep(0)
