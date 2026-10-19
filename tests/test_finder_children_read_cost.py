"""Diagnostic refuses before app access and never ranks unmatched replies."""
import builtins
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples.probes import finder_children_read_cost as probe


class FinderChildrenReadCostTests(unittest.TestCase):
    def test_unmatched_arrays_and_attribute_errors_cannot_improve_medians(self):
        rows = [{'role':'AXCell', 'same_ordered_children':True,
                 'value_us':100, 'range_us':300, 'same_attribute_replies':True,
                 'batch_us':150, 'names_batch_us':350},
                {'role':'AXCell', 'same_ordered_children':False,
                 'value_us':100000, 'range_us':0, 'same_attribute_replies':False,
                 'batch_us':100000, 'names_batch_us':0},
                {'role':'AXButton', 'same_ordered_children':False,
                 'value_us':0, 'range_us':0, 'same_attribute_replies':False,
                 'batch_us':0, 'names_batch_us':0}]
        summary = probe.summarize(rows)
        self.assertEqual({'rows':2,'comparable_rows':1,'value_median_us':100,
                          'range_median_us':300},summary['AXCell'])
        self.assertIsNone(summary['AXButton']['value_median_us'])
        attributes = probe.summarize_attributes(rows)
        self.assertEqual(150,attributes['AXCell']['batch_median_us'])
        self.assertEqual(350,attributes['AXCell']['names_batch_median_us'])
        self.assertIsNone(attributes['AXButton']['names_batch_median_us'])

    def test_existing_report_and_symlink_refuse_before_ax_import(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'existing';target.write_text('KEEP')
            link = Path(directory)/'link';link.symlink_to(target)
            original = builtins.__import__
            def importing(name,*args,**kwargs):
                if name == 'ApplicationServices':self.fail('must reserve report first')
                return original(name,*args,**kwargs)
            with patch('builtins.__import__',side_effect=importing):
                for path in (target,link):
                    with self.assertRaises(FileExistsError):probe.main(path)
            self.assertEqual('KEEP',target.read_text())

    def test_refusal_report_is_private_and_does_not_publish_exception_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'report';original = builtins.__import__
            def importing(name,*args,**kwargs):
                if name == 'ApplicationServices':raise RuntimeError('PRIVATE_WINDOW_TITLE')
                return original(name,*args,**kwargs)
            with patch('builtins.__import__',side_effect=importing):result = probe.main(path)
            self.assertFalse(result['passed']);self.assertFalse(result['authorizes_input'])
            self.assertFalse(result['readiness_verified']);self.assertTrue(result['source_subset_stable'])
            self.assertEqual('RuntimeError',result['error_class'])
            self.assertEqual(0o600,path.stat().st_mode & 0o777)
            self.assertNotIn('PRIVATE_WINDOW_TITLE',path.read_text())
            self.assertEqual([],json.loads(path.read_text())['rows'])
