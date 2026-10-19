"""Closed, non-authorizing metadata from a window preparation response."""

MARKER = 'preparation-diagnostic: '
PHASES = frozenset({'identity', 'context', 'binding', 'dispatch', 'foreground_write', 'revalidate'})
REASONS = frozenset({'readiness_deadline', 'readiness_nodes', 'readiness_batch_failed', 'readiness_batch_malformed', 'readiness_attribute_failed', 'observation_deadline',
                    'ax_timeout_unavailable', 'ax_timeout_reset_failed', 'titlebar_point_unavailable',
                    'modal_or_unverifiable', 'identity_changed', 'dispatched_unverified',
                    'unclassified_refusal', 'foreground_refusal', 'user_focus_unavailable',
                    'user_focus_unsupported', 'user_focus_malformed', 'user_focus_query_failed',
                    'user_foreground_changed', 'user_focus_deadline', 'validation_headroom',
                    'target_window_not_focused', 'target_input_changed', 'target_focus_outside_window',
                    'target_input_not_focused', 'target_row_not_in_focused_list'})


READINESS_FAILURE_FIELDS = {
    'stage': frozenset({'entry', 'windows', 'top', 'tree', 'recheck', 'complete'}),
    'attribute': frozenset({'AXRole', 'AXModal', 'AXChildren', 'AXSheets', 'AXWindows',
                            'AXMinimized', 'AXParent', 'batch', 'other'}),
    'source': frozenset({'scalar', 'batch_field', 'batch_call'}),
    'kind': frozenset({'ax_error', 'null_value', 'required_absent', 'numeric', 'malformed',
                       'empty', 'batch_failed', 'batch_malformed'}),
}


def readiness_failure(value):
    """The first unusable readiness read: closed codes and bounded numbers only."""
    if (type(value) is not dict or not set(READINESS_FAILURE_FIELDS) | {'depth', 'visit'} <= value.keys()
            or value.keys() - set(READINESS_FAILURE_FIELDS) - {'depth', 'visit', 'ax_error'}):
        return None
    if any(type(value[key]) is not str or value[key] not in allowed
           for key, allowed in READINESS_FAILURE_FIELDS.items()):
        return None
    if (type(value['depth']) is not int or not 0 <= value['depth'] <= 64
            or type(value['visit']) is not int or not 0 <= value['visit'] <= 20000):
        return None
    if 'ax_error' in value and (type(value['ax_error']) is not int
            or not -65536 <= value['ax_error'] <= 65536 or value['ax_error'] == 0):
        return None
    return dict(value)


IDENTITY_CAUSES = frozenset({'initial_binding','observation_id','process_id','target_name','process_start',
                             'element_map','document_token','document_pin','document_live','target_resolution'})

def preparation_diagnostic(value):
    if not isinstance(value, dict):
        return None
    required = {'phase', 'reason', 'elapsed_ms'}
    if not required <= value.keys() or value.keys() - required - {'scoped_ax_operations','context_elapsed_ms','ax_operation_elapsed_ms','full_context_checks','incremental_context_checks','readiness_ax_error','readiness_failure','readiness_progress','headroom','identity_cause'}:
        return None
    if (type(value['phase']) is not str or value['phase'] not in PHASES
            or type(value['reason']) is not str or value['reason'] not in REASONS
            or type(value['elapsed_ms']) is not int or not 0 <= value['elapsed_ms'] <= 600000):
        return None
    if 'identity_cause' in value and (value['reason'] != 'identity_changed'
            or type(value['identity_cause']) is not str or value['identity_cause'] not in IDENTITY_CAUSES):
        return None
    for key in ('scoped_ax_operations','full_context_checks','incremental_context_checks'):
        if key in value and (type(value[key]) is not int or not 0 <= value[key] <= 1000000):
            return None
    for key in ('context_elapsed_ms','ax_operation_elapsed_ms'):
        if key in value and (type(value[key]) is not int or not 0<=value[key]<=600000):
            return None
    if 'readiness_ax_error' in value and (type(value['readiness_ax_error']) is not int
            or not -65536<=value['readiness_ax_error']<=65536 or value['readiness_ax_error']==0):
        return None
    if 'readiness_failure' in value and readiness_failure(value['readiness_failure']) is None:
        return None
    if 'readiness_progress' in value:
        progress = value['readiness_progress']
        if (type(progress) is not dict or set(progress) !=
                {'pass','windows','completed','index','target','nodes','total_nodes','stage'}):
            return None
        for key in ('pass','windows','completed','nodes','total_nodes'):
            if type(progress[key]) is not int or not 0 <= progress[key] <= 1000000:
                return None
        if (progress['pass'] < 1 or type(progress['index']) is not int
                or not -1 <= progress['index'] < progress['windows']
                or progress['completed'] > progress['windows']
                or progress['nodes'] > progress['total_nodes']
                or type(progress['target']) is not bool
                or type(progress['stage']) is not str
                or progress['stage'] not in ('entry','windows','top','tree','recheck','complete')):
            return None
    if 'headroom' in value:
        headroom=value['headroom']
        durations={'required_ms','remaining_ms','validation_ms','routing_ms','input_ms','context_ms'}
        if (type(headroom) is not dict or set(headroom)!=durations|{'event_count','saturated'}
                or any(type(headroom[key]) is not int or not 0<=headroom[key]<=600000 for key in durations)
                or type(headroom['event_count']) is not int or not 1<=headroom['event_count']<=10000
                or type(headroom['saturated']) is not bool):
            return None
    result = dict(value)
    if 'readiness_progress' in value:
        result['readiness_progress'] = dict(value['readiness_progress'])
    if 'headroom' in value:result['headroom']=dict(value['headroom'])
    if 'readiness_failure' in value:result['readiness_failure']=dict(value['readiness_failure'])
    return result


def diagnostic_lines(output):
    """Extract bounded marker lines only, never arbitrary exception/CLI text."""
    import json
    if not isinstance(output, str) or len(output) > 1024 * 1024:
        return []
    result = []
    for line in output.splitlines():
        if line.startswith(MARKER) and len(line) <= 1024:
            try:
                detail = preparation_diagnostic(json.loads(line[len(MARKER):]))
            except (ValueError, RecursionError):
                detail = None
            if detail is not None:
                result.append(detail)
                if len(result) == 64:
                    break
    return result
