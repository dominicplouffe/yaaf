"""Runs INSIDE the sandbox container: one stateless run on the OpenCode CLI
(`opencode run --format json`), one harness for several providers
(docs/RUNTIMES.md, M5).

OpenCode reaches any provider through an AI SDK provider package; this driver
names one — the OpenAI-compatible one, which OpenCode carries built in — and
points it at the seat's provider, from the same spec yaaf's own loop reads.
Every line below was read off the CLI the image pins (1.19.0), run against a
stand-in chat API with the network cut to loopback, and the facts that shaped
it are each a guard here:

- **A config of its own, rewritten every attempt**, in a home on the
  credential tmpfs: the config, cache and plugin directories are rebuilt
  before each launch, so nothing an agent wrote there survives into the next;
  the session database is kept, so a resume finds its session.
- **Nothing in the workspace steers the run.** Project config, agents,
  commands, instruction files and skills are off by OpenCode's own switches.
  Its plugin loader honours none of them and executes any plugin it finds in
  a `.opencode/` directory or an `opencode.json` between the workspace and
  its repository root — so before every launch those are moved aside, and the
  trace says so.
- **The model key reaches no child.** It is read from a file the config
  names; OpenCode's environment — which its shell and every MCP server
  inherit whole — holds no model credential.
- **Tools by permission.** Everything is denied, then the role's grants are
  allowed back: yaaf's tools, the file tools for `files`, the shell for
  `bash`, fetch for `web`. A denied tool is absent from the request, and a
  call to one is refused by OpenCode, not run. Its sub-agent tool is never
  allowed: a sub-agent's calls never reach the stream, so they could not be
  metered. Nor is its title call made: it is billed and reported nowhere.
- **The governor counts every call.** Each `step_finish` is one model call
  with its tokens; the driver prices it, and stops the CLI — SIGTERM, which
  takes its MCP children with it; it ignores SIGINT — at the dollar cap, or
  at a call limit of its own: OpenCode's `steps` is advice it does not
  enforce, and its compaction can call the model in a loop (off).
- **No search.** OpenCode searches only through its own hosted service, so a
  role granted `web` is refused for it (R2) rather than handed half the web.

Everything a run must do whatever drives it is `sandbox_entry.run_harness`.
Imports `sandbox_entry` by bare name, as every entry beside it does.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sys
from dataclasses import replace
from pathlib import Path

import sandbox_entry as base

#: Overridable so the contract suite can stand a recorded one in.
OPENCODE = os.environ.get("YAAF_OPENCODE_BIN", "opencode")
#: The version the image pins: its plugin package is pre-seeded at it, so
#: OpenCode finds it installed and fetches nothing at startup.
VERSION = "1.19.0"
TOOLS_SERVER = Path(__file__).with_name("yaaf_tools.py")
#: OpenCode's `steps` is not a stop; this is. The SDK harness's own limit.
MAX_STEPS = 100
#: What the workspace may hold that OpenCode would load, plugins first.
PLANTABLE = (".opencode", "opencode.json", "opencode.jsonc")
#: OpenCode's own switches, each verified to close what it names.
LOCKDOWN = {
    "OPENCODE_DISABLE_PROJECT_CONFIG": "1",   # workspace config, agents, AGENTS.md, MCP
    "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",  # .claude/skills, .agents/skills
    "OPENCODE_DISABLE_CLAUDE_CODE": "1",      # CLAUDE.md and .claude/
    "OPENCODE_DISABLE_MODELS_FETCH": "1",     # the catalog it phones home for
    "OPENCODE_DISABLE_AUTOUPDATE": "1",
    "OPENCODE_DISABLE_SHARE": "1",
    "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
    "OPENCODE_DISABLE_AUTOCOMPACT": "1",      # compaction can loop on the model
    "OPENCODE_PURE": "1",
}
#: Variables that would replace the config this driver writes.
OVERRIDES = ("OPENCODE_CONFIG", "OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR",
             "OPENCODE_PERMISSION", "OPENCODE_AUTH_CONTENT", "OPENCODE_MODELS_PATH")
#: The model credentials no shell and no tool server is handed.
MODEL_CREDENTIAL_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                         "CODEX_AUTH_JSON")


class BudgetReached(RuntimeError):
    """The per-run cap is spent. Not transient: resuming is spending more."""


class Unpriced(RuntimeError):
    """A model metered in dollars with no price to meter it by (R3)."""


class OpenCodeDriver(base.CliDriver):
    name = "opencode"

    def __init__(self, system_prompt: str, system_prompt_path: Path):
        super().__init__()
        self.home = base._credential_dir() / f"opencode-{base.RUN_ID}"
        self.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.dirs = {name: self.home / name for name in ("config", "data", "cache", "state")}
        self.instructions = self.home / "instructions.md"
        self.instructions.write_text(system_prompt + base.inline_skills())
        self.spec = base.PROVIDER
        self.key_env = str(self.spec.get("key_env") or "")
        self.key_file = self.home / "key"
        self.key_file.write_text(os.environ.get(self.key_env, ""))
        self.key_file.chmod(0o600)
        self.session_id: str | None = None
        self.failure: dict | None = None
        self.cut: float | None = None
        self.capped = False
        self.steps = 0
        self.spent = 0.0
        #: Searches made through yaaf's tool server, billed as they land.
        self.searches = base.SearchLedger()

    # ── what the run is told and offered ──────────────────────────────────

    def _model(self) -> str:
        return base.MODEL.removeprefix(f"{self.spec.get('name')}/")

    def _hidden(self) -> list[str]:
        return [*MODEL_CREDENTIAL_VARS, *([self.key_env] if self.key_env else [])]

    def permission(self) -> dict:
        """Deny everything, then allow back what the role grants. A JSON
        object keeps its order, and OpenCode's last matching rule wins."""
        rules = {"*": "deny", "yaaf_*": "allow"}
        if base.PERMS.get("files", True):
            rules.update(dict.fromkeys(("read", "glob", "grep", "list", "edit"), "allow"))
            rules["external_directory"] = "allow"
        if base.PERMS.get("bash") or base.PERMS.get("data_processing"):
            rules["bash"] = "allow"
        if base.PERMS.get("web"):
            rules["webfetch"] = "allow"
        specs = base._data_source_specs()
        for source in self._sources():
            allowlist = specs[source].get("tools")
            if allowlist is None:
                rules[f"{source}_*"] = "allow"
            else:
                rules.update({f"{source}_{tool}": "allow" for tool in allowlist})
        return rules

    def _sources(self) -> dict[str, dict]:
        # Streamable HTTP is what was verified; an SSE source is said, not mounted.
        return {name: mount for name, mount in base._external_mcp_servers().items()
                if mount.get("type") != "sse"}

    def config(self) -> dict:
        model = self._model()
        price = base.PRICE or {}
        server_env = {k: v for k, v in os.environ.items() if k not in self._hidden()}
        mcp = {"yaaf": {"type": "local", "command": [sys.executable, str(TOOLS_SERVER)],
                        "environment": server_env, "enabled": True, "timeout": 300_000}}
        for source, mount in self._sources().items():
            mcp[source] = {"type": "remote", "url": mount["url"], "enabled": True,
                           **({"headers": mount["headers"]} if mount.get("headers") else {})}
        return {
            "$schema": "https://opencode.ai/config.json",
            "enabled_providers": ["yaaf"],
            "model": f"yaaf/{model}",
            "small_model": f"yaaf/{model}",
            "default_agent": "yaaf",
            "autoupdate": False,
            "share": "disabled",
            "snapshot": False,
            "lsp": False,
            "formatter": False,
            "instructions": [],
            "provider": {"yaaf": {
                "npm": "@ai-sdk/openai-compatible",
                "name": str(self.spec.get("name") or "yaaf"),
                "options": {"baseURL": str(self.spec.get("base_url") or ""),
                            "apiKey": f"{{file:{self.key_file}}}",
                            "timeout": 600_000, "chunkTimeout": 120_000},
                "models": {model: {
                    "name": model, "tool_call": True,
                    # OpenCode's own figure in the stream; the bill is yaaf's.
                    "cost": {"input": price.get("input", 0), "output": price.get("output", 0),
                             "cache_read": price.get("cache_read", 0),
                             "cache_write": price.get("cache_write_5m", 0)}}},
            }},
            "mcp": mcp,
            "permission": self.permission(),
            "agent": {
                "title": {"disable": True},
                **{name: {"disable": True} for name in ("build", "plan", "general", "explore")},
                "yaaf": {"mode": "primary", "description": "yaaf run",
                         "prompt": f"{{file:{self.instructions}}}"},
            },
        }

    def _fresh_dirs(self) -> None:
        """Config, cache and the home's own `.opencode` rebuilt: whatever was
        written there since the last launch is gone. The data directory holds
        the session, which a resume needs."""
        for name in ("config", "cache"):
            shutil.rmtree(self.dirs[name], ignore_errors=True)
        shutil.rmtree(self.home / ".opencode", ignore_errors=True)
        for path in self.dirs.values():
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        config = self.dirs["config"] / "opencode"
        (config / "node_modules").mkdir(parents=True)
        dependency = {"@opencode-ai/plugin": VERSION}
        (config / "package.json").write_text(json.dumps({"dependencies": dependency}))
        (config / "package-lock.json").write_text(json.dumps(
            {"lockfileVersion": 3, "packages": {"": {"dependencies": dependency}}}))
        (config / "opencode.json").write_text(json.dumps(self.config(), indent=2))

    def quarantine(self) -> list[Path]:
        """Move aside what OpenCode's plugin loader would execute, from the
        workspace up: it walks to the repository root and honours no switch."""
        moved = []
        here = base.WORKSPACE.resolve()
        for directory in (here, *here.parents):
            for name in PLANTABLE:
                path = directory / name
                if not (path.exists() or path.is_symlink()):
                    continue
                target = directory / f"{name}.quarantined-{base.RUN_ID}"
                try:
                    path.rename(target)
                except OSError:
                    continue  # somewhere this run cannot write, nor could an agent
                moved.append(target)
        return moved

    def argv(self, resume: str | None) -> list[str]:
        return [OPENCODE, "run", "--format", "json", *(["--session", resume] if resume else [])]

    def env(self) -> dict[str, str]:
        # The search key goes to yaaf's tool server (its `environment`, in
        # the config) and never to OpenCode itself, whose shell the model has.
        env = {k: v for k, v in os.environ.items()
               if k not in self._hidden() and k not in OVERRIDES and k != base.SEARCH_KEY_ENV}
        env.update(LOCKDOWN)
        env["HOME"] = str(self.home)
        for name, path in self.dirs.items():
            env[f"XDG_{name.upper()}_HOME"] = str(path)
        return env

    # ── the attempt, with the governor in the parser ───────────────────────

    async def attempt(self, prompt: str, resume: str | None = None):
        if base.PRICE is None:
            raise Unpriced(f"{base.MODEL} has no price on this install, and this harness's "
                           "spend is metered by it — a run nobody can price is a run the "
                           "budget cannot stop")
        for moved in self.quarantine():
            yield base.TraceLine({"type": "opencode_config_quarantined", "path": str(moved)})
        for source, mount in base._external_mcp_servers().items():
            if mount.get("type") == "sse":
                yield base.TraceLine({"type": "data_source_unmounted", "source": source,
                                      "why": "OpenCode's remote MCP is mounted over "
                                             "streamable HTTP, not SSE"})
        self._fresh_dirs()
        self.failure, self.cut, self.capped, self.steps, self.spent = None, None, False, 0, 0.0
        try:
            async for event in super().attempt(prompt, resume):
                yield event
        except base.CliRunError:
            if self.cut is not None:
                raise BudgetReached(f"per-run cap reached: ${self.cut:.4f} spent of "
                                    f"${self.max_usd:g} left") from None
            if self.capped:
                yield base.Text(f"[stopped at the {MAX_STEPS}-call limit]")
                return
            raise
        if self.failure is not None:
            # An error event on a run that still exited 0 is still a failure.
            raise base.CliRunError(self.failure_text(0, ""), result=self.failure, exit_code=0)

    def stop(self) -> None:
        # SIGTERM: OpenCode ends at once and takes its MCP servers with it.
        if self._process is not None and self._process.returncode is None:
            self._process.send_signal(signal.SIGTERM)

    def _bill(self, tokens: dict, subtype: str) -> base.AttemptResult:
        """One `step_finish` as a bill. OpenCode states the input less what
        the cache served and wrote, and the output less the reasoning — which
        is billed as output all the same."""
        cache = tokens.get("cache") or {}
        bill = base.AttemptResult(
            cost_usd=None, tokens_in=int(tokens.get("input") or 0),
            tokens_out=int(tokens.get("output") or 0) + int(tokens.get("reasoning") or 0),
            cache_creation=int(cache.get("write") or 0), cache_read=int(cache.get("read") or 0),
            subtype=subtype)
        return replace(bill, cost_usd=base._priced(base.PRICE, bill))

    # ── reading the stream ─────────────────────────────────────────────────

    def parse(self, record: dict) -> list:
        events: list = []
        session = record.get("sessionID")
        if session and session != self.session_id:
            self.session_id = session
            events.append(base.Session(session))
        kind = record.get("type")
        part = record.get("part") or {}
        if kind == "text" and part.get("text"):
            events.append(base.Text(str(part["text"])))
        elif kind == "step_finish":
            bill = self._bill(part.get("tokens") or {}, str(part.get("reason") or "step"))
            self.steps += 1
            self.spent += bill.cost_usd or 0.0
            events.append(bill)
            for search in self.searches.new():
                self.spent += search.cost_usd or 0.0
                events.append(search)
            if self.max_usd > 0 and self.spent >= self.max_usd:
                self.cut = self.spent
                self.stop()
            elif self.steps >= MAX_STEPS and part.get("reason") == "tool-calls":
                self.capped = True
                self.stop()
        elif kind == "tool_use":
            state = part.get("state") or {}
            events.append(base.TraceLine({"type": "tool_use", "tool": part.get("tool"),
                                          "status": state.get("status"),
                                          "repr": json.dumps(state, default=str)[:2000]}))
        elif kind == "error":
            error = record.get("error") or {}
            data = error.get("data") if isinstance(error.get("data"), dict) else {}
            self.failure = {"name": error.get("name"), **data}
            self.last_error_result = self.failure
            events.append(base.TraceLine({"type": "error", "error": {
                k: v for k, v in self.failure.items() if k != "responseHeaders"}}))
        return events

    def failure_text(self, exit_code: int, stderr: str) -> str:
        if self.failure is not None:
            status = self.failure.get("statusCode")
            return (f"OpenCode failed: {self.failure.get('name')}"
                    f"{f' (HTTP {status})' if status else ''}: {self.failure.get('message')}")
        return f"OpenCode exited {exit_code}: {stderr[-self.STDERR_TAIL:].strip()}"

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, BudgetReached | Unpriced | base.LedgerTampered):
            return "fatal"
        data = getattr(exc, "data", None) or {}
        status = data.get("statusCode")
        if isinstance(status, int):
            if status in (401, 402, 403):
                return "auth"
            if status in (408, 409, 429) or status >= 500:
                return "transient"
            said = str(data.get("message") or "").lower()
            if any(marker in said for marker in self.spec.get("failure_markers") or ()):
                return "auth"
            return "fatal"
        if data.get("name") == "ProviderHeaderTimeoutError" or data.get("isRetryable"):
            return "transient"
        return None


#: What builds this harness's driver — for a run, and for the boardroom
#: conversation (chat_entry), which holds one as resumed attempts.
make_driver = OpenCodeDriver


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
