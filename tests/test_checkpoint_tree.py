import unittest
from tacto.checkpoint_tree import equivalent,corresponding_index,snapshot


class CheckpointTreeTests(unittest.TestCase):
    def test_bare_wrapper_and_volatile_indexes_do_not_change_named_hierarchy(self):
        before='0 Window Title: Owned\n  1 Group\n    2 Button Identifier: do, Title: Run\n  3 StaticText Identifier: state, Value: review'
        after='7 Window Title: Owned\n  8 Button Identifier: do, Title: Run\n  9 StaticText Identifier: state, Value: review'
        self.assertTrue(equivalent(before,after));self.assertEqual(8,corresponding_index(before,after,2))
        with self.assertRaises(ValueError):corresponding_index(before,after,1)
    def test_values_flags_groups_modals_and_non_node_lines_remain_checked(self):
        base='0 Window Title: Owned\n  1 Button Identifier: do, Title: Run\n  2 StaticText Value: review'
        for changed in (base.replace('review','done'),base.replace('Run','Delete'),base+'\n3 Window Title: Modal',
                        base.replace('Run','Run [disabled]'),base+'\n  4 Group Identifier: important',
                        base+'\n  4 Group Subrole: AXSomething',base+'\n  4 Group [focused]',base+'\ntruncated'):
            self.assertFalse(equivalent(base,changed))
    def test_named_ancestor_and_order_changes_are_not_normalized(self):
        before='0 Window Title: Owned\n  1 Group Identifier: scope\n    2 Button Title: Run\n  3 Button Title: Other'
        for changed in (before.replace('    2','  2'),before.replace('scope','new'),
                        '0 Window Title: Owned\n  1 Button Title: Other\n  2 Group Identifier: scope\n    3 Button Title: Run'):
            self.assertFalse(equivalent(before,changed))
    def test_bare_group_changes_are_bounded_and_hidden_markers_are_preserved(self):
        base='0 Window Title: Owned\n  1 Button Title: Run'
        self.assertTrue(equivalent(base,base+'\n  2 Group\n  3 Group'))
        self.assertFalse(equivalent(base,base+'\n  2 Group\n  3 Group\n  4 Group'))
        for marker in (' …',' redacted',' [selected]',' Secondary Actions: Show'):
            self.assertFalse(equivalent(base,base+'\n  2 Group'+marker))

    def test_preserved_ancestor_paths_are_part_of_node_identity(self):
        before='0 Window Title: Owned\n  1 Group Identifier: A\n    2 Button Title: Run\n  3 Group Identifier: B'
        after='0 Window Title: Owned\n  1 Group Identifier: A\n  2 Group Identifier: B\n    3 Button Title: Run'
        self.assertFalse(equivalent(before,after))

    def test_duplicate_indices_are_rejected(self):
        with self.assertRaises(ValueError):snapshot('0 Window\n  1 Group\n  1 Button Title: Run')

if __name__=='__main__':unittest.main()
