"""Read-only document identity and complete text; no native permissions/imports.

The injected adapter must enumerate every document or raise. Element identity
comparison must use native identity, never labels. Tokens are local to this
reader instance and do not certify source format or a save-panel destination.
"""
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass
import os
import re
import threading
import time
from urllib.parse import unquote, urlsplit
import uuid

from .native_text_snapshot import read_complete_text
from .model_budget import model_deadline, remaining_model_seconds


class DocumentSnapshotError(ValueError):
    """A complete document witness could not be obtained."""


def validate_document_selection_path(path):
    if (type(path) is not str or not path or not path.startswith('/') or path.startswith('//')
            or '\\' in path or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in path)):
        raise DocumentSnapshotError('document selector must be a canonical absolute path')
    try:
        if len(path.encode('utf-8')) > 4096:
            raise DocumentSnapshotError('document selector is too long')
        if os.path.realpath(path) != path:
            raise DocumentSnapshotError('document selector must be a canonical absolute path')
    except UnicodeError as exc:
        raise DocumentSnapshotError('invalid document selector encoding') from exc
    return path


@dataclass(frozen=True)
class _Document:
    process: tuple
    window: object
    body: object
    selected_path: str | None = None


def document_url_syntax_path(value):
    """A local file URL's absolute path by syntax alone, or None.

    No file-system access, no reference resolution: the strict checks of
    `_document_path` up to decoding (an empty or `localhost` authority and
    nothing else, no query/fragment, no encoded separators/NUL, well-formed
    escapes decoding as strict UTF-8, an absolute path without control
    characters). Shared with the helper's documentURLSyntaxPath so a window
    line shows the same path either way (Codex review of 66433c5).
    """
    # Raw-text policy first, the same on both sides (Codex review of a33bcaa):
    # the scheme exactly "file://" in lower case (no leading space, no other
    # case), and the raw authority exactly "" or "localhost" — no escapes in
    # it, nothing a decoder would turn into localhost.
    if not isinstance(value, str) or not value.startswith('file://'):
        return None
    authority = value[len('file://'):].split('/', 1)[0]
    if authority not in ('', 'localhost') or '/' not in value[len('file://'):]:
        return None
    if (any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value)
            or re.search(r'%(?![0-9a-fA-F]{2})', value)
            or re.search(r'%(?:2f|5c|00)', value, re.I) or '?' in value or '#' in value):
        return None
    try:
        url = urlsplit(value)
        if url.scheme != 'file' or url.netloc not in ('', 'localhost') or url.query or url.fragment:
            return None
        path = unquote(url.path, encoding='utf-8', errors='strict')
    except (ValueError, UnicodeError):
        return None
    if (not path.startswith('/') or path.startswith('//') or '\\' in path
            or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in path)):
        return None
    return path


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
    # AXDocument uses directory URLs for document packages such as RTFD.
    # Accept exactly one terminal slash only for an existing canonical directory.
    if path.endswith('/') and path != '/':
        candidate = path[:-1]
        if os.path.realpath(candidate) == candidate and os.path.isdir(candidate):
            path = candidate
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

    @contextmanager
    def _observation_scope(self):
        """One shared soft deadline including lock wait and late native results."""
        try:
            deadline = time.monotonic() + remaining_model_seconds(3)
            with model_deadline(deadline), self._lock:
                remaining_model_seconds(3)
                yield
                remaining_model_seconds(3)
        except DocumentSnapshotError:
            raise
        except TimeoutError as exc:
            raise DocumentSnapshotError('document observation deadline exceeded') from exc
        except Exception as exc:
            raise DocumentSnapshotError('document observation failed') from exc

    def _call(self, name, *args, **kwargs):
        remaining_model_seconds(3)
        value = getattr(self._adapter, name)(*args, **kwargs)
        remaining_model_seconds(3)
        return value

    def _process(self, target):
        identity = self._call('process_identity', target)
        if (not isinstance(identity, tuple) or len(identity) != 2
                or not isinstance(identity[0], int) or isinstance(identity[0], bool)
                or identity[0] <= 0 or identity[1] is None):
            raise DocumentSnapshotError('process identity unavailable')
        try:
            hash(identity)  # Reject a mutable launch-stamp container.
        except TypeError as exc:
            raise DocumentSnapshotError('process identity must be immutable') from exc
        return identity

    def _documents(self, target, *, path=None, window=None):
        scoped = getattr(self._adapter, 'document_candidates', None)
        if scoped is not None and (path is not None or window is not None):
            documents = self._call('document_candidates', target, document_path=path, window=window)
        else:
            documents = self._call('documents', target)
        if not isinstance(documents, list) or any(
                not isinstance(pair, tuple) or len(pair) != 2
                or pair[0] is None or pair[1] is None for pair in documents):
            raise DocumentSnapshotError('complete document enumeration unavailable')
        if window is not None:
            documents = [pair for pair in documents if self._adapter.equal(pair[0], window)]
        if path is not None:
            documents = [pair for pair in documents
                if _document_path(self._call('read_attribute', pair[0], 'AXDocument')) == path]
        return documents

    def _matches(self, documents, document):
        return [(window, body) for window, body in documents
                if self._adapter.equal(window, document.window)
                and self._adapter.equal(body, document.body)]

    def read(self, target='com.apple.TextEdit', document_token=None, max_utf16_units=1000000,
             *, document_path=None):
        if type(max_utf16_units) is not int or not 0 <= max_utf16_units <= 1000000:
            raise DocumentSnapshotError('max_utf16_units must be an integer from 0 to 1000000')
        if target != 'com.apple.TextEdit':
            raise DocumentSnapshotError('only TextEdit document reads are supported')
        if document_token is not None and (not isinstance(document_token, str) or not document_token or len(document_token)>128):
            raise DocumentSnapshotError('invalid document token')
        if document_path is not None:
            validate_document_selection_path(document_path)
            if document_token is not None:
                raise DocumentSnapshotError('document path and token cannot be combined')
        # Include lock contention in the same operation/caller deadline. Native
        # callbacks cannot be forcibly interrupted; late results are refused.
        with self._observation_scope():
            return self._read(target, document_token, max_utf16_units, document_path)

    def _read(self, target, token, budget, selected_path=None):
        pinned = None
        if token is not None:
            pinned = self._tokens.get(token)
            if pinned is None:
                raise DocumentSnapshotError('unknown or expired document token')
        process = self._process(target)
        documents = self._documents(target, path=pinned.selected_path if pinned else selected_path,
                                    window=pinned.window if pinned else None)
        if pinned is None:
            if len(documents) != 1:
                raise DocumentSnapshotError('initial document selection is ambiguous or absent')
            pinned = _Document(process, *documents[0], selected_path=selected_path)
        elif process != pinned.process or len(self._matches(documents, pinned)) != 1:
            del self._tokens[token]
            raise DocumentSnapshotError('document token is stale')
        path = _document_path(self._call('read_attribute', pinned.window, 'AXDocument'))
        if pinned.selected_path is not None and path != pinned.selected_path:
            raise DocumentSnapshotError('selected document path changed before text read')
        text = read_complete_text(
            lambda name: self._call('read_attribute', pinned.body, name),
            lambda start, length: self._call('read_range', pinned.body, start, length),
            max_utf16_units=budget)
        after_documents = self._documents(target, window=pinned.window, path=pinned.selected_path)
        after_process = self._process(target)
        if after_process != process or len(self._matches(after_documents, pinned)) != 1:
            if token is not None:
                del self._tokens[token]
            raise DocumentSnapshotError('document identity changed during observation')
        after_path = _document_path(self._call('read_attribute', pinned.window, 'AXDocument'))
        if after_path != path:
            raise DocumentSnapshotError('document path changed during observation')
        remaining_model_seconds(3)
        if token is None:
            token = uuid.uuid4().hex
            self._tokens[token] = pinned
            if len(self._tokens) > self._max_tokens:
                self._tokens.popitem(last=False)
        return dict(type='document_text', document_token=token, text=text.text,
                    text_complete=True, document_path=path)

    def window_for_token(self, target, token):
        """Return a current pinned window without reading any body text."""
        if target != 'com.apple.TextEdit' or not isinstance(token, str) or not token or len(token)>128:
            raise DocumentSnapshotError('invalid scoped document token')
        with self._observation_scope():
            pinned = self._tokens.get(token)
            if pinned is None:
                raise DocumentSnapshotError('unknown or expired document token')
            documents = self._documents(target, window=pinned.window, path=pinned.selected_path)
            if self._process(target) != pinned.process or len(self._matches(documents, pinned)) != 1:
                del self._tokens[token]
                raise DocumentSnapshotError('document token is stale')
            return pinned.window

    def save_directory(self, target='com.apple.TextEdit', *, document_token, witness=None):
        """Observe one pinned document's directory; does not certify a save."""
        return self._save_observation(target, document_token, witness, 'save_directory')

    def save_panel(self, target='com.apple.TextEdit', *, document_token, witness=None,
                   require_format=False):
        """Bind read-only panel settings to a live document, without authorizing Save."""
        if type(require_format) is not bool:
            raise DocumentSnapshotError('require_format must be a boolean')
        return self._save_observation(target, document_token, witness, 'save_panel',
                                      require_format=require_format)

    def _save_observation(self, target, document_token, witness, operation, **kwargs):
        if target != 'com.apple.TextEdit' or not isinstance(document_token, str) or not document_token:
            raise DocumentSnapshotError('save observation requires a TextEdit document token')
        with self._observation_scope():
            pinned = self._tokens.get(document_token)
            if pinned is None:
                raise DocumentSnapshotError('unknown or expired document token')
            try:
                def validate():
                    if (self._process(target) != pinned.process
                            or len(self._matches(self._documents(target, window=pinned.window,
                                                               path=pinned.selected_path), pinned)) != 1):
                        self._tokens.pop(document_token, None)
                        raise DocumentSnapshotError('document token is stale')
                validate()
                if witness is None:
                    witness = self._call(operation, pinned.window, **kwargs)
                else:
                    self._call('revalidate_' + operation, pinned.window, witness, **kwargs)
                validate()
                return witness
            except DocumentSnapshotError:
                raise
            except TimeoutError:
                raise
            except Exception as exc:
                raise DocumentSnapshotError(operation + ' unavailable') from exc
