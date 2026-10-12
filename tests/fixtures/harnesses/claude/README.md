# Recorded `claude -p` streams

What the Claude Code CLI writes with `--output-format stream-json --verbose`,
one JSON object per line — the input to `tests/selftest_invariants/test_harness_contract.py`,
which replays each one through **both** Claude harnesses (`claude_cli` via a
stand-in binary, `claude_agent_sdk` via the SDK's own message parser) and
requires the same result from each.

| File | Exit | Provenance |
|---|---|---|
| `text_reply.jsonl` | 0 | **Recorded**, Claude Code 2.1.296, 2026-10-12: a one-line reply, with a `rate_limit_event` carrying the plan meter. |
| `invalid_key.jsonl` | 1 | **Recorded**, same CLI, with a deliberately invalid API key: the dead-credential shape — `error: authentication_failed`, `api_error_status: 401`, and the prose "Invalid API key · Fix external API key" that no marker matched before. |
| `connection_drop.jsonl` | 1 | **Synthetic**, built from the recorded shapes: a reply cut off by a transient API failure (`terminal_reason: api_error`). |

The recorded `system/init` lines are trimmed to the fields a harness reads;
everything else is as the CLI wrote it. Re-record against the version the
sandbox image pins when it moves: run the harness's argv with
`ANTHROPIC_API_KEY` set to an invalid key (free) or a real one (not free), and
keep stdout.
