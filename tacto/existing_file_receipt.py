"""Host receipt for saving an EXISTING plain-text document in place.

The fresh-file receipts cover "Save As" to a new name. A document that already
has a file was saved with File > Save and nothing could witness it: replay
reported commit_unverified ("no saved file") while the file on disk held the
text (measured 2026-10-09). This adapter witnesses that case, narrowly:

- the document is read through the backend's `document_text` (the signed
  helper's read-only witness), which names its file; only a `.txt` file —
  UTF-8 plain text, compared exactly, not RTF;
- before the Save: the text, the canonical path, and the file's identity and
  bytes are fixed;
- after it: the SAME document token still shows the same text, the path is
  unchanged, and the file — a regular file owned by this user, one link,
  within a byte budget — holds exactly that text.

`timeout_seconds` bounds the polling between reads; it cannot interrupt a
single backend call or file read in progress. Matching bytes returned after
the deadline are refused: this is a receipt-acceptance deadline, consistent
with FreshFileReceipt, not just a deadline for starting another poll. It does
not imply that the Save failed. Hosts with slow AX/large documents must choose
a suitable timeout; this adapter never silently extends that contract.
After Save, a temporary rename gap or unstable read
may be observed again within that budget. Each poll rechecks the same document
token and exact text. This never repeats the Save; initial preparation still
requires a stable file before any input is authorized.

What it certifies is the STATE after the Save: the named file holds the
document's text. It does not certify that this press wrote it. macOS autosaves
documents that already have a file, so the file usually held the text before
the press (measured 2026-10-09: written with no Save sent); requiring a change
refused every real case. Which writer wrote it is not attributed.

Filesystem readback cannot attribute a write to an application: another
writer with the same OS identity can race it. One prepare, one after-commit.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import hashlib
import math
import os
import stat
import time
import threading
import uuid

from .artifact_receipt import ArtifactReceiptError
from .commit_evidence import SavedFileStateEvidence, CommitEvidence, action_digest, _event_binding_matches

TARGET = 'com.apple.TextEdit'
TEXT_CAPABILITY = 'evidence.document_text'
FOCUSED_DOCUMENT_CAPABILITY = 'action.menu.focused_document'
MAX_BYTES = 4 * 1024 * 1024
SAVE_PATH = ["File", ["Save", "저장"]]


class ExistingFileReceiptUnavailable(ArtifactReceiptError):
    """The backend cannot witness this document's file."""


# What replay may show about a refusal: these codes and nothing else — never
# an exception's message, which could quote the document (Codex review of
# 31955f0).
PUBLIC_CODES = frozenset({
    'driver_dispatch_unverified',
    'file_unreadable', 'not_a_regular_owned_file', 'file_over_budget',
    'path_not_canonical_txt', 'no_text_witness', 'no_focused_document_guard',
    'text_unavailable', 'document_identity_changed', 'no_canonical_path',
    'different_document', 'already_prepared', 'not_a_bound_save',
    'event_malformed', 'already_used', 'commit_mismatch', 'commit_changed_during_prepare',
    'changed_during_save', 'file_does_not_hold_text',
    'file_changed_during_read',
    'file_readback_deadline',
})


class _TransientDocumentRead(ExistingFileReceiptUnavailable):
    """Retry only the same pinned document snapshot, never the Save."""


class _TransientFileRead(ArtifactReceiptError):
    """A fresh read may observe safe-save completion; never retry the Save."""


def _unreadable(error):
    # Only disappearance can be a rename/swap gap. Permission failures and
    # other I/O errors must not be converted into retryable observations.
    return _refuse('file_unreadable',
                   _TransientFileRead if error.errno == errno.ENOENT else ArtifactReceiptError)


def _nfc(path):
    import unicodedata
    return unicodedata.normalize('NFC', path)


def _refuse(code, cls=ArtifactReceiptError):
    assert code in PUBLIC_CODES, code
    error = cls(code)
    error.public_code = code
    return error


@dataclass(frozen=True)
class _Prepared:
    event_id: str
    action_digest: str
    token: str
    path: str
    text: str
    before: tuple          # (dev, ino, size, mtime_ns, sha256) of the file before (recorded)


def _file_fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _file_state(path):
    """(dev, ino, size, mtime_ns, sha256, bytes, full stat fingerprint)
    of a regular, owned, single-link
    file within budget — read without following a final symlink, and never
    opened unless lstat already says it is a regular file: a FIFO named .txt
    blocked the read (Codex review of 4d04b80). Opened non-blocking and
    checked again after opening and reading, so a swap or mutation during
    readback is refused. These checks do not prevent changes after return."""
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise _unreadable(exc) from None
    if not stat.S_ISREG(before.st_mode):
        raise _refuse('not_a_regular_owned_file')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise _unreadable(exc) from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode)
                or st.st_uid != os.getuid()
                or st.st_nlink != 1 or st.st_size > MAX_BYTES):
            raise _refuse('not_a_regular_owned_file')
        if (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
            raise _refuse('file_changed_during_read', _TransientFileRead)
        chunks, total = [], 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_BYTES:
                raise _refuse('file_over_budget')
            chunks.append(chunk)
        data = b''.join(chunks)
        # Reading an open descriptor can return the old file after the name
        # has been atomically replaced. A successful read alone says nothing
        # about what the approved path now holds. Check both the descriptor
        # and the named file, including ctime (mtime can be restored).
        after = os.fstat(fd)
        named = os.lstat(path)
        if (_file_fingerprint(before) != _file_fingerprint(st) or _file_fingerprint(st) != _file_fingerprint(after)
                or _file_fingerprint(after) != _file_fingerprint(named) or len(data) != after.st_size
                or _nfc(os.path.realpath(path)) != _nfc(path)):
            raise _refuse('file_changed_during_read', _TransientFileRead)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                hashlib.sha256(data).hexdigest(), data, _file_fingerprint(after))
    except OSError as exc:
        raise _unreadable(exc) from None
    finally:
        os.close(fd)


class ExistingTextFileReceipt:
    """Witness one in-place Save of the TextEdit document the backend reads."""

    def __init__(self, driver, *, document_path, target=TARGET, timeout_seconds=3.0,
                 poll_interval=.05, clock=None, sleep=None):
        if target != TARGET:
            raise ValueError('existing-file receipt requires com.apple.TextEdit')
        if (not isinstance(document_path, str) or not os.path.isabs(document_path)
                or '\0' in document_path
                or _nfc(os.path.realpath(document_path)) != _nfc(document_path)
                or not document_path.endswith('.txt')):
            raise _refuse('path_not_canonical_txt', ExistingFileReceiptUnavailable)
        self._path = document_path
        capabilities = getattr(driver, 'capabilities', ()) or ()
        if TEXT_CAPABILITY not in capabilities or not callable(getattr(driver, 'document_text', None)):
            raise _refuse('no_text_witness', ExistingFileReceiptUnavailable)
        if FOCUSED_DOCUMENT_CAPABILITY not in capabilities:
            # Without the helper-side file check the press could save another
            # document; no receipt is offered for that (Codex review of 31955f0).
            raise _refuse('no_focused_document_guard', ExistingFileReceiptUnavailable)
        for name, number in (('timeout_seconds', timeout_seconds), ('poll_interval', poll_interval)):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError(f'{name} must be finite and positive')
        self._driver = driver
        self._timeout = float(timeout_seconds)
        self._poll = float(poll_interval)
        from .receipt_clock import checked_receipt_clock
        self._clock = checked_receipt_clock(clock)
        self._sleep = sleep or time.sleep
        self._prepared = None
        self._prepare_attempted = False
        self._prepare_lock = threading.Lock()
        self._after_used = False

    # Fixed before execution: nothing; the text is only known at the Save.
    @property
    def commit_expected(self):
        return {'artifact_evidence_scope':'saved_state'}

    def _read(self, token=None):
        # First read selects the document by its path (several may be open);
        # later reads hold the token the backend issued for it.
        result = (self._driver.document_text(TARGET, document_token=token) if token is not None
                  else self._driver.document_text(TARGET, document_path=self._path))
        if (isinstance(result, dict) and result.get('type') == 'error'
                and result.get('error') == 'document_snapshot_unavailable'):
            raise _refuse('text_unavailable', _TransientDocumentRead)
        if (not isinstance(result, dict) or result.get('type') != 'document_text'
                or not isinstance(result.get('document_token'), str) or not result['document_token']
                or not isinstance(result.get('text'), str) or result.get('text_complete') is not True):
            raise _refuse('text_unavailable', ExistingFileReceiptUnavailable)
        if token is not None and result['document_token'] != token:
            raise _refuse('document_identity_changed')
        path = result.get('document_path')
        if (not isinstance(path, str) or not os.path.isabs(path) or '\0' in path
                or _nfc(os.path.realpath(path)) != _nfc(path)):
            raise _refuse('no_canonical_path', ExistingFileReceiptUnavailable)
        # TextEdit reports names decomposed (NFD); the observed / approved path
        # is composed. Same file, compared composed (2026-10-10, live).
        if _nfc(path) != _nfc(self._path):
            raise _refuse('different_document')
        return result['document_token'], self._path, result['text']

    def prepare_commit(self, event):
        with self._prepare_lock:
            if self._prepare_attempted:
                raise _refuse('already_prepared')
            self._prepare_attempted = True
        action, effects = event.get('action'), set(event.get('effects') or ())
        if (event.get('target') != TARGET or not isinstance(action, dict)
                or action.get('name') != 'menu'
                or set(action) != {'name', 'path', 'focused_document'}
                or action.get('path') != SAVE_PATH
                # the press must be bound, at the helper, to this very file
                or action.get('focused_document') != self._path
                or 'write_persistent' not in effects
                or effects - {'read', 'write_ephemeral', 'write_persistent'}):
            raise _refuse('not_a_bound_save')
        digest = action_digest(action, event['effects'], TARGET, obs_id=event.get('obs_id'))
        if event.get('action_digest') != digest or not isinstance(event.get('event_id'), str):
            raise _refuse('event_malformed')
        identifier = event['event_id']
        token, path, text = self._read()
        before = _file_state(path)[:5]
        if not _event_binding_matches(event, identifier, digest):
            raise _refuse('commit_changed_during_prepare')
        self._prepared = _Prepared(identifier, digest, token, path, text, before)
        return self._prepared

    def after_commit(self, event, driver_result, context):
        if self._after_used:
            raise _refuse('already_used')
        self._after_used = True
        prepared = self._prepared
        if (type(context) is not _Prepared or context is not prepared
                or event.get('event_id') != prepared.event_id
                or event.get('action_digest') != prepared.action_digest):
            raise _refuse('commit_mismatch')
        # As in replay/FreshFileReceipt, an error without effect is uncertain.
        # State readback may resolve it; it never attributes the writer.
        if (not isinstance(driver_result, dict)
                or driver_result.get('type') not in ('acted', 'error')
                or driver_result.get('effect') == 'not_started'):
            raise _refuse('driver_dispatch_unverified')
        expected = prepared.text.encode('utf-8')
        from .receipt_clock import receipt_deadline
        deadline = receipt_deadline(self._clock, self._timeout)
        def read_pinned():
            while True:
                if self._clock() >= deadline:
                    raise _refuse('text_unavailable')
                try:
                    return self._read(prepared.token)
                except _TransientDocumentRead:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise
                    self._sleep(min(self._poll, remaining))
        last_code = 'file_does_not_hold_text'
        while True:
            if self._clock() >= deadline:
                raise _refuse(last_code)
            token, path, text = read_pinned()
            if path != prepared.path or text != prepared.text:
                raise _refuse('changed_during_save')
            if self._clock() >= deadline:
                raise _refuse('file_readback_deadline')
            last_code = 'file_does_not_hold_text'
            try:
                state = _file_state(path)
                if state[5] == expected:
                    if self._clock() >= deadline:
                        raise _refuse('file_readback_deadline')
                    # Bracket the exact file read with the same document token
                    # and text, then confirm that the named file survived the
                    # final backend read. Neither reader is an atomic transaction.
                    _, current_path, current_text = read_pinned()
                    if current_path != prepared.path or current_text != prepared.text:
                        raise _refuse('changed_during_save')
                    if self._clock() >= deadline:
                        raise _refuse('file_readback_deadline')
                    try:
                        named=os.lstat(path)
                    except OSError as error:
                        raise _unreadable(error) from None
                    if (_file_fingerprint(named) != state[6]
                            or _nfc(os.path.realpath(path)) != _nfc(path)):
                        raise _refuse('file_changed_during_read', _TransientFileRead)
                    if self._clock() >= deadline:
                        raise _refuse('file_readback_deadline')
                    return CommitEvidence(prepared.event_id, prepared.action_digest,
                                          SavedFileStateEvidence(uuid.uuid4().hex, path,
                                                           hashlib.sha256(prepared.text.encode()).hexdigest()))
            except _TransientFileRead as exc:
                last_code = exc.public_code
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise _refuse(last_code)
            self._sleep(min(self._poll, remaining))


def make_existing_text_receipt(driver, **kwargs):
    return ExistingTextFileReceipt(driver, **kwargs)
