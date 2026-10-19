import json
import unittest
from examples.benchmark_agent import usage_from_stream, codex_turn_delta
from examples.repeat_benchmark import jobs, sum_usage, reuse_schedule
from examples.summarize_repeat_benchmark import measured_batch, savings
from examples.summarize_reuse_ratios import summarize

class RepeatBenchmarkTests(unittest.TestCase):
    def test_claude_input_components_are_not_mistaken_for_codex_total(self):
        row={'type':'result','is_error':False,'usage':{'input_tokens':10,'cache_read_input_tokens':50,
             'cache_creation_input_tokens':20,'output_tokens':7},'modelUsage':{'observed-model':{}}}
        result=usage_from_stream('claude',json.dumps(row))
        self.assertEqual(80,result['total_input_tokens']);self.assertEqual(30,result['new_input_tokens'])
        self.assertEqual(['observed-model'],result['observed_models'])
    def test_codex_cached_input_is_part_of_total(self):
        row={'type':'turn.completed','usage':{'input_tokens':80,'cached_input_tokens':50,'output_tokens':7}}
        result=usage_from_stream('codex',json.dumps(row))
        self.assertEqual(80,result['total_input_tokens']);self.assertEqual(30,result['new_input_tokens'])
        self.assertIsNone(result['cache_creation_tokens'])
    def test_missing_usage_stays_unknown_not_free(self):
        self.assertEqual('missing_or_ambiguous',usage_from_stream('claude','')['status'])
        self.assertIsNone(sum_usage([{'usage':{'status':'missing_or_ambiguous'}}])['total_input_tokens'])
    def test_same_session_cumulative_usage_is_differenced_not_added_twice(self):
        prior={'status':'reported','total_input_tokens':66055,'cache_read_tokens':60288,
               'new_input_tokens':5767,'output_tokens':465}
        current={'status':'reported','total_input_tokens':83645,'cache_read_tokens':77440,
                 'new_input_tokens':6205,'output_tokens':470}
        delta=codex_turn_delta(current,prior)
        self.assertEqual(17590,delta['total_input_tokens']);self.assertEqual(5,delta['output_tokens'])
        self.assertEqual(83645,sum_usage([{'usage':prior},{'usage':delta}])['total_input_tokens'])
    def test_counter_reset_is_unknown_not_zero_or_negative_cost(self):
        keys=('total_input_tokens','cache_read_tokens','new_input_tokens','output_tokens')
        result=codex_turn_delta(dict.fromkeys(keys,1),dict.fromkeys(keys,2))
        self.assertEqual('counter_scope_unverified',result['status'])
        self.assertIsNone(result['total_input_tokens'])
    def test_ten_distinct_jobs_include_both_bonus_branches(self):
        rows=jobs();self.assertEqual(10,len(rows));self.assertEqual(10,len({r['job'] for r in rows}))
        self.assertEqual({True,False},{r['bonus'] for r in rows})
        for r in rows:self.assertEqual(int(r['result']),int(r['amount'])*int(r['factor'])+(5 if r['bonus'] else 0))
    def test_summary_counts_authoring_once_and_repair_and_failed_attempt(self):
        result={'arm':'codex-nlc-reuse','passed':True,'rows':[
            {'seconds':10,'oracle':{'passed':True,'single_commit':True}} for _ in range(10)],
            'agent_runs':[{'seconds':10},{'seconds':7}],
            'usage':{'agent_invocations':2,'unknown_usage_records':0,'total_input_tokens':100,
                     'new_input_tokens':20,'cache_read_tokens':80,'output_tokens':10}}
        result['rows'][6]['recovery']={'seconds':3}
        self.assertEqual(110,measured_batch(result)['seconds'])
        result['rows'][6]['oracle']['single_commit']=False
        with self.assertRaises(ValueError):measured_batch(result)
    def test_historical_claude_summary_includes_cache_creation_without_mutating_raw(self):
        from copy import deepcopy
        result={'arm':'claude-nlc-reuse','passed':True,'rows':[
            {'seconds':1,'oracle':{'passed':True,'single_commit':True}} for _ in range(10)],
            'agent_runs':[], 'usage':{'agent_invocations':2,'unknown_usage_records':0,
                'total_input_tokens':238913,'cache_read_tokens':216996,'cache_creation_tokens':21893,
                'new_input_tokens':24,'output_tokens':4685}}
        original=deepcopy(result)
        summary=measured_batch(result)
        self.assertEqual(21917,summary['new_input_tokens'])
        self.assertEqual(24,summary['reported_new_input_tokens'])
        self.assertEqual(original,result)

    def test_reuse_ratios_count_runtime_jobs_not_seed_authoring(self):
        for percent in (0,30,70,100):
            selected=reuse_schedule(percent)
            self.assertEqual(percent//10,len(selected))
            self.assertTrue(selected<=set(range(1,11)))
        self.assertIn(9,reuse_schedule(30))
        self.assertIn(7,reuse_schedule(70))
        with self.assertRaises(ValueError):reuse_schedule(90)
    def test_mixed_summary_includes_seed_repair_and_agent_jobs_once(self):
        rows=[{'seconds':2,'oracle':{'passed':True,'single_commit':True}} for _ in range(10)]
        rows[0].update(job={'job':'T01'},seconds=9)
        rows[6]['recovery']={'seconds':3}
        result={'kind':'mixed-reuse','arm':'codex-nlc-ratio70','passed':True,'rows':rows,
                'agent_runs':[{'seconds':50},rows[0],{'seconds':7}],
                'usage':{'agent_invocations':3,'unknown_usage_records':0,'total_input_tokens':100,
                         'new_input_tokens':20,'cache_read_tokens':80,'output_tokens':10}}
        self.assertEqual(87,measured_batch(result)['seconds'])
    def test_ratio_summary_rejects_misrouted_and_failed_agent_jobs(self):
        rows=[{'seconds':2,'exit':0,'mode':'agent','job':{'job':f'T{i:02d}'},
               'oracle':{'passed':True,'single_commit':True,'active':False}} for i in range(1,11)]
        for r in rows:r['usage']={'status':'reported','total_input_tokens':10,'new_input_tokens':2,
                                  'cache_read_tokens':8,'cache_creation_tokens':None,'output_tokens':1}
        result={'kind':'mixed-reuse','reuse_percent':0,'arm':'codex-nlc-ratio0','passed':True,
                'rows':rows,'agent_runs':rows,'usage':sum_usage(rows),'setup':None}
        self.assertEqual(10,summarize(result)['agent_jobs'])
        rows[0]['mode']='flow'
        with self.assertRaises(ValueError):summarize(result)
        rows[0]['mode']='agent';rows[0]['exit']=1
        with self.assertRaises(ValueError):summarize(result)
    def test_failed_auth_and_partial_batches_cannot_create_a_saving_rate(self):
        with self.assertRaises(ValueError):measured_batch({'passed':False,'rows':[]})

if __name__=='__main__':unittest.main()
