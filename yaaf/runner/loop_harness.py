"""Runs INSIDE the sandbox container: yaaf's own agent loop, the harness family
for every provider reached by plain API (docs/RUNTIMES.md, M2).

A CLI harness hands the model to someone else's loop. This one is the loop:
send the transcript, run the tool calls, send it again, until the model stops.
Owning it buys three things no CLI offers — an exact bill per call, so the
per-run cap is a governor rather than a hope; control of what is cached; and a
run with nothing underneath it but an HTTP client — and costs one: the built-in
tools (files, shell, fetch) are yaaf's to write, under the same grants Claude
Code's built-ins answer to on the other harnesses.

A provider supplies a `ModelClient` and nothing else: how a transcript becomes
its request, how its reply becomes a `Reply`, which of its errors are which.
The loop never sees the provider's shape — except as `Turn.native`, carried
opaque and replayed verbatim, because a transcript is append-only and what the
model wrote (thinking, signatures, server tool results) must go back exactly as
it came.

Everything a run must do whatever drives it is `sandbox_entry.run_harness`.
Imports `sandbox_entry` by bare name, as every entry beside it does.
"""

from __future__ import annotations

import asyncio
import dataclasses
import fnmatch
import json
import os
import re
import signal
from collections.abc import AsyncIterator
from contextlib import aclosing
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol

import httpx
import sandbox_entry as base

#: What `max_turns` gives the Claude harnesses: model calls, not tool calls.
MAX_TURNS = 100
#: A call's output ceiling before the governor narrows it. Long enough that a
#: real deliverable written in one turn is not cut off.
MAX_OUTPUT_TOKENS = 64_000
#: What any one tool result may put in the context. Claude Code caps an MCP
#: result at 25,000 tokens; this is that, in characters, for every tool — a
#: result past it is a result to write to disk and read in part.
TOOL_OUTPUT_CHARS = 100_000
BASH_OUTPUT_CHARS = 30_000
BASH_TIMEOUT_MS = 120_000
BASH_MAX_TIMEOUT_MS = 600_000
READ_LINES = 2000
READ_LINE_CHARS = 2000
SEARCH_RESULTS = 250
FETCH_BYTES = 5 * 1024 * 1024
FETCH_TIMEOUT_S = 30
#: The model credentials a shell is never handed. Nothing an agent runs needs
#: to pay for the model, and a key a shell can print is a key a transcript can
#: carry anywhere (SECURITY.md).
MODEL_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN")


# ── the loop's own shapes: what every provider's client speaks ─────────────────


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass(frozen=True)
class ToolOutput:
    call_id: str
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class Turn:
    """One message of the transcript. The loop reads `text` and `calls`; the
    client that produced an assistant turn replays `native`, its own form of
    it, verbatim."""
    role: str  # "user" | "assistant"
    text: str = ""
    calls: tuple[ToolCall, ...] = ()
    outputs: tuple[ToolOutput, ...] = ()
    native: Any = None


@dataclass(frozen=True)
class Reply:
    """One finished model call. `stop` is the loop's word for why it ended:
    "end", "tool_use", "pause" (the provider's own server-side loop paused;
    send the transcript again as it is), "max_tokens", or "refusal"."""
    turn: Turn
    stop: str
    usage: base.AttemptResult
    model: str | None = None
    detail: str | None = None


class ModelClient(Protocol):
    """A provider, as the loop needs it. `stream` yields the call's usage as
    soon as the provider states it (an `AttemptResult` with no cost — the loop
    prices it) and then exactly one `Reply`, or raises."""
    name: str

    def stream(self, system: str, transcript: list[Turn], tools: list[ToolSpec],
               max_tokens: int) -> AsyncIterator[base.AttemptResult | Reply]: ...

    def error_kind(self, exc: BaseException) -> str | None: ...


class BudgetReached(RuntimeError):
    """The per-run cap is spent. Not transient: resuming is spending more."""


class Unpriced(RuntimeError):
    """A model this harness has no price for (R3). Refused at every door; this
    is the line behind them."""


class ModelRefused(RuntimeError):
    """The model declined to continue. Ends the run; never retried."""


# ── the built-in tools: files, shell, fetch ─────────────────────────────────────
#
# Claude Code's names, so a role written for one harness reads the same on this
# one, and Claude Code's grants (sandbox_entry._builtin_tools decides which are
# offered — once, for every harness). Containment is the container, as it is
# there: paths are not fenced to /workspace, because Claude Code's are not
# either, and a fence one harness has and another lacks is no fence.


def _text(text: str, *, error: bool = False) -> dict:
    result: dict = {"content": [{"type": "text", "text": text}]}
    if error:
        result["is_error"] = True
    return result


def _path(raw: Any) -> Path:
    path = Path(str(raw or "."))
    return path if path.is_absolute() else base.WORKSPACE / path


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return (f"{text[:half]}\n\n[... {len(text) - limit} characters cut; read the rest "
            f"in parts ...]\n\n{text[-half:]}")


async def b_read(args: dict) -> dict:
    path = _path(args.get("file_path"))
    if not path.is_file():
        return _text(f"no file at {path}", error=True)
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, int(args.get("limit") or READ_LINES))
    lines: list[str] = []
    more = False
    with path.open(errors="replace") as handle:
        for number, line in enumerate(handle, start=1):
            if number < offset:
                continue
            if len(lines) >= limit:
                more = True
                break
            lines.append(f"{number:>6}\t{line.rstrip(chr(10))[:READ_LINE_CHARS]}")
    if not lines:
        return _text(f"{path} has no lines from {offset} (empty, or shorter than that)")
    body = "\n".join(lines)
    if more:
        body += f"\n[more lines follow; read from offset {offset + len(lines)}]"
    return _text(body)


async def b_write(args: dict) -> dict:
    path = _path(args.get("file_path"))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return _text(f"wrote {len(content.encode())} bytes to {path}")


async def b_edit(args: dict) -> dict:
    path = _path(args.get("file_path"))
    old, new = str(args.get("old_string", "")), str(args.get("new_string", ""))
    if not path.is_file():
        return _text(f"no file at {path}", error=True)
    if not old:
        return _text("old_string is empty — use Write to create or replace a whole file",
                     error=True)
    text = path.read_text(errors="replace")
    found = text.count(old)
    if found == 0:
        return _text(f"old_string does not appear in {path}", error=True)
    if found > 1 and not args.get("replace_all"):
        return _text(f"old_string appears {found} times in {path} — include more of the "
                     "surrounding text so it is unique, or set replace_all", error=True)
    path.write_text(text.replace(old, new) if args.get("replace_all")
                    else text.replace(old, new, 1))
    return _text(f"replaced {found if args.get('replace_all') else 1} occurrence(s) in {path}")


_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv"}


def _files(root: Path, glob: str | None):
    if root.is_file():
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if glob and not fnmatch.fnmatch(name, glob):
                continue
            yield Path(dirpath) / name


async def b_glob(args: dict) -> dict:
    root = _path(args.get("path"))
    pattern = str(args.get("pattern") or "*")
    found = [p for p in root.glob(pattern) if p.is_file()
             and not _SKIP_DIRS.intersection(p.relative_to(root).parts)]
    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not found:
        return _text(f"no files match {pattern} under {root}")
    listed = "\n".join(str(p) for p in found[:SEARCH_RESULTS])
    if len(found) > SEARCH_RESULTS:
        listed += f"\n[{len(found) - SEARCH_RESULTS} more; narrow the pattern]"
    return _text(listed)


async def b_grep(args: dict) -> dict:
    try:
        regex = re.compile(str(args.get("pattern", "")),
                           re.IGNORECASE if args.get("-i") else 0)
    except re.error as exc:
        return _text(f"not a valid regular expression: {exc}", error=True)
    root = _path(args.get("path"))
    mode = args.get("output_mode") or "files_with_matches"
    out: list[str] = []
    for path in _files(root, args.get("glob")):
        try:
            if path.stat().st_size > FETCH_BYTES:
                continue
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        hits = [(n, line) for n, line in enumerate(raw.decode(errors="replace").splitlines(), 1)
                if regex.search(line)]
        if not hits:
            continue
        if mode == "content":
            out += [f"{path}:{n}:{line[:READ_LINE_CHARS]}" for n, line in hits]
        elif mode == "count":
            out.append(f"{path}:{len(hits)}")
        else:
            out.append(str(path))
        if len(out) >= SEARCH_RESULTS:
            out = out[:SEARCH_RESULTS] + ["[more matches; narrow the pattern or the path]"]
            break
    return _text("\n".join(out) if out else "no matches")


async def b_bash(args: dict) -> dict:
    command = str(args.get("command", ""))
    timeout_ms = min(int(args.get("timeout") or BASH_TIMEOUT_MS), BASH_MAX_TIMEOUT_MS)
    hidden = {*MODEL_CREDENTIAL_ENV, base.PROVIDER.get("key_env")}
    env = {k: v for k, v in os.environ.items() if k not in hidden}
    process = await asyncio.create_subprocess_exec(
        "bash", "-c", command, cwd=str(base.WORKSPACE), env=env,
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout_ms / 1000)
    except TimeoutError:
        # The whole group: a command that started children would otherwise
        # leave them running, and holding the pipe open, after its shell died.
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        return _text(f"timed out after {timeout_ms} ms and was killed", error=True)
    text = _clip(output.decode(errors="replace"), BASH_OUTPUT_CHARS)
    if process.returncode:
        return _text(f"{text}\n[exit code {process.returncode}]", error=True)
    return _text(text or "(no output)")


class _PageText(HTMLParser):
    """An HTML page's readable text: what a person would read, not its markup."""

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


async def b_web_fetch(args: dict) -> dict:
    url = str(args.get("url", ""))
    if not url.startswith(("http://", "https://")):
        return _text("url must start with http:// or https://", error=True)
    try:
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT_S) as client,
            client.stream("GET", url) as response,
        ):
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > FETCH_BYTES:
                    break
            kind = response.headers.get("content-type", "")
            status = response.status_code
    except httpx.HTTPError as exc:
        return _text(f"could not fetch {url}: {exc!r}", error=True)
    if "html" in kind:
        page = _PageText()
        page.feed(body.decode(errors="replace"))
        text = "\n".join(page.parts)
    elif kind.startswith("text/") or "json" in kind or "xml" in kind or not kind:
        text = body.decode(errors="replace")
    else:
        return _text(f"{url} is {kind} ({len(body)} bytes) — not text; download it with "
                     "Bash (curl) if a shell is granted, and read it from disk")
    return _text(_clip(f"HTTP {status} {url}\n\n{text}", TOOL_OUTPUT_CHARS), error=status >= 400)


_FILE_PATH = {"type": "string", "description": "Absolute, or relative to /workspace."}
BUILTINS: dict[str, base.Tool] = {t.name: t for t in (
    base.Tool("Read", "Read a text file, with line numbers. Long files: pass offset (first "
              "line, 1-based) and limit (how many lines) and read in parts.",
              {"type": "object", "properties": {"file_path": _FILE_PATH,
                                                "offset": {"type": "integer"},
                                                "limit": {"type": "integer"}},
               "required": ["file_path"]}, b_read),
    base.Tool("Write", "Write a whole file, creating directories as needed. Replaces "
              "anything already there.",
              {"type": "object", "properties": {"file_path": _FILE_PATH,
                                                "content": {"type": "string"}},
               "required": ["file_path", "content"]}, b_write),
    base.Tool("Edit", "Replace text in a file. old_string must appear exactly once unless "
              "replace_all is set.",
              {"type": "object", "properties": {"file_path": _FILE_PATH,
                                                "old_string": {"type": "string"},
                                                "new_string": {"type": "string"},
                                                "replace_all": {"type": "boolean"}},
               "required": ["file_path", "old_string", "new_string"]}, b_edit),
    base.Tool("Glob", "Find files by glob pattern (`**/*.csv`), newest first.",
              {"type": "object", "properties": {"pattern": {"type": "string"},
                                                "path": _FILE_PATH},
               "required": ["pattern"]}, b_glob),
    base.Tool("Grep", "Search file contents by regular expression. output_mode: "
              "files_with_matches (default), content (matching lines), or count. glob "
              "filters file names; -i ignores case.",
              {"type": "object", "properties": {
                  "pattern": {"type": "string"}, "path": _FILE_PATH,
                  "glob": {"type": "string"},
                  "output_mode": {"type": "string",
                                  "enum": ["files_with_matches", "content", "count"]},
                  "-i": {"type": "boolean"}},
               "required": ["pattern"]}, b_grep),
    base.Tool("Bash", "Run a bash command in /workspace and return its output. Each call is "
              "a fresh shell: cd and exported variables do not carry over. timeout in ms "
              f"(default {BASH_TIMEOUT_MS}, max {BASH_MAX_TIMEOUT_MS}).",
              {"type": "object", "properties": {"command": {"type": "string"},
                                                "timeout": {"type": "integer"}},
               "required": ["command"]}, b_bash),
    base.Tool("WebFetch", "Fetch a URL and return its readable text (HTML is reduced to "
              "text). For binary files, download with Bash instead.",
              {"type": "object", "properties": {"url": {"type": "string"}},
               "required": ["url"]}, b_web_fetch),
)}


# ── the loop ────────────────────────────────────────────────────────────────────


def _skills() -> dict[str, dict]:
    """The role's skills, straight from the grant. Never from the workspace,
    which is agent-writable: an unlisted skill stays dark because this is the
    only list there is."""
    out = {}
    for spec in base.PERMS.get("skills", []):
        slug = base._skill_slug(str(spec.get("name", "")))
        body = str(spec.get("body", "")).strip()
        if slug and body:
            out[slug] = {"description": str(spec.get("description", "")).strip() or slug,
                         "body": body}
    return out


def _flatten(result: Any) -> tuple[str, bool]:
    """A tool handler's result — MCP-shaped, as every yaaf tool returns — as
    the text the model reads."""
    if not isinstance(result, dict):
        return str(result), False
    parts = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        elif isinstance(block, dict):
            parts.append(f"[{block.get('type', 'non-text')} content omitted]")
    return _clip("\n".join(parts), TOOL_OUTPUT_CHARS), bool(result.get("is_error"))


class LoopDriver:
    """A `ModelClient` driven as a run: the transcript, the tools, the bill."""

    name = "loop"

    def __init__(self, client: ModelClient, system_prompt: str, *,
                 price: dict | None = None, max_usd: float | None = None):
        self.client = client
        self.price = base.PRICE if price is None else price
        self.max_usd = base.MAX_USD if max_usd is None else max_usd
        self.skills = _skills()
        self.system = system_prompt
        if self.skills:
            self.system += ("\n\n# Skills\nLoad one with the Skill tool when its description "
                            "fits the work in front of you:\n" + "\n".join(
                                f"- {slug}: {s['description']}"
                                for slug, s in self.skills.items()))
        self.tools: dict[str, base.Tool] | None = None
        self.transcript: list[Turn] = []
        self.calls = 0

    def budget(self, remaining_usd: float) -> None:
        self.max_usd = remaining_usd

    async def _toolbox(self) -> tuple[dict[str, base.Tool], list[dict]]:
        tools = [dataclasses.replace(t, name=f"mcp__yaaf__{t.name}",
                                     input_schema=base.wire_schema(t))
                 for t in base.run_tools()]
        tools += [BUILTINS[name] for name in base._builtin_tools() if name in BUILTINS]
        if self.skills:
            tools.append(base.Tool(
                "Skill", "Load a skill's instructions by name.",
                {"type": "object", "properties": {"skill": {"type": "string",
                                                            "enum": sorted(self.skills)}},
                 "required": ["skill"]}, self._skill))
        # Inline data sources, tool by tool — and only the tools an allowlist
        # names, where it names any. Bulk sources are not here: they are
        # reached through data_fetch alone (sandbox_entry._external_mcp_servers).
        problems: list[dict] = []
        specs = base._data_source_specs()
        for source, config in base._external_mcp_servers().items():
            allowlist = specs[source].get("tools")
            try:
                listed = await base._mcp_list_tools(config)
            except Exception as exc:
                problems.append({"type": "data_source_unreachable", "source": source,
                                 "error": repr(exc)[:500]})
                continue
            for offered in listed.tools:
                if allowlist is not None and offered.name not in allowlist:
                    continue
                tools.append(base.Tool(
                    f"mcp__{source}__{offered.name}", offered.description or "",
                    offered.inputSchema or {"type": "object", "properties": {}},
                    self._source_tool(config, offered.name)))
        return {t.name: t for t in tools}, problems

    async def _skill(self, args: dict) -> dict:
        skill = self.skills.get(str(args.get("skill", "")))
        if skill is None:
            return _text(f"no skill named {args.get('skill')!r} — this role has: "
                         f"{', '.join(sorted(self.skills))}", error=True)
        return _text(skill["body"])

    @staticmethod
    def _source_tool(config: dict, tool_name: str):
        async def call(args: dict) -> dict:
            result = await base._mcp_call_tool(config, tool_name, args)
            parts = [base._block_text(block) or f"[{getattr(block, 'type', '?')} content omitted]"
                     for block in result.content]
            return {"content": [{"type": "text", "text": "\n".join(parts)}],
                    "is_error": bool(getattr(result, "isError", False))}
        return call

    def _bill(self, usage: base.AttemptResult, subtype: str) -> base.AttemptResult:
        """What a call cost: what the provider said it cost, where it says
        (OpenRouter); otherwise the call priced at the model's price."""
        cost = usage.cost_usd if usage.cost_usd is not None else base._priced(self.price, usage)
        return dataclasses.replace(usage, cost_usd=cost, subtype=subtype)

    async def _run(self, call: ToolCall) -> ToolOutput:
        tool = (self.tools or {}).get(call.name)
        if tool is None:
            return ToolOutput(call.id, f"no tool named {call.name} in this run", True)
        try:
            text, is_error = _flatten(await tool.handler(call.input))
        except Exception as exc:
            return ToolOutput(call.id, f"{call.name} failed: {exc!r}"[:2000], True)
        return ToolOutput(call.id, text or "(no output)", is_error)

    async def attempt(self, prompt: str, resume: str | None = None):
        if self.price is None and not getattr(self.client, "reports_cost", False):
            raise Unpriced(f"{base.MODEL} has no price on this install, and this harness "
                           "meters every call by it — a run nobody can price is a run the "
                           "budget cannot stop")
        if self.tools is None:
            self.tools, problems = await self._toolbox()
            for problem in problems:
                yield base.TraceLine(problem)
        # The transcript is held here, so a resume is the interrupted call made
        # again: no resume prompt, nothing redone, the cache intact. Only a
        # first attempt (or one that died before the model said anything)
        # starts from the prompt.
        if resume is None or not self.transcript:
            self.transcript = [Turn("user", text=prompt)]
        yield base.Session(base.RUN_ID)
        specs = [ToolSpec(t.name, t.description, t.input_schema) for t in self.tools.values()]
        spent = 0.0
        while True:
            if self.calls >= MAX_TURNS:
                yield base.TraceLine({"type": "max_turns", "calls": self.calls})
                yield base.Text(f"[stopped at the {MAX_TURNS}-call limit]")
                return
            # The governor. Before the call: nothing starts once the cap is
            # spent, and the output is clamped to what is left. During it: the
            # call is abandoned the moment its prompt alone crosses the cap —
            # where the provider states the prompt's size before it writes a
            # word (Anthropic does; the OpenAI shape states usage only at the
            # end). Either way what one call can overshoot by is bounded by its
            # own prompt. A model metered by the provider's reported cost, with
            # no price to clamp by, is stopped between calls only.
            room = self.max_usd - spent if self.max_usd > 0 else None
            if room is not None and room <= 0:
                raise BudgetReached(f"per-run cap reached: ${spent:.4f} spent of "
                                    f"${self.max_usd:g} left for this attempt")
            max_tokens = MAX_OUTPUT_TOKENS
            if room is not None and self.price is not None and self.price["output"] > 0:
                affordable = int(room / self.price["output"] * 1_000_000)
                max_tokens = max(1, min(MAX_OUTPUT_TOKENS, affordable))
            reply: Reply | None = None
            stated: base.AttemptResult | None = None
            cut = False
            try:
                async with aclosing(self.client.stream(self.system, self.transcript, specs,
                                                       max_tokens)) as events:
                    async for event in events:
                        if isinstance(event, Reply):
                            reply = event
                            continue
                        stated = event
                        if (room is not None and self.price is not None
                                and base._priced(self.price, event) >= room):
                            cut = True
                            break
            except Exception:
                # A call that dies after the provider stated its prompt is
                # metered at that prompt. Whether a provider bills a call it
                # failed is its business; the budget assumes it does.
                if stated is not None:
                    yield self._bill(stated, "interrupted")
                raise
            self.calls += 1
            if cut:
                bill = self._bill(stated, "budget_stop")
                yield bill
                raise BudgetReached(f"per-run cap reached mid-call: this call's prompt "
                                    f"alone costs ${bill.cost_usd:.4f} of ${room:.4f} left")
            if reply is None:
                raise RuntimeError(f"{self.client.name} stream ended without a reply")
            bill = self._bill(reply.usage, reply.stop)
            yield bill
            if bill.cost_usd is None:
                # A provider that states costs, on a call it did not state one
                # for, and no price to fall back on: the meter is blind, so the
                # run stops rather than spend on it.
                raise Unpriced(f"{self.client.name} stated no cost for a call, and "
                               f"{base.MODEL} has no price on this install to meter it by")
            spent += bill.cost_usd
            self.transcript.append(reply.turn)
            yield base.TraceLine({"type": "AssistantMessage", "model": reply.model,
                                  "stop": reply.stop,
                                  "calls": [c.name for c in reply.turn.calls]})
            if reply.turn.text:
                yield base.Text(reply.turn.text)
            if reply.stop == "refusal":
                # A refusal can cut a tool call off mid-input: run nothing.
                raise ModelRefused(f"the model refused to continue: {reply.detail or '-'}")
            if reply.stop == "pause":
                continue
            if not reply.turn.calls:
                return
            outputs = []
            for call in reply.turn.calls:
                yield base.TraceLine({"type": "ToolUseBlock", "name": call.name,
                                      "input": json.dumps(call.input, default=str)[:2000]})
                if reply.stop == "max_tokens":
                    # A truncated input parses as a valid partial object. Never
                    # run it; say why, so the model reissues it smaller.
                    output = ToolOutput(call.id, "not run: this call was cut off at the output "
                                        "limit — reissue it, smaller", True)
                else:
                    output = await self._run(call)
                yield base.TraceLine({"type": "ToolResultBlock", "name": call.name,
                                      "is_error": output.is_error,
                                      "preview": output.text[:2000]})
                outputs.append(output)
            self.transcript.append(Turn("user", outputs=tuple(outputs)))

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, (BudgetReached, Unpriced, ModelRefused)):
            return "fatal"
        return self.client.error_kind(exc)

    def drain(self) -> list[dict]:
        return []
