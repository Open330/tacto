"""One reservation under contention and bounded refusal without eviction."""
from concurrent.futures import ThreadPoolExecutor
import unittest
from tacto.capture_ledger import CaptureLedger


class CaptureLedgerTests(unittest.TestCase):
    def test_capacity_never_evicts_a_spent_id(self):
        ledger=CaptureLedger(2)
        self.assertIsNone(ledger.reserve('first'))
        self.assertIsNone(ledger.reserve('second'))
        self.assertEqual('capture_ledger_full',ledger.reserve('third'))
        self.assertEqual('capture_already_used',ledger.reserve('first'))
        for invalid in (None, [], '', 'x'*129):
            self.assertEqual('invalid_capture_id',ledger.reserve(invalid))

    def test_concurrent_same_id_is_reserved_once(self):
        ledger=CaptureLedger()
        with ThreadPoolExecutor(max_workers=16) as pool:
            results=list(pool.map(ledger.reserve,['same']*100))
        self.assertEqual(1,results.count(None))
        self.assertEqual(99,results.count('capture_already_used'))
