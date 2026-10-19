"""Bounded read-only modality evidence; unreadable is never clear."""
SUCCESS = 0
UNSUPPORTED = -25205
NO_VALUE = -25212


def modal_presence(app, read, *, is_element, same_element=lambda a,b:a==b,
                   max_nodes=20000, max_depth=64):
    if (type(max_nodes) is not int or max_nodes <= 0
            or type(max_depth) is not int or max_depth < 0):
        return 'unobservable'
    try:
        def fetch(node,name):
            status,value = read(node,name)
            if type(status) is not int: raise ValueError('invalid AX status')
            return status,value
        status, roots = fetch(app,'AXWindows')
        if (status != SUCCESS or type(roots) is not list or len(roots)>max_nodes
                or not all(is_element(x) for x in roots)):
            return 'unobservable'
        remaining = max_nodes
        def inspect(node):
            nonlocal remaining
            if remaining<=0: raise ValueError('modal node budget')
            remaining -= 1
            status,role = fetch(node,'AXRole')
            if status != SUCCESS or type(role) is not str or not role:
                return 'unobservable'
            if role == 'AXSheet':
                return 'present'
            status,modal = fetch(node,'AXModal')
            if status == SUCCESS:
                if type(modal) is not bool: return 'unobservable'
                if modal: return 'present'
            elif status not in (UNSUPPORTED,NO_VALUE):
                return 'unobservable'
            status,sheets = fetch(node,'AXSheets')
            if status == SUCCESS:
                if type(sheets) is not list or len(sheets)>max_nodes or not all(is_element(x) for x in sheets):
                    return 'unobservable'
                if sheets: return 'present'
            elif status not in (UNSUPPORTED,NO_VALUE):
                return 'unobservable'
            return 'clear'
        if len(roots)>remaining: return 'unobservable'
        for window in roots:
            state=inspect(window)
            if state!='clear': return state
            status,children=fetch(window,'AXChildren')
            if (status!=SUCCESS or type(children) is not list or len(children)>remaining
                    or not all(is_element(x) for x in children)):
                return 'unobservable'
            for child in children:
                state=inspect(child)
                if state!='clear': return state
        status,focused=fetch(app,'AXFocusedUIElement')
        if status in (UNSUPPORTED,NO_VALUE): return 'clear'
        if status!=SUCCESS or not is_element(focused): return 'unobservable'
        for _ in range(max_depth+1):
            if same_element(focused,app): return 'clear'
            state=inspect(focused)
            if state!='clear': return state
            status,parent=fetch(focused,'AXParent')
            if status!=SUCCESS or not is_element(parent): return 'unobservable'
            focused=parent
        return 'unobservable'
    except Exception:
        return 'unobservable'
