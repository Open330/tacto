"""Receipt identity includes the observation used to resolve an action index."""
import unittest
from nlc.artifact_receipt import FreshFileReceipt, ArtifactReceiptError
from nlc.commit_evidence import make_event, CommitEvidence, ArtifactEvidence, evidence_error


class CommitObservationBindingTests(unittest.TestCase):
    def test_changing_or_removing_observation_invalidates_existing_receipt(self):
        event = make_event({'name': 'click', 'element_index': 2},
                           {'write_persistent'}, 'fixture', obs_id='current')
        event['commit_evidence'] = CommitEvidence(event['event_id'], event['action_digest'],
            ArtifactEvidence('receipt', '/tmp/example', '0' * 64))
        self.assertIsNone(evidence_error(event))
        FreshFileReceipt._event(event)
        for replacement in ('old', None, '', False, 1):
            with self.subTest(replacement=replacement):
                changed = {**event, 'obs_id': replacement}
                self.assertIsNotNone(evidence_error(changed))
                with self.assertRaises(ArtifactReceiptError):
                    FreshFileReceipt._event(changed)
        removed = dict(event)
        del removed['obs_id']
        self.assertEqual('commit_action_mismatch', evidence_error(removed))
        with self.assertRaises(ArtifactReceiptError):
            FreshFileReceipt._event(removed)

    def test_same_index_in_new_observation_is_a_different_action_binding(self):
        first = make_event({'name': 'click', 'element_index': 2},
                           {'write_persistent'}, 'fixture', obs_id='first')
        second = make_event(first['action'], first['effects'], 'fixture', obs_id='second')
        self.assertNotEqual(first['action_digest'], second['action_digest'])

    def test_standalone_non_ui_host_events_remain_supported(self):
        event = make_event({'name': 'save'}, {'write_persistent'}, 'host')
        self.assertNotIn('obs_id', event)
        FreshFileReceipt._event(event)
