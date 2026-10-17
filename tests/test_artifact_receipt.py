import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tacto.artifact_receipt import ArtifactReceiptError, FreshFileReceipt
from tacto.commit_evidence import evidence_error, make_event


class Clock:
    def __init__(self):
        self.now = 10.0
        self.on_sleep = lambda: None

    def __call__(self):
        return self.now

    def sleep(self, duration):
        self.now += duration
        self.on_sleep()


@unittest.skipUnless(hasattr(os, 'O_NOFOLLOW'), 'POSIX file-descriptor boundary required')
class ArtifactReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve() / 'private'
        self.directory.mkdir(mode=0o700)
        self.clock = Clock()
        self.event = make_event({'name': 'click', 'element_index': 4},
                                {'write_persistent'}, 'com.apple.TextEdit')
        self.path = self.directory / 'fresh.txt'

    def adapter(self, **kwargs):
        return FreshFileReceipt(self.directory, 'fresh.txt', '안녕하세요\n',
                                clock=self.clock, sleep=self.clock.sleep, **kwargs)

    def write(self):
        self.path.write_bytes('안녕하세요\n'.encode())

    def test_fresh_actual_file_returns_event_bound_exact_readback(self):
        adapter = self.adapter()
        context = adapter.prepare_commit(self.event)
        self.write()
        proof = adapter.after_commit(self.event, {'type': 'acted'}, context)
        self.assertIsNone(evidence_error({**self.event, 'commit_evidence': proof}, adapter.commit_expected))
        self.assertEqual(proof.evidence.path, str(self.path))
        self.assertEqual(proof.evidence.sha256, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_existing_file_and_dangling_symlink_refuse_before_dispatch(self):
        self.write()
        with self.assertRaises(ArtifactReceiptError):
            self.adapter().prepare_commit(self.event)
        self.path.unlink()
        self.path.symlink_to(self.directory / 'missing')
        with self.assertRaises(ArtifactReceiptError):
            self.adapter().prepare_commit(self.event)

    def test_public_directory_or_symlink_directory_is_rejected(self):
        self.directory.chmod(0o755)
        with self.assertRaises(ArtifactReceiptError):
            self.adapter().prepare_commit(self.event)
        self.directory.chmod(0o700)
        link = self.directory.parent / 'linked'
        link.symlink_to(self.directory, target_is_directory=True)
        adapter = FreshFileReceipt(link, 'fresh.txt', 'hello')
        with self.assertRaises((ArtifactReceiptError, OSError)):
            adapter.prepare_commit(self.event)

    def test_changed_parent_identity_cannot_supply_replacement_artifact(self):
        adapter = self.adapter()
        context = adapter.prepare_commit(self.event)
        self.directory.rename(self.directory.parent / 'original')
        self.directory.mkdir(mode=0o700)
        self.write()
        with self.assertRaises(ArtifactReceiptError):
            adapter.after_commit(self.event, {'type': 'acted'}, context)

    def test_wrong_bytes_symlink_and_hardlink_never_produce_receipt(self):
        for mode in ('wrong', 'symlink', 'hardlink'):
            with self.subTest(mode=mode):
                adapter = self.adapter()
                context = adapter.prepare_commit(self.event)
                if mode == 'wrong':
                    self.path.write_bytes(b'x' * len('안녕하세요\n'.encode()))
                else:
                    other = self.directory / 'other.txt'
                    other.write_bytes('안녕하세요\n'.encode())
                    if mode == 'symlink':
                        self.path.symlink_to(other)
                    else:
                        os.link(other, self.path)
                with self.assertRaises((ArtifactReceiptError, OSError)):
                    adapter.after_commit(self.event, {'type': 'acted'}, context)
                self.path.unlink()
                other = self.directory / 'other.txt'
                if other.exists():
                    other.unlink()

    def test_noop_and_zero_budget_do_not_produce_receipt(self):
        adapter = self.adapter(timeout_seconds=0.05, poll_interval=0.02)
        context = adapter.prepare_commit(self.event)
        self.assertIsNone(adapter.after_commit(self.event, {'type': 'acted'}, context))
        self.assertAlmostEqual(self.clock.now, 10.05)
        adapter = self.adapter(timeout_seconds=0)
        context = adapter.prepare_commit(self.event)
        self.write()
        self.assertIsNone(adapter.after_commit(self.event, {'type': 'acted'}, context))

    def test_delayed_creation_is_read_within_bounded_poll(self):
        adapter = self.adapter(timeout_seconds=0.1)
        context = adapter.prepare_commit(self.event)
        self.clock.on_sleep = self.write
        self.assertIsNotNone(adapter.after_commit(self.event, {'type': 'acted'}, context))
        self.assertLess(self.clock.now, 10.1)

    def test_readback_finishing_after_budget_cannot_issue_receipt(self):
        adapter = self.adapter(timeout_seconds=0.1)
        context = adapter.prepare_commit(self.event)
        self.write()
        original = adapter._read_file
        def slow_read(descriptor):
            result = original(descriptor)
            self.clock.now += 0.2
            return result
        with patch.object(adapter, '_read_file', side_effect=slow_read):
            self.assertIsNone(adapter.after_commit(self.event, {'type': 'acted'}, context))

    def test_replay_confirms_one_real_filesystem_write_with_adapter(self):
        from tacto.contract import ContextBinding, Resource, SafetyContract, issue_approval
        from tacto.flow import Flow, Selector, Step, replay
        adapter = self.adapter()
        path = self.path
        writes = []
        class Driver:
            def identity(self, target):
                return target
            def observe(self, *args, **kwargs):
                return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Button Title: Save'}
            def act(self, *args, **kwargs):
                path.write_bytes('안녕하세요\n'.encode())
                writes.append(path)
                return {'type': 'acted'}
        contract = SafetyContract(effects=['write_persistent'], reversibility='recoverable',
                                  resource=Resource(scope='item', object_identity='fresh.txt'))
        binding = ContextBinding(bundle_id='app', role='Button', title='Save', operation='click')
        flow = Flow('save', 'app', [Step('click', Selector(role='Button', title='Save'),
                                        safety=contract, binding=binding)])
        result = replay(Driver(), flow, approvals={0: issue_approval(binding, contract)},
                        prepare_commit=adapter.prepare_commit, after_commit=adapter.after_commit,
                        commit_expected=adapter.commit_expected)
        self.assertTrue(result.ok, result.log)
        self.assertEqual(len(writes), 1)
        self.assertEqual(len(result.commit_evidence), 1)
        self.assertEqual(result.events[0]['state'], 'confirmed')
        self.assertEqual(path.read_bytes(), '안녕하세요\n'.encode())

    def test_file_replaced_after_read_is_rejected_even_with_identical_bytes(self):
        adapter = self.adapter()
        context = adapter.prepare_commit(self.event)
        self.write()
        original = adapter._read_file
        def replacement(descriptor):
            result = original(descriptor)
            replacement = self.directory / 'replacement'
            replacement.write_bytes(self.path.read_bytes())
            replacement.replace(self.path)
            return result
        with patch.object(adapter, '_read_file', side_effect=replacement):
            with self.assertRaises(ArtifactReceiptError):
                adapter.after_commit(self.event, {'type': 'acted'}, context)

    def test_duplicate_prepare_and_duplicate_completion_are_rejected(self):
        adapter = self.adapter()
        context = adapter.prepare_commit(self.event)
        with self.assertRaises(ArtifactReceiptError):
            adapter.prepare_commit(self.event)
        self.write()
        self.assertIsNotNone(adapter.after_commit(self.event, {'type': 'acted'}, context))
        with self.assertRaises(ArtifactReceiptError):
            adapter.after_commit(self.event, {'type': 'acted'}, context)

    def test_mutated_event_foreign_context_and_not_started_are_rejected(self):
        for kind in ('event', 'context', 'not_started'):
            with self.subTest(kind=kind):
                adapter = self.adapter()
                context = adapter.prepare_commit(self.event)
                event = self.event
                response = {'type': 'acted'}
                if kind == 'event':
                    event = make_event({'name': 'click', 'element_index': 5}, {'write_persistent'}, 'com.apple.TextEdit')
                elif kind == 'context':
                    context = object()
                else:
                    response = {'type':'error', 'effect':'not_started'}
                with self.assertRaises(ArtifactReceiptError):
                    adapter.after_commit(event, response, context)

    def test_expectation_copy_cannot_change_verified_path_or_content(self):
        adapter = self.adapter()
        copy = adapter.commit_expected
        copy['다시 읽은 내용'] = 'other'
        copy['저장한 이름'] = 'other.txt'
        context = adapter.prepare_commit(self.event)
        self.path.write_text('other')
        with self.assertRaises(ArtifactReceiptError):
            adapter.after_commit(self.event, {'type': 'acted'}, context)

    def test_relative_paths_bad_names_and_nonfinite_budgets_are_rejected(self):
        with self.assertRaises(ValueError):
            FreshFileReceipt('relative', 'file.txt', 'hello')
        for name in ('', '..', '../x', 'dir/x', 'dir\\x', '\0'):
            with self.assertRaises(ValueError):
                FreshFileReceipt(self.directory, name, 'hello')
        for timeout in (-1, True, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.adapter(timeout_seconds=timeout)


if __name__ == '__main__':
    unittest.main()
