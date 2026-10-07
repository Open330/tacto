"""Save-folder selection and retained native identity, without app input."""
from pathlib import Path
import importlib.util
import tempfile
import unittest

from nlc.native_save_directory import SaveDirectoryReader, SaveDirectoryUnavailable


class Node(dict):
    pass


def node(role, parent=None, **attrs):
    item = Node(AXRole=role, AXChildren=[], **attrs)
    if parent is not None:
        item['AXParent'] = parent
        parent['AXChildren'].append(item)
    return item


class Adapter:
    def __init__(self):
        self.window = node('AXWindow')
        self.sheet = node('AXSheet', self.window, AXIdentifier='save-panel')
        self.group = node('AXSplitGroup', self.sheet)
        self.browser = node('AXBrowser', self.group, AXIdentifier='ColumnView')
        self.container = node('AXScrollArea', self.browser)
        self.columns, self.lists, self.rows, self.fields = [], [], [], []
        self.resources = {}
        for index, path in enumerate(('/owned', '/owned/destination', None)):
            column = node('AXScrollArea', self.container)
            listing = node('AXList', column, AXSelectedChildren=[])
            self.columns.append(column)
            self.lists.append(listing)
            if path:
                row = node('AXGroup', listing)
                field = node('AXTextField', row, AXURL=path, AXSelected=False)
                listing['AXSelectedChildren'] = [row]
                self.rows.append(row)
                self.fields.append(field)
                self.resources[path] = (path, 1, index + 1)
        self.hook = None
        self.calls = 0

    @staticmethod
    def equal(left, right):
        return left is right

    def read_attribute(self, item, name):
        self.calls += 1
        if self.hook:
            self.hook(item, name)
        result = item.get(name)
        if isinstance(result, tuple):
            return result
        return (0, result) if name in item else (-25205, None)

    def directory_state(self, raw):
        return self.resources[raw]


class SaveDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.adapter = Adapter()
        self.reader = SaveDirectoryReader(self.adapter)

    def test_selected_group_and_empty_terminal_prove_folder(self):
        witness = self.reader.read(self.adapter.window)
        self.assertEqual(witness.directory, '/owned/destination')
        self.assertEqual(self.reader.revalidate(self.adapter.window, witness), witness.directory)

    def test_selection_errors_and_nonempty_terminal_are_not_empty(self):
        for selected in ((-25205, None), (-25212, None), (-25204, None), None, 'bad', [self.adapter.rows[0]]):
            with self.subTest(selected=repr(selected)):
                self.adapter.lists[-1]['AXSelectedChildren'] = selected
                with self.assertRaises(SaveDirectoryUnavailable):
                    self.reader.read(self.adapter.window)

    def test_wrong_parent_or_missing_membership_is_rejected(self):
        for mutation in ('parent', 'membership'):
            adapter = Adapter()
            if mutation == 'parent':
                adapter.rows[0]['AXParent'] = adapter.lists[1]
            else:
                adapter.lists[0]['AXChildren'] = []
            with self.subTest(mutation=mutation), self.assertRaises(SaveDirectoryUnavailable):
                SaveDirectoryReader(adapter).read(adapter.window)

    def test_duplicate_url_or_nonadjacent_chain_is_rejected(self):
        node('AXTextField', self.adapter.rows[0], AXURL='/owned')
        with self.assertRaises(SaveDirectoryUnavailable):
            self.reader.read(self.adapter.window)
        self.adapter.rows[0]['AXChildren'].pop()
        self.adapter.resources['/owned/destination'] = ('/different/destination', 1, 2)
        with self.assertRaises(SaveDirectoryUnavailable):
            self.reader.read(self.adapter.window)

    def test_retained_witness_rejects_replacement_reorder_and_resource_change(self):
        for change in ('sheet', 'column_order', 'row', 'field', 'resource', 'terminal'):
            adapter = Adapter()
            reader = SaveDirectoryReader(adapter)
            witness = reader.read(adapter.window)
            if change == 'sheet':
                replacement = Node(adapter.sheet)
                adapter.window['AXChildren'] = [replacement]
                adapter.group['AXParent'] = replacement
            elif change == 'column_order':
                adapter.container['AXChildren'].reverse()
            elif change == 'row':
                replacement = Node(adapter.rows[0])
                adapter.lists[0]['AXChildren'] = [replacement]
                adapter.lists[0]['AXSelectedChildren'] = [replacement]
                adapter.fields[0]['AXParent'] = replacement
            elif change == 'field':
                adapter.rows[0]['AXChildren'] = [Node(adapter.fields[0])]
            elif change == 'resource':
                adapter.resources['/owned/destination'] = ('/owned/destination', 1, 99)
            else:
                adapter.lists[-1]['AXSelectedChildren'] = [adapter.rows[0]]
            with self.subTest(change=change), self.assertRaises(SaveDirectoryUnavailable):
                reader.revalidate(adapter.window, witness)

    def test_mid_read_change_and_budgets_are_rejected(self):
        reads = 0
        def drift(item, name):
            nonlocal reads
            if item is self.adapter.window and name == 'AXChildren':
                reads += 1
                if reads == 2:
                    self.adapter.resources['/owned/destination'] = ('/owned/destination', 1, 99)
        self.adapter.hook = drift
        with self.assertRaises(SaveDirectoryUnavailable):
            self.reader.read(self.adapter.window)
        self.adapter.hook = None
        for kwargs in ({'max_nodes': 5}, {'max_depth': 1}, {'max_columns': 2}, {'max_seconds': 1e-10}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SaveDirectoryUnavailable):
                SaveDirectoryReader(self.adapter, **kwargs).read(self.adapter.window)

    def test_unselected_file_rows_never_supply_destination(self):
        node('AXTextField', self.adapter.lists[-1], AXURL='/arbitrary/file')
        self.assertEqual(self.reader.read(self.adapter.window).directory, '/owned/destination')

    @unittest.skipUnless(importlib.util.find_spec('ApplicationServices'), 'PyObjC unavailable')
    def test_native_directory_url_and_resource_validation(self):
        from Foundation import NSURL
        from nlc.native_document_ax import AXDocumentAdapter
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory).resolve()
            for url in (path.as_uri() + '/', str(NSURL.fileURLWithPath_(str(path)).fileReferenceURL().absoluteString())):
                state = AXDocumentAdapter.directory_state(url)
                self.assertEqual(state, (str(path), path.stat().st_dev, path.stat().st_ino))
            file = path / 'file'
            file.write_text('owned')
            link = path / 'link'
            link.symlink_to(path, target_is_directory=True)
            for url in (file.as_uri(), link.as_uri(), 'file://remote/folder/', (path/'missing').as_uri()):
                with self.subTest(url=url), self.assertRaises((ValueError, RuntimeError, OSError)):
                    AXDocumentAdapter.directory_state(url)


if __name__ == '__main__':
    unittest.main()
