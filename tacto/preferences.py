"""Shared GUI/runtime preferences. Reading settings never creates files."""
import json
import os
from pathlib import Path
import stat
import sys

DEFAULTS = {'schema': 1, 'logging_enabled': True, 'retention_days': 30,
            'show_control_banner': True, 'keep_helper_warm': False, 'helper_idle_minutes': 2}


def product_directory():
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/Tacto'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'Tacto'
    return Path.home()/'.local/state/tacto'


def load_preferences(root=None):
    root = Path(root) if root is not None else product_directory()
    result = dict(DEFAULTS)
    try:
        if root.is_symlink():
            return result
        fd = os.open(root/'preferences.json', os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_size > 16384
                    or hasattr(os, 'getuid') and info.st_uid != os.getuid()):
                return result
            value = json.loads(stream.read(16385))
        if not isinstance(value, dict) or type(value.get('schema', 1)) is not int or value.get('schema', 1) != 1:
            return result
        for key in ('logging_enabled', 'show_control_banner', 'keep_helper_warm'):
            if type(value.get(key)) is bool:
                result[key] = value[key]
        if type(value.get('helper_idle_minutes')) is int and 1 <= value['helper_idle_minutes'] <= 10:
            result['helper_idle_minutes'] = value['helper_idle_minutes']
        if type(value.get('retention_days')) is int and value['retention_days'] in (7, 30, 90):
            result['retention_days'] = value['retention_days']
    except (OSError, ValueError, UnicodeError):
        pass
    return result
