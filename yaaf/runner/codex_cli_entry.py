"""Runs INSIDE the sandbox container: one stateless run on the Codex CLI
(`codex exec --json`), OpenAI's agent, driven the way the Claude CLI is.

The first CLI that is not Claude's (docs/RUNTIMES.md, M4), and so the test of
whether `CliDriver` was general or merely Claude-shaped. What it needed beyond
`argv` and `parse` is all here, and every line of it was read off the CLI the
image pins (0.162.1) rather than guessed — its `--strict-config` refuses a key
it does not know, so each one below was checked by being accepted:

- **A config file per run, rewritten every attempt**, in a home of its own on
  the credential tmpfs. Codex reads `.codex/config.toml` and `AGENTS.md` from
  the workspace it runs in, and trusts that workspace by writing so into its
  home after the first run — so an agent could plant either and steer the next
  run. The workspace is named untrusted, project docs are capped at zero, and
  the home is never reused.
- **Its own prompt replaced**, not appended to (`model_instructions_file`), as
  the Claude harnesses replace Claude Code's.
- **Tools withheld per grant.** The shell (`shell_tool`, `unified_exec`) only
  for `bash` or `data_processing`; search (`web_search`) only for `web`; the
  rest of what Codex brings — sub-agents, goals, images, plugins, its bundled
  skills — never. Codex has no file tools but its shell, so a role granted
  files and not a shell gets yaaf's own (Read, Write, Edit, Glob, Grep) from
  the stdio tool server instead of a shell it was not hired with.
- **The per-tool allowlist is Codex's own** (`enabled_tools`), and it refuses a
  call outside it rather than only hiding the tool — so the data-source proxy
  the design held in reserve for a CLI without one is not needed.
- **The governor reads the session files.** `--json` reports usage once, at
  the end of the turn, and only for the thread it ran; the session files Codex
  writes as it goes — one per thread, sub-agents included, which the models it
  knows best are given whatever the config says — record every model call's
  running total. The driver tails all of them, prices the run as it goes, and
  kills the CLI at the line: the cap is a stop, not an invoice, and a
  sub-agent is spend like any other.
- **Retries are bounded.** Codex retries a dead network forever by default
  (`unbounded_connection_retries`), which would hang a run to its timeout and
  bill the wait. The run's own resume policy is the retry.

Everything a run must do whatever drives it is `sandbox_entry.run_harness`.
Imports `sandbox_entry` by bare name, as every entry beside it does.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import sandbox_entry as base

#: Overridable so the contract suite can stand a recorded one in.
CODEX = os.environ.get("YAAF_CODEX_BIN", "codex")
TOOLS_SERVER = Path(__file__).with_name("yaaf_tools.py")
#: Where an API key's money goes, named in the run's own config rather than
#: left to the CLI's built-in provider.
OPENAI_BASE_URL = "https://api.openai.com/v1"
#: What Codex brings that no yaaf role grants, ever.
FEATURES_OFF = ("goals", "multi_agent", "view_image", "image_generation", "plugins", "apps",
                "skill_search", "unbounded_connection_retries")
#: Codex states an API failure as prose with the status in it.
STATUS = re.compile(r"unexpected status (\d{3})")
WATCH_INTERVAL_S = 0.25
#: The model credentials no shell and no tool server is handed: nothing an
#: agent runs needs to pay for the model (SECURITY.md).
MODEL_CREDENTIAL_VARS = ("OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_AUTH_JSON",
                         "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


class BudgetReached(RuntimeError):
    """The per-run cap is spent. Not transient: resuming is spending more."""


class Unpriced(RuntimeError):
    """A model metered in dollars with no price to meter it by (R3)."""


def _toml(value) -> str:
    """One TOML value. JSON's strings, numbers and arrays are TOML's."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + " }"
    return json.dumps(value)


def _table(name: str, values: dict) -> str:
    return f"\n[{name}]\n" + "".join(f"{k} = {_toml(v)}\n" for k, v in values.items())


class CodexCliDriver(base.CliDriver):
    name = "codex_cli"

    def __init__(self, system_prompt: str, system_prompt_path: Path):
        super().__init__()
        # Run-keyed, on the tmpfs credentials go to, never reused: the home
        # holds the login, the config and the session, and a home a second run
        # inherited would carry the first run's trust in the workspace.
        self.home = base._credential_dir() / f"codex-{base.RUN_ID}"
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.instructions = self.home / "instructions.md"
        self.instructions.write_text(system_prompt + base.inline_skills())
        login = os.environ.get("CODEX_AUTH_JSON")
        self.subscription = bool(login)
        if login:
            auth = self.home / "auth.json"
            auth.write_text(login)
            auth.chmod(0o600)
        self.thread_id: str | None = None
        self.failure: dict | None = None
        self.billed = False
        self.cut: float | None = None
        self.web_searches = 0
        #: Per session file: how far it has been read, and its running total
        #: when this attempt began and now. Totals, not per-call figures, so a
        #: line Codex writes twice cannot be counted twice.
        self._offsets: dict[Path, int] = {}
        self._start: dict[Path, dict] = {}
        self._now: dict[Path, dict] = {}

    # ── what the run is told and offered ──────────────────────────────────

    def _shell(self) -> bool:
        return bool(base.PERMS.get("bash") or base.PERMS.get("data_processing"))

    def _file_tools(self) -> bool:
        """yaaf's file tools, for a role granted files and not a shell."""
        return bool(base.PERMS.get("files", True)) and not self._shell()

    def config(self) -> str:
        shell = self._shell()
        hidden = list(MODEL_CREDENTIAL_VARS)
        out = "".join(f"{k} = {_toml(v)}\n" for k, v in {
            "model": base.MODEL.removeprefix("openai/"),
            "model_reasoning_effort": base.EFFORT,
            "approval_policy": "never",
            "sandbox_mode": "danger-full-access",
            "model_instructions_file": str(self.instructions),
            "project_doc_max_bytes": 0,
            "web_search": "live" if base.PERMS.get("web") else "disabled",
            "check_for_update_on_startup": False,
            **({} if self.subscription else {"model_provider": "yaaf_openai"}),
        }.items())
        out += _table("features", {"shell_tool": shell, "unified_exec": shell,
                                   **{f: False for f in FEATURES_OFF}})
        out += _table("skills", {"include_instructions": False})
        out += _table("analytics", {"enabled": False})
        out += _table("feedback", {"enabled": False})
        # The shell inherits the run's environment — role-granted secrets are
        # the agent's to use — minus the model's own credential. Codex's
        # default drops anything named like a key or a token, which would
        # take the role's credentials with it.
        out += _table("shell_environment_policy", {"inherit": "all",
                                                   "ignore_default_excludes": True,
                                                   "exclude": hidden})
        out += _table(f'projects.{json.dumps(str(base.WORKSPACE))}', {"trust_level": "untrusted"})
        if not self.subscription:
            out += _table("model_providers.yaaf_openai", {
                "name": "OpenAI", "base_url": OPENAI_BASE_URL, "env_key": "OPENAI_API_KEY",
                "wire_api": "responses", "request_max_retries": 2, "stream_max_retries": 2})
        tools = [t.name for t in base.run_tools()]
        server_env = {k: v for k, v in os.environ.items() if k not in hidden}
        if self._file_tools():
            server_env["YAAF_SERVE_FILE_TOOLS"] = "1"
            tools += ["Read", "Write", "Edit", "Glob", "Grep"]
        out += _table("mcp_servers.yaaf", {"command": sys.executable,
                                           "args": [str(TOOLS_SERVER)], "env": server_env,
                                           "enabled_tools": tools, "startup_timeout_sec": 30,
                                           "tool_timeout_sec": 300})
        specs = base._data_source_specs()
        for source, mount in base._external_mcp_servers().items():
            if mount.get("type") == "sse":
                continue  # Codex speaks stdio and streamable HTTP; said in the trace.
            values: dict = {"url": mount["url"]}
            if mount.get("headers"):
                values["http_headers"] = mount["headers"]
            if specs[source].get("tools") is not None:
                values["enabled_tools"] = list(specs[source]["tools"])
            out += _table(f"mcp_servers.{json.dumps(source)}", values)
        return out

    def argv(self, resume: str | None) -> list[str]:
        cmd = [CODEX, "exec", "--json", "--skip-git-repo-check", "-C", str(base.WORKSPACE)]
        return cmd + (["resume", resume, "-"] if resume else ["-"])

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env.pop("CODEX_AUTH_JSON", None)  # in the home now; never in a child's env
        env["CODEX_HOME"] = str(self.home)
        return env

    # ── the attempt, with the governor beside it ───────────────────────────

    async def attempt(self, prompt: str, resume: str | None = None):
        if base.PRICE is None and not self.subscription:
            raise Unpriced(f"{base.MODEL} has no price on this install, and an API key's "
                           "spend is metered by it — a run nobody can price is a run the "
                           "budget cannot stop")
        (self.home / "config.toml").write_text(self.config())
        self.failure, self.billed, self.cut, self.web_searches = None, False, None, 0
        # What every session file stood at before this attempt: a resumed
        # thread appends to the file it began, and that much is already billed.
        self._read_rollouts()
        self._start = {path: dict(total) for path, total in self._now.items()}
        for source, mount in base._external_mcp_servers().items():
            if mount.get("type") == "sse":
                yield base.TraceLine({"type": "data_source_unmounted", "source": source,
                                      "why": "Codex reaches MCP over stdio or streamable HTTP, "
                                             "not SSE"})
        watcher = asyncio.create_task(self._watch())
        try:
            async for event in super().attempt(prompt, resume):
                yield event
        except base.CliRunError:
            # Killed at the cap, or failed mid-turn: the turn never reported,
            # so what the session files saw is the bill.
            self._read_rollouts()
            if not self.billed and self._spent_tokens():
                yield self._bill(self._spent_tokens(), "interrupted")
            if self.cut is not None:
                raise BudgetReached(f"per-run cap reached mid-attempt: ${self.cut:.4f} "
                                    f"spent of ${self.max_usd:g} left") from None
            raise
        finally:
            watcher.cancel()

    _KEYS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens")

    def _read_rollouts(self) -> None:
        """Every session file in this run's home, read on from where it was
        left: each thread's latest running total."""
        for path in sorted((self.home / "sessions").rglob("rollout-*.jsonl")):
            offset = self._offsets.get(path, 0)
            with path.open() as handle:
                handle.seek(offset)
                for line in handle:
                    if not line.endswith("\n"):
                        break  # a line still being written: read it next time
                    offset += len(line.encode())
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    payload = record.get("payload") or {}
                    if isinstance(payload, dict) and payload.get("type") == "token_count":
                        total = (payload.get("info") or {}).get("total_token_usage")
                        if isinstance(total, dict):
                            self._now[path] = total
            self._offsets[path] = offset

    def _spent_tokens(self) -> dict:
        """This attempt's tokens across every thread: each file's total now,
        less what it stood at when the attempt began."""
        out = dict.fromkeys(self._KEYS, 0)
        for path, total in self._now.items():
            before = self._start.get(path, {})
            for key in self._KEYS:
                out[key] += max(0, int(total.get(key) or 0) - int(before.get(key) or 0))
        return out

    async def _watch(self) -> None:
        """Price each model call as Codex records it, and stop the CLI at the
        line. Only under a dollar meter with a price: tokens are capped by the
        project's token budget, between runs."""
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            self._read_rollouts()
            if self.max_usd <= 0 or base.PRICE is None or self.subscription:
                continue
            spent = self._bill(self._spent_tokens(), None).cost_usd or 0.0
            if spent >= self.max_usd:
                self.cut = spent
                self.stop()
                return

    def _bill(self, usage: dict, subtype: str | None) -> base.AttemptResult:
        """A Codex usage as a bill. `input_tokens` counts the cached part;
        `output_tokens` already counts the reasoning."""
        cached = int(usage.get("cached_input_tokens") or 0)
        bill = base.AttemptResult(
            cost_usd=None, tokens_in=max(0, int(usage.get("input_tokens") or 0) - cached),
            tokens_out=int(usage.get("output_tokens") or 0),
            cache_creation=int(usage.get("cache_write_input_tokens") or 0), cache_read=cached,
            subtype=subtype, web_searches=self.web_searches)
        if base.PRICE is not None and not self.subscription:
            from dataclasses import replace

            bill = replace(bill, cost_usd=base._priced(base.PRICE, bill))
        return bill

    # ── reading the stream ─────────────────────────────────────────────────

    def parse(self, record: dict) -> list:
        kind = record.get("type")
        if kind == "thread.started":
            self.thread_id = record.get("thread_id") or self.thread_id
            return [base.Session(self.thread_id)] if self.thread_id else []
        if kind == "turn.completed":
            # The turn's own figure covers its own thread. The session files
            # cover every thread, sub-agents too; the larger is the bill.
            self.billed = True
            self._read_rollouts()
            usage = record.get("usage") or {}
            spent = self._spent_tokens()
            if sum(spent.values()) > sum(int(usage.get(k) or 0) for k in self._KEYS):
                usage = spent
            return [self._bill(usage, "success")]
        if kind == "turn.failed":
            self.failure = record.get("error") if isinstance(record.get("error"), dict) else {
                "message": str(record.get("error"))}
            self.last_error_result = self.failure
            return [base.TraceLine({"type": "turn_failed", "error": self.failure})]
        if kind in ("item.started", "item.completed", "item.updated"):
            item = record.get("item") or {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                return [base.Text(str(item.get("text", "")))]
            if kind == "item.completed" and item.get("type") == "web_search":
                self.web_searches += 1
            if kind != "item.completed":
                return []
            return [base.TraceLine({"type": item.get("type"),
                                    "repr": json.dumps(item)[:2000]})]
        return [base.TraceLine({"type": str(kind), "repr": json.dumps(record)[:2000]})]

    def failure_text(self, exit_code: int, stderr: str) -> str:
        if self.failure is not None:
            return f"Codex turn failed: {self.failure.get('message', self.failure)}"
        return (f"Codex exited {exit_code}: "
                f"{stderr[-self.STDERR_TAIL:].strip()}")

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, BudgetReached | Unpriced):
            return "fatal"
        data = getattr(exc, "data", None)
        message = str((data or {}).get("message") or exc)
        status = STATUS.search(message)
        if status:
            code = int(status.group(1))
            if code in (401, 402, 403):
                return "auth"
            if code in (408, 409, 429) or code >= 500:
                return "transient"
            return "fatal"
        if "stream disconnected" in message or "error sending request" in message:
            return "transient"
        return None


#: What builds this harness's driver — for a run, and for the boardroom
#: conversation (chat_entry), which holds one as resumed attempts.
make_driver = CodexCliDriver


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
