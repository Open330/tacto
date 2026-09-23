"""Explicit tool-free Claude CLI adapter (validated with CLI 2.1.275).

Result usage is the CLI's completed-turn telemetry; total_cost_usd is its
reported aggregate estimate, not an invoice. No default routing is modified.
"""
from __future__ import annotations

import base64
import json
import math
import os
import signal
import subprocess
import tempfile
import time
import uuid

from .authoring import _provider_env
from .provider_result import ProviderError, ProviderResult, ProviderUnavailable, classify_unavailable


class ClaudeProtocolError(ValueError):
    pass


def _count(mapping, key):
    value = mapping.get(key)
    if value is not None and (type(value) is not int or value < 0):
        raise ClaudeProtocolError('invalid usage counter')
    return value


def parse_claude_events(stdout: str, session_id: str) -> dict:
    """Require one tool-free fresh session and an unambiguous complete answer."""
    initialized = False
    finished = False
    models = set()
    final_text = None
    fields = None
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise ClaudeProtocolError('malformed JSONL') from None
        if not isinstance(event, dict) or event.get('session_id') != session_id or finished:
            raise ClaudeProtocolError('invalid session or terminal boundary')
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            if initialized or event.get('tools') != [] or event.get('mcp_servers') != []:
                raise ClaudeProtocolError('session exposed tools or restarted')
            initialized = True
        elif not initialized:
            raise ClaudeProtocolError('event before initialization')
        elif kind == 'rate_limit_event' or (kind == 'system' and event.get('subtype') in (
                'thinking_tokens',
                # Added by a Claude CLI update (seen 2026-09-23): a notice that
                # the slash-command set changed, emitted right after init. Not a
                # turn, not answer content, not a tool. Unknown, it made every
                # structured call fail as "unexpected event or attempted tool
                # use", and the whole model path looked broken.
                'commands_changed')):
            # Observed on image reasoning calls: progress counters are neither
            # another turn nor answer content. Session/terminal checks above
            # still apply, and final usage comes only from the result event.
            continue
        elif kind == 'assistant':
            if event.get('error') or event.get('parent_tool_use_id') is not None:
                raise ClaudeProtocolError('assistant error or nested tool response')
            message = event.get('message')
            if not isinstance(message, dict) or message.get('role') != 'assistant':
                raise ClaudeProtocolError('invalid assistant message')
            model = message.get('model')
            if not isinstance(model, str) or not model or model == '<synthetic>':
                raise ClaudeProtocolError('missing observed model')
            models.add(model)
            if len(models) != 1:
                raise ClaudeProtocolError('multiple response models')
            blocks = message.get('content')
            if not isinstance(blocks, list):
                raise ClaudeProtocolError('invalid content blocks')
            text = []
            for block in blocks:
                if not isinstance(block, dict):
                    raise ClaudeProtocolError('invalid content block')
                if block.get('type') == 'text' and isinstance(block.get('text'), str):
                    text.append(block['text'])
                elif block.get('type') not in ('thinking', 'redacted_thinking'):
                    raise ClaudeProtocolError('unexpected or tool content')
            if text:
                final_text = '\n'.join(text)
        elif kind == 'result':
            if (event.get('subtype') != 'success' or event.get('is_error') is not False
                    or type(event.get('num_turns')) is not int or event['num_turns'] != 1
                    or event.get('stop_reason') != 'end_turn'
                    or event.get('permission_denials', []) != []):
                raise ClaudeProtocolError('incomplete or failed turn')
            result = event.get('result')
            if not isinstance(result, str) or not result.strip() or result != final_text or len(models) != 1:
                raise ClaudeProtocolError('unverified final answer')
            usage = event.get('usage')
            if not isinstance(usage, dict):
                raise ClaudeProtocolError('missing completed-turn usage')
            cost = event.get('total_cost_usd')
            if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
                raise ClaudeProtocolError('invalid cost telemetry')
            # Claude reports uncached input separately from cache-read input;
            # do not impose Codex's cached <= input relationship on this API.
            model_usage = event.get('modelUsage')
            list_basis = (isinstance(model_usage, dict) and bool(model_usage)
                          and all(isinstance(v, dict) and v.get('costBasis') == 'list'
                                  for v in model_usage.values()))
            uncached = _count(usage, 'input_tokens')
            cache_read = _count(usage, 'cache_read_input_tokens')
            cache_created = _count(usage, 'cache_creation_input_tokens')
            parts = (uncached, cache_read, cache_created)
            total_input = sum(parts) if all(v is not None for v in parts) else None
            fields = dict(text=result, model=next(iter(models)), provider_thread_id=session_id,
                          input_tokens_basis='uncached', total_input_tokens=total_input,
                          cache_creation_input_tokens=cache_created,
                          input_tokens=uncached,
                          output_tokens=_count(usage, 'output_tokens'),
                          cached_input_tokens=cache_read,
                          cost=cost, cost_currency='USD' if cost is not None else None,
                          cost_basis='cli_reported_list_estimate' if cost is not None and list_basis else None)
            finished = True
        else:
            raise ClaudeProtocolError('unexpected event or attempted tool use')
    if not finished:
        raise ClaudeProtocolError('missing terminal result')
    return fields


def input_message(prompt: str, image_bytes: bytes | None = None,
                  image_media_type: str = 'image/png') -> str:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('prompt must be nonempty text')
    blocks = []
    if image_bytes is not None:
        if not isinstance(image_bytes, bytes) or not image_bytes or len(image_bytes) > 5 * 1024 * 1024:
            raise ValueError('image must be nonempty bytes up to 5 MiB')
        valid = {
            'image/png': image_bytes.startswith(b'\x89PNG\r\n\x1a\n'),
            'image/jpeg': image_bytes.startswith(b'\xff\xd8\xff'),
            'image/gif': image_bytes.startswith((b'GIF87a', b'GIF89a')),
            'image/webp': image_bytes.startswith(b'RIFF') and image_bytes[8:12] == b'WEBP',
        }
        if not valid.get(image_media_type):
            raise ValueError('image MIME/signature mismatch')
        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': image_media_type,
                                                  'data': base64.b64encode(image_bytes).decode('ascii')}})
    blocks.append({'type': 'text', 'text': prompt})
    return json.dumps({'type': 'user', 'message': {'role': 'user', 'content': blocks}}) + '\n'


def _run_claude(argv, payload, timeout, cwd):
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env=_provider_env(), cwd=cwd,
                          start_new_session=(os.name == 'posix')) as proc:
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise
        return proc.returncode, stdout, stderr


def claude_result(prompt: str, *, model: str | None = None, timeout: float = 120,
                  image_bytes: bytes | None = None,
                  image_media_type: str = 'image/png') -> ProviderResult:
    """Opt-in fresh Claude inference; text/image stdin and tools disabled by CLI.

    model is a requested alias; the returned model comes from assistant events.
    No raw prompt, image, stdout or stderr is persisted by this adapter. CLI
    no-session-persistence does not imply that the remote provider stores none.
    """
    started = time.monotonic()
    request_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    fields, error, status = {}, None, 'ok'
    try:
        payload = input_message(prompt, image_bytes, image_media_type)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError('model must be nonempty text')
        argv = ['claude', '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
                '--verbose', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                '--no-session-persistence', '--safe-mode', '--disable-slash-commands', '--no-chrome',
                '--permission-mode', 'dontAsk', '--permission-prompts', 'none',
                '--session-id', session_id, '--system-prompt-snapshot', 'off',
                '--system-prompt', 'Return the requested short answer without tools.']
        if model is not None:
            argv += ['--model', model]
        with tempfile.TemporaryDirectory(prefix='nlc-claude-result-') as folder:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('deadline exceeded')
            code, stdout, stderr = _run_claude(argv, payload, remaining, folder)
            # Only a run that failed is classified: an answer that happens to
            # talk about quotas is still an answer. The streams are mapped to a
            # closed set of codes and then dropped, never kept.
            try:
                if code != 0:
                    raise ClaudeProtocolError('CLI exited unsuccessfully')
                fields = parse_claude_events(stdout, session_id)
            except ClaudeProtocolError:
                reason = classify_unavailable(stdout, stderr)
                if reason:
                    raise ProviderUnavailable(reason) from None
                raise
    except Exception as exc:
        status = 'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'error'
        reason = getattr(exc, 'reason', None)
        error = (ProviderError(reason, {'not_logged_in': 'Claude CLI is not logged in — run `claude /login`',
                                        'usage_limit': 'Claude usage limit reached'}[reason])
                 if reason else
                 ProviderError(type(exc).__name__, 'Claude request exceeded deadline' if status == 'timeout'
                               else 'Claude CLI request or structured response failed'))
    return ProviderResult(request_id=request_id, requested_provider='claude', provider='claude',
                          transport='cli-json', status=status, text=fields.pop('text', None),
                          error=error, elapsed_seconds=max(0.0, time.monotonic() - started), **fields)
