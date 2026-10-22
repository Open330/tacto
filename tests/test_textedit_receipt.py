"""Contract fake only: does not launch TextEdit or use a native AX backend."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tacto.artifact_receipt import ArtifactReceiptError
from tacto.commit_evidence import make_event
from tacto.contract import ContextBinding, Resource, SafetyContract, issue_approval
from tacto.flow import Flow, Selector, Step, replay
from tacto.textedit_receipt import make_textedit_receipt, TextEditReceiptUnavailable


class SnapshotDriver:
    capabilities = {'evidence.document_snapshot', 'action.click.save_panel_guard'}

    def __init__(self, path, text='한글'):
        self.path = path
        self.snapshot = dict(type='document_snapshot', document_token='window-1',
                             text=text, text_complete=True, document_path=None,
                             source_format='rtf')
        self.calls = []
        self.dispatches = 0
        self.after_patch = {}
        self.panel = dict(panel_token='panel-1', document_token='window-1', directory=str(path.parent),
                          filename=path.name, format='rtf',
                          commit_control={'obs_id': 'o', 'enabled': True,
                                          'action': {'name': 'click', 'element_index': 0}})

    def document_snapshot(self, target, document_token=None):
        self.calls.append((target, document_token))
        if document_token is not None:
            self.snapshot['save_panel'] = self.panel
        return self.snapshot  # Deliberately mutable: host must copy its expectation.

    def identity(self, target):
        return target

    def observe(self, *args, **kwargs):
        return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Button Title: Save'}

    def act(self, *args, **kwargs):
        if (kwargs.get('save_panel_token') != self.panel['panel_token']
                or kwargs.get('approved') is not True):
            return {'type':'error', 'error':'save_panel_guard_unavailable', 'effect':'not_started'}
        self.dispatches += 1
        raw = b'{\\rtf1\\ansi\\uc1 \\u54620?\\u44544?}' if self.snapshot['text'] else b'{\\rtf1\\ansi }'
        self.path.write_bytes(raw)
        self.snapshot.update(document_path=str(self.path))
        self.snapshot.update(self.after_patch)
        return {'type': 'acted'}


class TextEditReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.directory = self.home / 'Downloads'
        self.directory.mkdir(mode=0o700)
        self.path = self.directory / '보고서.rtf'
        self.driver = SnapshotDriver(self.path)
        self.event = make_event({'name': 'click', 'element_index': 0},
                                {'write_persistent'}, 'com.apple.TextEdit', obs_id='o')

    def adapter(self, **kwargs):
        with patch('tacto.textedit_receipt.Path.home', return_value=self.home):
            return make_textedit_receipt(self.driver, target=kwargs.pop('target', 'com.apple.TextEdit'),
                                         directory=kwargs.pop('directory', str(self.directory)),
                                         filename=kwargs.pop('filename', self.path.name),
                                         timeout_seconds=kwargs.pop('timeout_seconds', 3.0), **kwargs)

    def run_save(self, adapter):
        contract = SafetyContract(effects=['write_persistent'], reversibility='recoverable',
                                  resource=Resource(scope='item', object_identity=self.path.name))
        binding = ContextBinding(bundle_id='com.apple.TextEdit', role='Button', title='Save', operation='click')
        flow = Flow('save', 'com.apple.TextEdit', [
            Step('click', Selector(role='Button', title='Save'), safety=contract, binding=binding)])
        return replay(self.driver, flow, approvals={0: issue_approval(binding, contract)},
                      prepare_commit=adapter.prepare_commit, after_commit=adapter.after_commit,
                      commit_expected=adapter.commit_expected)

    def test_capability_is_required_without_backend_fallback(self):
        for caps in (set(), {'evidence.document_snapshot'}, {'action.click.save_panel_guard'}):
            self.driver.capabilities = caps
            with self.assertRaises(TextEditReceiptUnavailable): self.adapter()
        self.assertEqual(self.driver.calls, [])

    def test_initial_snapshot_requires_complete_supported_exact_contract(self):
        original = dict(self.driver.snapshot)
        for update in ({'text_complete': False}, {'text': None}, {'document_token': ''},
                       {'source_format': 'rtfd'}, {'source_format': 'text'},
                       {'document_path': 'relative'}, {'type': 'error'}):
            with self.subTest(update=update):
                self.driver.snapshot = {**original, **update}
                with self.assertRaises(TextEditReceiptUnavailable):
                    self.adapter()

    def test_target_folder_and_filename_must_be_explicit_and_supported(self):
        for args in ({'target': 'other'}, {'directory': str(self.home)},
                     {'filename': '../report.rtf'}, {'filename': 'report.txt'}, {'filename': '.rtf'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.adapter(**args)
        self.assertEqual(self.driver.calls, [])

    def test_existing_file_is_never_dispatched_or_overwritten(self):
        self.path.write_bytes(b'original')
        with self.assertRaises(ArtifactReceiptError):
            self.adapter()
        self.assertEqual(self.path.read_bytes(), b'original')
        self.assertEqual(self.driver.dispatches, 0)

    def test_changed_identity_body_path_or_format_prevents_dispatch(self):
        original = dict(self.driver.snapshot)
        for update in ({'document_token': 'other'}, {'text': 'changed'},
                       {'document_path': str(self.home / 'other.rtf')}, {'source_format': 'text'}):
            with self.subTest(update=update):
                self.driver.snapshot = dict(original)
                adapter = self.adapter()
                self.driver.snapshot.update(update)
                result = self.run_save(adapter)
                self.assertFalse(result.ok)
                self.assertEqual(self.driver.dispatches, 0)
                self.assertEqual(result.events, [])
        self.assertTrue(all(token == 'window-1' for _, token in self.driver.calls if token is not None))

    def test_save_panel_must_match_pinned_document_directory_name_and_format(self):
        expected = dict(self.driver.panel)
        cases = [None, {}, {**expected, 'document_token': 'other-document'},
                 {**expected, 'panel_token': None}, {**expected, 'panel_token': True},
                 {**expected, 'panel_token': 'x'*129},
                 {**expected, 'directory': str(self.home / 'other' / 'Downloads')},
                 {**expected, 'filename': 'other.rtf'}, {**expected, 'format': 'text'}]
        for panel in cases:
            with self.subTest(panel=panel):
                self.driver = SnapshotDriver(self.path)
                adapter = self.adapter()  # Initial snapshot need not contain a save panel.
                self.driver.panel = panel
                result = self.run_save(adapter)
                self.assertFalse(result.ok)
                self.assertEqual(self.driver.dispatches, 0)
                self.assertEqual(result.events, [])
                self.assertEqual(result.log[-1]['detail']['effect'], 'not_started')
                self.assertFalse(self.path.exists())

    def test_native_guard_refusal_after_host_prepare_never_writes_or_retries(self):
        adapter=self.adapter()
        def refuse(*args, **kwargs):
            self.assertEqual(kwargs['save_panel_token'], 'panel-1')
            self.assertIs(kwargs['approved'], True)
            self.driver.dispatches += 1
            return {'type':'error','error':'save_panel_guard_unavailable','effect':'not_started'}
        self.driver.act=refuse
        result=self.run_save(adapter)
        self.assertFalse(result.ok)
        self.assertEqual(self.driver.dispatches, 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(result.events, [])

    def test_forged_prepared_event_refuses_before_native_dispatch(self):
        from dataclasses import replace
        adapter=self.adapter()
        # Preserve the original callback while replay calls the forged one.
        original=adapter.prepare_commit
        adapter.prepare_commit=lambda event: replace(original(event), event_id='foreign-event')
        result=self.run_save(adapter)
        self.assertFalse(result.ok)
        self.assertEqual(self.driver.dispatches,0)
        self.assertFalse(self.path.exists())
        self.assertEqual(result.events,[])

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(),'macOS textutil decoder')
    def test_uncertain_save_with_changed_document_never_confirms_or_retries(self):
        adapter=self.adapter()
        original=self.driver.act
        def uncertain(*args,**kwargs):
            original(*args,**kwargs)
            self.driver.snapshot['text']='changed after dispatch'
            return {'type':'acted','unverified':'cannot_complete'}
        self.driver.act=uncertain
        result=self.run_save(adapter)
        self.assertFalse(result.ok)
        self.assertEqual(self.driver.dispatches,1)
        self.assertEqual(result.events[0]['state'],'unknown')
        self.assertEqual(result.commit_evidence,{})

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(),'macOS textutil decoder')
    def test_uncertain_save_resolves_only_through_exact_host_readback_without_retry(self):
        adapter=self.adapter()
        original=self.driver.act
        def uncertain(*args, **kwargs):
            original(*args, **kwargs)
            return {'type':'acted','unverified':'cannot_complete'}
        self.driver.act=uncertain
        result=self.run_save(adapter)
        self.assertTrue(result.ok, result.log)
        self.assertEqual(self.driver.dispatches, 1)
        self.assertEqual(result.events[0]['state'], 'confirmed')
        self.assertEqual(len(result.commit_evidence), 1)

    def test_wrong_action_or_observation_cannot_prepare_a_save(self):
        cases = [({'name': 'click', 'element_index': 999}, 'o'),
                 ({'name': 'menu', 'path': ['Format', 'Font', 'Bold']}, 'o'),
                 ({'name': 'set_value', 'element_index': 1, 'value': 'other'}, 'o'),
                 ({'name': 'click', 'element_index': True}, 'o'),
                 ({'name': 'click', 'element_index': 0, 'extra': 'unvetted'}, 'o'),
                 ({'name': 'click', 'element_index': 0}, 'previous'),
                 ({'name': 'click', 'element_index': 0}, None)]
        for action, obs_id in cases:
            with self.subTest(action=action, obs_id=obs_id):
                adapter = self.adapter()
                event = make_event(action, {'write_persistent'}, 'com.apple.TextEdit', obs_id=obs_id)
                with self.assertRaises(ArtifactReceiptError):
                    adapter.prepare_commit(event)
                self.assertFalse(self.path.exists())
                self.assertEqual(0, self.driver.dispatches)

    def test_unavailable_disabled_or_replaced_commit_control_prevents_dispatch(self):
        cases = [None, {}, {'obs_id': 'previous', 'enabled': True,
                           'action': {'name': 'click', 'element_index': 0}},
                 {'obs_id': 'o', 'enabled': False,
                  'action': {'name': 'click', 'element_index': 0}},
                 {'obs_id': 'o', 'enabled': 1,
                  'action': {'name': 'click', 'element_index': 0}},
                 {'obs_id': 'o', 'enabled': True,
                  'action': {'name': 'click', 'element_index': 1}},
                 {'obs_id': 'o', 'enabled': True,
                  'action': {'name': 'click', 'element_index': False}}]
        for control in cases:
            with self.subTest(control=control):
                self.driver = SnapshotDriver(self.path)
                adapter = self.adapter()
                self.driver.panel['commit_control'] = control
                result = self.run_save(adapter)
                self.assertFalse(result.ok)
                self.assertEqual(0, self.driver.dispatches)
                self.assertEqual([], result.events)
                self.assertFalse(self.path.exists())

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(), 'macOS textutil decoder')
    def test_fresh_korean_and_empty_documents_replay_with_pinned_expectations(self):
        for text in ('한글', ''):
            with self.subTest(text=text):
                self.path.unlink(missing_ok=True)
                self.driver = SnapshotDriver(self.path, text)
                adapter = self.adapter()
                mutable_copy = adapter.commit_expected
                mutable_copy['저장한 이름'] = 'wrong.rtf'
                result = self.run_save(adapter)
                self.assertTrue(result.ok, result.log)
                self.assertEqual(self.driver.dispatches, 1)
                self.assertEqual(result.events[0]['state'], 'confirmed')
                self.assertEqual(adapter.commit_expected['저장한 이름'], '보고서.rtf')

    @unittest.skipUnless(Path('/usr/bin/textutil').is_file(), 'macOS textutil decoder')
    def test_post_dispatch_wrong_document_or_mutated_body_cannot_receive_done(self):
        for update in ({'document_token': 'other'}, {'text': 'changed'},
                       {'document_path': str(self.home / 'wrong.rtf')}):
            with self.subTest(update=update):
                self.path.unlink(missing_ok=True)
                self.driver = SnapshotDriver(self.path)
                self.driver.after_patch = update
                result = self.run_save(self.adapter())
                self.assertFalse(result.ok)
                self.assertEqual(result.log[-1]['status'], 'unverified', result.log)
                self.assertEqual(self.driver.dispatches, 1)
                self.assertEqual(result.commit_evidence, {})


if __name__ == '__main__':
    unittest.main()
