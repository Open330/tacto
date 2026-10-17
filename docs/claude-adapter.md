# Claude CLI result adapter

`tacto.claude_result.claude_result(prompt, *, model=None, timeout=120,
image_bytes=None, image_media_type='image/png')` is the restricted Claude
adapter used by planning and the visual API when provider="claude". It can
also be called directly for a structured result.

Each invocation supplies one user message on stdin in a fresh temporary working
directory. It creates separate Tacto request and Claude session UUIDs. The CLI is
started with `--tools ''`, strict empty MCP configuration, safe mode, disabled
skills/Chrome, denied permission prompts, and `--no-session-persistence`.
`--system-prompt-snapshot off` also disables rendered prompt snapshots. Startup
must report an empty tools and MCP list; tool messages are rejected.

The adapter reports model identity from actual assistant messages, never from
the requested alias. Multiple response models are rejected. A session ID is
stored as `provider_thread_id`; API request identity remains unknown. Usage
comes from the successful terminal result, not partial assistant messages.
Claude's input counter excludes separately reported cached input, so cached
input can exceed input. Cache creation counts are preserved as
`cache_creation_input_tokens`; `total_input_tokens` sums ordinary, cached-read
and cache-creation input when all three are observed. Cost, when present, is the CLI's `total_cost_usd`, with USD
as its explicit source unit. This is a reported estimate, not a billing receipt,
and can include auxiliary models that do not produce the answer. When every
reported model entry explicitly has `costBasis=list`, `cost_basis` is
`cli_reported_list_estimate`; absent or mixed basis metadata leaves it null.
The top-level usage counters describe the answered turn, while total cost can
include additional model usage. They must not be combined to derive a token price.

The CLI measured on 2026-09-19 was **Claude Code 2.1.275**. That day's authorized minimal
text call returned these observed facts:

- Initialization: `tools=[]`; assistant model: `claude-opus-5`.
- Terminal: `subtype=success`, `is_error=false`, `num_turns=1`, `stop_reason=end_turn`.
- Terminal usage: input 466, output 22, cache-read 0.
- Assistant message's partial output counter was 8; it is not used.
- `total_cost_usd=0.003876`. Per-model metadata included both the response model
  and `claude-haiku-4-5-20251001`, with `costBasis=list`.
- Reply matched the fixed benign expected marker. Raw stdout, stderr and prompt
  were not saved as a log.

`claude --help` on that installation confirms stream-JSON input/output and all
isolation flags used above. The user-message envelope follows the official
[Agent SDK client](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/client.py).
Optional images use an inline base64 content block with the MIME type described
by [Anthropic's vision documentation](https://platform.claude.com/docs/en/build-with-claude/vision).
The adapter accepts PNG, JPEG, GIF and WebP bytes up to 5 MiB, checks their header
against the supplied MIME type, and transmits image before text. Header checking
is not full image decoding. Image request construction is covered by tests. A real browser canvas probe initially exposed an unrecognized `system/thinking_tokens` event. The parser now accepts that progress event within an initialized session while preserving terminal and session checks. The next actual image call produced the correct coordinate and one independently verified click; both attempts remain in `reviews/2026-09-19/visual-action-claude-probe.jsonl`.

Malformed, incomplete, multi-turn, session-mismatched, failed or tool-bearing
streams never yield successful text. A local timeout kills the CLI process group;
it does not promise remote cancellation. The adapter does not persist prompts,
images or raw provider output. CLI session non-persistence is not a claim about
remote-provider data retention. Admin-managed policies and provider-side service
behavior remain outside this adapter's control.

## Default planning route

The default authoring/general Claude route now calls this restricted adapter through
`authoring._try_cli`. It does not first call muxa for Claude and does not fall back
to unrestricted `claude -p` if the adapter fails. The legacy string API is retained;
`ask_model_result` additionally forwards observed model, usage, session ID, and
CLI cost metadata. Its local request ID remains separate from the adapter session.

Explicit provider selection remains pinned. `auto` preserves provider order and
logs each transition to another provider without prompts or raw provider errors.
Other providers retain their existing transports: an auto fallback to Codex,
OpenCode, or another provider **does not inherit Claude's inference-only boundary**.
A caller needing this boundary must currently pin `claude`. Muxa priority can be
restored for Claude only after an enforced inference-only transport is available.
Launcher requests reach the same planning route, but its current string protocol
does not propagate model/usage/cost metadata back to the caller.

Candidate paging bounds the rendered request, not the entire CLI/provider context.
Fixed adapter instructions and any provider-internal framing are additional input;
actual returned usage is observation, not a prior context-budget guarantee.
