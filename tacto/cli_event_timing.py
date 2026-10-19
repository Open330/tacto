"""Bounded CLI event arrival times; not provider inference or native RPC timing."""
import json
import time


class CLIEventTiming:
    MAX_LINE_BYTES = 1024 * 1024
    MAX_EVENTS = 10000

    def __init__(self, *, clock=time.monotonic, start=None):
        self._clock = clock
        self._start = clock() if start is None else start
        self._buffer = bytearray()
        self._discarding = False
        self._events = []
        self._dropped = 0

    def feed(self, data):
        # Transport already bounds total output. Bound partial lines too and
        # discard oversized records until newline; never retain UI payloads.
        parts = data.split(b'\n')
        for index, part in enumerate(parts):
            complete = index < len(parts) - 1
            if not self._discarding:
                if len(self._buffer) + len(part) > self.MAX_LINE_BYTES:
                    self._buffer.clear()
                    self._discarding = True
                    self._dropped += 1
                else:
                    self._buffer.extend(part)
            if complete:
                if not self._discarding and self._buffer:
                    self._record(bytes(self._buffer))
                self._buffer.clear()
                self._discarding = False

    def _record(self, line):
        if len(self._events) >= self.MAX_EVENTS:
            self._dropped += 1
            return
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            self._dropped += 1
            return
        if not isinstance(event, dict):
            self._dropped += 1
            return
        kind = event.get('type')
        if kind not in {'thread.started', 'turn.started', 'turn.completed', 'turn.failed',
                        'item.started', 'item.updated', 'item.completed', 'error'}:
            return
        record = {'event': kind, 'received_seconds': round(max(0., self._clock() - self._start), 6)}
        item = event.get('item')
        if isinstance(item, dict):
            # IDs and item types are bounded protocol labels. Nothing from
            # command, arguments, output, reasoning or message is copied.
            for key in ('id', 'type'):
                value = item.get(key)
                if isinstance(value, str) and len(value) <= 256:
                    record['item_' + key] = value
        self._events.append(record)

    def snapshot(self):
        return {'schema': 'cli-event-arrival-timing-v1', 'clock': 'monotonic',
                'scope': 'host_received_stdout_events; transport_buffering_possible; not_inference_time',
                'events': [dict(event) for event in self._events],
                'dropped_records': self._dropped, 'partial_line_bytes': len(self._buffer)}
