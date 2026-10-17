"""Bounded synthetic-image CLI framing diagnostic; no GUI or raw stream logs."""
from pathlib import Path
import hashlib
import json
import os
import struct
import time
import uuid
import zlib


def stream_headers(stdout, session_id):
    """Closed shape facts only, never arbitrary keys, strings or event bodies."""
    if not isinstance(stdout, str) or len(stdout.encode('utf8')) > 2 * 1024 * 1024:
        return {'bounded': False, 'headers': []}
    headers = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        if len(headers) == 64:
            return {'bounded': False, 'headers': headers}
        try:
            event = json.loads(line)
        except ValueError:
            headers.append({'kind': 'malformed_json'})
            continue
        if not isinstance(event, dict):
            headers.append({'kind': 'invalid_event'})
            continue
        kind = event.get('type')
        kind = kind if isinstance(kind, str) and kind in {
            'system', 'assistant', 'result', 'rate_limit_event'} else 'unknown'
        header = {'kind': kind, 'same_session': event.get('session_id') == session_id}
        if kind == 'system':
            subtype = event.get('subtype')
            header['subtype'] = subtype if isinstance(subtype, str) and subtype in {
                'init', 'commands_changed', 'thinking_tokens',
                'model_refusal_no_fallback', 'model_refusal_fallback',
                'status', 'api_retry', 'hook_started', 'hook_progress', 'hook_response',
                'plugin_install', 'task_started', 'task_notification', 'task_updated',
                'task_progress', 'background_tasks_changed', 'session_state_changed',
                'worker_shutting_down', 'informational', 'notification',
                'conversation_reset', 'compact_boundary',
                # Closed system-event identifiers found in the installed CLI
                # 2.1.289. These are diagnostic labels, never parser approvals.
                'agents_killed', 'api_error', 'away_summary', 'bridge_state',
                'bridge_status', 'cloud_session_delta', 'cloud_session_status',
                'code_change_published', 'control_request_progress', 'dev_intent',
                'elicitation_complete', 'feedback_draft_queued', 'file_snapshot',
                'instruction_size_warning', 'local_command', 'memory_recall',
                'memory_saved', 'mirror_error', 'model_consent_fallback', 'model_fallback',
                'peer_message_hold', 'per_turn_effort_changed', 'permission_denied',
                'permission_retry', 'post_turn_summary', 'scheduled_task_fire',
                'session_metadata', 'session_title_changed', 'stop_hook_summary',
                'task_summary', 'tool_host_result', 'turn_duration',
                'turn_handoff_available', 'turn_preempted', 'turn_starting',
                'ui_focus', 'ui_invalidate', 'ui_log', 'ui_panes', 'ui_scroll',
                'ui_status', 'ui_toast', 'vcs_state_changed'} else 'unknown'
            if subtype == 'commands_changed':
                header['exact_notice_fields'] = set(event) == {
                    'type', 'subtype', 'session_id', 'uuid', 'commands'}
                header['commands_empty'] = event.get('commands') == []
                value = event.get('uuid')
                try:
                    header['canonical_uuid'] = isinstance(value, str) and str(uuid.UUID(value)) == value
                except ValueError:
                    header['canonical_uuid'] = False
            elif subtype == 'init':
                header['tools_empty'] = event.get('tools') == []
                header['mcp_empty'] = event.get('mcp_servers') == []
            elif subtype == 'ui_invalidate':
                header['exact_render_notice_fields'] = set(event) == {
                    'type', 'subtype', 'event', 'session_id', 'uuid'}
                header['event_is_render'] = event.get('event') == 'ui.render'
                header['instances_absent'] = 'instances' not in event
                value = event.get('uuid')
                try:
                    header['canonical_uuid'] = isinstance(value, str) and str(uuid.UUID(value)) == value
                except ValueError:
                    header['canonical_uuid'] = False
        headers.append(header)
    return {'bounded': True, 'headers': headers}


def synthetic_png():
    def chunk(kind, payload):
        return struct.pack('>I', len(payload)) + kind + payload + struct.pack(
            '>I', zlib.crc32(kind + payload) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 16, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\x80' * 48) * 16)) + chunk(b'IEND', b''))


def main(output, *, requests=2, timeout=40):
    from unittest.mock import patch
    import tacto.claude_result as adapter
    if type(requests) is not int or not 1 <= requests <= 4:
        raise ValueError('requests must be 1..4')
    if type(timeout) not in (int, float) or not 0 < timeout <= 60 or requests * timeout > 120:
        raise ValueError('finite total budget must be at most 120 seconds')
    root = Path(__file__).resolve().parents[2]
    paths = ['examples/probes/claude-stream-framing.py', 'tacto/claude_result.py',
             'tacto/authoring.py', 'tacto/provider_result.py']
    hashes = lambda: {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}
    report = {'scope': 'synthetic PNG only; strict adapter, no GUI', 'requests': [],
              'source_hashes_before': hashes(), 'passed': False}
    original = adapter.parse_claude_events
    deadline = time.monotonic() + requests * timeout
    # Reserve a new owner-only artifact before any paid request. Never append
    # raw streams or overwrite an existing report, even on interruption.
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        try:
            for _ in range(requests):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                observed = {}
                def parse(stdout, session_id):
                    observed.update(stream_headers(stdout, session_id))
                    return original(stdout, session_id)
                with patch.object(adapter, 'parse_claude_events', side_effect=parse):
                    result = adapter.claude_result('Reply with exactly TACTO_FRAME_OK.',
                        image_bytes=synthetic_png(), timeout=min(timeout, remaining))
                metadata = result.to_dict()
                metadata.pop('text', None)
                report['requests'].append({'provider': metadata, 'framing': observed,
                    'answer_verified': result.status == 'ok' and result.text == 'TACTO_FRAME_OK'})
            report['passed'] = len(report['requests']) == requests and all(
                r['answer_verified'] for r in report['requests'])
        finally:
            report['source_hashes_after'] = hashes()
            report['source_stable_during_run'] = report['source_hashes_before'] == report['source_hashes_after']
            report['passed'] = report['passed'] and report['source_stable_during_run']
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    return report


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output')
    parser.add_argument('--requests', type=int, default=2)
    parser.add_argument('--timeout', type=float, default=40)
    args = parser.parse_args()
    result = main(args.output, requests=args.requests, timeout=args.timeout)
    print(json.dumps({'passed': result['passed'], 'requests': len(result['requests'])}))
    raise SystemExit(0 if result['passed'] else 1)
