"""Read-only document identity and complete text; no native permissions/imports.

The injected adapter must enumerate every document or raise. Element identity
comparison must use native identity, never labels. Tokens are local to this
reader instance and do not certify source format or a save-panel destination.
"""
from collections import OrderedDict
from dataclasses import dataclass
import os
import re
import threading
from urllib.parse import unquote, urlsplit
import uuid

from .native_text_snapshot import read_complete_text


class DocumentSnapshotError(ValueError):
    """A complete document witness could not be obtained."""


@dataclass(frozen=True)
class _Document:
    process: tuple
    window: object
    body: object


def _document_path(result):
    if (not isinstance(result, tuple) or len(result) != 2
            or not isinstance(result[0], int) or isinstance(result[0], bool)):
        raise DocumentSnapshotError('invalid AXDocument response')
    error, value = result
    if error == -25212:  # kAXErrorNoValue: the only unsaved-document sentinel.
        return None
    if error != 0 or not isinstance(value, str) or not value:
        raise DocumentSnapshotError('AXDocument unavailable')
    if (any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value)
            or re.search(r'%(?![0-9a-fA-F]{2})', value)
            or re.search(r'%(?:2f|5c|00)', value, re.I)):
        raise DocumentSnapshotError('invalid document file URL')
    try:
        url = urlsplit(value)
        if (url.scheme != 'file' or url.netloc not in ('', 'localhost')
                or url.query or url.fragment or '?' in value or '#' in value):
            raise ValueError('not a local file URL')
        path = unquote(url.path, encoding='utf-8', errors='strict')
    except (ValueError, UnicodeError) as exc:
        raise DocumentSnapshotError('invalid document file URL') from exc
    if (not path.startswith('/') or path.startswith('//') or '\\' in path
            or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in path)
            or os.path.realpath(path) != path):
        raise DocumentSnapshotError('document path is not canonical absolute')
    return path


class TextEditDocumentReader:
    def __init__(self, adapter, *, max_tokens=32):
        if type(max_tokens) is not int or max_tokens <= 0:
            raise ValueError('max_tokens must be a positive integer')
        self._adapter = adapter
        self._max_tokens = max_tokens
        self._tokens = OrderedDict()
        self._lock = threading.Lock()

    def _process(self, target):
        identity = self._adapter.process_identity(target)
        if (not isinstance(identity, tuple) or len(identity) != 2
                or not isinstance(identity[0], int) or isinstance(identity[0], bool)
                or identity[0] <= 0 or identity[1] is None):
            raise DocumentSnapshotError('process identity unavailable')
        try:
            hash(identity)  # Reject a mutable launch-stamp container.
        except TypeError as exc:
            raise DocumentSnapshotError('process identity must be immutable') from exc
        return identity

    def _documents(self, target):
        documents = self._adapter.documents(target)
        if not isinstance(documents, list) or any(
                not isinstance(pair, tuple) or len(pair) != 2
                or pair[0] is None or pair[1] is None for pair in documents):
            raise DocumentSnapshotError('complete document enumeration unavailable')
        return documents

    def _matches(self, documents, document):
        return [(window, body) for window, body in documents
                if self._adapter.equal(window, document.window)
                and self._adapter.equal(body, document.body)]

    def read(self, target='com.apple.TextEdit', document_token=None, max_utf16_units=1000000):
        if type(max_utf16_units) is not int or not 0 <= max_utf16_units <= 1000000:
            raise DocumentSnapshotError('max_utf16_units must be an integer from 0 to 1000000')
        if target != 'com.apple.TextEdit':
            raise DocumentSnapshotError('only TextEdit document reads are supported')
        if document_token is not None and (not isinstance(document_token, str) or not document_token):
            raise DocumentSnapshotError('invalid document token')
        with self._lock:
            try:
                return self._read(target, document_token, max_utf16_units)
            except DocumentSnapshotError:
                raise
            except Exception as exc:
                raise DocumentSnapshotError('document observation failed') from exc

    def _read(self, target, token, budget):
        pinned = None
        if token is not None:
            pinned = self._tokens.get(token)
            if pinned is None:
                raise DocumentSnapshotError('unknown or expired document token')
        process = self._process(target)
        documents = self._documents(target)
        if pinned is None:
            if len(documents) != 1:
                raise DocumentSnapshotError('initial document selection is ambiguous or absent')
            pinned = _Document(process, *documents[0])
        elif process != pinned.process or len(self._matches(documents, pinned)) != 1:
            del self._tokens[token]
            raise DocumentSnapshotError('document token is stale')
        path = _document_path(self._adapter.read_attribute(pinned.window, 'AXDocument'))
        text = read_complete_text(
            lambda name: self._adapter.read_attribute(pinned.body, name),
            lambda start, length: self._adapter.read_range(pinned.body, start, length),
            max_utf16_units=budget)
        after_documents = self._documents(target)
        after_process = self._process(target)
        if after_process != process or len(self._matches(after_documents, pinned)) != 1:
            if token is not None:
                del self._tokens[token]
            raise DocumentSnapshotError('document identity changed during observation')
        after_path = _document_path(self._adapter.read_attribute(pinned.window, 'AXDocument'))
        if after_path != path:
            raise DocumentSnapshotError('document path changed during observation')
        if token is None:
            token = uuid.uuid4().hex
            self._tokens[token] = pinned
            if len(self._tokens) > self._max_tokens:
                self._tokens.popitem(last=False)
        return dict(type='document_text', document_token=token, text=text.text,
                    text_complete=True, document_path=path)
