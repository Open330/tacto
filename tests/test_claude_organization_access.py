"""An isolated API denial is a specific failure, never answer/usage evidence."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from nlc.claude_result import (claude_result, _observed_organization_access_disabled)
from nlc.provider_observation import provider_failure_reason
from nlc.provider_result import ProviderError, ProviderResult, parse_provider_result
from tests.test_claude_result import events, stream

MESSAGE = ('Your organization has disabled Claude subscription access for Claude Code'
           ' · PRIVATE_SENTINEL')


def denial(session):
    return [
        {'type':'system', 'subtype':'init', 'session_id':session, 'tools':[], 'mcp_servers':[]},
        {'type':'assistant', 'session_id':session, 'is_api_error_message':True,
         'parent_tool_use_id':None, 'message':{'role':'assistant',
             'content':[{'type':'text', 'text':MESSAGE}]}},
        {'type':'result', 'session_id':session, 'subtype':'success', 'is_error':True,
         'num_turns':1, 'result':MESSAGE, 'total_cost_usd':0,
         'usage':{'input_tokens':0, 'output_tokens':0}},
    ]


class ClaudeOrganizationAccessTests(unittest.TestCase):
    def test_exact_complete_isolated_failed_stream_is_recognized(self):
        self.assertTrue(_observed_organization_access_disabled(stream(denial('owned')), 'owned'))

    def test_incomplete_foreign_tool_enabled_or_non_error_streams_are_not_recognized(self):
        original = denial('owned')
        cases = [original[:-1], original[1:], original + [original[-1]],
                 [original[0], {'type':'system','subtype':'unknown','session_id':'owned'}, *original[1:]]]
        for index, field, value in [(0,'tools',['Read']), (0,'mcp_servers',[{}]),
                                    (1,'session_id','other'), (1,'parent_tool_use_id','tool'),
                                    (1,'is_api_error_message',False), (1,'is_api_error_message',1),
                                    (2,'is_error',False), (2,'is_error',1),
                                    (2,'num_turns',2), (2,'result',MESSAGE+' changed')]:
            changed = deepcopy(original); changed[index][field] = value; cases.append(changed)
        for data in cases:
            with self.subTest(data=data):
                self.assertFalse(_observed_organization_access_disabled(stream(data), 'owned'))

    def test_other_api_failures_and_tool_content_do_not_get_this_reason(self):
        for text in ('different failure', 'API rate limit', '', MESSAGE.casefold()):
            data = denial('owned');data[1]['message']['content'][0]['text'] = text
            data[2]['result'] = text
            self.assertFalse(_observed_organization_access_disabled(stream(data), 'owned'))
        data = denial('owned')
        data[1]['message']['content'].append({'type':'tool_use','name':'Read'})
        self.assertFalse(_observed_organization_access_disabled(stream(data), 'owned'))

    def test_output_budget_and_bad_json_fail_closed(self):
        for text in ('not JSON', 'x' * (8*1024*1024+1), '[]', 'null', '[' * 2000 + ']' * 2000):
            self.assertFalse(_observed_organization_access_disabled(text, 'owned'))

    def test_cli_denial_is_specific_and_drops_text_usage_cost_and_model(self):
        for code in (0, 1):
            def run(argv, payload, timeout, cwd):
                session = argv[argv.index('--session-id')+1]
                return code, stream(denial(session)), ''
            with self.subTest(exit=code), patch('nlc.claude_result._run_claude', side_effect=run):
                result = claude_result('owned test', timeout=2)
            self.assertEqual('error', result.status)
            self.assertEqual('organization_access_disabled', result.error.code)
            self.assertIsNone(result.text)
            self.assertIsNone(result.input_tokens)
            self.assertIsNone(result.output_tokens)
            self.assertIsNone(result.cost)
            self.assertIsNone(result.model)
            self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.to_dict()))
            parse_provider_result(result.to_dict())

    def test_successful_answer_discussing_access_is_still_an_answer(self):
        def run(argv, payload, timeout, cwd):
            session = argv[argv.index('--session-id')+1];data = events(session)
            data[2]['message']['content'][0]['text'] = MESSAGE
            data[-1]['result'] = MESSAGE
            return 0, stream(data), ''
        with patch('nlc.claude_result._run_claude', side_effect=run):
            result = claude_result('explain access policy', timeout=2)
        self.assertEqual('ok', result.status)
        self.assertEqual(MESSAGE, result.text)

    def test_common_route_retains_closed_attempt_and_friendly_reason(self):
        from nlc.authoring import ask_model_result
        def run(argv, payload, timeout, cwd):
            session = argv[argv.index('--session-id')+1]
            return 1, stream(denial(session)), ''
        with patch('nlc.claude_result._run_claude', side_effect=run):
            result = ask_model_result('owned test', 'claude', timeout=2)
        self.assertEqual('error', result.status)
        self.assertEqual('organization_access_disabled', result.error.code)
        self.assertEqual('organization_access_disabled', result.attempts[-1].error_code)
        self.assertEqual('조직에서 Claude Code 구독 사용을 제한하여 작업을 중단했습니다',
                         provider_failure_reason([result.to_dict()]))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.to_dict()))

    def test_known_unavailable_reasons_are_primary_codes_without_attempts(self):
        from nlc.authoring import ask_model_result
        for code in ('not_logged_in', 'usage_limit', 'organization_access_disabled'):
            failure = ProviderResult('owned', 'claude', 'claude', 'cli-json',
                                     'error', None, ProviderError(code, 'PRIVATE_SENTINEL'), .1)
            with self.subTest(code=code), patch('nlc.authoring._try_cli', return_value=failure):
                result = ask_model_result('owned test', 'claude', timeout=2)
            self.assertEqual(code, result.error.code)
            metadata = result.to_dict(); metadata['attempts'] = []
            self.assertIsNotNone(provider_failure_reason([metadata]))
            self.assertNotIn('PRIVATE_SENTINEL', json.dumps(metadata))

    def test_later_route_failure_does_not_inherit_prior_access_denial(self):
        from nlc.authoring import ask_model_result
        def route(provider, prompt, timeout):
            code = 'organization_access_disabled' if provider == 'claude' else 'DifferentFailure'
            return ProviderResult('owned', provider, provider, 'cli-json',
                                  'error', None, ProviderError(code, 'PRIVATE_SENTINEL'), .1)
        with patch('nlc.authoring.PROVIDER_ORDER', ('claude', 'codex')), \
             patch('nlc.authoring._try_muxa', return_value=None), \
             patch('nlc.authoring._try_cli', side_effect=route):
            result = ask_model_result('owned test', 'auto', timeout=2)
        self.assertEqual('RuntimeError', result.error.code)
        self.assertIsNone(provider_failure_reason([result.to_dict()]))
        self.assertNotIn('PRIVATE_SENTINEL', json.dumps(result.to_dict()))

    def test_timeout_keeps_timeout_code_instead_of_prior_access_failure(self):
        from nlc.authoring import ask_model_result
        timeout = ProviderResult('owned', 'claude', 'claude', 'cli-json',
                                 'timeout', None, ProviderError('organization_access_disabled', 'PRIVATE'), .1)
        with patch('nlc.authoring._try_cli', return_value=timeout):
            result = ask_model_result('owned test', 'claude', timeout=2)
        self.assertEqual('timeout', result.status)
        self.assertEqual('TimeoutError', result.error.code)


if __name__ == '__main__':
    unittest.main()
