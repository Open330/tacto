"""Repair can reach later controls without replaying any completed action."""
import json
import unittest
from unittest.mock import Mock

from tacto.repair_packet import candidate_packet_page, selector_candidate_nodes
from tacto.flow import Selector
from tests import test_auto_repair as helpers
response = helpers.response
from tests.test_flow_batch import SignedCalculator


class ManyControls(SignedCalculator):
    @property
    def tree(self):
        lines = super().tree.splitlines()
        chosen = next(line for line in lines if 'Identifier: Two,' in line)
        lines.remove(chosen)
        lines.extend(f'  {i+20} Button Identifier: unrelated-{i}, Title: '+('x'*180)
                     for i in range(80))
        lines.append(chosen)
        return '\n'.join(lines)


class RepairCandidatePagingTests(unittest.TestCase):
    execute = helpers.AutoRepairTests.execute

    def test_later_control_is_reachable_with_byte_budget_and_no_prefix_resend(self):
        packets = []
        def choose(packet, timeout):
            packets.append(packet)
            for index, candidate in enumerate(packet['candidates']):
                if candidate.get('identifier') == 'Two':
                    return response(json.dumps({'candidate': index}))
            return response('{"next_page":true}')
        code, learned, automatic, app, messages = self.execute(
            app=ManyControls(), chooser=choose, maximum=10)
        self.assertEqual(0, code)
        self.assertGreater(len(packets), 1)
        self.assertEqual('Two', learned.steps[1].selector.identifier)
        self.assertEqual([2, 3], [a['element_index'] for a in app.calls])
        self.assertEqual('12', app.value)
        self.assertEqual(len(packets), len(automatic.provider_calls))
        seen = [candidate['identifier'] for packet in packets for candidate in packet['candidates']]
        self.assertEqual(87, len(seen))
        self.assertEqual(87, len(set(seen)))
        for packet in packets:
            self.assertLessEqual(len(json.dumps(packet,ensure_ascii=False,separators=(',',':')).encode()),8192)
            self.assertEqual(list(range(len(packet['candidates']))),[c['index'] for c in packet['candidates']])

    def test_page_requests_consume_existing_call_budget_without_actions(self):
        chooser=Mock(return_value=response('{"next_page":true}'))
        code, learned, automatic, app, messages=self.execute(
            app=ManyControls(),chooser=chooser,maximum=1)
        self.assertEqual(1, code)
        self.assertEqual(1, automatic.calls)
        self.assertEqual([2], [a['element_index'] for a in app.calls])
        self.assertTrue(any('repair_budget_exhausted' in m for m in messages))

    def test_declining_does_not_request_more_pages(self):
        chooser=Mock(return_value=response('{"candidate":null}'))
        code, learned, automatic, app, messages=self.execute(app=ManyControls(),chooser=chooser,maximum=10)
        self.assertEqual(1, code)
        self.assertEqual(1, chooser.call_count)
        self.assertEqual([2], [a['element_index'] for a in app.calls])

    def test_requesting_page_after_last_is_not_a_repair_or_resend(self):
        code, learned, automatic, app, messages=self.execute(
            chooser=Mock(return_value=response('{"next_page":true}')))
        self.assertEqual(1, code)
        self.assertEqual(1, automatic.calls)
        self.assertTrue(any('no_more_candidates' in m for m in messages))
        self.assertEqual([2], [a['element_index'] for a in app.calls])

    def test_changes_during_page_search_still_prevent_dispatch(self):
        app=ManyControls()
        def choose(packet, timeout):
            for index, candidate in enumerate(packet['candidates']):
                if candidate.get('identifier')=='Two':
                    app.value='changed'
                    return response(json.dumps({'candidate':index}))
            return response('{"next_page":true}')
        code, learned, automatic, app, messages=self.execute(app=app,chooser=choose,maximum=10)
        self.assertEqual(1,code)
        self.assertEqual([2],[a['element_index'] for a in app.calls])
        self.assertEqual('OldTwo',learned.steps[1].selector.identifier)

    def test_helper_preserves_full_observed_node_mapping(self):
        nodes=selector_candidate_nodes(Selector(role='Button'),ManyControls().tree,limit=None)
        picks=[];cursor=None
        while True:
            page=candidate_packet_page({'selector':{'role':'Button'}},nodes,budget=900,cursor=cursor)
            self.assertFalse(page.overflow)
            picks.extend(page.picks)
            cursor=page.next_cursor
            if cursor is None:break
        self.assertEqual(list(range(len(nodes))),picks)
        self.assertEqual(3,nodes[-1][0])
