# Recorded `opencode run --format json` streams

What the OpenCode CLI writes to stdout with `--format json`, one JSON object
per line — the input to `tests/selftest_invariants/test_opencode_harness.py`,
which replays each through `opencode_cli_entry.OpenCodeDriver` behind a
stand-in binary.

**Every line here is the real CLI's (1.19.0, the version the sandbox image
pins), recorded 2026-10-12** — against a local stand-in for an
OpenAI-compatible chat API, configured as the run's one provider, with the
network cut to loopback. So the *CLI's* output is real; the *model's* replies,
usage figures and error bodies are the stand-in's.

| File | Exit | What |
|---|---|---|
| `text_reply.jsonl` | 0 | A one-line reply: `step_start`, `text`, `step_finish` with tokens and cost. |
| `tool_call.jsonl` | 0 | A call to `yaaf_board` on yaaf's real stdio tool server (`yaaf_tools.py`), under the driver's locked-down config, then a reply: two `step_finish` lines, one per model call. |
| `invalid_key.jsonl` | 1 | A 401: one `error` event with `data.statusCode`; OpenCode does not retry it. |
| `rate_limited.jsonl` | 1 | A 429 on every call: one `error` event, after OpenCode's own five silent retries. |
| `server_error.jsonl` | 1 | A 500 on every call, likewise. |

Every `step_finish` states 600 uncached input tokens, 40 output, 10 of
reasoning and 400 read from the cache — the stand-in's 1,000-token prompt with
400 cached and a 50-token completion, as OpenCode splits them.

Re-record when the image's pin moves: run the driver's config against a
stand-in (`provider.yaaf.options.baseURL`), keep stdout, and re-check the
lockdown — plant a plugin in the workspace's `.opencode/` and confirm the
driver moves it aside, and confirm a forged call to a denied tool is refused.
The driver pre-seeds OpenCode's plugin package at the pinned version
(`VERSION`); move both together.
