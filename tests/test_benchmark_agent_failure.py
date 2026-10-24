"""Tool-using Claude errors stay categorical, including startup hook frames."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from examples.benchmark_agent import reported_agent_failure,run_agent


class AgentFailureTests(unittest.TestCase):
    def stream(self):
        text='Your organization has disabled Claude subscription access for Claude Code. PRIVATE_ACCOUNT_DETAIL'
        return [
            {'type':'system','subtype':'hook_started','session_id':'startup'},
            {'type':'system','subtype':'hook_response','session_id':'startup'},
            {'type':'system','subtype':'init','session_id':'owned','tools':['Bash','Read'],'mcp_servers':[]},
            {'type':'assistant','session_id':'owned','is_api_error_message':True,'parent_tool_use_id':None,
             'message':{'role':'assistant','content':[{'type':'text','text':text}]}},
            {'type':'result','session_id':'owned','subtype':'success','is_error':True,'result':text,
             'usage':{'input_tokens':0,'cache_read_input_tokens':0,'cache_creation_input_tokens':0,'output_tokens':0}}]

    def encode(self,events):return '\n'.join(json.dumps(e) for e in events)

    def test_known_synthetic_refusal_has_closed_code_not_private_text(self):
        code=reported_agent_failure('claude',self.encode(self.stream()))
        self.assertEqual('organization_access_disabled',code)
        self.assertNotIn('PRIVATE',code)

    def test_model_mentions_tool_actions_and_mismatched_sessions_are_not_api_refusals(self):
        for mode in ('model_text','success','wrong_session','different_terminal','tool_content','user_result','missing','malformed','unknown_system','misordered_hook'):
            events=self.stream()
            if mode=='model_text':events[-2]['is_api_error_message']=False
            elif mode=='success':events[-1]['is_error']=False
            elif mode=='wrong_session':events[-1]['session_id']='other'
            elif mode=='different_terminal':events[-1]['result']='different'
            elif mode=='tool_content':events[-2]['message']['content'].append({'type':'tool_use','id':'t'})
            elif mode=='user_result':events.insert(-1,{'type':'user','message':{'content':[]}})
            elif mode=='missing':events=events[:-1]
            elif mode=='unknown_system':events.insert(0,{'type':'system','subtype':'unknown'})
            elif mode=='misordered_hook':events.insert(-1,{'type':'system','subtype':'hook_response'})
            stream='not JSON' if mode=='malformed' else self.encode(events)
            with self.subTest(mode=mode):self.assertIsNone(reported_agent_failure('claude',stream))
        self.assertIsNone(reported_agent_failure('codex',self.encode(self.stream())))

    def test_failed_real_agent_route_preserves_usage_and_categorical_error(self):
        child=Mock();child.returncode=1;child.__enter__=Mock(return_value=child);child.__exit__=Mock(return_value=False)
        with tempfile.TemporaryDirectory() as tmp, \
             patch('examples.benchmark_agent.subprocess.Popen',return_value=child), \
             patch('tacto.provider_io.require_provider_child_ownership'), \
             patch('tacto.provider_io.communicate_owned_provider',return_value=(self.encode(self.stream()),'')), \
             patch('examples.agent_timeline.AgentTimeline') as timeline:
            result=run_agent('claude','owned probe',Path(tmp)/'agent',profile=True)
            timeline.assert_not_called()
        self.assertEqual(1,result['exit'])
        self.assertEqual('organization_access_disabled',result['error'])
        self.assertEqual('reported_failed',result['usage']['status'])
        self.assertFalse(result['usage']['billing_verified'])
        self.assertEqual('unsupported_for_provider',result['profile']['status'])
        self.assertIsNone(result['profile']['completed_tool_union_seconds'])
        self.assertIsNone(result['profile']['incomplete_tools'])
        self.assertNotIn('PRIVATE',json.dumps(result))
