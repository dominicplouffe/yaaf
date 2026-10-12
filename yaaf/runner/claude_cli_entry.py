"""Runs INSIDE the sandbox container: one stateless run on the Claude Code CLI,
driven directly (``claude -p``) instead of through the Agent SDK.

The reference implementation of a CLI harness (docs/RUNTIMES.md, M1). Same
model, same credential, same tools under the same names — ``mcp__yaaf__board``
either way — so a difference between an SDK run and a CLI run is this file's
fault, not a vendor's. That is why the flags below are the ones the SDK itself
builds from the options ``sandbox_entry.SdkDriver`` sets (read off the SDK's
``subprocess_cli._build_command``, not guessed), and why the result is
classified the way the SDK classifies it: a run fails when the CLI exits
non-zero, and the reason is the error result the CLI reported just before.

Everything a run must do whatever drives it — the trace, the checkpoints, the
per-run cap across resumes, the retry policy, result.json — is
``sandbox_entry.run_harness``. This file only starts the CLI and reads its lines.
Imports ``sandbox_entry`` by bare name, as ``chat_entry`` does.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import sandbox_entry as base

#: The CLI the image installs for the Agent SDK to drive — one binary, two
#: harnesses. Overridable so the contract suite can stand a recorded one in.
CLAUDE = os.environ.get("YAAF_CLAUDE_BIN", "claude")
TOOLS_SERVER = Path(__file__).with_name("yaaf_tools.py")

#: The SDK's class names for what the CLI calls by type, so a trace reads the
#: same whichever harness wrote it.
_BLOCK_NAMES = {"tool_use": "ToolUseBlock", "tool_result": "ToolResultBlock",
                "thinking": "ThinkingBlock", "server_tool_use": "ServerToolUseBlock"}
_MESSAGE_NAMES = {"system": "SystemMessage", "user": "UserMessage",
                  "rate_limit_event": "RateLimitEvent", "stream_event": "StreamEvent"}


def _int(usage: dict, key: str) -> int:
    return int(usage.get(key) or 0)


def _error_result_text(result: dict) -> str:
    """The most informative text in a result with ``is_error`` — the SDK's own
    order (``_internal.query._error_result_text``): ``errors[]``, then
    ``result``, then a non-success subtype, then the HTTP status."""
    errors = [e if isinstance(e, str) else json.dumps(e) for e in result.get("errors") or []]
    if errors:
        return "; ".join(errors)
    text = result.get("result")
    if isinstance(text, str) and text.strip():
        return text.strip()
    subtype = result.get("subtype")
    if isinstance(subtype, str) and subtype and subtype != "success":
        return subtype
    if result.get("api_error_status") is not None:
        return f"API error (HTTP {result['api_error_status']})"
    return "unknown error"


class ClaudeCliDriver(base.CliDriver):
    name = "claude_cli"

    def __init__(self, system_prompt: str, system_prompt_path: Path):
        super().__init__()
        self.system_prompt_path = system_prompt_path
        self.mcp_config_path = self._write_mcp_config()

    def _write_mcp_config(self) -> Path:
        """The yaaf tools as a stdio server, plus every data source this role
        mounts, exactly as the SDK hands them to the CLI. The file carries the
        run token and any source's bearer, so it goes where file credentials
        go: a tmpfs outside /workspace, mode 0600, gone with the container."""
        servers = {
            "yaaf": {"type": "stdio", "command": sys.executable,
                     "args": [str(TOOLS_SERVER)], "env": dict(os.environ)},
            **base._external_mcp_servers(),
        }
        path = base._credential_dir() / f"mcp-{base.RUN_ID}.json"
        path.write_text(json.dumps({"mcpServers": servers}))
        path.chmod(0o600)
        return path

    def argv(self, resume: str | None) -> list[str]:
        # `skills=[...]` becomes Skill(name) rules in the SDK, and its empty
        # `setting_sources` an explicit empty flag: an unlisted skill an agent
        # wrote into the workspace stays dark here exactly as it does there.
        allowed = base._allowed_tools() + [f"Skill({name})" for name in base.skill_names()]
        cmd = [
            CLAUDE, "-p", "--output-format", "stream-json", "--verbose",
            # A file, not the flag's string form: a system prompt can outgrow
            # what one argument may hold.
            "--system-prompt-file", str(self.system_prompt_path),
            "--tools", ",".join(base._builtin_tools()),
            "--allowedTools", ",".join(allowed),
            "--max-turns", "100",
            "--model", base.MODEL,
            "--permission-mode", "bypassPermissions",
            "--mcp-config", str(self.mcp_config_path),
            "--setting-sources=",
            "--effort", base.EFFORT,
        ]
        if base.TASK_BUDGET > 0:
            cmd += ["--task-budget", str(base.TASK_BUDGET)]
        if self.max_usd > 0:
            cmd += ["--max-budget-usd", str(self.max_usd)]
        if resume:
            # The equals form, as the SDK passes it: a value starting with a
            # dash would otherwise parse as a flag of its own.
            cmd.append(f"--resume={resume}")
        return cmd

    def parse(self, record: dict) -> list:
        events: list = []
        kind = record.get("type")
        if record.get("session_id"):
            events.append(base.Session(record["session_id"]))
        if kind == "assistant":
            self.assistant_error = record.get("error") or self.assistant_error
            for block in (record.get("message") or {}).get("content") or []:
                if block.get("type") == "text":
                    events.append(base.Text(block.get("text", "")))
                else:
                    events.append(base.TraceLine({
                        "type": _BLOCK_NAMES.get(block.get("type"), str(block.get("type"))),
                        "repr": json.dumps(block)[:2000]}))
        elif kind == "result":
            if record.get("is_error"):
                self.last_error_result = record
            usage = record.get("usage") or {}
            events.append(base.AttemptResult(
                cost_usd=record.get("total_cost_usd"),
                tokens_in=_int(usage, "input_tokens"),
                tokens_out=_int(usage, "output_tokens"),
                cache_creation=_int(usage, "cache_creation_input_tokens"),
                cache_read=_int(usage, "cache_read_input_tokens"),
                subtype=record.get("subtype"),
            ))
        else:
            if kind == "rate_limit_event":
                info = record.get("rate_limit_info") or {}
                plan = base._plan_windows(SimpleNamespace(raw=info, status=info.get("status")))
                if plan:
                    events.append(base.Plan(plan))
            events.append(base.TraceLine({"type": _MESSAGE_NAMES.get(kind, str(kind)),
                                          "repr": json.dumps(record)[:2000]}))
        return events

    def failure_text(self, exit_code: int, stderr: str) -> str:
        # The SDK's sentence for the same failure, so the transient-error
        # markers and the run summary read the same on either harness.
        if self.last_error_result is not None:
            return ("Claude Code returned an error result: "
                    + _error_result_text(self.last_error_result))
        return (f"Command failed with exit code {exit_code}: "
                f"{stderr[-self.STDERR_TAIL:].strip()}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(ClaudeCliDriver)))
