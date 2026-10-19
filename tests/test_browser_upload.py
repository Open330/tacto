import unittest
from dataclasses import replace

from tacto.browser_upload import (BrowserUploadFile, BrowserUploadScope,
                                BrowserUploadDriver, upload_params_valid)
from tacto.flow import Flow, Step, Selector, replay, pin_params
from tacto.contract import SafetyContract, Resource, ContextBinding


class BrowserUploadContract(unittest.TestCase):
    def setUp(self):
        self.selector = Selector(title='attachment')
        self.scope = BrowserUploadScope('https://owned.test', self.selector,
            self.selector, (BrowserUploadFile('검토.txt', b'owned'),))

    def test_digest_binds_bytes_filename_mime_origin_and_both_selectors(self):
        original = self.scope.digest
        for changed in (
                replace(self.scope, origin='https://other.test'),
                replace(self.scope, opener=Selector(title='other')),
                replace(self.scope, chooser=Selector(title='other')),
                replace(self.scope, files=(BrowserUploadFile('other.txt', b'owned'),)),
                replace(self.scope, files=(BrowserUploadFile('검토.txt', b'changed'),)),
                replace(self.scope, files=(BrowserUploadFile('검토.txt', b'owned', 'text/plain'),))):
            self.assertNotEqual(original, changed.digest)

    def test_file_refuses_path_names_mutable_bytes_bad_mime_and_oversize(self):
        for name, content, mime in (
                ('../private', b'', 'text/plain'), ('a\\b', b'', 'text/plain'),
                ('a\n.txt', b'', 'text/plain'), ('', b'', 'text/plain'),
                ('a', bytearray(b'x'), 'text/plain'), ('a', b'', 'text/plain\n'),
                ('a', b'x' * (16 * 1024 * 1024 + 1), 'text/plain')):
            with self.subTest(name=name, mime=mime), self.assertRaises(ValueError):
                BrowserUploadFile(name, content, mime)

    def test_scope_refuses_unnamed_targets_duplicate_names_and_empty_payload(self):
        for scope_args in (
                ('', self.selector, self.selector, self.scope.files),
                ('https://owned.test', Selector(role='button'), self.selector, self.scope.files),
                ('https://owned.test', self.selector, self.selector, ()),
                ('https://owned.test', self.selector, self.selector, self.scope.files * 2)):
            with self.assertRaises(ValueError):
                BrowserUploadScope(*scope_args)

    def adapter(self):
        from tacto.browser import BrowserDriver
        # No GUI or browser startup: only test refusal paths preceding reads.
        driver = BrowserDriver.__new__(BrowserDriver)
        driver._latest = {}
        return BrowserUploadDriver(driver, {'owned': self.scope})

    def test_registration_copies_selectors_and_never_serializes_bytes_or_paths(self):
        adapter = self.adapter()
        original = adapter.parameters('owned')
        self.selector.title = 'changed after registration'
        self.assertEqual(original, adapter.parameters('owned'))
        self.assertEqual({'scope', 'attachment_digest'}, set(original))

    def test_adapter_refuses_unapproved_wrong_digest_scope_and_payload_keys(self):
        adapter = self.adapter()
        action = {'name': 'upload', 'element_index': 0, **adapter.parameters('owned')}
        for change in ({'scope': 'missing'}, {'attachment_digest': '0' * 64},
                       {'paths': ['/private']}, {'element_index': True}):
            result = adapter.act('old', 'page', {**action, **change},
                risk_class='confirm', approved=True)
            self.assertEqual('not_started', result.get('effect'))
        for approved in (False, 1, 'true'):
            self.assertEqual('confirm_request', adapter.act('old', 'page', action,
                risk_class='confirm', approved=approved)['type'])
        self.assertEqual('upload_contract_required', adapter.act('old', 'page', action,
            risk_class='none', approved=True)['error'])

    def test_flow_refuses_absent_capability_wrong_effect_or_missing_binding_before_observe(self):
        params = self.adapter().parameters('owned')
        safe = SafetyContract(effects=['transmit'], reversibility='irreversible',
            resource=Resource(object_type='owned attachment', scope='entry'))
        binding = ContextBinding(operation='upload', params_digest=pin_params(params))
        for effects, bound, caps in ((safe, binding, ()),
                (replace(safe, effects=['write_ephemeral']), binding, {'action.upload'}),
                (safe, None, {'action.upload'})):
            class NoInput:
                capabilities = caps
                def observe(self, *args, **kwargs):
                    raise AssertionError('invalid upload must fail before observation')
            flow = Flow('upload', 'page', [Step('upload', self.selector,
                params=params, safety=effects, binding=bound)])
            result = replay(NoInput(), flow)
            self.assertFalse(result.ok)
            self.assertEqual('upload_contract_required', result.log[-1]['error'])

    def test_upload_effect_is_intrinsically_transmit(self):
        from tacto.safety import effects_of
        self.assertEqual({'transmit'}, effects_of({'name': 'upload'}, '0 button Title: harmless')[0])

    def test_params_require_exact_closed_scope_and_digest(self):
        params = self.adapter().parameters('owned')
        self.assertTrue(upload_params_valid(params))
        for changed in ({'scope': '../file'}, {'attachment_digest': True},
                        {'attachment_digest': '0' * 63}, {'paths': []}):
            self.assertFalse(upload_params_valid({**params, **changed}))

    def test_upload_recipe_roundtrips_registration_reference_without_payload(self):
        params=self.adapter().parameters('owned')
        safe=SafetyContract(effects=['transmit'],reversibility='irreversible',
            resource=Resource(object_type='owned attachment',scope='entry'))
        flow=Flow('upload','page',[Step('upload',self.selector,params=params,safety=safe,
            binding=ContextBinding(operation='upload',params_digest=pin_params(params)))])
        serialized=flow.to_json()
        restored=Flow.from_json(serialized)
        self.assertEqual(params,restored.steps[0].params)
        self.assertNotIn('검토.txt',serialized)
        self.assertNotIn('content',serialized)
