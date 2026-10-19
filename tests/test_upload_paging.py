"""Registered attachments must remain selectable within prompt budgets."""
import re
import unittest
from unittest.mock import patch

from tacto.ask import ask_here_paged


class UploadPagingTests(unittest.TestCase):
    def candidate(self):
        return {'label': 'attachments', 'can': ['upload'], 'uploads': [
            {'scope': f'payload_{i:03}', 'attachment_digest': f'{i:064x}',
             'files': [{'filename': f'검토_{i:03}_' + '한글' * 70 + '.txt',
                        'size_bytes': 25, 'sha256': 'a' * 64}]}
            for i in range(64)]}

    def test_late_attachment_manifest_is_referenced_and_original_digest_retained(self):
        candidate = self.candidate()
        prompts = []
        def respond(q, **kwargs):
            prompts.append(q.prompt)
            self.assertLessEqual(len(q.prompt.encode()), 16000)
            if '# 참조 내용' not in q.prompt:
                self.assertNotIn(candidate['uploads'][-1]['files'][0]['filename'], q.prompt)
                ref = re.search(r'"ref": "([^"]+)"', q.prompt).group(1)
                return q.check({'read_context': {'ref': ref,
                    'find': '"scope": "payload_063"', 'length': 1000}})
            self.assertIn('검토_063_', q.prompt)
            return q.check({'actions': [{'pick': 0, 'do': 'upload', 'scope': 'payload_063'}]})
        with patch('tacto.ask.ask', side_effect=respond):
            result = ask_here_paged('upload 검토_063 only', [candidate], budget=16000,
                token_counter=lambda text: len(text.encode()), max_pages=3)
        self.assertEqual(2, len(prompts))
        self.assertEqual(candidate, result['actions'][0]['candidate'])
        self.assertEqual(candidate['uploads'][-1]['attachment_digest'], result['actions'][0]['attachment_digest'])

    def test_display_excerpt_never_authorizes_unknown_registration(self):
        candidate = self.candidate()
        with patch('tacto.ask.ask', side_effect=lambda q, **_: q.check(
                {'actions': [{'pick': 0, 'do': 'upload', 'scope': 'unregistered'}]})):
            result = ask_here_paged('upload', [candidate], budget=16000,
                token_counter=lambda text: len(text.encode()))
        self.assertIsNone(result)

    def test_context_reads_consume_request_limit_before_upload(self):
        def respond(q, **_):
            ref = re.search(r'"ref": "([^"]+)"', q.prompt).group(1)
            return q.check({'read_context': {'ref': ref, 'offset': 0, 'length': 1}})
        with patch('tacto.ask.ask', side_effect=respond) as ask:
            result = ask_here_paged('upload', [self.candidate()], budget=16000,
                token_counter=lambda text: len(text.encode()), max_pages=2)
        self.assertEqual(2, ask.call_count)
        self.assertEqual([], result['actions'])
        self.assertEqual('context_page_limit', result['stop_reason'])

    def test_fixed_context_reserve_does_not_reject_a_fitting_candidate(self):
        def counter(text):
            if '# 참조 내용' in text:
                return 2000
            if '# 목표' in text:
                return 1750
            return len(text.encode())
        def respond(q, **_):
            self.assertLessEqual(counter(q.prompt), 2500)
            if '# 참조 내용' not in q.prompt:
                ref = re.search(r'"ref": "([^"]+)"', q.prompt).group(1)
                return q.check({'read_context': {'ref': ref, 'find': 'payload_063', 'length': 1000}})
            return q.check({'actions': [{'pick': 0, 'do': 'upload', 'scope': 'payload_063'}]})
        with patch('tacto.ask.ask', side_effect=respond) as ask:
            result = ask_here_paged('upload', [self.candidate()], budget=2500,
                token_counter=counter, max_pages=3)
        self.assertEqual(2, ask.call_count)
        self.assertEqual('payload_063', result['actions'][0]['scope'])


if __name__ == '__main__':
    unittest.main()
