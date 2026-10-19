"""Host event spans and numeric native timing; never store UI or prompt text."""
import json
import math
import time
from tacto.cli_event_timing import CLIEventTiming


def numeric_timings(value):
    if not isinstance(value, dict):
        return {}
    result = {key: value[key] for key in (
        'observation_ready_seconds', 'input_ready_seconds', 'flow_received_seconds',
        'replay_done_seconds', 'session_done_seconds', 'replay_seconds', 'run_flow_seconds')
        if type(value.get(key)) in (int, float) and math.isfinite(value[key]) and value[key] >= 0}
    calls = value.get('calls')
    if isinstance(calls, dict):
        result['calls'] = {name: {key: stats[key] for key in ('count', 'seconds', 'exceptions')
            if type(stats.get(key)) in (int, float) and math.isfinite(stats[key]) and stats[key] >= 0}
            for name, stats in calls.items()
            if name in ('observe', 'probe', 'act', 'wait_until', 'identity', 'selection')
            and isinstance(stats, dict)}
    return result


class AgentTimeline:
    def __init__(self, start, clock=time.monotonic):
        self.observer = CLIEventTiming(clock=clock, start=start)

    def feed(self, chunk):
        self.observer.feed(chunk)

    def finish(self, duration, stream=''):
        snapshot = self.observer.snapshot()
        starts, spans, timings = {}, [], []
        timing_records_dropped = 0
        tools = {'command_execution', 'mcp_tool_call', 'web_search', 'tool_call'}
        for event in snapshot['events']:
            if event.get('item_type') not in tools or not isinstance(event.get('item_id'), str):
                continue
            key = event['item_id']
            if event['event'] == 'item.started':
                starts[key] = event
            elif event['event'] == 'item.completed' and key in starts:
                before = starts.pop(key)
                spans.append({'item_type': event['item_type'], 'start': before['received_seconds'],
                    'end': event['received_seconds'],
                    'seconds': event['received_seconds']-before['received_seconds']})
        union, end = 0.0, 0.0
        for span in sorted(spans, key=lambda x: x['start']):
            union += max(0.0, span['end']-max(end, span['start']))
            end = max(end, span['end'])
        for line in stream.splitlines():
            try:
                event = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if not isinstance(event, dict) or event.get('type') != 'item.completed':
                continue
            item = event.get('item', {})
            output = item.get('aggregated_output') if isinstance(item, dict) else None
            if not isinstance(output, str):
                continue
            for text in output.splitlines():
                for marker in ('TACTO_SESSION_TIMINGS ', 'TACTO_TIMINGS '):
                    if text.startswith(marker):
                        try:
                            value = numeric_timings(json.loads(text[len(marker):]))
                        except (ValueError, RecursionError):
                            continue
                        if len(timings)<1000:
                            timings.append({'marker': marker.strip(), 'values': value})
                        else:
                            timing_records_dropped += 1
        return {**snapshot, 'duration_seconds': duration, 'tool_spans': spans,
            'completed_tool_union_seconds': union,
            'outside_completed_tool_spans_seconds': max(0.0, duration-union),
            'incomplete_tools': len(starts), 'tacto_timings': timings,
            'timing_records_dropped': timing_records_dropped,
            'limits': ['tool spans include scheduling and interactive stdin waits',
                       'outside spans is not pure model time or time to first token']}
