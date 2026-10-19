"""Verify an explicitly absent AX Window without treating read errors as absence."""


def _empty_window_list(reply):
    diagnostic = reply.get('window_listing_diagnostic')
    if not isinstance(diagnostic, dict):
        return False
    return (diagnostic.get('status') == 'ok'
            and type(diagnostic.get('version')) is int and diagnostic['version'] == 1
            and all(type(diagnostic.get(key)) is int and diagnostic[key] == 0
                    for key in ('ax_error', 'raw_count', 'role_window_count',
                                'role_transient_count', 'role_unavailable_count',
                                'filtered_count', 'badge_count')))


def reobserve_closed_window(observe, target, post, failed):
    """Only a pure Window-absence assertion can use a windowless observation.

    The first read must have successfully enumerated zero windows in an
    unlocked console session. Read again through the driver to obtain a real
    observation ID; never invent one or repeat the action. Older drivers that
    cannot supply this evidence retain their original refusal.
    """
    role = getattr(post, 'absent_role', None)
    if (not isinstance(role, str) or role.lower() not in ('window', 'axwindow')
            or post.field is not None or post.equals is not None or post.contains is not None
            or post.absent is not None or post.attribute is not None
            or getattr(post, 'all_of', ())):
        return failed
    lock = failed.get('lock')
    if (failed.get('type') != 'error' or failed.get('error') != 'no_window_content'
            or failed.get('target') != target
            or failed.get('reason') not in ('no_visible_window', 'window_offscreen')
            or not isinstance(lock, dict) or lock.get('locked') is not False
            or lock.get('on_console') is not True or lock.get('login_done') is not True
            or not _empty_window_list(failed)):
        return failed
    fresh = observe(target, 'tree_full', require_window=False)
    if fresh.get('type') == 'error':
        return fresh
    if (fresh.get('type') != 'observation' or fresh.get('target') != target
            or not isinstance(fresh.get('obs_id'), str) or not fresh['obs_id']
            or type(fresh.get('element_count')) is not int or fresh['element_count'] != 0
            or fresh.get('truncated') is not False
            or not _empty_window_list(fresh) or not post.check(fresh.get('tree', ''))):
        return failed
    return {**fresh, 'verification_evidence': 'ax_window_list_empty'}
