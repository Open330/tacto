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


def launch_app(target, *, backend=None, timeout=10, clock=time.monotonic):
    def error(code, effect='not_started'):
        return {'type': 'error', 'error': code, 'target': target, 'effect': effect}
    if (not isinstance(target, str) or len(target) > 255
            or re.fullmatch(r'[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+', target) is None):
        return error('invalid_bundle_id')
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        return error('invalid_launch_timeout')
    backend = backend if backend is not None else NativeLaunchBackend()
    if not backend.session_ready():
        return error('gui_session_unavailable')
    path = backend.resolve(target)
    if path is None:
        return error('application_not_installed')
    deadline = clock() + timeout
    request = None
    started = False
    while True:
        apps = backend.running(target)
        if len(apps) > 1 or (apps and apps[0]['path'] != path):
            return error('application_identity_ambiguous', 'unknown' if started else 'not_started')
        if apps and apps[0]['ready']:
            return {'type': 'app_launch', 'target': target, 'pid': apps[0]['pid'],
                    'status': 'launched' if started else 'already_running',
                    'application_running': True, 'window_verified': False}
        if request is None and not apps:
            if not backend.session_ready():
                return error('gui_session_unavailable')
            request = backend.start(path)
            started = True
        if request is not None and request.get('error'):
            return error('launch_failed', 'unknown')
        if clock() >= deadline:
            # LaunchServices is asynchronous; a timeout must never cause a
            # second launch. The caller should observe before doing anything.
            return error('launch_timeout', 'unknown')
        backend.pump(min(.05, max(0, deadline - clock())))
