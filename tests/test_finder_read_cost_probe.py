"""Closed measurement metadata and private refusal reports, no GUI input."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from examples.probes import signed_finder_read_cost as probe

class FinderReadCostProbeTests(unittest.TestCase):
    def test_cpu_mode_requires_explicit_preflight_before_report_or_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            for options in ({'collect_cpu':True},{'collect_cpu':'true','include_preflight':True}):
                with self.assertRaises(ValueError):probe.main(None,path,**options)
                self.assertFalse(path.exists())

    def test_preflight_keeps_closed_failure_counters_and_discards_ui_text(self):
        value={'type':'error','reason':'preflight_deadline','read_only':True,'authorizes_input':False,
               'elapsed_ms':15000,'scoped_ax_operations':9000,'ax_operation_elapsed_ms':12000,
               'context_elapsed_ms':300,'detail':'PRIVATE_WINDOW_TITLE'}
        summary=probe.preflight_summary(value)
        self.assertEqual('preflight_deadline',summary['reason'])
        self.assertNotIn('PRIVATE',json.dumps(summary))
        self.assertIsNotNone(probe.preflight_for_strategy(value,'batch'))
        self.assertIsNone(probe.preflight_for_strategy(value,'single'))
        self.assertIsNone(probe.preflight_for_strategy({**value,'read_strategy':'batch'},'single'))
        self.assertIsNotNone(probe.preflight_for_strategy({**value,'read_strategy':'single'},'single'))
        for changes in ({'authorizes_input':True},{'elapsed_ms':True},{'scoped_ax_operations':-1},
                        {'reason':{}},{'reason':'PRIVATE'},{'read_strategy':True},{'read_strategy':'PRIVATE'},
                        {'readiness_progress':{'stage':'PRIVATE'}}):
            self.assertIsNone(probe.preflight_summary({**value,**changes}))

        row={'role':'AXCell','nodes':3,'scoped_ax_operations':3,'ax_operation_us':2000,
             'context_us':200,'wall_us':2300}
        self.assertIsNotNone(probe.preflight_summary({**value,'readiness_role_costs':[row]}))
        for strategy in ('batch','single'):
            self.assertEqual(strategy,probe.preflight_summary({**value,'read_strategy':strategy})['read_strategy'])
        attribute={'attribute':'AXChildren','scoped_ax_operations':3,'ax_operation_us':2000}
        self.assertIsNotNone(probe.preflight_summary({**value,'readiness_attribute_costs':[attribute]}))
        for rows in ([attribute,attribute],[{**attribute,'attribute':'PRIVATE'}],
                     [{**attribute,'scoped_ax_operations':True}],[{**attribute,'scoped_ax_operations':9001}],
                     [{**attribute,'ax_operation_us':12001000}],[{**attribute,'extra':'PRIVATE'}]):
            self.assertIsNone(probe.preflight_summary({**value,'readiness_attribute_costs':rows}))
        for rows in ([{**row,'role':'PRIVATE'}],[row,row],[{**row,'nodes':True}],
                     [{**row,'ax_operation_us':12001000}], [{**row,'extra':'PRIVATE'}]):
            self.assertIsNone(probe.preflight_summary({**value,'readiness_role_costs':rows}))

    def test_identity_counts_require_exact_shape_and_progress_consistency(self):
        value={'type':'error','reason':'preflight_unavailable','read_only':True,'authorizes_input':False,
               'elapsed_ms':1,'scoped_ax_operations':4,'ax_operation_elapsed_ms':1,'context_elapsed_ms':0,
               'readiness_progress':{'pass':1,'windows':2,'completed':0,'index':0,'target':False,
                                     'nodes':4,'total_nodes':4,'stage':'tree'}}
        counts={'visits':4,'unique_nodes':3,'repeated_nodes':1}
        self.assertEqual(counts,probe.preflight_summary({**value,'readiness_identity_counts':counts})['readiness_identity_counts'])
        for invalid in (None,{'visits':4}, {**counts,'visits':True},{**counts,'repeated_nodes':-1},
                        {**counts,'unique_nodes':2},{**counts,'visits':5,'unique_nodes':4},
                        {**counts,'extra':'PRIVATE'}):
            self.assertIsNone(probe.preflight_summary({**value,'readiness_identity_counts':invalid}))
        no_progress=dict(value);del no_progress['readiness_progress']
        self.assertIsNone(probe.preflight_summary({**no_progress,'readiness_identity_counts':counts}))

    def rows(self):
        attrs=[{'ax_error':0,'kind':'string'},{'ax_error':-25205,'kind':'nil'},
               {'ax_error':0,'kind':'array','count':300}]
        return [{'ordinal':0,'round':i,'batch_first':i==0,'batch_ax_us':20,'single_ax_us':60,
                 'batch_transport':'ok','batch_attributes':copy.deepcopy(attrs),
                 'single_attributes':copy.deepcopy(attrs)} for i in range(2)]

    def test_complete_and_partial_measurements_do_not_admit_ui_values(self):
        rows=self.rows();self.assertTrue(probe.rows_valid(rows,1))
        self.assertTrue(probe.rows_valid([{'ordinal':0,'round':0,'batch_first':True}],1,partial=True))
        self.assertFalse(probe.rows_valid([{'ordinal':0,'round':0,'batch_first':True}],1))
        for key,value in [('ordinal',True),('round',3),('batch_first',1),('batch_ax_us',-1),
                          ('single_ax_us',True),('batch_transport','PRIVATE'),('filename','PRIVATE')]:
            changed=self.rows();changed[0][key]=value
            self.assertFalse(probe.rows_valid(changed,1),key)
        changed=self.rows();changed[0]['single_attributes'][0]['value']='PRIVATE'
        self.assertFalse(probe.rows_valid(changed,1))
        changed=self.rows();changed[0]['single_attributes'][2]['count']=True
        self.assertFalse(probe.rows_valid(changed,1))
        self.assertFalse(probe.rows_valid(rows*17,1,partial=True))
        self.assertFalse(probe.rows_valid(None,1,partial=True))
        changed=self.rows()
        for row in changed:row['batch_transport']='unsupported';row['batch_attributes']=None
        self.assertTrue(probe.rows_valid(changed,1))

    def test_report_is_reserved_before_backend_or_native_work(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'existing';target.write_text('KEEP')
            link=Path(directory)/'link';link.symlink_to(target)
            for output in (target,link):
                with patch('tacto.helper.InheritedHelperDriver.available') as available,self.assertRaises(FileExistsError):
                    probe.main(None,output)
                available.assert_not_called();self.assertEqual(target.read_text(),'KEEP')

    def test_unsigned_backend_refusal_is_private_and_cannot_observe(self):
        class Foreign:
            def observe(self,*args):raise AssertionError('must not observe')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report'
            with self.assertRaises(RuntimeError):probe.main(Foreign(),path)
            result=json.loads(path.read_text());self.assertFalse(result['passed'])
            self.assertFalse(result['all_dependencies_frozen']);self.assertEqual(path.stat().st_mode&0o777,0o600)
