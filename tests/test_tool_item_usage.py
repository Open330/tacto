import unittest
from tacto.tool_item_usage import ToolItemUsage


def event(kind,identity):return {'type':kind,'item':{'id':identity}}


class ToolItemUsageTests(unittest.TestCase):
    def test_start_update_completion_and_duplicate_completion_are_one_item(self):
        count=ToolItemUsage()
        for kind in ('item.started','item.updated','item.completed','item.completed'):
            count.record(event(kind,'one'),'shell:tacto')
        result=count.snapshot(stream_complete=True)
        self.assertEqual({'shell:tacto':1},result['observed_unique_items'])
        self.assertEqual({'shell:tacto':1},result['completed_unique_items'])
        self.assertTrue(result['complete_unique_counts'])

    def test_timeout_and_missing_ids_do_not_invent_complete_counts(self):
        count=ToolItemUsage()
        count.record(event('item.started','one'),'node_repl:js')
        count.record(event('item.completed',None),'node_repl:js')
        result=count.snapshot(stream_complete=False)
        self.assertEqual({'node_repl:js':1},result['incomplete_unique_items'])
        self.assertEqual({},result['completed_unique_items'])
        self.assertEqual(1,result['unidentified_events'])
        self.assertFalse(result['complete_unique_counts'])

    def test_reused_identity_with_changed_tool_is_ambiguous(self):
        count=ToolItemUsage()
        count.record(event('item.started','one'),'shell:tacto')
        count.record(event('item.completed','one'),'shell:other')
        result=count.snapshot(stream_complete=True)
        self.assertEqual({},result['observed_unique_items'])
        self.assertEqual(1,result['ambiguous_ids'])
        self.assertFalse(result['complete_unique_counts'])

    def test_completed_only_and_bounded_storage(self):
        count=ToolItemUsage(max_items=1)
        count.record(event('item.completed','one'),'shell:tacto')
        count.record(event('item.started','two'),'shell:tacto')
        result=count.snapshot(stream_complete=True)
        self.assertEqual({'shell:tacto':1},result['completed_unique_items'])
        self.assertTrue(result['item_limit_reached'])
        self.assertFalse(result['complete_unique_counts'])


class PrimarySummaryTests(unittest.TestCase):
    def test_consistent_samples_are_reported_without_permission_or_whole_run_claim(self):
        from tacto.process_responsibility import summarize_primary_responsibility
        sample={'status':'observed','pid':42,'responsible_pid':42,
                'process_path':'/host/codex','responsible_path':'/host/codex'}
        result=summarize_primary_responsibility([{'samples':[sample,sample],
            'sample_limit_reached':True,'observer_stopped':True}])
        self.assertTrue(result['consistent_observed_samples'])
        self.assertEqual(2,result['observed_samples'])
        for key in ('entire_execution_verified','tcc_permission_verified','descendant_responsibility_verified'):
            self.assertFalse(result[key])

    def test_missing_changed_or_malformed_samples_are_not_consistent(self):
        from tacto.process_responsibility import summarize_primary_responsibility
        self.assertIsNone(summarize_primary_responsibility([])['observer_stopped'])
        sample={'status':'observed','pid':42,'responsible_pid':42,
                'process_path':'/host/codex','responsible_path':'/host/codex'}
        for samples in ([],[sample,{'status':'unavailable'}],[sample,{**sample,'responsible_pid':43}],
                        [sample,{**sample,'pid':True}]):
            result=summarize_primary_responsibility([{'samples':samples,'observer_stopped':True}])
            self.assertFalse(result['consistent_observed_samples'])


if __name__=='__main__':unittest.main()
