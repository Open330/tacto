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
single backend call or file read in progress.

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
import hashlib
import math
import os
import stat
import time
import uuid

from .artifact_receipt import ArtifactReceiptError
from .commit_evidence import ArtifactEvidence, CommitEvidence, action_digest

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
    'file_unreadable', 'not_a_regular_owned_file', 'file_over_budget',
    'path_not_canonical_txt', 'no_text_witness', 'no_focused_document_guard',
    'text_unavailable', 'document_identity_changed', 'no_canonical_path',
    'different_document', 'already_prepared', 'not_a_bound_save',
    'event_malformed', 'already_used', 'commit_mismatch',
    'changed_during_save', 'file_does_not_hold_text',
})


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


def _file_state(path):
    """(dev, ino, size, mtime_ns, sha256, bytes) of a regular, owned, single-link
    file within budget — read without following a final symlink, and never
    opened unless lstat already says it is a regular file: a FIFO named .txt
    blocked the read (Codex review of 4d04b80). Opened non-blocking and
    checked again after opening, so a swap in between is refused."""
    try:
        before = os.lstat(path)
    except OSError as exc:
        raise _refuse('file_unreadable') from None
    if not stat.S_ISREG(before.st_mode):
        raise _refuse('not_a_regular_owned_file')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise _refuse('file_unreadable') from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino)
                or st.st_uid != os.getuid()
                or st.st_nlink != 1 or st.st_size > MAX_BYTES):
            raise _refuse('not_a_regular_owned_file')
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
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                hashlib.sha256(data).hexdigest(), data)
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
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._prepared = None
        self._after_used = False

    # Fixed before execution: nothing; the text is only known at the Save.
    commit_expected = {}

    def _read(self, token=None):
        # First read selects the document by its path (several may be open);
        # later reads hold the token the backend issued for it.
        result = (self._driver.document_text(TARGET, document_token=token) if token is not None
                  else self._driver.document_text(TARGET, document_path=self._path))
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
        if self._prepared is not None:
            raise _refuse('already_prepared')
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
        token, path, text = self._read()
        before = _file_state(path)[:5]
        self._prepared = _Prepared(event['event_id'], digest, token, path, text, before)
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
        expected = prepared.text.encode('utf-8')
        deadline = self._clock() + self._timeout
        while True:
            token, path, text = self._read(prepared.token)
            if path != prepared.path or text != prepared.text:
                raise _refuse('changed_during_save')
            state = _file_state(path)
            if state[5] == expected:
                return CommitEvidence(prepared.event_id, prepared.action_digest,
                                      ArtifactEvidence(uuid.uuid4().hex, path,
                                                       hashlib.sha256(prepared.text.encode()).hexdigest()))
            if self._clock() >= deadline:
                raise _refuse('file_does_not_hold_text')
            self._sleep(self._poll)


def make_existing_text_receipt(driver, **kwargs):
    return ExistingTextFileReceipt(driver, **kwargs)
