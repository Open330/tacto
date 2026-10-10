"""A matching field subset must never become a full fair-comparison claim."""
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('pair_audit', ROOT / 'examples/audit_product_pairs.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PairAuditTests(unittest.TestCase):
    def setUp(self):
        files = {'owned_source.py':'a'*64}
        manifest = {'version':2, 'files':files, 'sha256':hashlib.sha256(
            json.dumps(files, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}
        base = {'task':'owned', 'rep':0, 'status':'passed', 'oracle_passed':True,
                'process_exit':0, 'layer_violation':False, 'shortcut':False,
                'comparison_eligible':True, 'locked':[False, False],
                'source_changed':False, 'source_manifest_before':manifest,
                'source_manifest_after':deepcopy(manifest),
                'initial_frontmost_bundle':'owned.sentinel', 'oracle_contract':'exact-v2',
                'runtime_policy':'background-v2','background_constraint':'owned constraint',
                'timeout_seconds':120}
        self.rows = [dict(deepcopy(base), arm=arm) for arm in ('nlc', 'sky')]

    def pair(self):
        return module.audit(self.rows)['pairs'][0]

    def test_matching_subset_is_not_a_complete_fair_comparison(self):
        pair = self.pair()
        self.assertTrue(pair['recorded_prerequisites_match'])
        self.assertFalse(pair['same_conditions_verified'])
        self.assertFalse(module.audit(self.rows)['comparison_rates_computed'])
        self.assertIn('independent_initial_task_state', pair['additional_evidence_required'])

    def test_frontmost_mismatch_excludes_pair_without_changing_outcomes(self):
        self.rows[1]['initial_frontmost_bundle'] = 'other.app'
        original = deepcopy(self.rows)
        pair = self.pair()
        self.assertIn('initial_frontmost_differs', pair['issues'])
        self.assertFalse(pair['recorded_prerequisites_match'])
        self.assertEqual([True, True], [r['oracle_passed'] for r in pair['observed_outcomes']])
        self.assertEqual(original, self.rows)

    def test_failed_arm_is_preserved_in_a_matching_prerequisite_pair(self):
        self.rows[0].update(status='failed', oracle_passed=False, process_exit=1)
        pair = self.pair()
        self.assertTrue(pair['recorded_prerequisites_match'])
        self.assertEqual('failed', pair['observed_outcomes'][0]['status'])

    def test_missing_fields_are_not_inferred_from_status(self):
        for key in ('locked', 'comparison_eligible', 'source_changed',
                    'source_manifest_before', 'initial_frontmost_bundle', 'oracle_contract',
                    'runtime_policy','background_constraint','timeout_seconds'):
            rows = deepcopy(self.rows)
            rows[0].pop(key)
            with self.subTest(key=key):
                self.assertFalse(module.audit(rows)['pairs'][0]['recorded_prerequisites_match'])

    def test_policy_and_timeout_mismatch_cannot_be_paired(self):
        for key, value, code in (('runtime_policy','other-v1','runtime_policy_differs'),
                                 ('background_constraint','altered constraint','runtime_constraint_differs'),
                                 ('timeout_seconds',60,'timeout_differs')):
            rows=deepcopy(self.rows)
            rows[1][key]=value
            result=module.audit(rows)['pairs'][0]
            self.assertIn(code,result['issues'])
            self.assertFalse(result['recorded_prerequisites_match'])
        for value in (True,0,-1,None,'120'):
            rows=deepcopy(self.rows);rows[0]['timeout_seconds']=value
            self.assertIn('timeout_not_recorded',module.audit(rows)['pairs'][0]['issues'])

    def test_duplicates_cannot_be_cherry_picked_and_unpaired_arm_is_reported(self):
        self.rows.append(deepcopy(self.rows[0]))
        self.assertIn('missing_or_duplicate_arm', self.pair()['issues'])
        self.rows = self.rows[:1]
        self.assertIn('missing_or_duplicate_arm', self.pair()['issues'])

    def test_changed_manifest_and_forged_hash_cannot_match(self):
        self.rows[0]['source_manifest_after']['files']['owned_source.py'] = 'b'*64
        self.assertIn('source_manifest_missing_or_invalid', self.pair()['issues'])
        for value in (None, {}, {'version':2, 'files':{'missing':None}, 'sha256':'a'*64}):
            self.assertFalse(module.manifest_valid(value))

    def test_false_success_and_malformed_rows_are_reported(self):
        self.rows[0]['oracle_passed'] = False
        self.assertIn('success_not_supported_by_oracle_and_process', self.pair()['issues'])
        report = module.audit([{}, None, dict(self.rows[0], rep=True)])
        self.assertEqual([0, 1, 2], report['invalid_row_indices'])


if __name__ == '__main__':
    unittest.main()
