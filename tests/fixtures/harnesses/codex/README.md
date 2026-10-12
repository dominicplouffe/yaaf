# Recorded `codex exec --json` streams

What the Codex CLI writes to stdout with `--json`, one JSON object per line —
the input to `tests/selftest_invariants/test_codex_harness.py`, which replays
each through `codex_cli_entry.CodexCliDriver` behind a stand-in binary.

**Every line here is the real CLI's (0.162.1, the version the sandbox image
pins), recorded 2026-10-12** — against a local stand-in for the OpenAI
Responses API, configured as a Codex model provider, because `api.openai.com`
was not reachable from where they were recorded. So the *CLI's* output is
real; the *model's* replies, usage figures and the 401 body are the stand-in's,
built from the Responses API's documented shapes.

| File | Exit | What |
|---|---|---|
| `text_reply.jsonl` | 0 | A one-line reply: `thread.started`, a `turn.completed` with usage. |
| `tool_call.jsonl` | 0 | A call to `board` on yaaf's real stdio tool server (`yaaf_tools.py`), as an `mcp_tool_call` item. |
| `web_search.jsonl` | 0 | A hosted web search, as a `web_search` item. |
| `invalid_key.jsonl` | 1 | A 401: an `error` and a `turn.failed` whose message carries the status. |
| `tool_call.rollout.jsonl` | — | The `token_count` lines of the session file Codex wrote for `tool_call` — one per model call, each with the thread's running total. The governor reads these. |

Each stream opens with a model-metadata warning because the stand-in was
asked for a model name this CLI's presets do not list; a real seat names one
they do (`yaaf/runtimes/catalog.py`).

Re-record when the image's pin moves: point a model provider at a stand-in
(`base_url`, `env_key`, `wire_api = "responses"`), run the driver's own argv,
and keep stdout — and re-check every config key with `--strict-config`, which
refuses one it does not know.
