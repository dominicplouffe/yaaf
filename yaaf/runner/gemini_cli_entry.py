"""Runs INSIDE the sandbox container: one stateless run on the Gemini CLI
(`gemini --output-format stream-json`), Google's agent, driven the way the
Claude and Codex CLIs are (docs/RUNTIMES.md, M5).

Every line below was read off the CLI the image pins (0.63.0), run against a
stand-in Gemini API with the network cut to loopback, and the facts that
shaped it are each a guard here:

- **A home of its own per run** (`GEMINI_CLI_HOME`), on the credential tmpfs:
  settings, login, sessions and the telemetry file live there, never in the
  workspace. The workspace stays untrusted (`--skip-trust`, which keeps its
  `.gemini/settings.json` from loading — the trust switches would load it).
- **Nothing in the workspace steers the run.** Two things the CLI loads from
  a workspace whatever its settings say — `GEMINI.md`, into the first message,
  and `.gemini/.env`, which once sent the key to a planted address — are
  moved aside before every launch, with the rest of `.gemini/`, and the trace
  says so. The variables a planted `.env` could set are set here first; the
  CLI never overwrites one that is set.
- **Its own prompt replaced** (`GEMINI_SYSTEM_MD`, an absolute path).
- **Tools by an admin policy**: deny everything, allow back the role's
  grants — the only allowlist both enforced and beyond a workspace's reach.
  The CLI ignores a policy file it cannot parse and runs on without it, so
  the driver watches stderr for that and stops the run.
- **The governor reads the telemetry file.** `stream-json` reports usage
  once, at the end, without the thinking; the local telemetry the CLI writes
  as each call finishes has every call, thinking included. The driver prices
  it as it goes and stops the CLI at the line.
- **Billed on the model hired.** The CLI rewrites some model names on the
  wire and, for one family, falls back to another model after repeated rate
  limits, saying nothing. A name it would rewrite is refused before the CLI
  starts; a call served by another model stops the run. Auto-routing is never
  used: it bills a classifier call every turn.
- **Stopped for real.** The CLI relaunches itself as a child that ignores
  signals unless told not to; it is told not to, so a stop is a stop.

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
GEMINI = os.environ.get("YAAF_GEMINI_BIN", "gemini")
#: Where an API key's money goes, set here so nothing planted can move it.
GEMINI_BASE_URL = os.environ.get("YAAF_GEMINI_BASE_URL",
                                 "https://generativelanguage.googleapis.com")
TOOLS_SERVER = Path(__file__).with_name("yaaf_tools.py")
WATCH_INTERVAL_S = 0.25
#: What a workspace may hold that the CLI loads whatever its settings say.
PLANTABLE = (".gemini", "GEMINI.md")
#: Names the CLI swaps for another model on the wire with an API key, and
#: the aliases that route or swap — refused rather than billed as hired.
REWRITTEN = {"gemini-3.5-flash": "gemini-3.8-flash", "gemini-3-flash": "gemini-3.8-flash",
             "gemini-3-pro-preview": "gemini-3.1-pro-preview",
             "gemini-3.1-flash-lite": "gemini-3.5-flash-lite"}
ALIASES = ("auto", "pro", "flash", "flash-lite", "auto-gemini-3", "auto-gemini-2.5")
#: The CLI's tools, by the grant that allows each.
GRANTED = {
    "files": ["read_file", "list_directory", "glob", "grep_search", "write_file", "replace"],
    "bash": ["run_shell_command", "list_background_processes", "read_background_output"],
    "web": ["google_web_search", "web_fetch"],
}
#: The model credentials no shell and no tool server is handed.
MODEL_CREDENTIAL_VARS = ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_OAUTH_CREDS",
                         "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY",
                         "CODEX_AUTH_JSON")
#: An API error exits with its HTTP status, mod 256.
EXIT_STATUS = {144: 400, 145: 401, 146: 402, 147: 403, 148: 404, 173: 429, 243: 499, 244: 500,
               246: 502, 247: 503, 248: 504}


class BudgetReached(RuntimeError):
    """The per-run cap is spent. Not transient: resuming is spending more."""


class Unpriced(RuntimeError):
    """A model metered in dollars with no price to meter it by (R3)."""


class ModelSwitched(RuntimeError):
    """The run would be, or was, served by a model the seat was not hired on."""


class PolicyRejected(RuntimeError):
    """The CLI could not read the tool policy, and would have run without it."""


class GeminiCliDriver(base.CliDriver):
    name = "gemini_cli"

    def __init__(self, system_prompt: str, system_prompt_path: Path):
        super().__init__()
        self.home = base._credential_dir() / f"gemini-{base.RUN_ID}"
        (self.home / ".gemini").mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.home / "tmp").mkdir(mode=0o700, exist_ok=True)
        self.instructions = self.home / "system.md"
        self.instructions.write_text(system_prompt + base.inline_skills())
        self.policy = self.home / "yaaf-admin.toml"
        self.telemetry = self.home / "telemetry.log"
        login = os.environ.get("GEMINI_OAUTH_CREDS")
        self.subscription = bool(login)
        if login:
            creds = self.home / ".gemini" / "oauth_creds.json"
            creds.write_text(login)
            creds.chmod(0o600)
        self.model = base.MODEL.removeprefix("google/")
        self.session_id: str | None = None
        self.failure: dict | None = None
        self.cut: float | None = None
        self.switched: str | None = None
        self.policy_broken = False
        self.billed = False
        self.stats: dict | None = None
        self.text: list[str] = []
        self.web_searches = 0
        #: Telemetry: how far it has been read, and every call's usage in it.
        self._offset = 0
        self._calls: list[dict] = []
        self._start = 0

    # ── what the run is told and offered ──────────────────────────────────

    def _shell(self) -> bool:
        return bool(base.PERMS.get("bash") or base.PERMS.get("data_processing"))

    def _sources(self) -> dict[str, dict]:
        return base._external_mcp_servers()

    def settings(self) -> dict:
        server_env = {k: v for k, v in os.environ.items() if k not in MODEL_CREDENTIAL_VARS}
        servers = {"yaaf": {"command": sys.executable, "args": [str(TOOLS_SERVER)],
                            "cwd": str(base.WORKSPACE),
                            # Passed whole: the CLI strips anything named like a
                            # token from what a server inherits, the run's own
                            # API token with it.
                            "env": server_env, "timeout": 300_000}}
        specs = base._data_source_specs()
        for source, mount in self._sources().items():
            server = {"url": mount["url"], "type": "sse" if mount.get("type") == "sse" else "http",
                      "timeout": 300_000}
            if mount.get("headers"):
                server["headers"] = mount["headers"]
            if specs[source].get("tools") is not None:
                server["includeTools"] = list(specs[source]["tools"])
            servers[source] = server
        thinking = ({"customOverrides": [{"match": {"model": self.model}, "modelConfig": {
            "generateContentConfig": {"thinkingConfig": {"thinkingLevel": base.EFFORT.upper()}}}}]}
            if self.model.startswith("gemini-3") and base.EFFORT else {})
        return {
            "security": {"auth": {"selectedType": "oauth-personal" if self.subscription
                                  else "gemini-api-key"},
                         "folderTrust": {"enabled": True}},
            "privacy": {"usageStatisticsEnabled": False},
            "telemetry": {"enabled": True, "target": "local", "outfile": str(self.telemetry),
                          "logPrompts": False},
            "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False,
                        "maxAttempts": 3, "checkpointing": {"enabled": False},
                        "plan": {"enabled": False}, "topicUpdateNarration": False},
            "model": {"maxSessionTurns": 100, "disableLoopDetection": True,
                      "skipNextSpeakerCheck": True},
            "modelConfigs": thinking,
            "context": {"includeDirectoryTree": False},
            "skills": {"enabled": False},
            "experimental": {"enableAgents": False},
            "hooksConfig": {"enabled": False},
            "useWriteTodos": False,
            "ide": {"enabled": False},
            "advanced": {"ignoreLocalEnv": True},
            "mcp": {"allowed": list(servers)},
            "mcpServers": servers,
        }

    def admin_policy(self) -> str:
        """Deny everything, then allow back the role's grants — at the admin
        tier, which no settings file and no yolo mode outranks."""
        rules = ['[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 1\n'
                 'denyMessage = "This tool is not granted to this role."\n']
        allowed = []
        if base.PERMS.get("files", True):
            allowed += GRANTED["files"]
        if self._shell():
            allowed += GRANTED["bash"]
        if base.PERMS.get("web"):
            allowed += GRANTED["web"]
        if allowed:
            rules.append(f'[[rule]]\ntoolName = {json.dumps(allowed)}\ndecision = "allow"\n'
                         f'priority = 100\nallowRedirection = true\n')
        for server in ("yaaf", *self._sources()):
            rules.append(f'[[rule]]\nmcpName = {json.dumps(server)}\ntoolName = "*"\n'
                         f'decision = "allow"\npriority = 100\n')
        return "\n".join(rules)

    def quarantine(self) -> list[Path]:
        """Move aside what the CLI loads from a workspace whatever its settings
        say, from the workspace up."""
        moved = []
        here = base.WORKSPACE.resolve()
        for directory in (here, *here.parents):
            for name in PLANTABLE:
                path = directory / name
                if not (path.exists() or path.is_symlink()):
                    continue
                target = directory / f"{name}.quarantined-{base.RUN_ID}"
                try:
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    path.rename(target)
                except OSError:
                    continue  # somewhere this run cannot write, nor could an agent
                moved.append(target)
        return moved

    def argv(self, resume: str | None) -> list[str]:
        cmd = [GEMINI, "--output-format", "stream-json", "--skip-trust",
               "--approval-mode", "yolo", "-m", self.model,
               "--admin-policy", str(self.policy),
               "--allowed-mcp-server-names", ",".join(("yaaf", *self._sources()))]
        return cmd + (["--resume", resume] if resume else [])

    def env(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in MODEL_CREDENTIAL_VARS}
        key = os.environ.get("GEMINI_API_KEY")
        if key and not self.subscription:
            env["GEMINI_API_KEY"] = key
        env.update({
            "GEMINI_CLI_HOME": str(self.home),
            "GEMINI_SYSTEM_MD": str(self.instructions),
            "GEMINI_CLI_NO_RELAUNCH": "true",
            "GEMINI_CLI_SYSTEM_SETTINGS_PATH": str(self.home / "no-system-settings.json"),
            "NO_BROWSER": "true",
            "TMPDIR": str(self.home / "tmp"),
            # Set before the CLI looks, so a planted .env cannot set them: it
            # never overwrites a variable that is already there.
            "GEMINI_TELEMETRY_ENABLED": "true", "GEMINI_TELEMETRY_TARGET": "local",
            "GEMINI_TELEMETRY_OUTFILE": str(self.telemetry),
            "GOOGLE_GENAI_USE_VERTEXAI": "false", "GOOGLE_GENAI_USE_GCA": "false",
            "GEMINI_CLI_CUSTOM_HEADERS": "", "GEMINI_API_KEY_AUTH_MECHANISM": "",
        })
        if not self.subscription:
            env["GOOGLE_GEMINI_BASE_URL"] = GEMINI_BASE_URL
        return env

    # ── the attempt, with the governor beside it ───────────────────────────

    async def attempt(self, prompt: str, resume: str | None = None):
        if self.model in REWRITTEN or self.model in ALIASES:
            served = REWRITTEN.get(self.model)
            why = (f"the Gemini CLI serves {served} for {self.model}" if served else
                   f"{self.model} lets the Gemini CLI choose the model, and bills a classifier "
                   "call for choosing")
            raise ModelSwitched(f"{why}; hire the seat on the model that actually runs")
        if base.PRICE is None and not self.subscription:
            raise Unpriced(f"{base.MODEL} has no price on this install, and an API key's "
                           "spend is metered by it — a run nobody can price is a run the "
                           "budget cannot stop")
        for moved in self.quarantine():
            yield base.TraceLine({"type": "gemini_config_quarantined", "path": str(moved)})
        (self.home / ".gemini" / "settings.json").write_text(json.dumps(self.settings()))
        self.policy.write_text(self.admin_policy())
        self.failure, self.cut, self.switched, self.policy_broken = None, None, None, False
        self.billed, self.text, self.web_searches, self.stats = False, [], 0, None
        self._read_telemetry()
        self._start = len(self._calls)
        watcher = asyncio.create_task(self._watch())
        error: base.CliRunError | None = None
        try:
            # `@path` in a prompt is a file the CLI inlines: never in ours.
            async for event in super().attempt(prompt.replace("@", "\\@"), resume):
                yield event
        except base.CliRunError as exc:
            error = exc
        finally:
            watcher.cancel()
        # Billed once the CLI has exited: it writes the telemetry in batches,
        # and only its exit is sure to have flushed the last of it.
        self._read_telemetry()
        if self.text:
            yield base.Text("".join(self.text))
        if self.billed or self._attempt_calls():
            yield self._final_bill()
        if self.policy_broken:
            raise PolicyRejected("the Gemini CLI could not read the tool policy and would have "
                                 "run with every tool")
        if self.switched is not None:
            raise ModelSwitched(f"the Gemini CLI answered with {self.switched}, not {self.model} "
                                "— the run stops rather than bill a model it was not hired on")
        if self.cut is not None:
            raise BudgetReached(f"per-run cap reached mid-attempt: ${self.cut:.4f} spent of "
                                f"${self.max_usd:g} left")
        if error is not None:
            raise error
        if self.failure is not None:
            raise base.CliRunError(self.failure_text(0, ""), result=self.failure, exit_code=0)
        if not self.billed:
            # A run that ends with no result was stopped from outside: what the
            # telemetry saw is billed, and it did not finish.
            raise base.CliRunError("the Gemini CLI ended without a result", exit_code=0)

    def failure_text(self, exit_code: int, stderr: str) -> str:
        if self.failure is not None:
            return f"Gemini CLI failed (exit {exit_code}): {self.failure.get('message')}"[:2000]
        return f"Gemini CLI exited {exit_code}: {stderr[-self.STDERR_TAIL:].strip()}"

    def on_stderr(self, chunk: bytes) -> None:
        if b"Policy file error" in chunk:
            self.policy_broken = True
            self.stop()

    def stop(self) -> None:
        if self._process is not None and self._process.returncode is None:
            self._process.send_signal(signal.SIGTERM)

    def _read_telemetry(self) -> None:
        """The telemetry file, read on from where it was left: one object per
        model call the CLI made, the served model named. Concatenated JSON
        objects, not lines; an object still being written is read next time."""
        if not self.telemetry.exists():
            return
        with self.telemetry.open("rb") as handle:
            handle.seek(self._offset)
            text = handle.read().decode("utf-8", errors="replace")
        decoder, at = json.JSONDecoder(), 0
        while True:
            while at < len(text) and text[at].isspace():
                at += 1
            if at >= len(text):
                break
            try:
                record, end = decoder.raw_decode(text, at)
            except ValueError:
                break
            at = end
            attrs = record.get("attributes") if isinstance(record, dict) else None
            if isinstance(attrs, dict) and attrs.get("event.name") == "gemini_cli.api_response":
                self._calls.append(attrs)
                if (attrs.get("role") == "main" and not self.subscription
                        and not str(attrs.get("model", "")).startswith(self.model)):
                    self.switched = str(attrs.get("model"))
                    self.stop()
        self._offset += len(text[:at].encode())

    def _final_bill(self) -> base.AttemptResult:
        """The telemetry's calls, or the `result` line's stats where they say
        more — they leave out the thinking, so they are only ever a floor."""
        bill = self._bill(self._attempt_calls(), "success" if self.billed else "interrupted")
        stats = self.stats or {}
        cached = int(stats.get("cached") or 0)
        floor = replace(bill, tokens_in=max(0, int(stats.get("input_tokens") or 0) - cached),
                        tokens_out=int(stats.get("output_tokens") or 0), cache_read=cached,
                        cost_usd=None)
        if (floor.tokens_in + floor.tokens_out + floor.cache_read
                > bill.tokens_in + bill.tokens_out + bill.cache_read):
            bill = floor
            if base.PRICE is not None and not self.subscription:
                bill = replace(bill, cost_usd=base._priced(base.PRICE, bill))
        return bill

    def _attempt_calls(self) -> list[dict]:
        return self._calls[self._start:]

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(WATCH_INTERVAL_S)
            self._read_telemetry()
            if self.max_usd <= 0 or base.PRICE is None or self.subscription:
                continue
            spent = self._bill(self._attempt_calls(), None).cost_usd or 0.0
            if spent >= self.max_usd:
                self.cut = spent
                self.stop()
                return

    def _bill(self, calls: list[dict], subtype: str | None) -> base.AttemptResult:
        """Every call's usage as one bill. The input counts the cached part;
        the output does not count the thinking, which is billed as output.
        Utility calls the CLI makes on other models (a web search's) are
        priced at the seat's rate: the sandbox holds that one price."""
        def total(key: str) -> int:
            return sum(int(call.get(key) or 0) for call in calls)

        cached = total("cached_content_token_count")
        bill = base.AttemptResult(
            cost_usd=None,
            tokens_in=max(0, total("input_token_count") - cached) + total("tool_token_count"),
            tokens_out=total("output_token_count") + total("thoughts_token_count"),
            cache_creation=0, cache_read=cached, subtype=subtype,
            web_searches=self.web_searches)
        if base.PRICE is not None and not self.subscription:
            bill = replace(bill, cost_usd=base._priced(base.PRICE, bill))
        return bill

    # ── reading the stream ─────────────────────────────────────────────────

    def parse(self, record: dict) -> list:
        kind = record.get("type")
        if kind == "init":
            self.session_id = record.get("session_id") or self.session_id
            return [base.Session(self.session_id)] if self.session_id else []
        if kind == "message":
            if record.get("role") == "assistant":
                self.text.append(str(record.get("content") or ""))
            return []
        events: list = []
        if self.text:
            events.append(base.Text("".join(self.text)))
            self.text = []
        if kind == "tool_use":
            if record.get("tool_name") == "google_web_search":
                self.web_searches += 1
            events.append(base.TraceLine({"type": "tool_use", "tool": record.get("tool_name"),
                                          "input": json.dumps(record.get("parameters"),
                                                              default=str)[:2000]}))
        elif kind == "tool_result":
            events.append(base.TraceLine({"type": "tool_result", "status": record.get("status"),
                                          "error": record.get("error"),
                                          "preview": str(record.get("output", ""))[:2000]}))
        elif kind == "result":
            self.stats = record.get("stats") if isinstance(record.get("stats"), dict) else None
            if record.get("status") == "success":
                self.billed = True
            else:
                self.failure = record.get("error") if isinstance(record.get("error"), dict) \
                    else {"message": str(record.get("error"))}
                self.last_error_result = self.failure
                events.append(base.TraceLine({"type": "result_error", "error": self.failure}))
        else:
            events.append(base.TraceLine({"type": str(kind),
                                          "repr": json.dumps(record, default=str)[:2000]}))
        return events

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, BudgetReached | Unpriced | ModelSwitched | PolicyRejected):
            return "fatal"
        exit_code = getattr(exc, "exit_code", None)
        message = str(((getattr(exc, "data", None) or {}).get("message")) or exc).lower()
        status = EXIT_STATUS.get(exit_code)
        if exit_code == 41 or status in (401, 402, 403) or "api key not valid" in message:
            return "auth"
        if status in (408, 409, 429, 499) or (status or 0) >= 500:
            return "transient"
        if exit_code == 1 and ("fetch failed" in message or "terminated" in message):
            return "transient"
        if status is not None or exit_code in (42, 52, 53, 55):
            return "fatal"
        return None


#: What builds this harness's driver — for a run, and for the boardroom
#: conversation (chat_entry), which holds one as resumed attempts.
make_driver = GeminiCliDriver


if __name__ == "__main__":
    raise SystemExit(asyncio.run(base.run_harness(make_driver)))
