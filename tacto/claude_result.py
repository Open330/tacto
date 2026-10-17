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
    _CODES = {
        'malformed JSONL': 'malformed_jsonl',
        'invalid event': 'invalid_event',
        'session mismatch': 'session_mismatch',
        'event after terminal result': 'after_terminal',
        'session exposed tools or restarted': 'tools_or_restart',
        'event before initialization': 'before_init',
        'assistant error or nested tool response': 'assistant_error',
        'invalid assistant message': 'assistant_shape',
        'missing observed model': 'missing_model',
        'multiple response models': 'multiple_models',
        'invalid content blocks': 'content_shape',
        'invalid content block': 'content_shape',
        'unexpected or tool content': 'tool_content',
        'incomplete or failed turn': 'failed_turn',
        'unverified final answer': 'answer_mismatch',
        'missing completed-turn usage': 'missing_usage',
        'invalid usage counter': 'invalid_usage',
        'invalid cost telemetry': 'invalid_cost',
        'unexpected event or attempted tool use': 'unexpected_event',
        'provider refused request': 'model_refusal',
        'provider initiated model fallback': 'model_fallback',
        'missing terminal result': 'missing_terminal',
        'CLI exited unsuccessfully': 'nonzero_exit',
    }

    def __init__(self, message, *, diagnostic=None):
        super().__init__(message)
        allowed = {'system_commands_changed', 'system_ui_invalidate', 'system_other', 'assistant', 'result', 'rate_limit_event', 'unknown'}
        self.diagnostic = diagnostic if isinstance(diagnostic, str) and diagnostic in allowed else None
        reason = self._CODES.get(message) if isinstance(message, str) else None
        self.code = 'ClaudeProtocolError' + ('_' + reason if reason else '')


def _count(mapping, key):
    value = mapping.get(key)
    if value is not None and (type(value) is not int or value < 0):
        raise ClaudeProtocolError('invalid usage counter')
    return value


def _observed_refusal_fallback(stdout, session_id):
    """Closed diagnostic only; a rejected response remains rejected.

    CLI 2.1.286 emits informational/synthetic-user events before an explicit
    refusal fallback notice. Do not loosen the one-turn parser to accept them.
    Only a complete same-session stream can identify that failure more precisely.
    """
    try:
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if (not events or len(events) > 1000
                or any(not isinstance(event, dict) or event.get('session_id') != session_id
                       for event in events)
                or events[0].get('type') != 'system' or events[0].get('subtype') != 'init'
                or events[0].get('tools') != [] or events[0].get('mcp_servers') != []
                or sum(event.get('type') == 'result' for event in events) != 1
                or events[-1].get('type') != 'result'):
            return False
        return any(event.get('type') == 'system'
                   and event.get('subtype') == 'model_refusal_fallback'
                   and event.get('trigger') == 'refusal'
                   and event.get('direction') == 'retry'
                   and event.get('scope') == 'session'
                   and all(isinstance(event.get(key), str) and 0 < len(event[key]) <= 256
                           for key in ('original_model', 'fallback_model'))
                   for event in events)
    except (ValueError, TypeError):
        return False


def _observed_refusal_no_fallback(stdout, session_id):
    """Identify an explicit refusal in a complete failed isolated stream.

    CLI 2.1.289 can exit nonzero after informational/synthetic-user events,
    with a success-subtype result whose is_error is true and num_turns is two.
    This is failure classification only, never accepted answer/usage evidence.
    """
    try:
        if not isinstance(stdout, str) or len(stdout.encode('utf8')) > 8 * 1024 * 1024:
            return False
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if (not events or len(events) > 1000 or any(not isinstance(e, dict)
                or e.get('session_id') != session_id for e in events)):
            return False
        initial = [i for i, e in enumerate(events)
                   if e.get('type') == 'system' and e.get('subtype') == 'init']
        if len(initial) != 1:
            return False
        init = events[initial[0]]
        if init.get('tools') != [] or init.get('mcp_servers') != []:
            return False
        seen = set()
        for e in events[:initial[0]]:
            subtype = e.get('subtype')
            if e.get('type') != 'system' or not isinstance(subtype, str) or subtype in seen:
                return False
            if subtype == 'commands_changed':
                valid = set(e) == {'type', 'subtype', 'session_id', 'uuid', 'commands'} and e.get('commands') == []
            elif subtype == 'ui_invalidate':
                valid = set(e) == {'type', 'subtype', 'session_id', 'uuid', 'event'} and e.get('event') == 'ui.render'
            else:
                return False
            value = e.get('uuid')
            if not valid or not isinstance(value, str) or str(uuid.UUID(value)) != value:
                return False
            seen.add(subtype)
        terminal = events[-1]
        if (sum(e.get('type') == 'result' for e in events) != 1
                or terminal.get('type') != 'result' or terminal.get('is_error') is not True
                or terminal.get('subtype') not in ('success', 'error_during_execution')
                or type(terminal.get('num_turns')) is not int or terminal['num_turns'] not in (1, 2)):
            return False
        notices = [e for e in events if e.get('type') == 'system'
                   and e.get('subtype') == 'model_refusal_no_fallback']
        if len(notices) != 1 or any(e.get('type') == 'system'
                and e.get('subtype') == 'model_refusal_fallback' for e in events):
            return False
        notice = notices[0]
        model, identity = notice.get('original_model'), notice.get('uuid')
        return (isinstance(model, str) and 0 < len(model) <= 256
                and isinstance(identity, str) and str(uuid.UUID(identity)) == identity)
    except (ValueError, TypeError):
        return False


def _observed_organization_access_disabled(stdout, session_id):
    """Classify a failed isolated API stream; never accept its text or usage.

    CLI 2.1.296 can emit a synthetic assistant API error and a success-subtype
    result with is_error=True when organization subscription access is off.
    A model discussing that restriction is not itself an access denial.
    """
    try:
        if not isinstance(stdout, str) or len(stdout.encode('utf8')) > 8 * 1024 * 1024:
            return False
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if (len(events) != 3
                or any(type(e) is not dict or e.get('session_id') != session_id for e in events)
                or events[0].get('type') != 'system' or events[0].get('subtype') != 'init'
                or events[0].get('tools') != [] or events[0].get('mcp_servers') != []
                or sum(e.get('type') == 'system' and e.get('subtype') == 'init' for e in events) != 1
                or sum(e.get('type') == 'result' for e in events) != 1):
            return False
        terminal = events[-1]
        if (terminal.get('type') != 'result' or terminal.get('is_error') is not True
                or terminal.get('subtype') not in ('success', 'error_during_execution')
                or type(terminal.get('num_turns')) is not int or terminal['num_turns'] != 1):
            return False
        assistants = [e for e in events if e.get('type') == 'assistant']
        if len(assistants) != 1:
            return False
        assistant = assistants[0]
        message = assistant.get('message')
        if (assistant.get('is_api_error_message') is not True
                or assistant.get('parent_tool_use_id') is not None or type(message) is not dict
                or message.get('role') != 'assistant'):
            return False
        blocks = message.get('content')
        if (type(blocks) is not list or len(blocks) != 1 or type(blocks[0]) is not dict
                or blocks[0].get('type') != 'text' or type(blocks[0].get('text')) is not str):
            return False
        text = blocks[0]['text']
        return (text.startswith('Your organization has disabled Claude subscription access for Claude Code')
                and terminal.get('result') == text
                and all(e.get('type') in ('system', 'assistant', 'result') for e in events))
    except (ValueError, TypeError, RecursionError):
        return False


def parse_claude_events(stdout: str, session_id: str) -> dict:
    """Require one tool-free fresh session and an unambiguous complete answer."""
    initialized = False
    pre_init_notice = False
    pre_init_render_notice = False
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
        if not isinstance(event, dict):
            raise ClaudeProtocolError('invalid event')
        if event.get('session_id') != session_id:
            raise ClaudeProtocolError('session mismatch')
        if finished:
            raise ClaudeProtocolError('event after terminal result')
        kind = event.get('type')
        if kind == 'system' and event.get('subtype') == 'init':
            if initialized or event.get('tools') != [] or event.get('mcp_servers') != []:
                raise ClaudeProtocolError('session exposed tools or restarted')
            initialized = True
        elif not initialized:
            # CLI 2.1.286 can publish this empty slash-command advisory before
            # init. It carries no answer/tool content. Accept only the exact
            # observed shape once; initialization and terminal checks remain.
            if (not pre_init_notice and kind == 'system'
                    and event.get('subtype') == 'commands_changed'
                    and set(event) == {'type', 'subtype', 'session_id', 'uuid', 'commands'}
                    and event.get('commands') == []
                    and isinstance(event.get('uuid'), str) and len(event['uuid']) == 36):
                try:
                    if str(uuid.UUID(event['uuid'])) != event['uuid']:
                        raise ValueError('noncanonical notice identity')
                except ValueError:
                    raise ClaudeProtocolError('event before initialization') from None
                pre_init_notice = True
                continue
            # CLI 2.1.289 also emits a content-free render invalidation during
            # startup. Accept only the observed same-session exact shape once,
            # before init. It is neither a UI instruction nor answer evidence.
            if (not pre_init_render_notice and kind == 'system'
                    and event.get('subtype') == 'ui_invalidate'
                    and set(event) == {'type', 'subtype', 'event', 'session_id', 'uuid'}
                    and event.get('event') == 'ui.render'
                    and isinstance(event.get('uuid'), str) and len(event['uuid']) == 36):
                try:
                    if str(uuid.UUID(event['uuid'])) != event['uuid']:
                        raise ValueError('noncanonical notice identity')
                except ValueError:
                    raise ClaudeProtocolError('event before initialization',
                                              diagnostic='system_ui_invalidate') from None
                pre_init_render_notice = True
                continue
            diagnostic = ('system_commands_changed' if kind == 'system' and event.get('subtype') == 'commands_changed'
                          else 'system_ui_invalidate' if kind == 'system' and event.get('subtype') == 'ui_invalidate'
                          else 'system_other' if kind == 'system'
                          else kind if isinstance(kind, str) and kind in ('assistant','result','rate_limit_event') else 'unknown')
            raise ClaudeProtocolError('event before initialization', diagnostic=diagnostic)
        elif kind == 'system' and event.get('subtype') == 'model_refusal_no_fallback':
            raise ClaudeProtocolError('provider refused request')
        elif kind == 'system' and event.get('subtype') == 'model_refusal_fallback':
            raise ClaudeProtocolError('provider initiated model fallback')
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
        except BaseException:
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
                # Preserve the single-turn/model contract. This applies only
                # to this process and never edits the user's settings file.
                '--settings', '{"switchModelsOnFlag":false}',
                '--system-prompt', 'Return the requested short answer without tools.']
        if model is not None:
            argv += ['--model', model]
        with tempfile.TemporaryDirectory(prefix='tacto-claude-result-') as folder:
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
            except ClaudeProtocolError as exc:
                if _observed_organization_access_disabled(stdout, session_id):
                    raise ProviderUnavailable('organization_access_disabled') from None
                reason = classify_unavailable(stdout, stderr)
                if reason:
                    raise ProviderUnavailable(reason) from None
                if (exc.code in ('ClaudeProtocolError_nonzero_exit', 'ClaudeProtocolError_unexpected_event')
                        and _observed_refusal_no_fallback(stdout, session_id)):
                    raise ClaudeProtocolError('provider refused request') from None
                if (exc.code == 'ClaudeProtocolError_unexpected_event'
                        and _observed_refusal_fallback(stdout, session_id)):
                    raise ClaudeProtocolError('provider initiated model fallback') from None
                raise
    except Exception as exc:
        status = 'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'error'
        reason = getattr(exc, 'reason', None)
        error = (ProviderError(reason, {'not_logged_in': 'Claude CLI is not logged in — run `claude /login`',
                                        'usage_limit': 'Claude usage limit reached',
                                        'organization_access_disabled': 'Organization has disabled Claude Code subscription access'}[reason])
                 if reason else
                 ProviderError(getattr(exc, 'code', type(exc).__name__), 'Claude request exceeded deadline' if status == 'timeout'
                               else 'Claude CLI request or structured response failed'
                               + (' (pre-init event: ' + exc.diagnostic + ')'
                                  if isinstance(exc, ClaudeProtocolError) and exc.diagnostic else '')))
    return ProviderResult(request_id=request_id, requested_provider='claude', provider='claude',
                          transport='cli-json', status=status, text=fields.pop('text', None),
                          error=error, elapsed_seconds=max(0.0, time.monotonic() - started), **fields)
