"""Read-only AX adapter for complete TextEdit document text and identity.

No app launch, activation, AX mutation, or synthesized input is used here.
Traversal budgets fail explicitly; a partial enumeration is never returned.
"""
import math
import os
import stat

from AppKit import NSRunningApplication
from Foundation import NSURL, NSArray, NSNumber

from .native_document import _document_path
from CoreFoundation import CFEqual
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementCopyParameterizedAttributeValue, AXValueCreate, kAXValueCFRangeType,
    AXUIElementSetMessagingTimeout,
)
from .model_budget import remaining_model_seconds


def _bounded_read(element, callback):
    timeout = min(.5, remaining_model_seconds(3))
    if AXUIElementSetMessagingTimeout(element, timeout) != 0:
        raise RuntimeError('document messaging timeout unavailable')
    failed = False
    try:
        result = callback()
    except BaseException:
        failed = True
        raise
    finally:
        try:
            reset = AXUIElementSetMessagingTimeout(element, 0)
        except Exception:
            if not failed:
                raise
            reset = None
        if reset != 0 and not failed:
            raise RuntimeError('document messaging timeout reset failed')
    remaining_model_seconds(3)
    return result


class AXDocumentAdapter:
    def __init__(self, resolve, *, max_nodes=5000, max_depth=64):
        self.resolve = resolve
        self.max_nodes = max_nodes
        self.max_depth = max_depth

    @staticmethod
    def equal(left, right):
        return bool(CFEqual(left, right))

    @staticmethod
    def read_attribute(element, name):
        result = _bounded_read(element, lambda: AXUIElementCopyAttributeValue(element, name, None))
        if name == 'AXDocument' and result[0] == 0:
            return 0, resolve_document_url(result[1])
        if (name == 'AXValue' and result[0] == 0
                and isinstance(result[1], NSNumber)
                and result[1].objCType() in (b'c', b'C', b's', b'S', b'i', b'I',
                                           b'l', b'L', b'q', b'Q', b'B')
                and _bounded_read(element, lambda: AXUIElementCopyAttributeValue(element, 'AXRole', None)) == (0, 'AXCheckBox')):
            # AppKit may expose an integral NSNumber proxy (OC_PythonLong),
            # rather than a Python int. Preserve strict integer checkbox
            # semantics; floating-point and textual values remain unsupported.
            return 0, int(result[1].longLongValue())
        return result

    def _required(self, element, name):
        remaining_model_seconds(3)
        error, value = self.read_attribute(element, name)
        remaining_model_seconds(3)
        if error != 0 or value is None:
            raise RuntimeError(f'{name} unavailable: {error}')
        return value

    def _process(self, target):
        pid = self.resolve(target, launch=False)
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise RuntimeError('document application is not running')
        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
        if app is None or app.isTerminated() or app.bundleIdentifier() != target:
            raise RuntimeError('document application identity unavailable')
        return pid, app

    def process_identity(self, target):
        pid, app = self._process(target)
        date = app.launchDate()
        if date is None:
            raise RuntimeError('application launch identity unavailable')
        stamp = float(date.timeIntervalSince1970())
        if not math.isfinite(stamp):
            raise RuntimeError('invalid application launch identity')
        return pid, stamp

    def documents(self, target):
        return self.document_candidates(target)

    def document_candidates(self, target, *, document_path=None, window=None):
        pid, _ = self._process(target)
        app = AXUIElementCreateApplication(pid)
        windows = self._required(app, 'AXWindows')
        documents = []
        remaining = self.max_nodes
        for candidate in windows:
            remaining_model_seconds(3)
            if window is not None and not self.equal(candidate, window):
                continue
            if self._required(candidate, 'AXSubrole') != 'AXStandardWindow':
                continue
            if document_path is not None:
                if _document_path(self.read_attribute(candidate, 'AXDocument')) != document_path:
                    continue
            bodies = []
            stack = [(candidate, 0)]
            while stack:
                remaining_model_seconds(3)
                element, depth = stack.pop()
                remaining -= 1
                if remaining < 0 or depth > self.max_depth:
                    raise RuntimeError('document traversal budget exceeded')
                role = self._required(element, 'AXRole')
                if role == 'AXTextArea':
                    identifier = self._required(element, 'AXIdentifier')
                    if identifier == 'First Text View':
                        bodies.append(element)
                error, children = self.read_attribute(element, 'AXChildren')
                if error in (-25205, -25212):
                    continue  # Unsupported/no-value is valid for a leaf.
                if error != 0 or children is None:
                    raise RuntimeError(f'document children unavailable: {error}')
                if len(children) > remaining:
                    raise RuntimeError('document traversal budget exceeded')
                stack.extend((child, depth + 1) for child in reversed(children))
            if len(bodies) != 1:
                raise RuntimeError('document body is absent or ambiguous')
            documents.append((candidate, bodies[0]))
        return documents

    @staticmethod
    def read_range(body, start, length):
        value = AXValueCreate(kAXValueCFRangeType, (start, length))
        if value is None:
            raise RuntimeError('cannot construct AX text range')
        return _bounded_read(body, lambda: AXUIElementCopyParameterizedAttributeValue(
            body, 'AXStringForRange', value, None))

    @staticmethod
    def directory_state(value):
        if isinstance(value, NSURL):
            value = str(value.absoluteString())
        path = _document_path((0, resolve_document_url(value, directory=True)))
        if path is None or path.startswith('/.file/'):
            raise RuntimeError('selected directory URL unresolved')
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode):
            raise RuntimeError('selected resource is not a directory')
        return path, info.st_dev, info.st_ino

    def save_directory(self, window):
        from .native_save_directory import SaveDirectoryReader
        return SaveDirectoryReader(self, array_types=(list, tuple, NSArray)).read(window)

    def revalidate_save_directory(self, window, witness):
        from .native_save_directory import SaveDirectoryReader
        return SaveDirectoryReader(self, array_types=(list, tuple, NSArray)).revalidate(window, witness)

    def save_panel(self, window, *, require_format=False):
        from .native_save_panel import SavePanelReader
        return SavePanelReader(self, array_types=(list, tuple, NSArray)).read(
            window, require_format=require_format)

    def revalidate_save_panel(self, window, witness, *, require_format=False):
        from .native_save_panel import SavePanelReader
        return SavePanelReader(self, array_types=(list, tuple, NSArray)).revalidate(
            window, witness, require_format=require_format)


def resolve_document_url(value, *, directory=False):
    """Resolve local file-reference URLs without interpreting IDs as paths.

    Validation precedes Foundation parsing, which can repair malformed URLs.
    Path URLs may name missing files (an open document can outlive its file).
    File references must resolve to a reachable resource; never guess its path.
    """
    # Directory URLs legitimately end in '/'. Normalize that one delimiter
    # before canonical path validation, without relaxing dot/symlink checks.
    if directory and isinstance(value, str) and value.endswith('/') and value != 'file:///':
        value = value[:-1]
    path = _document_path((0, value))
    if not path.startswith('/.file/'):
        return value
    url = NSURL.URLWithString_(value)
    if url is None or not url.isFileReferenceURL():
        raise RuntimeError('invalid document file reference')
    resolved = url.filePathURL()
    if resolved is None:
        raise RuntimeError('document file reference is unreachable')
    absolute = str(resolved.absoluteString())
    if directory and absolute.endswith('/') and absolute != 'file:///':
        absolute = absolute[:-1]
    resolved_path = _document_path((0, absolute))
    if resolved_path.startswith('/.file/'):
        raise RuntimeError('document file reference did not resolve')
    return absolute
