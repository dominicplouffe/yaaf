# Recorded `gemini --output-format stream-json` streams

What the Gemini CLI writes to stdout with `--output-format stream-json`, one
JSON object per line, and the local telemetry file it writes beside — the
input to `tests/selftest_invariants/test_gemini_harness.py`, which replays each
through `gemini_cli_entry.GeminiCliDriver` behind a stand-in binary.

**Every stream here is the real CLI's (0.63.0, the version the sandbox image
pins), recorded 2026-10-12** — against a local stand-in for the Gemini API
(`GOOGLE_GEMINI_BASE_URL`), with the network cut to loopback. So the *CLI's*
output is real; the *model's* replies, usage figures and error bodies are the
stand-in's, and the MCP server in `tool_call` was a small stand-in for
`yaaf_tools.py` (its `whoami` tool and `cwd` are that stand-in's).

| File | Exit | What |
|---|---|---|
| `text_reply.jsonl` | 0 | `init`, the user turn, an assistant delta, a `result` with stats. |
| `tool_call.jsonl` | 0 | Two MCP calls and a shell call the admin policy refused (`tool_not_registered`), then the answer — under the driver's own flags (`--approval-mode yolo`, `--admin-policy`). |
| `invalid_key.jsonl` | 145 | A 401: a `result` with `status: "error"`. The exit is the HTTP status mod 256. |
| `api_key_invalid.jsonl` | 144 | A 400 `API_KEY_INVALID` — Google's word for a dead key. |
| `rate_limited.jsonl` | 173 | A 429 after the CLI's own three attempts (`general.maxAttempts: 3`). Its message carries no status: the exit code does. |
| `server_error.jsonl` | 244 | A 500, likewise. |
| `tool_call.telemetry.log` | — | The `gemini_cli.api_response` records the CLI wrote for `tool_call`, one per model call, thinking tokens included — what the governor reads. Trimmed to those records, without the process `resource` block (which carried the probe's paths) or the installation id. |
| `text_reply.telemetry.log` | — | The same record shape with `text_reply`'s figures: 1,200 in, 4 out, 9 of thinking. |

The `result` line's `output_tokens` leaves out the thinking; the telemetry
does not. That difference is why the driver bills from the telemetry.

Re-record when the image's pin moves: point `GOOGLE_GEMINI_BASE_URL` at a
stand-in, run the driver's own argv and settings, and keep stdout and the
telemetry outfile — and re-check what the driver relies on that the CLI does
not document: that `--skip-trust` keeps a workspace's `.gemini/settings.json`
out, which model names it rewrites, and that a policy file it cannot parse
says `Policy file error` on stderr.
