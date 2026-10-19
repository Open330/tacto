"""`auto` stops re-asking a provider that just said it cannot serve."""
import unittest
from unittest.mock import patch

import tacto.authoring as authoring
from tacto.provider_result import ProviderUnavailable


class UnavailableMemoTests(unittest.TestCase):
    def setUp(self):
        authoring._unavailable_until.clear()
        self.addCleanup(authoring._unavailable_until.clear)

    def setUp_route(self, behaviour):
        # One route object for the whole test, as in a real process.
        self.calls = []
        def cli(name, prompt, remaining):
            self.calls.append(name)
            return behaviour(name)
        route = patch.object(authoring, '_try_cli', side_effect=cli)
        muxa = patch.object(authoring, '_try_muxa', return_value=None)
        route.start(); muxa.start()
        self.addCleanup(route.stop); self.addCleanup(muxa.stop)

    def run_with(self, behaviour, provider='auto'):
        if not hasattr(self, 'calls'):
            self.setUp_route(behaviour)
        self.calls.clear()
        meta = {}
        try:
            reply = authoring._ask_model('q', provider, 30, _metadata=meta)
        except Exception as exc:
            reply = exc
        return list(self.calls), reply, meta

    @staticmethod
    def claude_disabled(name):
        if name == 'claude':
            raise ProviderUnavailable('organization_access_disabled')
        return 'answer'

    def test_second_question_skips_the_refusing_provider(self):
        calls, reply, _ = self.run_with(self.claude_disabled)
        self.assertEqual(['claude', 'codex'], calls)
        self.assertEqual('answer', reply)
        calls, reply, meta = self.run_with(self.claude_disabled)
        self.assertEqual(['codex'], calls)
        self.assertEqual('answer', reply)
        skipped = meta['attempts'][0]
        self.assertEqual(('claude', 'remembered', 'error', 'organization_access_disabled'),
                         (skipped.provider, skipped.transport, skipped.status, skipped.error_code))

    def test_memory_expires_and_never_applies_to_a_pinned_provider(self):
        self.run_with(self.claude_disabled)
        calls, _, _ = self.run_with(self.claude_disabled, provider='claude')
        self.assertEqual(['claude'], calls)
        with patch.object(authoring, 'UNAVAILABLE_SKIP_SECONDS', 0.0):
            authoring._unavailable_until.clear()
            self.run_with(self.claude_disabled)
            calls, _, _ = self.run_with(self.claude_disabled)
        self.assertEqual(['claude', 'codex'], calls)

    def test_ordinary_failures_are_not_remembered(self):
        def flaky(name):
            if name == 'claude':
                raise RuntimeError('transient')
            return 'answer'
        self.run_with(flaky)
        calls, _, _ = self.run_with(flaky)
        self.assertEqual(['claude', 'codex'], calls)


if __name__ == '__main__':
    unittest.main()
