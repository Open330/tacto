"""A Safari navigation is confirmed by the window that loaded it."""
import json
import unittest
from unittest.mock import patch

from tacto.commit_evidence import CommitEvidence, NavigationEvidence, evidence_error, make_event
from tacto.navigation_receipt import NavigationReceipt, front_window_url, normalized_url

URL = 'https://example.com/expected'
FIELD = 'WEB_BROWSER_ADDRESS_AND_SEARCH_FIELD'


def tree(document, typed=URL, focused=True, status='pending', frame=None):
    """A Safari window: address field, the page's WebArea (AXURL), a frame."""
    window = '0 Window  Title: "Page"' + (' [focused]' if focused else '')
    page = '  2 WebArea  Title: "Page"'
    if document is not None:
        page += f', URL: {json.dumps(document)}'
    lines = [window, f'  1 TextField  Identifier: {FIELD}, Value: {typed}', page]
    if frame is not None:
        lines.append(f'    3 WebArea  Title: "ad", URL: {json.dumps(frame)}')
    lines.append(f'    4 StaticText  Identifier: navigation-status, Value: {status}')
    return '\n'.join(lines)


def press(effects=('transmit',)):
    return make_event({'name': 'press_key', 'key': 'Return', 'element_index': 1},
                      set(effects), 'com.apple.Safari', obs_id='o')


class UrlTests(unittest.TestCase):
    def test_normalized_url(self):
        self.assertEqual('https://example.com/', normalized_url('HTTPS://Example.COM:443'))
        self.assertEqual('http://a.example:8080/x?q=1#f', normalized_url('http://A.example:8080/x?q=1#f'))
        for bad in ('example.com', 'javascript:alert(1)', 'file:///tmp/x', 'https://u:p@x.com/', '', None,
                    'https://x.com:99999/', 'https://' + 'a' * 2050):
            with self.subTest(bad=bad):
                self.assertIsNone(normalized_url(bad))

    def test_only_the_one_focused_window_and_its_page_count(self):
        self.assertEqual(URL, front_window_url(tree(URL)))
        self.assertIsNone(front_window_url(tree(URL, focused=False)))
        self.assertIsNone(front_window_url(tree(URL) + '\n' + tree(URL).replace('0 Window', '9 Window')))
        self.assertIsNone(front_window_url(tree('/Users/x/file.txt')))
        # A frame is a deeper WebArea and never stands for the page.
        self.assertEqual('https://old.example/', front_window_url(tree('https://old.example/', frame=URL)))
        self.assertIsNone(front_window_url(tree(None, frame=URL)))
        # Two pages side by side in one window: no guessing.
        two = tree(URL).replace('  2 WebArea', '  5 WebArea  Title: "x", URL: "https://b.example/"\n  2 WebArea')
        self.assertIsNone(front_window_url(two))
        # Only the focused window's own lines: another window's page is not it.
        other = tree('https://old.example/') + '\n9 Window  Title: "B"\n  10 WebArea  Title: "B", URL: ' + json.dumps(URL)
        self.assertEqual('https://old.example/', front_window_url(other))

    def test_a_page_title_cannot_spell_the_url_field(self):
        spoof = ('0 Window  Title: "Page" [focused]\n'
                 f'  2 WebArea  Title: x, URL: "{URL}"')            # unquoted title: not a helper line
        self.assertIsNone(front_window_url(spoof))
        quoted = ('0 Window  Title: "Page" [focused]\n'
                  f'  2 WebArea  Title: {json.dumps("x, URL: " + json.dumps(URL))}')
        self.assertIsNone(front_window_url(quoted))


class ReceiptTests(unittest.TestCase):
    def receipt(self, before, after_trees, **kwargs):
        reads = iter(after_trees)
        self.clock = 0.0
        def sleep(seconds): self.clock += seconds
        return NavigationReceipt(lambda target: next(reads), target='com.apple.Safari', destination=URL,
                                 before_tree=before, clock=lambda: self.clock, sleep=sleep, **kwargs)

    def test_the_loaded_destination_is_evidence(self):
        receipt = self.receipt(tree('https://old.example/'), [tree('https://old.example/'), tree(URL)])
        event = press(); context = receipt.prepare_commit(event)
        proof = receipt.after_commit(event, {'type': 'acted'}, context)
        self.assertIs(NavigationEvidence, type(proof.evidence))
        self.assertIsNone(evidence_error({**event, 'commit_evidence': proof}, receipt.commit_expected))

    def test_a_start_page_without_a_document_can_navigate(self):
        receipt = self.receipt(tree(None), [tree(URL)])
        event = press(); context = receipt.prepare_commit(event)
        self.assertIsNotNone(receipt.after_commit(event, {}, context))

    def test_typing_alone_or_a_redirect_is_not_shown(self):
        for shown in ('https://old.example/', 'https://example.com/login'):
            with self.subTest(shown=shown):
                receipt = self.receipt(tree('https://old.example/'), [tree(shown)] * 100, timeout_seconds=1)
                event = press(); context = receipt.prepare_commit(event)
                with self.assertRaises(Exception) as caught:
                    receipt.after_commit(event, {}, context)
                self.assertEqual('destination_not_shown', caught.exception.public_code)

    def test_already_there_or_not_a_navigation_is_refused_before_the_press(self):
        cases = ((tree(URL), press(), 'already_at_destination'),
                 (tree(None), press(('write_persistent',)), 'not_a_bound_navigation'),
                 (tree(None), make_event({'name': 'click', 'element_index': 1}, {'transmit'},
                                         'com.apple.Safari', obs_id='o'), 'not_a_bound_navigation'))
        for before, event, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(Exception) as caught:
                    self.receipt(before, []).prepare_commit(event)
                self.assertEqual(code, caught.exception.public_code)

    def test_one_prepare_and_one_after(self):
        receipt = self.receipt(tree(None), [tree(URL)])
        event = press(); context = receipt.prepare_commit(event)
        with self.assertRaises(Exception):receipt.prepare_commit(event)
        receipt.after_commit(event, {}, context)
        with self.assertRaises(Exception):receipt.after_commit(event, {}, context)

    def test_evidence_must_match_the_expected_destination_and_effects(self):
        event = press()
        proof = CommitEvidence(event['event_id'], event['action_digest'], NavigationEvidence('r', URL))
        bound = {**event, 'commit_evidence': proof}
        self.assertEqual('navigation_expectation_missing', evidence_error(bound, {}))
        self.assertEqual('navigation_destination_mismatch', evidence_error(bound, {'이동할 주소': URL + 'x'}))
        other = press(('transmit', 'write_persistent'))
        proof = CommitEvidence(other['event_id'], other['action_digest'], NavigationEvidence('r', URL))
        self.assertEqual('navigation_effect_mismatch',
                         evidence_error({**other, 'commit_evidence': proof}, {'이동할 주소': URL}))


class ReplayTests(unittest.TestCase):
    def run_flow(self, loads, capability=True):
        from tests.test_bound_return import App, CAPS, step
        from tacto.flow import Flow, Selector, Postcondition, replay, pin_params
        from tacto.contract import ContextBinding

        class Browser(App):
            def observe(self, *args, **kwargs):
                document = loads if self.calls else 'https://old.example/'
                return {'type': 'observation', 'obs_id': f'o{len(self.calls)}',
                        'tree': tree(document, status='done' if self.calls else 'pending')}
        app = Browser(response={'type': 'acted', 'action': 'press_key', 'input_route': 'safari_address_confirm',
                                'mechanism': 'ax_confirm', 'posted_events': 0, 'reobserve_required': True,
                                'unverified': 'observe_effect', 'effect': 'unknown'})
        app.capabilities = CAPS | ({'press_key.safari_address_confirm'} if capability else set())
        st = step(effects={'transmit'}, selector=Selector(identifier=FIELD),
                  binding=ContextBinding(bundle_id='com.apple.Safari', identifier=FIELD, operation='press_key',
                                         params_digest=pin_params({'key': 'Return'}), destination_identity=URL,
                                         destination_identity_from={'identifier': FIELD, 'attribute': 'Value'}),
                  postcondition=Postcondition(field=Selector(identifier='navigation-status'),
                                              attribute='Value', equals='done'))
        with patch('tacto.flow._approved', return_value=True):
            return app, replay(app, Flow('navigate', 'com.apple.Safari', [st], schema=2))

    def test_a_loaded_destination_completes_the_replay(self):
        app, result = self.run_flow(URL)
        self.assertEqual(1, len(app.calls))
        self.assertTrue(result.ok, result.log)
        kinds = [e.get('commit_evidence_kind') for e in result.events]
        self.assertIn('browser_navigation', kinds)

    def test_another_page_stays_unverified_and_is_not_resent(self):
        from unittest.mock import patch as patching
        from tacto.navigation_receipt import NavigationReceipt as Receipt
        with patching.dict(Receipt.__init__.__kwdefaults__, {'timeout_seconds': 0.2, 'poll_interval': 0.05}):
            app, result = self.run_flow('https://example.com/login')
        self.assertEqual(1, len(app.calls))
        self.assertFalse(result.ok)
        self.assertEqual(('commit_unverified', 'destination_not_shown'),
                         (result.log[-1].get('error'), result.log[-1].get('detail')))

    def test_without_the_semantic_route_there_is_no_built_in_receipt(self):
        app, result = self.run_flow(URL, capability=False)
        self.assertFalse(result.ok)
        self.assertEqual('missing_typed_host_commit_evidence', result.log[-1].get('detail'))


if __name__ == '__main__':
    unittest.main()
