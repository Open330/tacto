"""Read-only polling classification; never authorizes a recovery or input."""


def retryable_observation_error(reply):
    """A temporarily windowless app may settle within the original deadline.

    Unknown errors/reasons, lock, hidden/minimized windows and identity failures
    need a stop or explicit host action, rather than another automatic input.
    """
    from .window_listing_diagnostic import window_listing_diagnostic
    if isinstance(reply,dict):
        lock=reply.get('lock')
        if lock is not None and (type(lock) is not dict or lock.get('locked') is not False):
            return False
        if reply.get('reason') in ('screen_locked','app_hidden','minimized'):
            return False
    if isinstance(reply,dict) and reply.get('type')=='error' and reply.get('error')=='window_list_unavailable':
        diagnostic=window_listing_diagnostic(reply.get('window_listing_diagnostic'))
        return bool(diagnostic and (
            (diagnostic['status']=='ax_error' and diagnostic['ax_error']==-25204)
            or (diagnostic['status']=='role_unavailable'
                and diagnostic['role_transient_count']==diagnostic['role_unavailable_count'])))
    if (not isinstance(reply, dict) or reply.get('type') != 'error'
            or reply.get('error') != 'no_window_content'
            or reply.get('reason') not in (None, 'no_visible_window', 'window_offscreen')):
        return False
    lock = reply.get('lock')
    return lock is None or (isinstance(lock, dict) and lock.get('locked') is False)
