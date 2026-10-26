"""Small automatic access receipts, deliberately excluding screen/argument data."""
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from .preferences import load_preferences
import json
import os
import re
import stat
import sys
import time
import uuid

current = ContextVar('tacto_access_log', default=None)
COMMANDS = frozenset(('doctor', 'apps', 'launch', 'activate', 'open-url', 'keep-awake', 'first-run', 'observe', 'session', 'batch',
                      'document-attachments', 'capture', 'drag', 'vroid-drag', 'vroid-focus',
                      'run', 'replay', 'do', 'web-upload', 'auth'))
_NAMES = re.compile(r'^tacto-\d{8}T\d{12}Z-[0-9a-f]{32}\.jsonl$')
_CODE = re.compile(r'^[a-z][a-z0-9_]{0,80}$')
_BUNDLE = re.compile(r'^[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$')
_METHODS = frozenset(('credential_input','credential_submit','observe', 'act', 'ask', 'capture', 'list_targets', 'probe', 'selection',
                     'menu_state', 'menu_items', 'document_text', 'document_window_status',
                     'document_rtf_export', 'document_attachment_metadata', 'save_panel',
                     'readiness_read_cost', 'readiness_preflight'))
_ACTIONS = frozenset(('click', 'click_point', 'double_click', 'drag', 'vroid_drag', 'vroid_focus',
                     'window_input', 'prepare_window', 'set_value', 'type_text', 'scroll',
                     'secondary_action', 'menu', 'open_folder', 'reopen_window', 'close_menu',
                     'read', 'press_key', 'select_option', 'open_select', 'select_custom_option', 'upload'))
MAX_FILE = 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 200
MAX_AGE = 30 * 86400


def log_directory():
    if sys.platform == 'darwin':
        return Path.home()/'Library/Application Support/Tacto/Logs'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'Tacto/Logs'
    return Path.home()/'.local/state/tacto/logs'


def private_directory(path):
    """Only product-owned leaf directories; never follow a Logs/Tacto symlink."""
    path = Path(path)
    for folder in (path.parent, path):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or (hasattr(os, 'getuid') and info.st_uid != os.getuid()):
            raise OSError('unsafe_access_log_directory')
        if os.name != 'nt':
            folder.chmod(0o700)
    return path


def _files(root):
    found = []
    if not root.exists():
        return found
    if root.is_symlink() or root.parent.is_symlink():
        raise OSError('unsafe_access_log_directory')
    for p in root.iterdir():
        if _NAMES.fullmatch(p.name):
            info = p.lstat()
            if stat.S_ISREG(info.st_mode) and (not hasattr(os, 'getuid') or info.st_uid == os.getuid()):
                found.append((p, info))
    return sorted(found, key=lambda row: row[1].st_mtime, reverse=True)


def recent_logs(root=None, limit=20):
    root = Path(root) if root is not None else log_directory()
    return {'directory': str(root), 'files': [
        {'name': p.name, 'bytes': info.st_size, 'modified_at': info.st_mtime}
        for p, info in _files(root)[:limit]]}


class AccessLog:
    def __init__(self, command, *, root=None):
        self.fd = None
        self.path = None
        self.bytes = 0
        self.truncated = False
        self.finished = False
        self.started = time.monotonic()
        self.sequence = 0
        self.preferences = load_preferences((Path(root) if root is not None else log_directory()).parent)
        if not self.preferences['logging_enabled']:
            return
        try:
            root = private_directory(root or log_directory())
            total = count = 0
            for p, info in _files(root):
                total += info.st_size
                count += 1
                if count >= MAX_FILES or total > MAX_TOTAL - MAX_FILE or time.time() - info.st_mtime > self.preferences['retention_days'] * 86400:
                    p.unlink()
            name = 'tacto-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ-') + uuid.uuid4().hex + '.jsonl'
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0)
            self.fd = os.open(root/name, flags, 0o600)
            self.path = root/name
            self.event('command_start', command=command, pid=os.getpid())
        except OSError:
            self.close()
            print('tacto: 실행 기록을 저장할 수 없습니다 (작업은 계속합니다).', file=sys.stderr)

    def event(self, kind, *, force=False, **fields):
        if self.fd is None:
            return
        if self.bytes >= MAX_FILE and not force:
            if not self.truncated:
                self.truncated = True
                self.event('log_truncated', force=True)
            return
        self.sequence += 1
        row = dict(schema=1, sequence=self.sequence, event=kind,
                   at=datetime.now(timezone.utc).isoformat(),
                   elapsed_ms=round((time.monotonic() - self.started)*1000), **fields)
        data = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n').encode()
        try:
            view = memoryview(data)
            while view:
                size = os.write(self.fd, view)
                if size <= 0:
                    raise OSError('access_log_write_failed')
                view = view[size:]
            self.bytes += len(data)
        except OSError:
            self.close()
            print('tacto: 실행 기록 저장이 중단되었습니다 (작업은 계속합니다).', file=sys.stderr)

    def task_result(self, result):
        fields = {}
        target = result.get('target')
        if isinstance(target, str) and len(target) <= 255 and _BUNDLE.fullmatch(target):
            fields['target'] = target
        for key in ('status', 'stage', 'error', 'presentation_error'):
            value = result.get(key)
            if isinstance(value, str) and _CODE.fullmatch(value):
                fields[key] = value
        for key in ('model_calls', 'identity_reads', 'before_document_count', 'after_document_count',
                    'new_document_count', 'missing_existing_document_count'):
            value = result.get(key)
            if type(value) is int and 0 <= value < 1000000:
                fields[key] = value
        for key in ('foreground_verified', 'window_verified', 'presentation_skipped', 'save_command_sent', 'repeat_write'):
            if type(result.get(key)) is bool:
                fields[key] = result[key]
        self.event('task_result', **fields)

    def finish(self, exit_code):
        if not self.finished:
            self.finished = True
            self.event('command_end', force=True, exit_code=exit_code)

    def close(self):
        if self.fd is not None:
            fd = self.fd
            self.fd = None
            try:
                os.close(fd)
            except OSError:
                pass


def record_failure(error, stage=None):
    """Name why a run failed, so the history shows a reason and not only an exit code.

    Only closed codes reach the receipt; free text (selectors, typed values,
    detail strings) never does.
    """
    log = current.get()
    if log is not None:
        if not (isinstance(error, str) and _CODE.fullmatch(error)):
            error = 'unknown_failure'
        log.task_result({'status': 'failed', 'error': error, 'stage': stage})


class AccessLoggingDriver:
    """Transparent transport wrapper. No argument bodies or returned UI text."""
    def __init__(self, inner, log):
        self._inner, self._log = inner, log

    def __getattr__(self, name):
        attribute = getattr(self._inner, name)
        if name not in _METHODS or not callable(attribute):
            return attribute
        def call(*args, **kwargs):
            fields = {'operation': name}
            target = (kwargs.get('target') or (args[1] if name in ('act','credential_input','credential_submit') and len(args) > 1 else
                      args[0] if args and name != 'ask' else None))
            if isinstance(target, str) and len(target) <= 255 and _BUNDLE.fullmatch(target):
                fields['target'] = target
            if name == 'act':
                action = kwargs.get('action') or (args[2] if len(args) > 2 else None)
                if isinstance(action, dict) and isinstance(action.get('name'), str) and action['name'] in _ACTIONS:
                    fields['action'] = action['name']
            # Start survives a worker crash; an absent end means unknown,
            # never evidence of success or permission to resend input.
            self._log.event('access_start', **fields)
            try:
                result = attribute(*args, **kwargs)
            except BaseException:
                self._log.event('access_end', **fields, outcome='exception')
                raise
            result_fields = {}
            if name != 'ask' and isinstance(result, dict):
                for key in ('type', 'status', 'error', 'effect'):
                    value = result.get(key)
                    if isinstance(value, str) and _CODE.fullmatch(value):
                        result_fields[key] = value
            self._log.event('access_end', **fields, **result_fields)
            return result
        return call
