"""Read-only AX adapter for complete TextEdit document text and identity.

No app launch, activation, AX mutation, or synthesized input is used here.
Traversal budgets fail explicitly; a partial enumeration is never returned.
"""
import math

from AppKit import NSRunningApplication
from Foundation import NSURL

from .native_document import _document_path
from CoreFoundation import CFEqual
from ApplicationServices import (
    AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementCopyParameterizedAttributeValue, AXValueCreate, kAXValueCFRangeType,
)


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
        result = AXUIElementCopyAttributeValue(element, name, None)
        if name == 'AXDocument' and result[0] == 0:
            return 0, resolve_document_url(result[1])
        return result

    def _required(self, element, name):
        error, value = self.read_attribute(element, name)
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
        pid, _ = self._process(target)
        app = AXUIElementCreateApplication(pid)
        windows = self._required(app, 'AXWindows')
        documents = []
        remaining = self.max_nodes
        for window in windows:
            if self._required(window, 'AXSubrole') != 'AXStandardWindow':
                continue
            bodies = []
            stack = [(window, 0)]
            while stack:
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
            documents.append((window, bodies[0]))
        return documents

    @staticmethod
    def read_range(body, start, length):
        value = AXValueCreate(kAXValueCFRangeType, (start, length))
        if value is None:
            raise RuntimeError('cannot construct AX text range')
        return AXUIElementCopyParameterizedAttributeValue(
            body, 'AXStringForRange', value, None)


def resolve_document_url(value):
    """Resolve local file-reference URLs without interpreting IDs as paths.

    Validation precedes Foundation parsing, which can repair malformed URLs.
    Path URLs may name missing files (an open document can outlive its file).
    File references must resolve to a reachable resource; never guess its path.
    """
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
    resolved_path = _document_path((0, absolute))
    if resolved_path.startswith('/.file/'):
        raise RuntimeError('document file reference did not resolve')
    return absolute
