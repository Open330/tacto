"""Native/eval RTF save receipts with byte identity and exact decoded readback.

Unlike FreshFileReceipt this permits an owned directory such as Downloads that
is readable by others but not writable by group/others. It is not producer
attestation: another process running under the same UID can race the save.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import stat
import subprocess
import threading

from .artifact_receipt import ArtifactReceiptError, FreshFileReceipt


class _ReadbackPending(FileNotFoundError):
    """The parent polls a not-yet-complete artifact without repeating the UI action."""


class FreshRTFReceipt(FreshFileReceipt):
    """One fresh .rtf file, exact predetermined text and document path.

    `document_path()` must return the currently saved document's absolute path,
    obtained independently from the target application (for TextEdit, AXDocument
    after decoding its file URL). It is never used to choose the expected path.
    """
    _MAX_RTF_BYTES = 2 * 1024 * 1024
    _MAX_TEXT_BYTES = 4 * 1024 * 1024

    def __init__(self, directory, filename: str, expected_text: str, *, document_path,
                 timeout_seconds: float = 3.0, poll_interval: float = 0.02,
                 clock=None, sleep=None):
        if (not isinstance(filename, str) or not filename.endswith('.rtf')
                or not filename[:-4] or filename[:-4] in ('.', '..')):
            raise ValueError('RTF filename must have a nonempty basename and end in .rtf')
        if not callable(document_path):
            raise TypeError('document_path must be an independent host callback')
        super().__init__(directory, filename, expected_text, timeout_seconds=timeout_seconds,
                         poll_interval=poll_interval, clock=clock, sleep=sleep)
        self._document_path = document_path
        self._after_lock = threading.Lock()
        self._decode_deadline = None
        descriptor = self._open_directory()
        try:
            self._initial_directory = self._check_directory(descriptor)
            try:
                os.stat(self._filename, dir_fd=descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ArtifactReceiptError('expected RTF destination already exists')
            self._check_directory(descriptor, self._initial_directory)
        finally:
            os.close(descriptor)

    @property
    def commit_expected(self):
        # ArtifactEvidence.sha256 describes raw RTF bytes. Its generic UTF-8
        # comparator cannot compare formatted-document contents. Exact text is
        # therefore checked by this host adapter, not put under that comparator.
        return {'저장 위치': self._directory, '저장한 이름': self._filename}

    def _check_directory(self, descriptor, expected=None):
        if os.path.realpath(self._directory) != self._directory:
            raise ArtifactReceiptError('directory contains a symlink')
        named = os.lstat(self._directory)
        opened = os.fstat(descriptor)
        if (not stat.S_ISDIR(named.st_mode) or not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) & 0o022
                or self._directory_id(named) != self._directory_id(opened)):
            raise ArtifactReceiptError('RTF directory must be stable, owned, and not writable by others')
        identity = self._directory_id(opened)
        expected = expected if expected is not None else getattr(self, '_initial_directory', None)
        if expected is not None and identity != expected:
            raise ArtifactReceiptError('directory identity changed after preparation')
        return identity

    def _decode(self, raw):
        remaining = self._decode_deadline - self._clock()
        if remaining <= 0:
            raise _ReadbackPending('readback deadline elapsed')
        if not raw.startswith(b'{\\rtf'):
            raise _ReadbackPending('artifact is not complete RTF')
        argv = ['/usr/bin/textutil', '-convert', 'txt', '-format', 'rtf',
                '-encoding', 'UTF-8', '-stdin', '-stdout']
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, start_new_session=True) as proc:
            try:
                output, _ = proc.communicate(input=raw, timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
                raise _ReadbackPending('RTF decoding exceeded deadline') from None
        if proc.returncode != 0 or len(output) > self._MAX_TEXT_BYTES:
            raise _ReadbackPending('RTF decoder did not return bounded text')
        try:
            return output.decode('utf-8')
        except UnicodeDecodeError:
            raise _ReadbackPending('RTF decoder returned invalid UTF-8') from None

    @staticmethod
    def _file_id(info):
        identity = FreshFileReceipt._file_id(info)
        # Group ownership also belongs to the stable identity. Keep ctime last
        # so the retry rule cannot mistake a group change for an xattr update.
        return identity[:-1] + (info.st_gid, identity[-1])

    @staticmethod
    def _require_stable(*identities):
        if all(identity == identities[0] for identity in identities[1:]):
            return
        if all(identity[:-1] == identities[0][:-1] for identity in identities[1:]):
            # ctime is not discarded. None of these bytes are evidence yet:
            # read, decode, application-path check and final stat must all run
            # again against one entirely stable identity within the deadline.
            raise _ReadbackPending('RTF metadata changed; complete readback required')
        raise ArtifactReceiptError('RTF content or security identity changed')

    def _final_readback_matches(self, observed_identity, read_identity):
        try:
            self._require_stable(read_identity, observed_identity)
        except _ReadbackPending:
            return False
        return True

    def _read_file(self, directory_descriptor):
        descriptor = os.open(self._filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_descriptor)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.getuid()):
                raise ArtifactReceiptError('RTF must be an owned regular file with one link')
            if before.st_size > self._MAX_RTF_BYTES:
                raise ArtifactReceiptError('RTF exceeds readback byte budget')
            chunks, remaining = [], self._MAX_RTF_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 65536))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            after = os.fstat(descriptor)
            named = os.stat(self._filename, dir_fd=directory_descriptor, follow_symlinks=False)
            self._require_stable(self._file_id(before), self._file_id(after), self._file_id(named))
            raw = b''.join(chunks)
            if len(raw) > self._MAX_RTF_BYTES:
                raise ArtifactReceiptError('RTF exceeds readback byte budget')
            if self._decode(raw) != self._text:
                raise _ReadbackPending('decoded RTF differs from predetermined text')
            expected_path = str(Path(self._directory) / self._filename)
            if self._document_path() != expected_path:
                raise _ReadbackPending('application document does not match predetermined path')
            if self._clock() >= self._decode_deadline:
                raise _ReadbackPending('readback completed too late')
            # Decode and AX lookup may yield to another writer. Recheck both
            # descriptor and name before returning evidence to the parent.
            final = os.fstat(descriptor)
            named = os.stat(self._filename, dir_fd=directory_descriptor, follow_symlinks=False)
            self._require_stable(self._file_id(after), self._file_id(final), self._file_id(named))
            return raw, self._file_id(final)
        finally:
            os.close(descriptor)

    def after_commit(self, event, driver_result, context):
        if not self._after_lock.acquire(blocking=False):
            raise ArtifactReceiptError('concurrent receipt use is not allowed')
        try:
            self._decode_deadline = self._clock() + self._timeout
            receipt = super().after_commit(event, driver_result, context)
            return receipt if self._clock() < self._decode_deadline else None
        finally:
            self._after_lock.release()
