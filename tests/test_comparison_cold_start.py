import subprocess
from pathlib import Path
import unittest
from unittest.mock import patch
from tests.test_product_comparison_provenance import comparison
from tacto.comparison_tokens import reported_turn_tokens


class ColdStartTests(unittest.TestCase):
    def test_both_arms_launch_in_distinct_empty_ephemeral_workspaces(self):
        directories=[]
        def execute(argv,prompt,timeout,records,*,cwd):
            path=Path(cwd)
            self.assertTrue(path.is_dir())
            self.assertEqual([],list(path.iterdir()))
            self.assertEqual(cwd,argv[argv.index('-C')+1])
            self.assertIn('--ephemeral',argv)
            self.assertNotIn('resume',argv)
            self.assertNotIn('fork',argv)
            self.assertEqual(0o700,path.stat().st_mode & 0o777)
            directories.append(cwd)
            return subprocess.CompletedProcess(argv,0,'','')
        with patch.object(comparison,'_COLD_START',True),patch('tacto.codex_result._run_codex',side_effect=execute):
            for arm in ('nlc','sky'):
                result=comparison._run_agent(['codex','exec','-C','/tmp','-'],comparison._arm_prompt('Read the same app.',arm),9,[])
                self.assertTrue(result.comparison_context['workspace_empty_before_launch'])
                self.assertFalse(result.comparison_context['prior_conversation_supplied_by_harness'])
                self.assertFalse(result.comparison_context['server_cache_cleared_verified'])
        self.assertEqual(2,len(set(directories)))
        self.assertTrue(all(not Path(path).exists() for path in directories))

    def test_history_resume_is_refused_before_spawn(self):
        with patch.object(comparison,'_COLD_START',True),patch('tacto.codex_result._run_codex') as run:
            for argv in [['codex','exec','resume','--last'],['codex','exec','fork','id']]:
                with self.assertRaises(ValueError):comparison._run_agent(argv,'task',2,[])
            run.assert_not_called()

    def test_failed_execution_keeps_context_evidence(self):
        error=subprocess.TimeoutExpired('owned',1)
        with patch.object(comparison,'_COLD_START',True),patch('tacto.codex_result._run_codex',side_effect=error):
            with self.assertRaises(subprocess.TimeoutExpired):comparison._run_agent(['codex','exec','-'],'task',1,[])
        self.assertTrue(error.comparison_context['ephemeral_requested'])
        self.assertFalse(Path(error.comparison_context['workspace']).exists())

    def test_cached_and_uncached_usage_are_separate_without_price_claim(self):
        result=reported_turn_tokens([{'input_tokens':100,'cached_input_tokens':60,'output_tokens':7}],stream_complete=True)
        self.assertEqual((100,60,40,7,107),tuple(result[k] for k in ('input_tokens','cached_input_tokens','uncached_input_tokens','output_tokens','total_tokens')))
        self.assertIsNone(result['cost_usd']);self.assertFalse(result['billing_verified'])
        missing=reported_turn_tokens([{'input_tokens':100,'output_tokens':7}],stream_complete=True)
        self.assertIsNone(missing['cached_input_tokens']);self.assertIsNone(missing['uncached_input_tokens'])

    def test_missing_partial_invalid_and_multiple_turns_are_not_zero_cost_success(self):
        self.assertEqual('missing',reported_turn_tokens([],stream_complete=False)['status'])
        event={'input_tokens':100,'cached_input_tokens':20,'output_tokens':2}
        self.assertEqual('partial_reported',reported_turn_tokens([event],stream_complete=False)['status'])
        self.assertIsNone(reported_turn_tokens([event,event],stream_complete=True)['total_tokens'])
        for bad in [{'input_tokens':True,'output_tokens':1},{'input_tokens':1,'output_tokens':1,'cached_input_tokens':2}]:
            self.assertEqual('invalid',reported_turn_tokens([bad],stream_complete=True)['status'])
