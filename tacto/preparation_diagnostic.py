"""Closed, non-authorizing metadata from a window preparation response."""

MARKER = 'preparation-diagnostic: '
PHASES = frozenset({'identity', 'context', 'binding', 'dispatch', 'foreground_write', 'revalidate'})
REASONS = frozenset({'readiness_deadline', 'readiness_nodes', 'observation_deadline',
                    'ax_timeout_unavailable', 'ax_timeout_reset_failed', 'titlebar_point_unavailable',
                    'modal_or_unverifiable', 'identity_changed', 'dispatched_unverified',
                    'unclassified_refusal', 'foreground_refusal'})


def preparation_diagnostic(value):
    if not isinstance(value, dict):
        return None
    required = {'phase', 'reason', 'elapsed_ms'}
    if not required <= value.keys() or value.keys() - required - {'scoped_ax_operations'}:
        return None
    if (type(value['phase']) is not str or value['phase'] not in PHASES
            or type(value['reason']) is not str or value['reason'] not in REASONS
            or type(value['elapsed_ms']) is not int or not 0 <= value['elapsed_ms'] <= 600000):
        return None
    count = value.get('scoped_ax_operations')
    if 'scoped_ax_operations' in value and (type(count) is not int or not 0 <= count <= 1000000):
        return None
    return dict(value)


def diagnostic_lines(output):
    """Extract bounded marker lines only, never arbitrary exception/CLI text."""
    import json
    if not isinstance(output, str) or len(output) > 1024 * 1024:
        return []
    result = []
    for line in output.splitlines():
        if line.startswith(MARKER) and len(line) <= 512:
            try:
                detail = preparation_diagnostic(json.loads(line[len(MARKER):]))
            except (ValueError, RecursionError):
                detail = None
            if detail is not None:
                result.append(detail)
                if len(result) == 64:
                    break
    return result
