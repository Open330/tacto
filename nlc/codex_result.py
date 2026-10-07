"""Structured Codex CLI adapter used by the common local provider route.

Validated against local 0.155.1 CLI help, live 0.159.2 JSONL, and the public exec
contract. The available local Rust checkout is not known to match that
binary, so incompatible event streams fail rather than fall back to raw text.
Reported token counts are CLI telemetry, not a billing receipt. A requested
model is passed verbatim but never claimed as an observed actual model.
"""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import uuid
import json
import logging

from .authoring import _provider_env
from .provider_result import ProviderError, ProviderResult, ProviderUnavailable, classify_unavailable


class CodexProtocolError(ValueError):
    pass


def parse_codex_events(stdout: str, final_text: str) -> dict:
    """Accept one complete fresh turn and its independently exported final text."""
    thread_id = None
    started = completed = False
    final_agent = None
    usage = None
    startup_diagnostics = 0
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            raise CodexProtocolError('malformed JSONL') from None
        if not isinstance(event, dict) or not isinstance(event.get('type'), str):
            raise CodexProtocolError('invalid event')
        kind = event['type']
        if completed:
            raise CodexProtocolError('events after terminal result')
        if kind in ('error', 'turn.failed'):
            raise CodexProtocolError('provider reported an error')
        if kind == 'thread.started':
            if thread_id is not None or started or not isinstance(event.get('thread_id'), str) or not event['thread_id']:
                raise CodexProtocolError('invalid thread boundary')
            thread_id = event['thread_id']
        elif kind == 'turn.started':
            if thread_id is None or started:
                raise CodexProtocolError('invalid turn boundary')
            started = True
        elif kind.startswith('item.'):
            if kind not in ('item.started', 'item.updated', 'item.completed'):
                raise CodexProtocolError('item outside turn')
            item = event.get('item')
            if not isinstance(item, dict):
                raise CodexProtocolError('invalid item')
            if not started:
                # CLI 0.159.2 emits nonterminal startup diagnostics here.
                # Accept only their exact observed shape, after a thread was
                # opened. A completed turn and matching final file remain
                # mandatory; errors within a turn still reject the response.
                if (thread_id is None or kind != 'item.completed'
                        or set(item) != {'id', 'type', 'message'}
                        or item['type'] != 'error'
                        or not isinstance(item['id'], str) or not 0 < len(item['id']) <= 128
                        or not isinstance(item['message'], str)
                        or not 0 < len(item['message']) <= 16384):
                    raise CodexProtocolError('item outside turn')
                startup_diagnostics += 1
                if startup_diagnostics > 32:
                    raise CodexProtocolError('too many startup diagnostics')
                continue
            if item.get('type') == 'error' or item.get('status') == 'failed':
                raise CodexProtocolError('provider item failed')
            if kind == 'item.completed' and item.get('type') == 'agent_message':
                if not isinstance(item.get('text'), str):
                    raise CodexProtocolError('invalid agent message')
                final_agent = item['text']
        elif kind == 'turn.completed':
            if not started or final_agent is None:
                raise CodexProtocolError('incomplete turn')
            usage = event.get('usage')
            if not isinstance(usage, dict):
                raise CodexProtocolError('missing usage')
            for key in ('input_tokens', 'output_tokens'):
                if type(usage.get(key)) is not int or usage[key] < 0:
                    raise CodexProtocolError('invalid usage')
            cached = usage.get('cached_input_tokens')
            if cached is not None and (type(cached) is not int or cached < 0 or cached > usage['input_tokens']):
                raise CodexProtocolError('invalid cached usage')
            cache_written = usage.get('cache_write_input_tokens')
            if cache_written is not None and (type(cache_written) is not int or cache_written < 0):
                raise CodexProtocolError('invalid cache write usage')
            completed = True
        else:
            raise CodexProtocolError('unsupported event type')
    if not completed or not final_text.strip() or final_text.strip() != final_agent.strip():
        raise CodexProtocolError('missing or inconsistent final answer')
    if startup_diagnostics:
        logging.getLogger(__name__).debug('Codex returned %d pre-turn diagnostic items',
                                         startup_diagnostics)
    return dict(text=final_text.strip(), provider_thread_id=thread_id,
                input_tokens=usage['input_tokens'], output_tokens=usage['output_tokens'],
                input_tokens_basis='total', total_input_tokens=usage['input_tokens'],
                cache_creation_input_tokens=usage.get('cache_write_input_tokens'),
                cached_input_tokens=usage.get('cached_input_tokens'))


def _run_codex(argv: list[str], prompt: str, timeout: float):
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, env=_provider_env(),
                          start_new_session=(os.name == 'posix')) as proc:
        try:
            stdout, stderr = proc.communicate(input=prompt, timeout=timeout)
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
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)


def codex_result(prompt: str, *, model: str | None = None,
                 timeout: float = 420, image_bytes: bytes | None = None) -> ProviderResult:
    """Call read-only ephemeral Codex explicitly, with prompt only on stdin.

    The common routing uses this adapter for the local Codex CLI. It reports
    actual model and API request ID as unknown: exec JSONL exposes a thread ID,
    which is stored separately. Local timeout cannot guarantee remote cancel.
    """
    started = time.monotonic()
    request_id = str(uuid.uuid4())
    fields = {}
    error, status = None, 'ok'
    try:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError('prompt must be nonempty text')
        if type(timeout) not in (int, float) or not 0 < timeout < float('inf'):
            raise ValueError('timeout must be positive and finite')
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ValueError('model must be nonempty text')
        with tempfile.TemporaryDirectory(prefix='nlc-codex-result-') as folder:
            output = Path(folder) / 'final.txt'
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            argv = ['codex', 'exec', '--json', '--ephemeral', '--sandbox', 'read-only',
                    '--output-last-message', str(output)]
            if image_bytes is not None:
                from .provider_images import encode_image, validate_model
                encode_image(image_bytes); validate_model(model)
                image = Path(folder) / 'image.png'
                fd = os.open(image, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(image_bytes)
                schema = Path(folder) / 'answer.schema.json'
                schema.write_text(json.dumps({'type':'object','properties':{'answer':{'type':'string'}},
                    'required':['answer'],'additionalProperties':False}))
                argv += ['--ignore-user-config', '--skip-git-repo-check', '--cd', folder,
                         '--image', str(image), '--output-schema', str(schema)]
            if model is not None:
                argv += ['--model', model]
            argv += ['-']
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("deadline exceeded")
            response = _run_codex(argv, prompt, remaining)
            if response.returncode != 0:
                reason = classify_unavailable(response.stdout, response.stderr)
                if reason:
                    raise ProviderUnavailable(reason)
                raise CodexProtocolError('CLI returned nonzero status')
            try:
                fields = parse_codex_events(response.stdout, output.read_text())
            except CodexProtocolError:
                reason = classify_unavailable(response.stdout, response.stderr)
                if reason:
                    raise ProviderUnavailable(reason) from None
                raise
    except Exception as exc:
        status = 'timeout' if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else 'error'
        error = ProviderError(getattr(exc, 'reason', type(exc).__name__), 'Codex request exceeded deadline' if status == 'timeout'
                              else 'Codex CLI request or structured response failed')
    return ProviderResult(request_id=request_id, requested_provider='codex', provider='codex',
                          transport='cli-json', status=status, text=fields.pop('text', None),
                          error=error, elapsed_seconds=max(0.0, time.monotonic()-started), **fields)
