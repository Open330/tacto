"""Pure validation for explicitly host-authorized opaque window input.

These payloads declare no semantics or receiving success. No shortcuts,
command/control characters, implicit Enter, or unbounded Unicode input.
"""
import unicodedata

WINDOW_INPUT_CAPABILITIES = frozenset({
    'action.window_input', 'window_input.window_bound', 'window_input.host_authorization'})
WINDOW_INPUT_KEYS = frozenset({'Tab', 'shift+Tab', 'Up', 'Down', 'Left', 'Right'})
MAX_TEXT_UTF16 = 64


def valid_window_text(text):
    if type(text) is not str or not text:
        return False
    try:
        if len(text.encode('utf-16-le')) // 2 > MAX_TEXT_UTF16:
            return False
    except UnicodeEncodeError:
        return False
    return not any(unicodedata.category(char) in {'Cc', 'Cs'}
                   or ord(char) in (0x2028, 0x2029)
                   or 0xF700 <= ord(char) <= 0xF8FF for char in text)


def window_input_problem(action):
    if not isinstance(action, dict) or action.get('name') != 'window_input':
        return 'window_input requires an exact bounded payload'
    field = 'key' if 'key' in action else 'text'
    if set(action) != {'name', 'element_index', field}:
        return 'window_input requires exactly one key or text payload'
    if type(action.get('element_index')) is not int or action['element_index'] < 0:
        return 'window_input requires an observed Window index'
    if field == 'key':
        if type(action['key']) is not str or action['key'] not in WINDOW_INPUT_KEYS:
            return 'window_input permits only Tab, shift+Tab and arrows'
    elif not valid_window_text(action.get('text')):
        return 'window_input text requires 1–64 UTF-16 units without controls or navigation characters'
    return None
