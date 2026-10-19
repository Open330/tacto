import json
import unittest
from examples.agent_timeline import AgentTimeline, numeric_timings


class AgentTimelineTests(unittest.TestCase):
    def test_chunked_events_overlap_and_incomplete_tools(self):
        now=[0.0];timer=AgentTimeline(0,clock=lambda:now[0])
        def event(t,kind,name):
            now[0]=t
            raw=(json.dumps({'type':kind,'item':{'id':name,'type':'command_execution',
                'command':'PRIVATE'}})+'\n').encode()
            timer.feed(raw[:8]);timer.feed(raw[8:])
        event(1,'item.started','a');event(2,'item.started','b')
        event(4,'item.completed','a');event(5,'item.completed','b')
        event(6,'item.started','c')
        result=timer.finish(8)
        self.assertEqual(4,result['completed_tool_union_seconds'])
        self.assertEqual(4,result['outside_completed_tool_spans_seconds'])
        self.assertEqual(1,result['incomplete_tools'])
        self.assertNotIn('PRIVATE',repr(result))

    def test_markers_keep_only_finite_numeric_closed_fields(self):
        marker={'replay_seconds':2,'run_flow_seconds':3,'PRIVATE':'SECRET',
            'calls':{'act':{'seconds':1,'count':2,'exceptions':0,'text':'SECRET'},
                     'SECRET':{'seconds':9}}}
        output='PRIVATE AX\nTACTO_TIMINGS '+json.dumps(marker)
        stream=json.dumps({'type':'item.completed','item':{'aggregated_output':output}})
        result=AgentTimeline(0,clock=lambda:0).finish(5,stream)
        self.assertEqual(1,result['tacto_timings'][0]['values']['calls']['act']['seconds'])
        self.assertNotIn('SECRET',repr(result));self.assertNotIn('PRIVATE',repr(result))
        self.assertEqual({},numeric_timings({'replay_seconds':float('nan'),'session_done_seconds':True}))

    def test_oversized_malformed_events_remain_bounded(self):
        timer=AgentTimeline(0,clock=lambda:0)
        timer.feed(b'x'*(1024*1024+1)+b'\nnull\n{invalid}\n')
        result=timer.finish(3)
        self.assertEqual([],result['events']);self.assertEqual(3,result['dropped_records'])

    def test_numeric_marker_records_are_bounded(self):
        output='\n'.join(['TACTO_TIMINGS {"replay_seconds":1}']*1005)
        stream=json.dumps({'type':'item.completed','item':{'aggregated_output':output}})
        result=AgentTimeline(0,clock=lambda:0).finish(3,stream)
        self.assertEqual(1000,len(result['tacto_timings']))
        self.assertEqual(5,result['timing_records_dropped'])

if __name__=='__main__':unittest.main()
