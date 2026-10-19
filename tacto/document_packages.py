"""Trusted-host private RTFD snapshots; no GUI or save/send authority."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import stat
import time

from .document_attachments import attachment_inventory, _name


class PackageEvidenceError(ValueError):
    pass


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _Capture:
    def __init__(self, deadline, entries, depth, payload):
        self.deadline = deadline
        self.entries = entries
        self.depth = depth
        self.payload = payload
        self.count = 0
        self.bytes = 0

    def check(self):
        if time.monotonic() >= self.deadline:
            raise PackageEvidenceError('package_deadline')

    def names(self, descriptor):
        self.check()
        names = []
        with os.scandir(descriptor) as rows:
            for row in rows:
                self.check()
                if len(names) >= self.entries:
                    raise PackageEvidenceError('package_entry_bound')
                try:
                    names.append(_name(row.name))
                except ValueError as exc:
                    raise PackageEvidenceError('package_name_invalid') from exc
        return tuple(sorted(names))

    def node(self, descriptor, expected, depth):
        self.check()
        if depth > self.depth:
            raise PackageEvidenceError('package_depth_bound')
        if _identity(os.fstat(descriptor)) != _identity(expected):
            raise PackageEvidenceError('package_identity_changed')
        before = self.names(descriptor)
        children = []
        for name in before:
            self.check()
            self.count += 1
            if self.count > self.entries:
                raise PackageEvidenceError('package_entry_bound')
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            directory = stat.S_ISDIR(info.st_mode)
            if (info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
                    or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
                raise PackageEvidenceError('package_entry_unsupported')
            if not directory and (info.st_size < 0 or info.st_size > self.payload - self.bytes):
                raise PackageEvidenceError('package_payload_bound')
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if directory:
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=descriptor)
            try:
                if _identity(os.fstat(child)) != _identity(info):
                    raise PackageEvidenceError('package_identity_changed')
                if directory:
                    value = self.node(child, info, depth + 1)
                else:
                    chunks = []
                    remaining = info.st_size
                    while remaining:
                        self.check()
                        chunk = os.read(child, min(65536, remaining))
                        if not chunk:
                            raise PackageEvidenceError('package_file_changed')
                        chunks.append(chunk)
                        remaining -= len(chunk)
                    self.check()
                    if os.read(child, 1):
                        raise PackageEvidenceError('package_file_changed')
                    value = b''.join(chunks)
                    self.bytes += len(value)
                if (_identity(os.fstat(child)) != _identity(info)
                        or _identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False)) != _identity(info)):
                    raise PackageEvidenceError('package_identity_changed')
                children.append((name, _identity(info), directory, value))
            finally:
                os.close(child)
        if self.names(descriptor) != before or _identity(os.fstat(descriptor)) != _identity(expected):
            raise PackageEvidenceError('package_directory_changed')
        self.check()
        return tuple(children)


def _snapshot(path, deadline, entries, depth, payload):
    capture = _Capture(deadline, entries, depth, payload)
    capture.check()
    parent = path.parent.lstat()
    info = path.lstat()
    if (str(path) != os.path.realpath(path) or path.suffix.lower() != '.rtfd'
            or not path.is_absolute() or not stat.S_ISDIR(info.st_mode)
            or not stat.S_ISDIR(parent.st_mode)
            or info.st_uid != os.getuid() or parent.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700 or stat.S_IMODE(parent.st_mode) != 0o700):
        raise PackageEvidenceError('private_canonical_package_required')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        tree = capture.node(descriptor, info, 0)
        if (_identity(path.lstat()) != _identity(info)
                or _identity(path.parent.lstat()) != _identity(parent)
                or str(path) != os.path.realpath(path)):
            raise PackageEvidenceError('package_identity_changed')
    finally:
        os.close(descriptor)
    capture.check()
    return (_identity(parent), _identity(info), tree), capture.count, capture.bytes


@dataclass(frozen=True)
class CapturedRTFDPackage:
    body: object
    attachment_inventory: dict
    entry_count: int
    captured_bytes: int
    sampled_stable: bool = True
    proves_no_intermediate_change: bool = False
    general_source_format_verified: bool = False
    attachment_completeness_verified: bool = False
    producer_attribution_verified: bool = False
    authorizes_save: bool = False


def capture_rtfd_package(path, *, max_entries=256, max_depth=16,
                         max_payload_bytes=8 * 1024 * 1024,
                         max_utf16_units=10000, timeout_seconds=3):
    """Read an explicitly registered private package; all limits are soft.

    Three matching descriptor-anchored snapshots bracket native decoding and
    bounded attachment inventory. Returned native body/inventory may be mutable;
    this result is sampled evidence, never an authorization or a durable token.
    No filesystem-backed wrapper constructor or external URL loader is used.
    """
    for value, lower, upper in ((max_entries, 1, 4096), (max_depth, 0, 16),
                               (max_payload_bytes, 1, 64 * 1024 * 1024),
                               (max_utf16_units, 1, 100000)):
        if type(value) is not int or not lower <= value <= upper:
            raise ValueError('invalid_package_bound')
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 10):
        raise ValueError('invalid_package_deadline')
    if not isinstance(path, (str, Path)):
        raise ValueError('invalid_package_path')
    # Do not normalize a host selector: relative paths, dots and slashes refuse.
    raw_path = str(path)
    if raw_path != os.path.realpath(raw_path):
        raise PackageEvidenceError('private_canonical_package_required')
    path = Path(raw_path)
    deadline = time.monotonic() + timeout_seconds
    snapshot = lambda: _snapshot(path, deadline, max_entries, max_depth, max_payload_bytes)
    try:
        first, count, size = snapshot()
        if snapshot()[0] != first:
            raise PackageEvidenceError('package_snapshot_changed')
        from AppKit import NSAttributedString
        from Foundation import NSData, NSFileWrapper
        def wrapper(tree):
            if time.monotonic() >= deadline:
                raise PackageEvidenceError('package_deadline')
            rows = {}
            for name, identity, directory, value in tree:
                child = (wrapper(value) if directory else
                         NSFileWrapper.alloc().initRegularFileWithContents_(
                             NSData.dataWithBytes_length_(value, len(value))))
                if child is None:
                    raise PackageEvidenceError('package_wrapper_unavailable')
                child.setPreferredFilename_(name)
                rows[name] = child
            return NSFileWrapper.alloc().initDirectoryWithFileWrappers_(rows)
        root = wrapper(first[2])
        if time.monotonic() >= deadline:
            raise PackageEvidenceError('package_deadline')
        body, _ = NSAttributedString.alloc().initWithRTFDFileWrapper_documentAttributes_(root, None)
        if time.monotonic() >= deadline:
            raise PackageEvidenceError('package_deadline')
        if body is None or body.length() > max_utf16_units:
            raise PackageEvidenceError('package_decode_unavailable')
        inventory = attachment_inventory(body, max_utf16_units=max_utf16_units,
            max_entries=max_entries, max_total_bytes=max_payload_bytes,
            timeout_seconds=deadline-time.monotonic())
        if snapshot()[0] != first:
            raise PackageEvidenceError('package_snapshot_changed')
        result = CapturedRTFDPackage(body, inventory, count, size)
        if time.monotonic() >= deadline:
            raise PackageEvidenceError('package_deadline')
        return result
    except PackageEvidenceError:
        raise
    except Exception as exc:
        raise PackageEvidenceError('package_capture_unavailable') from exc
