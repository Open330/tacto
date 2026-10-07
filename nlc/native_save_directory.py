"""Bounded read-only witness for an expanded Save panel's column directory.

Only the observed column-view layout is supported. A witness is retained native
identity plus selected folder resource identity, not permission to press Save.
Rechecks detect observed drift; they do not make AX/filesystem reads atomic.
"""
from dataclasses import dataclass
import time


class SaveDirectoryUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class SaveDirectoryWitness:
    directory: str
    identities: tuple
    resources: tuple


class SaveDirectoryReader:
    def __init__(self, adapter, *, array_types=(list, tuple), max_nodes=5000,
                 max_depth=32, max_columns=32, max_seconds=3.0):
        if (type(max_nodes) is not int or max_nodes < 1
                or type(max_depth) is not int or max_depth < 1
                or type(max_columns) is not int or max_columns < 2
                or type(max_seconds) not in (int, float)
                or not 0 < max_seconds <= 30):
            raise ValueError('invalid save-directory observation budget')
        self.adapter = adapter
        self.array_types = array_types
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.max_columns = max_columns
        self.max_seconds = max_seconds

    def read(self, window):
        """Read twice while the panel is live; reject hierarchy/selection drift."""
        try:
            before = self._capture(window)
            self._compare(before, self._capture(window))
            return before
        except SaveDirectoryUnavailable:
            raise
        except Exception as exc:
            raise SaveDirectoryUnavailable('save directory observation failed') from exc

    def revalidate(self, window, witness):
        if not isinstance(witness, SaveDirectoryWitness):
            raise SaveDirectoryUnavailable('invalid save directory witness')
        try:
            self._compare(witness, self._capture(window))
        except SaveDirectoryUnavailable:
            raise
        except Exception as exc:
            raise SaveDirectoryUnavailable('save directory revalidation failed') from exc
        return witness.directory

    def _compare(self, before, after):
        if (before.directory != after.directory or before.resources != after.resources
                or len(before.identities) != len(after.identities)
                or any(not self.adapter.equal(a, b)
                       for a, b in zip(before.identities, after.identities))):
            raise SaveDirectoryUnavailable('save panel directory or identity changed')

    def _capture(self, window):
        remaining = self.max_nodes
        deadline = time.monotonic() + self.max_seconds
        identities = [window]

        def charge(amount=1):
            nonlocal remaining
            remaining -= amount
            if remaining < 0 or time.monotonic() > deadline:
                raise SaveDirectoryUnavailable('save directory traversal budget exceeded')

        def value(node, name, *, optional=False):
            charge()
            result = self.adapter.read_attribute(node, name)
            if (not isinstance(result, tuple) or len(result) != 2
                    or type(result[0]) is not int):
                raise SaveDirectoryUnavailable('malformed AX attribute response')
            error, item = result
            if error == 0 and item is not None:
                return item
            if optional and error in (-25205, -25212):
                return None
            raise SaveDirectoryUnavailable(name + ' unavailable')

        def array(node, name, *, optional=False):
            items = value(node, name, optional=optional)
            if items is None and optional:
                return []
            if not isinstance(items, self.array_types):
                raise SaveDirectoryUnavailable(name + ' is not an array')
            charge(len(items))
            return list(items)

        def children(node):
            return array(node, 'AXChildren', optional=True)

        def role(node):
            item = value(node, 'AXRole')
            if not isinstance(item, str) or not item:
                raise SaveDirectoryUnavailable('malformed AX role')
            return item

        def bind(child, parent):
            if not self.adapter.equal(value(child, 'AXParent'), parent):
                raise SaveDirectoryUnavailable('save panel hierarchy changed')
            identities.append(child)

        def unique(items, message):
            if len(items) != 1:
                raise SaveDirectoryUnavailable(message)
            return items[0]

        # Save sheets are direct document children in the supported layout.
        sheet = unique([node for node in children(window) if role(node) == 'AXSheet'],
                       'save sheet absent or ambiguous')
        bind(sheet, window)
        if value(sheet, 'AXIdentifier') != 'save-panel':
            raise SaveDirectoryUnavailable('unsupported sheet')

        # Do not enumerate file rows while discovering the browser. Sidebar
        # outlines are unrelated to the selected destination and are pruned.
        found = []
        stack = [(sheet, 0, (sheet,))]
        while stack:
            node, depth, ancestry = stack.pop()
            charge()
            if depth > self.max_depth:
                raise SaveDirectoryUnavailable('save directory traversal budget exceeded')
            kind = role(node)
            if kind == 'AXBrowser':
                if value(node, 'AXIdentifier') != 'ColumnView':
                    raise SaveDirectoryUnavailable('unsupported save browser')
                found.append((node, ancestry))
                continue
            if kind in ('AXList', 'AXOutline'):
                continue
            for child in reversed(children(node)):
                if any(self.adapter.equal(child, ancestor) for ancestor in ancestry):
                    raise SaveDirectoryUnavailable('save panel hierarchy cycle')
                stack.append((child, depth + 1, ancestry + (child,)))
        browser, ancestry = unique(found, 'column browser absent or ambiguous')
        for parent, child in zip(ancestry, ancestry[1:]):
            bind(child, parent)
        container = unique(children(browser), 'column container absent or ambiguous')
        if role(container) != 'AXScrollArea':
            raise SaveDirectoryUnavailable('unsupported column container')
        bind(container, browser)
        columns = []
        for child in children(container):
            kind = role(child)
            if kind == 'AXScrollArea':
                columns.append(child)
            elif kind != 'AXScrollBar':
                raise SaveDirectoryUnavailable('unsupported column child')
        if not 2 <= len(columns) <= self.max_columns:
            raise SaveDirectoryUnavailable('selected folder column chain unavailable')
        if any(self.adapter.equal(a, b) for i, a in enumerate(columns)
               for b in columns[i + 1:]):
            raise SaveDirectoryUnavailable('duplicate column identity')

        resources = []
        for index, column in enumerate(columns):
            bind(column, container)
            lists = []
            for child in children(column):
                kind = role(child)
                if kind == 'AXList':
                    lists.append(child)
                elif kind != 'AXScrollBar':
                    raise SaveDirectoryUnavailable('unsupported list column child')
            listing = unique(lists, 'column list absent or ambiguous')
            bind(listing, column)
            selected = array(listing, 'AXSelectedChildren')
            if index == len(columns) - 1:
                if selected:
                    raise SaveDirectoryUnavailable('terminal content column has a selection')
                continue
            row = unique(selected, 'selected folder absent or ambiguous')
            if role(row) != 'AXGroup':
                raise SaveDirectoryUnavailable('unsupported selected folder row')
            if sum(self.adapter.equal(row, child) for child in children(listing)) != 1:
                raise SaveDirectoryUnavailable('selected folder is not a direct list child')
            bind(row, listing)
            fields = []
            for child in children(row):
                if role(child) == 'AXTextField':
                    raw = value(child, 'AXURL', optional=True)
                    if raw is not None:
                        fields.append((child, raw))
            field, raw = unique(fields, 'selected folder URL absent or ambiguous')
            bind(field, row)
            charge()
            resource = self.adapter.directory_state(raw)
            if (not isinstance(resource, tuple) or len(resource) != 3
                    or not isinstance(resource[0], str) or not resource[0].startswith('/')
                    or type(resource[1]) is not int or type(resource[2]) is not int):
                raise SaveDirectoryUnavailable('invalid directory resource identity')
            resources.append(resource)
        import os
        if any(os.path.dirname(child[0]) != parent[0]
               for parent, child in zip(resources, resources[1:])):
            raise SaveDirectoryUnavailable('selected folders are not a direct directory chain')
        charge(0)
        return SaveDirectoryWitness(resources[-1][0], tuple(identities), tuple(resources))
