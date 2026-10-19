"""Closed, content-free window-list diagnostics; never input authorization."""


def window_listing_diagnostic(value):
    keys={'version','status','ax_error','raw_count','role_window_count',
          'role_unavailable_count','role_transient_count','filtered_count','badge_count'}
    statuses={'ok','ax_error','no_value','malformed_array','role_unavailable'}
    if type(value) is not dict or set(value)!=keys:
        return None
    if type(value['version']) is not int or value['version']!=1:
        return None
    if type(value['status']) is not str or value['status'] not in statuses:
        return None
    if type(value['ax_error']) is not int or not -65536<=value['ax_error']<=65536:
        return None
    counts=keys-{'version','status','ax_error'}
    if any(type(value[key]) is not int or not 0<=value[key]<=2147483647 for key in counts):
        return None
    if (value['role_window_count']+value['role_unavailable_count']>value['raw_count']
            or value['filtered_count']+value['badge_count']!=value['role_window_count']
            or value['role_transient_count']>value['role_unavailable_count']):
        return None
    if value['status']=='ax_error':
        if value['ax_error']==0 or any(value[key] for key in counts):return None
    elif value['status']=='no_value':
        if value['ax_error'] not in (0,-25212):return None
    elif value['ax_error']!=0:
        return None
    if value['status']=='role_unavailable':
        if value['role_unavailable_count']==0:return None
    elif value['role_unavailable_count']!=0:
        return None
    if value['status']=='no_value' and any(value[key] for key in counts):return None
    if value['status']=='malformed_array' and any(value[key] for key in counts-{'raw_count'}):return None
    return dict(value)
