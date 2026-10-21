"""Explicit local application launch through LaunchServices, never AppleEvents."""
import math
import os
import re
import time


class NativeLaunchBackend:
    def __init__(self):
        from AppKit import NSWorkspace
        self.workspace = NSWorkspace.sharedWorkspace()

    def session_ready(self):
        from .session import lock_state
        state = lock_state()
        return state.get('locked') is False and state.get('on_console') is True

    def resolve(self, target):
        from Foundation import NSBundle
        url = self.workspace.URLForApplicationWithBundleIdentifier_(target)
        if url is None or not url.isFileURL():
            return None
        bundle = NSBundle.bundleWithURL_(url)
        if bundle is None or bundle.bundleIdentifier() != target:
            return None
        return os.path.realpath(str(url.path()))

    def running(self, target):
        return [dict(pid=int(app.processIdentifier()),
                     path=os.path.realpath(str(app.bundleURL().path())),
                     ready=bool(app.isFinishedLaunching()))
                for app in self.workspace.runningApplications()
                if app.bundleIdentifier() == target and app.bundleURL() is not None
                and not app.isTerminated()]

    def start(self, path):
        from AppKit import NSWorkspaceOpenConfiguration
        from Foundation import NSURL
        config = NSWorkspaceOpenConfiguration.configuration()
        config.setActivates_(False)
        config.setAddsToRecentItems_(False)
        config.setCreatesNewApplicationInstance_(False)
        result = {'error': None}
        def completed(app, error):
            if error is not None:
                result['error'] = 'launch_failed'
        self.workspace.openApplicationAtURL_configuration_completionHandler_(
            NSURL.fileURLWithPath_(path), config, completed)
        return result

    def pump(self, seconds):
        from Foundation import NSRunLoop, NSDate
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(seconds))


class NativePresentationBackend(NativeLaunchBackend):
    """Explicit result presentation, separate from background launch/input."""
    def activate(self, pid):
        from AppKit import NSRunningApplication, NSApplicationActivateIgnoringOtherApps
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        return app is not None and bool(app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps))

    def frontmost_pid(self):
        app = self.workspace.frontmostApplication()
        return int(app.processIdentifier()) if app is not None else None

    def open_activating(self, path):
        """Activate an already running app the way a Dock click does.

        Since macOS 14 a request from a process that is not itself in front
        may be declined; LaunchServices still honours an explicit open.
        """
        from AppKit import NSWorkspaceOpenConfiguration
        from Foundation import NSURL
        config = NSWorkspaceOpenConfiguration.configuration()
        config.setActivates_(True)
        config.setAddsToRecentItems_(False)
        config.setCreatesNewApplicationInstance_(False)
        self.workspace.openApplicationAtURL_configuration_completionHandler_(
            NSURL.fileURLWithPath_(path), config, None)


def present_app(target, pid, *, backend=None, clock=time.monotonic):
    """Bring an already verified result forward; never launch or write text."""
    failed = {'foreground_verified': False}
    if type(pid) is not int or pid <= 0:
        return dict(failed, error='invalid_presentation_pid')
    try:
        backend = backend if backend is not None else NativePresentationBackend()
        path = backend.resolve(target)
        apps = backend.running(target)
        if path is None or len(apps) != 1 or apps[0]['pid'] != pid or apps[0]['path'] != path:
            return dict(failed, error='presentation_identity_changed')
        if not backend.session_ready():
            return dict(failed, error='gui_session_unavailable')
        # A long-lived caller may not have run its loop since the last change;
        # deliver pending NSWorkspace notifications before trusting frontmost.
        backend.pump(0.05)
        if backend.frontmost_pid() == pid:
            return {'foreground_verified': True}
        if not backend.activate(pid):
            return dict(failed, error='activation_refused')
        deadline = clock() + 1
        while True:
            if backend.frontmost_pid() == pid:
                return {'foreground_verified': True}
            if clock() >= deadline:
                return dict(failed, error='activation_not_observed')
            backend.pump(min(.05, max(0, deadline - clock())))
    except Exception:
        return dict(failed, error='presentation_unavailable')


def launch_app(target, *, backend=None, timeout=10, clock=time.monotonic):
    def error(code, effect='not_started'):
        return {'type': 'error', 'error': code, 'target': target, 'effect': effect}
    if (not isinstance(target, str) or len(target) > 255
            or re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', target) is None):
        return error('invalid_bundle_id')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        return error('invalid_launch_timeout')
    started = False
    try:
        backend = backend if backend is not None else NativeLaunchBackend()
        if not backend.session_ready():
            return error('gui_session_unavailable')
        path = backend.resolve(target)
        if path is None:
            return error('application_not_installed')
        deadline = clock() + timeout
        request = None
        while True:
            apps = backend.running(target)
            if len(apps) > 1 or (apps and apps[0]['path'] != path):
                return error('application_identity_ambiguous', 'unknown' if started else 'not_started')
            if apps and apps[0]['ready']:
                return {'type': 'app_launch', 'target': target, 'pid': apps[0]['pid'],
                        'status': 'launched' if started else 'already_running',
                        'application_running': True, 'window_verified': False}
            if not started and not apps:
                if not backend.session_ready():
                    return error('gui_session_unavailable')
                started = True
                request = backend.start(path)
            if request is not None and request.get('error'):
                return error('launch_failed', 'unknown')
            if clock() >= deadline:
                # LaunchServices is asynchronous; a timeout must never cause a
                # second launch. The caller should observe before doing anything.
                return error('launch_timeout', 'unknown')
            backend.pump(min(.05, max(0, deadline - clock())))
    except Exception:
        # Native lookup and asynchronous dispatch can fail without an NSError
        # callback. Never expose raw OS exception text or resend a launch whose
        # dispatch may already have happened. Interrupts still propagate.
        return error('launch_failed' if started else 'native_launch_unavailable',
                     'unknown' if started else 'not_started')


def _open_activating(backend, target, path, pid, clock):
    """One LaunchServices activation of the same verified instance."""
    try:
        apps = backend.running(target)
        if len(apps) != 1 or apps[0]['pid'] != pid or apps[0]['path'] != path:
            return {'foreground_verified': False, 'error': 'presentation_identity_changed'}
        backend.open_activating(path)
        deadline = clock() + 2
        while backend.frontmost_pid() != pid:
            if clock() >= deadline:
                return {'foreground_verified': False, 'error': 'activation_not_observed'}
            backend.pump(min(.05, max(0, deadline - clock())))
        return {'foreground_verified': True, 'route': 'launch_services'}
    except Exception:
        return {'foreground_verified': False, 'error': 'activation_not_observed'}


def _windows_in(tree):
    """(element index, title, focused) for each top-level window line."""
    from .document_label import window_attributes, window_flag
    out = []
    for line in tree.splitlines():
        match = re.match(r'^(\d+)\s+(?:AX)?Window\b', line)
        if match is None:
            continue
        fields = window_attributes(line) or {}
        out.append((int(match.group(1)), fields.get('Title'), window_flag(line, 'focused')))
    return out


def activate_app(target, *, window=None, driver=None, backend=None,
                 clock=time.monotonic, sleep=time.sleep):
    """Bring a running app, and optionally one of its windows, to the front.

    Taking the foreground is never a side effect of another step: this exists
    for a person who asked for it. The app is activated only if exactly one
    verified instance runs; `window` must name exactly one of its windows by
    a part of its title, which the existing `prepare_window` then makes main.
    Nothing is launched, typed or clicked.
    """
    def error(code, effect='not_started', **extra):
        return {'type': 'error', 'error': code, 'target': target, 'effect': effect, **extra}
    if (not isinstance(target, str) or len(target) > 255
            or re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', target) is None):
        return error('invalid_bundle_id')
    if window is not None and (not isinstance(window, str) or not window.strip() or len(window) > 200
                               or any(ord(c) < 32 or ord(c) == 127 for c in window)):
        return error('invalid_window_text')
    if window is not None and driver is None:
        return error('window_needs_helper')
    try:
        backend = backend if backend is not None else NativePresentationBackend()
        if not backend.session_ready():
            return error('gui_session_unavailable')
        path = backend.resolve(target)
        if path is None:
            return error('application_not_installed')
        apps = [app for app in backend.running(target) if app['path'] == path]
        if not apps:
            return error('application_not_running', hint=f'tacto launch {target} --helper')
        if len(apps) > 1:
            return error('application_identity_ambiguous')
        pid = apps[0]['pid']
    except Exception:
        return error('native_activation_unavailable')
    presented = present_app(target, pid, backend=backend, clock=clock)
    if presented.get('error') in ('activation_refused', 'activation_not_observed'):
        presented = _open_activating(backend, target, path, pid, clock)
    if not presented.get('foreground_verified'):
        # An activation request may still land after the deadline.
        return error(presented.get('error', 'activation_refused'),
                     'unknown' if presented.get('error') == 'activation_not_observed' else 'not_started')
    result = {'type': 'app_activation', 'target': target, 'pid': pid, 'foreground_verified': True,
              'route': presented.get('route', 'running_application')}
    if window is None:
        return result

    def windows():
        obs = driver.observe(target)
        if obs.get('type') != 'observation':
            return None, None
        return obs, _windows_in(obs.get('tree', ''))
    obs, listed = windows()
    if obs is None:
        return dict(result, type='error', error='window_observation_unavailable', effect='not_started')
    titles = [title for _, title, _ in listed]
    matches = [w for w in listed if isinstance(w[1], str) and window in w[1]]
    if len(matches) != 1:
        return dict(result, type='error', effect='not_started', windows=titles[:20],
                    error='window_not_found' if not matches else 'window_ambiguous')
    index, title, focused = matches[0]
    if not focused:
        acted = driver.act(obs['obs_id'], target, {'name': 'prepare_window', 'element_index': index})
        if acted.get('type') != 'acted':
            return dict(result, type='error', error='window_not_prepared', window=title,
                        effect=acted.get('effect', 'unknown'), detail=acted.get('error'))
        deadline = clock() + 1
        while True:
            obs, listed = windows()
            focused = obs is not None and any(t == title and f for _, t, f in listed)
            if focused or clock() >= deadline:
                break
            sleep(0.1)
    # The app was in front before the window step; say so only if it still is.
    # Something else taking the foreground meanwhile is reported, never fought.
    # NSWorkspace's frontmost and running lists change only as this thread's
    # run loop delivers notifications, and the window step ran none: turn it
    # first, or the answer can be the one from before the step.
    try:
        backend.pump(0.05)
        still = (backend.session_ready() and backend.frontmost_pid() == pid
                 and [(a['pid'], a['path']) for a in backend.running(target)] == [(pid, path)])
    except Exception:
        still = False
    if not still:
        return dict(result, type='error', error='foreground_changed', effect='unknown',
                    foreground_verified=False, window=title, window_verified=False)
    return dict(result, window=title, window_verified=bool(focused))
