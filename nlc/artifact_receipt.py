"""Single-use host receipts for fresh UTF-8 files in private directories.

The host owns the expected path/content and is responsible for exclusive use of
the directory by the intended producer. Filesystem readback cannot attribute a
write to an application: another writer with the same OS identity can still
race it. This adapter neither creates files nor grants execution permission.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import stat
import threading
import time
import uuid

from .commit_evidence import ArtifactEvidence, CommitEvidence, action_digest


class ArtifactReceiptError(ValueError):
    """The evidence boundary cannot be established; do not retry the action."""


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    action_digest: str
    directory_identity: tuple
    nonce: str


class FreshFileReceipt:
    """Observe one new file in an existing, canonical, owner-only directory.

    ``directory`` must already exist, be owned by this user, have mode 0700,
    and have no symlink components. Pass ``Path(...).resolve()`` when choosing
    the host directory. ``filename`` is one path component. The format is exact
    UTF-8 bytes, not RTF or platform-specific text decoding.

    ``timeout_seconds`` bounds only after-dispatch polling. It does not cancel
    the producer or an operating-system read. A zero budget never issues a
    receipt. Each adapter permits one prepare and one after-commit attempt.
    """

    def __init__(self, directory, filename: str, text: str, *,
                 timeout_seconds: float = 2.0, poll_interval: float = 0.02,
                 clock=None, sleep=None):
        path = os.fspath(directory)
        if not isinstance(path, str) or not os.path.isabs(path) or '\0' in path:
            raise ValueError('directory must be an absolute path')
        if (not isinstance(filename, str) or not filename or filename in ('.', '..')
                or '/' in filename or '\\' in filename or '\0' in filename):
            raise ValueError('filename must be one path component')
        if not isinstance(text, str):
            raise TypeError('expected content must be text')
        for name, number, positive in (('timeout_seconds', timeout_seconds, False),
                                       ('poll_interval', poll_interval, True)):
            if (isinstance(number, bool) or not isinstance(number, (float, int))
                    or not math.isfinite(number) or number < 0 or (positive and number == 0)):
                raise ValueError(f'{name} has an invalid duration')
        if not all(hasattr(os, name) for name in ('O_NOFOLLOW', 'O_DIRECTORY', 'getuid')):
            raise RuntimeError('fresh-file receipts require POSIX no-follow directory descriptors')
        self._directory = os.path.normpath(path)
        self._filename = filename
        self._text = text
        self._bytes = text.encode('utf-8')
        self._timeout = timeout_seconds
        self._interval = poll_interval
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._lock = threading.Lock()
        self._prepared = None
        self._finished = False

    @property
    def commit_expected(self):
        """A fresh copy: callers cannot rewrite this adapter's expectations."""
        return {'저장 위치': self._directory, '저장한 이름': self._filename,
                '다시 읽은 내용': self._text}

    @staticmethod
    def _directory_id(info):
        return info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode)

    @staticmethod
    def _file_id(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def _check_directory(self, descriptor, expected=None):
        if os.path.realpath(self._directory) != self._directory:
            raise ArtifactReceiptError('directory contains a symlink')
        named = os.lstat(self._directory)
        opened = os.fstat(descriptor)
        if (not stat.S_ISDIR(named.st_mode) or not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o700
                or self._directory_id(named) != self._directory_id(opened)):
            raise ArtifactReceiptError('directory is not stable, private, and owned by this user')
        identity = self._directory_id(opened)
        if expected is not None and identity != expected:
            raise ArtifactReceiptError('directory identity changed after preparation')
        return identity

    def _open_directory(self):
        return os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    @staticmethod
    def _event(event):
        try:
            identifier = event['event_id']
            if 'obs_id' in event and (not isinstance(event['obs_id'], str)
                                      or not event['obs_id'].strip()):
                raise ValueError('invalid observation identity')
            digest = action_digest(event['action'], event['effects'], event['target'],
                                   obs_id=event.get('obs_id'))
            effects = set(event['effects'])
            if (not isinstance(identifier, str) or not identifier or digest != event['action_digest']
                    or 'write_persistent' not in effects or effects - {'write_persistent', 'read', 'write_ephemeral'}):
                raise ValueError('wrong event')
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactReceiptError('event is not a bound file-write operation') from exc
        return identifier, digest

    def prepare_commit(self, event):
        identifier, digest = self._event(event)
        with self._lock:
            if self._prepared is not None:
                raise ArtifactReceiptError('adapter already prepared a commit')
            descriptor = self._open_directory()
            try:
                identity = self._check_directory(descriptor)
                try:
                    os.stat(self._filename, dir_fd=descriptor, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ArtifactReceiptError('expected destination already exists')
                self._check_directory(descriptor, identity)
            finally:
                os.close(descriptor)
            self._prepared = _Prepared(identifier, digest, identity, uuid.uuid4().hex)
            return self._prepared

    def _read_file(self, directory_descriptor):
        descriptor = os.open(self._filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_descriptor)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.getuid():
                raise ArtifactReceiptError('artifact must be an owned regular file with a single link')
            if before.st_size != len(self._bytes):
                raise ArtifactReceiptError('artifact content length does not match')
            chunks = []
            remaining = len(self._bytes) + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            try:
                named = os.stat(self._filename, dir_fd=directory_descriptor, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ArtifactReceiptError('artifact disappeared during readback') from exc
            if self._file_id(before) != self._file_id(after) or self._file_id(after) != self._file_id(named):
                raise ArtifactReceiptError('artifact changed during readback')
            content = b''.join(chunks)
            if content != self._bytes:
                raise ArtifactReceiptError('artifact bytes do not match expected UTF-8 content')
            return content, self._file_id(after)
        finally:
            os.close(descriptor)

    def _final_readback_matches(self, observed_identity, read_identity):
        """Strict by default; a specialized reader may request complete re-read.

        False means discard the entire readback and poll again, never accept
        the old bytes under a new identity. Deadline and one-shot rules remain
        owned by after_commit.
        """
        if observed_identity != read_identity:
            raise ArtifactReceiptError('artifact changed after readback')
        return True

    def after_commit(self, event, driver_result, context):
        identifier, digest = self._event(event)
        with self._lock:
            if (self._finished or context is not self._prepared or context is None
                    or (identifier, digest) != (context.event_id, context.action_digest)):
                raise ArtifactReceiptError('commit context is missing, stale, or already consumed')
            self._finished = True
        if (not isinstance(driver_result, dict) or driver_result.get('type') not in ('acted', 'error')
                or driver_result.get('effect') == 'not_started'):
            raise ArtifactReceiptError('driver did not report dispatch')
        deadline = self._clock() + self._timeout
        while self._clock() < deadline:
            descriptor = self._open_directory()
            try:
                self._check_directory(descriptor, context.directory_identity)
                try:
                    readback = self._read_file(descriptor)
                except FileNotFoundError:
                    readback = None
                self._check_directory(descriptor, context.directory_identity)
                if readback is not None:
                    content, identity = readback
                    named = os.stat(self._filename, dir_fd=descriptor, follow_symlinks=False)
                    if self._final_readback_matches(self._file_id(named), identity):
                        if self._clock() >= deadline:
                            return None
                        return CommitEvidence(identifier, digest,
                            ArtifactEvidence('fresh-file-' + context.nonce,
                                str(Path(self._directory) / self._filename), hashlib.sha256(content).hexdigest()))
            finally:
                os.close(descriptor)
            remaining = deadline - self._clock()
            if remaining > 0:
                self._sleep(min(self._interval, remaining))
        return None
