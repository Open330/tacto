"""Opt-in private focus diagnostics, never input authority or text capture."""
import json
import os
from pathlib import Path
import stat
import time

from tacto.input_audit import MacInputReader, NO_FOCUS

# Unknown/custom role strings are omitted, not copied into diagnostic artifacts.
ROLES = frozenset(('AXApplication AXWindow AXSheet AXMenu AXMenuItem AXMenuBar '
    'AXMenuBarItem AXButton AXTextField AXTextArea AXStaticText AXWebArea AXGroup '
    'AXScrollArea AXComboBox AXPopUpButton AXCheckBox AXRadioButton AXTabGroup '
    'AXList AXRow AXCell AXOutline AXImage AXLink AXSlider AXToolbar AXSplitGroup '
    'AXUnknown').split())


class FocusDiagnosticReader(MacInputReader):
    def __init__(self, output, *, max_records=10000):
        super().__init__(strategy='application_frontmost')
        if type(max_records) is not int or not 1 <= max_records <= 20000:
            raise ValueError('invalid_diagnostic_budget')
        path = Path(output)
        info = path.parent.lstat()
        if (not path.is_absolute() or path.parent.resolve() != path.parent
                or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise ValueError('private_diagnostic_parent_required')
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            self.identity = (info.st_dev, info.st_ino)
        finally:
            os.close(fd)
        self.output = str(path)
        self.max_records = max_records
        self.records = 0

    def descriptor(self, snapshot):
        row = {'time': time.monotonic(), 'foreground_pid': snapshot.frontmost_pid,
               'focus_identity': snapshot.focus_identity, 'owner_pid': None,
               'role': None, 'focus_recheck_matches': None, 'status': 'unavailable'}
        if snapshot.focus_identity == NO_FOCUS:
            row['status'] = 'no_focus'
            return row
        element = next((element for pid, element, token in self._focuses
            if pid == snapshot.frontmost_pid and token == snapshot.focus_identity), None)
        if element is None:
            return row
        try:
            _, _, ax, cf = self._load()
            error, pid = ax.AXUIElementGetPid(element, None)
            if error == 0 and type(pid) is int and pid > 0:
                row['owner_pid'] = pid
            error, role = ax.AXUIElementCopyAttributeValue(element, 'AXRole', None)
            if error == 0 and isinstance(role, str) and role in ROLES:
                row['role'] = role
            application = ax.AXUIElementCreateApplication(snapshot.frontmost_pid)
            error, current = ax.AXUIElementCopyAttributeValue(application, 'AXFocusedUIElement', None)
            if error == 0 and current is not None:
                row['focus_recheck_matches'] = bool(cf.CFEqual(current, element))
            row['status'] = 'complete' if (row['owner_pid'] is not None
                and row['role'] is not None and row['focus_recheck_matches'] is True) else 'unavailable'
        except Exception:
            # No exception messages, labels, values or titles cross this path.
            pass
        return row

    def __call__(self):
        if self.records >= self.max_records:
            raise ValueError('focus_diagnostic_budget_exceeded')
        snapshot = super().__call__()
        data = (json.dumps(self.descriptor(snapshot), allow_nan=False) + '\n').encode()
        fd = os.open(self.output, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != self.identity):
                raise ValueError('focus_diagnostic_identity_changed')
            with os.fdopen(fd, 'wb', closefd=False) as stream:
                stream.write(data); stream.flush()
        finally:
            os.close(fd)
        self.records += 1
        return snapshot
