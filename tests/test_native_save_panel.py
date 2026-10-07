"""Save settings are explicit, identity-bound and fail closed on observed drift."""
import unittest

from nlc.native_save_directory import SaveDirectoryReader
from nlc.native_save_panel import SavePanelReader, SavePanelUnavailable
from tests.test_native_save_directory import Adapter, Node, node


class PanelAdapter(Adapter):
    def __init__(self, *, format_control=True):
        super().__init__()
        self.name = node('AXTextField', self.group,
                         AXIdentifier='saveAsNameTextField', AXValue='owned.rtf')
        self.commit = node('AXButton', self.group,
                           AXIdentifier='OKButton', AXEnabled=True)
        self.format = (node('AXPopUpButton', self.sheet,
                            AXIdentifier='ContentTypesPopup', AXValue='Rich Text Document')
                       if format_control else None)


class SavePanelTests(unittest.TestCase):
    def setUp(self):
        self.adapter = PanelAdapter()
        self.reader = SavePanelReader(self.adapter)

    def test_explicit_settings_and_commit_identity(self):
        witness = self.reader.read(self.adapter.window, require_format=True)
        self.assertEqual(witness.filename, 'owned.rtf')
        self.assertEqual(witness.directory, '/owned/destination')
        self.assertEqual(witness.format_name, 'rtf')
        self.assertEqual(witness.format_value, 'Rich Text Document')
        self.assertIsNone(witness.format_unavailable)
        self.assertIs(witness.commit_control, self.adapter.commit)
        self.assertIs(self.reader.revalidate(self.adapter.window, witness), witness)

    def test_format_requirement_cannot_be_weakened_with_nonboolean_values(self):
        witness = self.reader.read(self.adapter.window)
        for value in (0, 1, None, 'false', [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(SavePanelUnavailable, 'must be a boolean'):
                    self.reader.read(self.adapter.window, require_format=value)
                with self.assertRaisesRegex(SavePanelUnavailable, 'must be a boolean'):
                    self.reader.revalidate(self.adapter.window, witness, require_format=value)

    def test_filename_extension_never_supplies_missing_format(self):
        adapter = PanelAdapter(format_control=False)
        reader = SavePanelReader(adapter)
        witness = reader.read(adapter.window)
        self.assertIsNone(witness.format_name)
        self.assertEqual(witness.format_unavailable, 'explicit file format control absent')
        with self.assertRaisesRegex(SavePanelUnavailable, 'format control absent'):
            reader.read(adapter.window, require_format=True)
        with self.assertRaises(SavePanelUnavailable):
            reader.revalidate(adapter.window, witness, require_format=True)

    def test_only_observed_rtf_format_is_supported(self):
        self.adapter.format['AXValue'] = 'Plain Text'
        witness = self.reader.read(self.adapter.window)
        self.assertEqual(witness.format_value, 'Plain Text')
        self.assertIsNone(witness.format_name)
        with self.assertRaisesRegex(SavePanelUnavailable, 'value unsupported'):
            self.reader.read(self.adapter.window, require_format=True)

    def test_duplicate_identifiers_wrong_role_and_label_only_rejected(self):
        for change in ('name_duplicate', 'commit_duplicate', 'format_duplicate',
                       'wrong_role', 'label_only'):
            adapter = PanelAdapter()
            if change.endswith('duplicate'):
                source = {'name_duplicate': adapter.name,
                          'commit_duplicate': adapter.commit,
                          'format_duplicate': adapter.format}[change]
                replacement = Node(source)
                source['AXParent']['AXChildren'].append(replacement)
            elif change == 'wrong_role':
                adapter.commit['AXRole'] = 'AXStaticText'
            else:
                del adapter.commit['AXIdentifier']
                adapter.commit['AXTitle'] = 'Save'
            with self.subTest(change=change), self.assertRaises(SavePanelUnavailable):
                SavePanelReader(adapter).read(adapter.window)

    def test_missing_disabled_and_nonboolean_enabled_rejected(self):
        for value in (False, 1, 'true', None, (-25205, None), (-25204, None)):
            adapter = PanelAdapter()
            adapter.commit['AXEnabled'] = value
            with self.subTest(value=value), self.assertRaises(SavePanelUnavailable):
                SavePanelReader(adapter).read(adapter.window)

    def test_wrong_ancestry_and_cycle_rejected(self):
        self.adapter.name['AXParent'] = self.adapter.sheet
        with self.assertRaises(SavePanelUnavailable):
            self.reader.read(self.adapter.window)
        self.adapter.name['AXParent'] = self.adapter.group
        self.adapter.group['AXChildren'].append(self.adapter.group)
        with self.assertRaises(SavePanelUnavailable):
            self.reader.read(self.adapter.window)

    def test_revalidate_settings_controls_sheet_and_resource_drift(self):
        for change in ('filename', 'format', 'commit', 'name', 'format_control',
                       'sheet', 'directory', 'stale_url', 'disabled'):
            adapter = PanelAdapter()
            reader = SavePanelReader(adapter)
            witness = reader.read(adapter.window, require_format=True)
            if change == 'filename':
                adapter.name['AXValue'] = 'changed.rtf'
            elif change == 'format':
                adapter.format['AXValue'] = 'Rich Text Document with Attachments'
            elif change == 'disabled':
                adapter.commit['AXEnabled'] = False
            elif change == 'directory':
                adapter.resources['/owned/destination'] = ('/owned/destination', 1, 99)
            elif change == 'stale_url':
                del adapter.resources['/owned/destination']
            elif change == 'sheet':
                replacement = Node(adapter.sheet)
                adapter.window['AXChildren'] = [replacement]
                adapter.group['AXParent'] = replacement
                adapter.format['AXParent'] = replacement
            else:
                source = {'commit': adapter.commit, 'name': adapter.name,
                          'format_control': adapter.format}[change]
                siblings = source['AXParent']['AXChildren']
                siblings[:] = [Node(item) if item is source else item for item in siblings]
            with self.subTest(change=change), self.assertRaises(SavePanelUnavailable):
                reader.revalidate(adapter.window, witness)

    def test_directory_change_same_sheet_detected(self):
        witness = self.reader.read(self.adapter.window)
        self.adapter.fields[-1]['AXURL'] = '/owned/other'
        self.adapter.resources['/owned/other'] = ('/owned/other', 1, 50)
        with self.assertRaises(SavePanelUnavailable):
            self.reader.revalidate(self.adapter.window, witness)

    def test_mid_read_settings_drift_rejected(self):
        reads = 0
        def drift(item, name):
            nonlocal reads
            if item is self.adapter.name and name == 'AXValue':
                reads += 1
                if reads == 2:
                    item['AXValue'] = 'changed.rtf'
        self.adapter.hook = drift
        with self.assertRaisesRegex(SavePanelUnavailable, 'settings or identity changed'):
            self.reader.read(self.adapter.window)

    def test_panel_specific_budgets_and_malformed_response(self):
        # Directory reader retains its own budget; these exercise settings traversal.
        directory_reader = SaveDirectoryReader(self.adapter)
        for kwargs in ({'max_nodes': 5}, {'max_depth': 1}, {'max_seconds': 1e-10}):
            with self.subTest(kwargs=kwargs), self.assertRaises(SavePanelUnavailable):
                SavePanelReader(self.adapter, directory_reader=directory_reader,
                                **kwargs).read(self.adapter.window)
        self.adapter.name['AXValue'] = ('bad', 'filename')
        with self.assertRaisesRegex(SavePanelUnavailable, 'malformed AX'):
            self.reader.read(self.adapter.window)

    def test_original_changes_setting_is_explicit_and_binary(self):
        witness = self.reader.read(self.adapter.window)
        self.assertIsNone(witness.original_changes_setting)
        self.assertEqual(witness.original_changes_unavailable,
                         'explicit original changes control absent')
        checkbox = node('AXCheckBox', self.adapter.sheet,
                        AXIdentifier='KeepChangesButton', AXValue=1)
        for value in (0, 1, False, True):
            checkbox['AXValue'] = value
            witness = self.reader.read(self.adapter.window)
            self.assertEqual(witness.original_changes_setting, int(value))
            self.assertIsNone(witness.original_changes_unavailable)
        for unknown in (-1, 2, '1', '', 1.0, None, (-25205, None)):
            checkbox['AXValue'] = unknown
            with self.subTest(value=unknown), self.assertRaises(SavePanelUnavailable):
                self.reader.read(self.adapter.window)

    def test_original_changes_duplicate_identity_and_value_drift(self):
        checkbox = node('AXCheckBox', self.adapter.sheet,
                        AXIdentifier='KeepChangesButton', AXValue=1)
        witness = self.reader.read(self.adapter.window)
        checkbox['AXValue'] = 0
        with self.assertRaises(SavePanelUnavailable):
            self.reader.revalidate(self.adapter.window, witness)
        checkbox['AXValue'] = 1
        replacement = Node(checkbox)
        siblings = self.adapter.sheet['AXChildren']
        siblings[:] = [replacement if item is checkbox else item for item in siblings]
        with self.assertRaises(SavePanelUnavailable):
            self.reader.revalidate(self.adapter.window, witness)
        siblings.append(checkbox)
        with self.assertRaises(SavePanelUnavailable):
            self.reader.read(self.adapter.window)
        siblings.remove(replacement)
        siblings.remove(checkbox)
        with self.assertRaises(SavePanelUnavailable):
            self.reader.revalidate(self.adapter.window, witness)

    def test_commit_binding_uses_identity_not_save_label(self):
        witness = self.reader.read(self.adapter.window)
        impostor = Node(AXRole='AXButton', AXIdentifier='OKButton',
                        AXTitle='Save', AXEnabled=True)
        elements = [impostor, self.adapter.name, self.adapter.commit]
        self.assertEqual(self.reader.bind_commit(
            self.adapter.window, witness, obs_id='o-owned', elements=elements),
            {'obs_id': 'o-owned', 'enabled': True,
             'action': {'name': 'click', 'element_index': 2}})
        for candidates in ([impostor], [self.adapter.commit, self.adapter.commit], []):
            with self.subTest(candidates=len(candidates)), self.assertRaises(SavePanelUnavailable):
                self.reader.bind_commit(self.adapter.window, witness,
                                        obs_id='o-owned', elements=candidates)

    def test_commit_binding_rejects_settings_or_map_drift_during_matching(self):
        for change in ('disabled', 'filename', 'map'):
            adapter = PanelAdapter()
            reader = SavePanelReader(adapter)
            witness = reader.read(adapter.window)
            elements = [adapter.commit]
            equal = adapter.equal
            revalidate = reader.revalidate
            armed = False
            def arm_after_validation(*args, **kwargs):
                nonlocal armed
                result = revalidate(*args, **kwargs)
                armed = True
                return result
            reader.revalidate = arm_after_validation
            def changing_equal(left, right):
                if armed and left is adapter.commit and right is adapter.commit:
                    if change == 'disabled':
                        adapter.commit['AXEnabled'] = False
                    elif change == 'filename':
                        adapter.name['AXValue'] = 'changed.rtf'
                    else:
                        elements[:] = [object()]
                return equal(left, right)
            adapter.equal = changing_equal
            with self.subTest(change=change), self.assertRaises(SavePanelUnavailable):
                reader.bind_commit(adapter.window, witness, obs_id='o-owned', elements=elements)

    def test_commit_binding_rejects_invalid_observation(self):
        witness = self.reader.read(self.adapter.window)
        for obs_id, elements in (('', []), (' ', []), (1, []), ('x' * 129, []),
                                 ('o-owned', iter([self.adapter.commit])),
                                 ('o-owned', [self.adapter.commit] * 5001)):
            with self.subTest(obs_id=obs_id), self.assertRaises(SavePanelUnavailable):
                self.reader.bind_commit(self.adapter.window, witness,
                                        obs_id=obs_id, elements=elements)

    def test_invalid_filename_and_witness(self):
        for filename in ('', '.', '..', '../escape', 'bad\x00name',
                         'bad\\name', 'bad\tname', 'bad\nname', 'bad\x7fname', 4):
            self.adapter.name['AXValue'] = filename
            with self.subTest(filename=filename), self.assertRaises(SavePanelUnavailable):
                self.reader.read(self.adapter.window)
        with self.assertRaises(SavePanelUnavailable):
            self.reader.revalidate(self.adapter.window, object())


if __name__ == '__main__':
    unittest.main()
