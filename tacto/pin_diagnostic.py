"""Closed pin metadata only; CLI output is not an authority or delivery receipt."""
import json
from .document_scope import PIN_REASONS

MARKER = 'document-pin-diagnostic: '


def pin_diagnostic(value):
    if (type(value) is not dict or set(value) != {'schema','phase','reason','effect'}
            or value['schema'] != 'document-pin-v1' or value['phase'] != 'pin'
            or value['effect'] != 'not_started' or type(value['reason']) is not str
            or value['reason'] not in PIN_REASONS):
        return None
    return dict(value)


def format_pin_diagnostic(reason):
    value = pin_diagnostic({'schema':'document-pin-v1','phase':'pin',
                            'reason':reason,'effect':'not_started'})
    return MARKER + json.dumps(value,ensure_ascii=True,separators=(',',':')) if value else None


def diagnostic_lines(output):
    if type(output) is not str or len(output) > 1024*1024:
        return []
    result=[]
    for line in output.splitlines():
        if line.startswith(MARKER) and len(line)<=1024:
            try:value=pin_diagnostic(json.loads(line[len(MARKER):]))
            except (ValueError,RecursionError):value=None
            if value is not None:
                result.append(value)
                if len(result)==64:break
    return result
