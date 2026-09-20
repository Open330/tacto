"""Read-only modal guard for explicit native window preparation.

An unsupported AXSheets attribute is not evidence of absence. In that case a
complete bounded children traversal must establish that no AXSheet is exposed.
This observes AX state; it is not an atomic lock on an application's UI.
"""

class WindowNotReady(RuntimeError):
    pass


def require_window_ready(window, windows, read, equal, *, array_types=(list, tuple),
                         max_nodes=5000, max_depth=64):
    if type(max_nodes) is not int or max_nodes < 1 or type(max_depth) is not int or max_depth < 0:
        raise ValueError('invalid window-readiness budget')

    def value(node, name, optional=False):
        error, result = read(node, name)
        if error == 0 and result is not None:
            return result
        if optional and error in (-25205, -25212):
            return None
        raise WindowNotReady('window state unavailable: ' + name)

    before = windows()
    if not isinstance(before, array_types) or sum(bool(equal(w, window)) for w in before) != 1:
        raise WindowNotReady('target window absent or ambiguous')
    before = list(before)
    if value(window, 'AXMinimized') is not False:
        raise WindowNotReady('target window minimized or unknown')
    remaining = max_nodes
    for top in before:
        if value(top, 'AXModal') is not False:
            raise WindowNotReady('application has a modal or unknown window')
        sheets = value(top, 'AXSheets', optional=True)
        if sheets is not None:
            if not isinstance(sheets, array_types) or len(sheets):
                raise WindowNotReady('application has a sheet or malformed sheet list')
        # Even supported AXSheets may be stale/incomplete. Read the exposed
        # children without the ordinary observation's per-container truncation.
        stack = [(top, 0)]
        while stack:
            node, depth = stack.pop()
            remaining -= 1
            if remaining < 0 or depth > max_depth:
                raise WindowNotReady('window readiness traversal budget exceeded')
            role = value(node, 'AXRole')
            if not isinstance(role, str) or not role:
                raise WindowNotReady('window role malformed')
            if role == 'AXSheet':
                raise WindowNotReady('application has an attached sheet')
            children = value(node, 'AXChildren', optional=True)
            if children is None:
                continue
            if not isinstance(children, array_types):
                raise WindowNotReady('window children malformed')
            if len(children) > remaining:
                raise WindowNotReady('window readiness traversal budget exceeded')
            stack.extend((child, depth + 1) for child in reversed(children))
    after = windows()
    if not isinstance(after, array_types) or len(after) != len(before) or any(
            not equal(a, b) for a, b in zip(before, after)):
        raise WindowNotReady('application windows changed during validation')
    if value(window, 'AXMinimized') is not False or any(value(w, 'AXModal') is not False for w in before):
        raise WindowNotReady('window became minimized or modal during validation')
    return True
