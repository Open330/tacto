"""Decode an escaped file identity without treating filename text as UI flags."""
import json
import re
import unicodedata

_FLAGS=r'\s+\[(?:disabled|focused|selected)\]'
_ACTION=r'(?:ShowMenu|Increment|Decrement|Confirm|Cancel)'
_TAIL=re.compile(r'(?:(?:'+_FLAGS+r')|(?:,\s*Secondary Actions:\s*'+_ACTION+r'(?:,\s*'+_ACTION+r')*))*\s*')
_KEYS=r'Title|Description|Value|Help|Identifier|Placeholder|Subrole|Document|Secondary Actions'


def quote_value(value):
    encoded=json.dumps(value,ensure_ascii=False)
    for char in ('\x85','\u2028','\u2029'):encoded=encoded.replace(char,'\\u'+format(ord(char),'04x'))
    return encoded


def _parse_window(line,role_name='Window',keys=_KEYS):
    """Every window field is quoted, including titles that may spoof a key."""
    role=re.match(r'^\s*\d+\s+(?:AX)?'+role_name+r'(?:\s+|$)',line)
    if not role:return None
    value=line[role.end():];decoder=json.JSONDecoder();fields={}
    while value:
        if _TAIL.fullmatch(value):return fields,value
        marker=re.match(r'(?:'+keys+r'):\s*',value)
        if not marker:return None
        key=marker.group().split(':',1)[0]
        if key in fields:return None
        value=value[marker.end():]
        if not value.startswith('"'):return None
        try:decoded,end=decoder.raw_decode(value)
        except ValueError:return None
        if not isinstance(decoded,str):return None
        fields[key]=decoded;value=value[end:]
        if _TAIL.fullmatch(value):return fields,value
        if not value.startswith(', '):return None
        value=value[2:]
    return fields,''


def window_attributes(line):
    parsed=_parse_window(line)
    return parsed[0] if parsed is not None else None


def web_area_attributes(line):
    """Fields of a quoted WebArea line, URL included (its page sets the title)."""
    parsed=_parse_window(line,'WebArea',_KEYS+'|URL')
    return parsed[0] if parsed is not None else None


def window_flag(line, flag):
    """Read status outside quoted values; retain legacy human tree support."""
    parsed=_parse_window(line)
    if parsed is not None:return f"[{flag}]" in parsed[1]
    if re.match(r'\s*\d+\s+(?:AX)?Window\s+(?:'+_KEYS+r'):\s*"',line):return False
    return f"[{flag}]" in line


def document_attribute(line):
    fields=window_attributes(line)
    if fields is None:return None
    path=fields.get('Document')
    if not isinstance(path,str) or not path.startswith('/') or any(ord(c)<32 or ord(c)==127 or unicodedata.category(c)=='Cs' for c in path):return None
    return unicodedata.normalize('NFC',path)
