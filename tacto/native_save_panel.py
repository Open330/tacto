"""Read-only Save-panel settings bound to native control and folder identities.

This observer neither enables a capability nor presses Save. A supported format
is the explicit observed popup value, never a filename extension. Captures are
bounded and repeated, but independent AX/filesystem reads are not atomic.
"""
from dataclasses import dataclass
import time

from .native_save_directory import SaveDirectoryReader, SaveDirectoryWitness


class SavePanelUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class SavePanelWitness:
    filename: str
    format_name: str | None
    format_value: str | None
    format_unavailable: str | None
    directory_witness: SaveDirectoryWitness
    identities: tuple
    commit_control: object
    original_changes_setting: int | None
    original_changes_unavailable: str | None

    @property
    def directory(self):
        return self.directory_witness.directory


class SavePanelReader:
    def __init__(self, adapter, *, directory_reader=None, array_types=(list, tuple),
                 max_nodes=5000, max_depth=32, max_seconds=3.0):
        if (type(max_nodes) is not int or max_nodes < 1
                or type(max_depth) is not int or max_depth < 1
                or type(max_seconds) not in (int, float)
                or not 0 < max_seconds <= 30):
            raise ValueError('invalid save-panel observation budget')
        self.adapter = adapter
        self.directory_reader = directory_reader or SaveDirectoryReader(
            adapter, array_types=array_types, max_nodes=max_nodes,
            max_depth=max_depth, max_seconds=max_seconds)
        self.array_types = array_types
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.max_seconds = max_seconds

    def read(self, window, *, require_format=False):
        if type(require_format) is not bool:
            raise SavePanelUnavailable('require_format must be a boolean')
        try:
            directory = self.directory_reader.read(window)
            before = self._capture(window, directory, require_format)
            self._compare(before, self._capture(window, directory, require_format))
            self.directory_reader.revalidate(window, directory)
            return before
        except SavePanelUnavailable:
            raise
        except Exception as exc:
            raise SavePanelUnavailable('save panel observation failed') from exc

    def revalidate(self, window, witness, *, require_format=False):
        if type(require_format) is not bool:
            raise SavePanelUnavailable('require_format must be a boolean')
        if not isinstance(witness, SavePanelWitness):
            raise SavePanelUnavailable('invalid save panel witness')
        try:
            self.directory_reader.revalidate(window, witness.directory_witness)
            self._compare(witness, self._capture(window, witness.directory_witness,
                                                 require_format))
            self.directory_reader.revalidate(window, witness.directory_witness)
            return witness
        except SavePanelUnavailable:
            raise
        except Exception as exc:
            raise SavePanelUnavailable('save panel revalidation failed') from exc

    def bind_commit(self, window, witness, *, obs_id, elements, require_format=False):
        """Map the witnessed Save control to a backend-owned observation.

        Internal read-only evidence: callers must supply their current native
        element map, never a model-provided index. This neither dispatches Save
        nor replaces process/document/observation checks at final dispatch.
        """
        if (not isinstance(obs_id, str) or not obs_id.strip() or len(obs_id) > 128
                or type(elements) not in (list, tuple)
                or len(elements) > self.max_nodes):
            raise SavePanelUnavailable('invalid native observation map')
        snapshot = tuple(elements)
        self.revalidate(window, witness, require_format=require_format)
        try:
            matches = []
            deadline = time.monotonic() + self.max_seconds
            for index, element in enumerate(snapshot):
                if self.adapter.equal(element, witness.commit_control):
                    matches.append(index)
                if time.monotonic() > deadline:
                    raise SavePanelUnavailable('native observation binding budget exceeded')
        except SavePanelUnavailable:
            raise
        except Exception as exc:
            raise SavePanelUnavailable('native observation identity unavailable') from exc
        if len(matches) != 1:
            raise SavePanelUnavailable('save control absent or ambiguous in observation')
        self.revalidate(window, witness, require_format=require_format)
        if (len(elements) != len(snapshot)
                or any(current is not previous
                       for current, previous in zip(elements, snapshot))):
            raise SavePanelUnavailable('native observation map changed')
        return {'obs_id': obs_id, 'enabled': True,
                'action': {'name': 'click', 'element_index': matches[0]}}

    def _compare(self, before, after):
        if ((before.filename, before.format_name, before.format_value,
             before.format_unavailable, before.original_changes_setting,
             before.original_changes_unavailable) !=
                (after.filename, after.format_name, after.format_value,
                 after.format_unavailable, after.original_changes_setting,
                 after.original_changes_unavailable)
                or len(before.identities) != len(after.identities)
                or any(not self.adapter.equal(a, b)
                       for a, b in zip(before.identities, after.identities))
                or not self.adapter.equal(before.commit_control, after.commit_control)):
            raise SavePanelUnavailable('save panel settings or identity changed')

    def _capture(self, window, directory, require_format):
        remaining = self.max_nodes
        deadline = time.monotonic() + self.max_seconds

        def charge(amount=1):
            nonlocal remaining
            remaining -= amount
            if remaining < 0 or time.monotonic() > deadline:
                raise SavePanelUnavailable('save panel traversal budget exceeded')

        def value(item, name, *, optional=False):
            charge()
            result = self.adapter.read_attribute(item, name)
            if (not isinstance(result, tuple) or len(result) != 2
                    or type(result[0]) is not int):
                raise SavePanelUnavailable('malformed AX attribute response')
            error, raw = result
            if error == 0 and raw is not None:
                return raw
            if optional and error in (-25205, -25212):
                return None
            raise SavePanelUnavailable(name + ' unavailable')

        def children(item):
            raw = value(item, 'AXChildren', optional=True)
            if raw is None:
                return []
            if not isinstance(raw, self.array_types):
                raise SavePanelUnavailable('AXChildren is not an array')
            charge(len(raw))
            return list(raw)

        def role(item):
            raw = value(item, 'AXRole')
            if not isinstance(raw, str) or not raw:
                raise SavePanelUnavailable('malformed AX role')
            return raw

        sheets = [item for item in children(window) if role(item) == 'AXSheet']
        if len(sheets) != 1:
            raise SavePanelUnavailable('save sheet absent or ambiguous')
        sheet = sheets[0]
        if (not self.adapter.equal(value(sheet, 'AXParent'), window)
                or value(sheet, 'AXIdentifier') != 'save-panel'
                or len(directory.identities) < 2
                or not self.adapter.equal(directory.identities[0], window)
                or not self.adapter.equal(directory.identities[1], sheet)):
            raise SavePanelUnavailable('save sheet identity or hierarchy changed')
        identifiers = {'saveAsNameTextField': 'AXTextField',
                       'ContentTypesPopup': 'AXPopUpButton', 'OKButton': 'AXButton',
                       'KeepChangesButton': 'AXCheckBox'}
        found = {key: [] for key in identifiers}
        seen = []
        stack = [(sheet, 0, (window, sheet))]
        while stack:
            item, depth, ancestry = stack.pop()
            charge()
            if depth > self.max_depth:
                raise SavePanelUnavailable('save panel traversal budget exceeded')
            if any(self.adapter.equal(item, other) for other in seen):
                raise SavePanelUnavailable('duplicate or cyclic save panel identity')
            seen.append(item)
            kind = role(item)
            identifier = value(item, 'AXIdentifier', optional=True)
            if identifier in identifiers:
                if kind != identifiers[identifier]:
                    raise SavePanelUnavailable('unsupported save control role')
                found[identifier].append((item, ancestry))
            # Browser and sidebar rows cannot contain supported settings controls.
            if kind in ('AXBrowser', 'AXList', 'AXOutline'):
                continue
            for child in reversed(children(item)):
                if (any(self.adapter.equal(child, parent) for parent in ancestry)
                        or not self.adapter.equal(value(child, 'AXParent'), item)):
                    raise SavePanelUnavailable('save panel hierarchy changed')
                stack.append((child, depth + 1, ancestry + (child,)))
        identities = [window, sheet]

        def control(identifier, *, optional=False):
            matches = found[identifier]
            if not matches and optional:
                return None
            if len(matches) != 1:
                raise SavePanelUnavailable(identifier + ' absent or ambiguous')
            item, ancestry = matches[0]
            for parent, child in zip(ancestry, ancestry[1:]):
                if not self.adapter.equal(value(child, 'AXParent'), parent):
                    raise SavePanelUnavailable('save control ancestry changed')
            identities.extend(ancestry[2:])
            return item

        name = control('saveAsNameTextField')
        filename = value(name, 'AXValue')
        if (not isinstance(filename, str) or not filename or filename in ('.', '..')
                or '/' in filename or '\\' in filename
                or any(ord(char) < 32 or ord(char) == 127 for char in filename)):
            raise SavePanelUnavailable('invalid save filename')
        commit = control('OKButton')
        if value(commit, 'AXEnabled') is not True:
            raise SavePanelUnavailable('save commit control not explicitly enabled')
        popup = control('ContentTypesPopup', optional=True)
        format_value = None
        format_name = None
        unavailable = 'explicit file format control absent'
        if popup is not None:
            format_value = value(popup, 'AXValue')
            if not isinstance(format_value, str) or not format_value:
                raise SavePanelUnavailable('invalid save format value')
            if format_value == 'Rich Text Document':
                format_name = 'rtf'
                unavailable = None
            else:
                unavailable = 'explicit file format value unsupported'
        keep_changes = control('KeepChangesButton', optional=True)
        original_changes_setting = None
        original_changes_unavailable = 'explicit original changes control absent'
        if keep_changes is not None:
            raw = value(keep_changes, 'AXValue')
            if type(raw) not in (int, bool) or raw not in (0, 1):
                raise SavePanelUnavailable('unsupported original changes setting')
            original_changes_setting = int(raw)
            original_changes_unavailable = None
        if require_format and format_name is None:
            raise SavePanelUnavailable(unavailable)
        charge(0)
        return SavePanelWitness(filename, format_name, format_value, unavailable,
                                directory, tuple(identities), commit, original_changes_setting,
                                original_changes_unavailable)
