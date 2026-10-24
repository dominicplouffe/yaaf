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

try:
    import wire  # the sandbox: copied into the image beside this file
except ModuleNotFoundError:  # the control plane, and the tests that drive this
    from yaaf.runtimes import wire

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
MODEL_CREDENTIAL_ENV = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN",
                        base.SEARCH_KEY_ENV)
SEARCH_RESULTS_PER_QUERY = 8
#: Where the loop makes room, as a share of the model's context window: old
#: tool output goes first, and only if that is not enough is the history
#: summarised. Claude Code compacts on its own; a provider's API does not, and
#: a run that outgrows its window on a 128k model is a run that dies mid-task.
COMPACT_AT = 0.8
#: The tool results eliding leaves whole: the latest turns' — the ones the
#: model is most likely acting on.
KEEP_RECENT_OUTPUTS = 2
#: What a summary call may carry of the history, as a share of the window; the
#: oldest part goes first when the rest will not fit.
SUMMARY_INPUT_SHARE = 0.5
SUMMARY_MAX_TOKENS = 8_000
#: Tokens per character, for what has not been measured yet: the tool output a
#: call has not been sent with. Deliberately pessimistic — code and JSON run
#: nearer three characters a token than four.
CHARS_PER_TOKEN = 3
COMPACT_PROMPT = (
    "Your context window is nearly full, so this conversation is being compacted. "
    "Below is the work so far. Write the summary you will continue from: the task and "
    "its success criteria, decisions made and why, every file created or changed (exact "
    "paths), commands and their outcomes that matter, what is done, what remains, and "
    "the very next step. Keep exact identifiers, paths, numbers and error messages. Do "
    "not call tools; reply with the summary only.\n\n")


# ── the loop's own shapes: what every provider's client speaks ─────────────────
#
# The wire's (yaaf.runtimes.wire), so a client built on it hands its turns to
# the loop as they are, and one built on a vendor SDK speaks the same.

ToolSpec = wire.ToolSpec
ToolCall = wire.ToolCall
ToolOutput = wire.ToolOutput
Turn = wire.Turn


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


class ContextExhausted(RuntimeError):
    """The history no longer fits the model's window even summarised."""


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


#: The platform search backend's transport — a test's stand-in, else httpx's.
SEARCH_TRANSPORT: httpx.AsyncBaseTransport | None = None

#: The file tools — what a `files` grant means — by name.
FILE_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep")

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
        #: Bills a tool ran up between model calls — a search — yielded and
        #: held to the cap like a call's own.
        self.charges: list[base.AttemptResult] = []
        #: The model's context window in tokens, where the lease knew it; 0
        #: leaves only the provider's own refusal to compact on.
        self.window = int(base.MODEL_FLAGS.get("context") or 0)
        #: The transcript's size as the provider last measured it (the last
        #: call's prompt and reply), and the characters added since.
        self.measured = 0
        self.unmeasured_chars = 0

    def budget(self, remaining_usd: float) -> None:
        self.max_usd = remaining_usd

    async def _toolbox(self) -> tuple[dict[str, base.Tool], list[dict]]:
        tools = [dataclasses.replace(t, name=f"mcp__yaaf__{t.name}",
                                     input_schema=base.wire_schema(t))
                 for t in base.run_tools()]
        tools += [BUILTINS[name] for name in base._builtin_tools() if name in BUILTINS]
        if "WebSearch" in base._builtin_tools() and not getattr(self.client, "native_search",
                                                                 False):
            # Search the provider's wire does not do as a server tool: the
            # provider's own grounded call where it has one (Gemini), else the
            # platform's backend. Neither is a grant the doors did not check
            # (R2): a run reaches here searchless only by a door nobody built.
            searcher = getattr(self.client, "search", None)
            if searcher is not None or base.SEARCH:
                tools.append(base.Tool(
                    "WebSearch", "Search the web. Returns the top results — titles, addresses "
                    "and snippets — to read further with WebFetch.",
                    {"type": "object", "properties": {"query": {"type": "string"}},
                     "required": ["query"]}, self._web_search))
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

    async def _web_search(self, args: dict) -> dict:
        query = str(args.get("query", "")).strip()
        if not query:
            return _text("query is empty", error=True)
        searcher = getattr(self.client, "search", None)
        if searcher is not None:
            text, bill = await searcher(query)
            self.charges.append(bill)
            return _text(text)
        try:
            text, bill = await base.platform_search(query, SEARCH_RESULTS_PER_QUERY,
                                                    transport=SEARCH_TRANSPORT)
        except (wire.WireError, httpx.HTTPError) as exc:
            return _text(f"search failed: {exc}", error=True)
        self.charges.append(bill)
        return _text(text)

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
        return dataclasses.replace(usage, cost_usd=cost, subtype=usage.subtype or subtype)

    async def _run(self, call: ToolCall) -> ToolOutput:
        tool = (self.tools or {}).get(call.name)
        if tool is None:
            return ToolOutput(call.id, f"no tool named {call.name} in this run", True)
        try:
            text, is_error = _flatten(await tool.handler(call.input))
        except Exception as exc:
            return ToolOutput(call.id, f"{call.name} failed: {exc!r}"[:2000], True)
        return ToolOutput(call.id, text or "(no output)", is_error)

    # ── room in the context window ────────────────────────────────────────────

    def _size(self) -> int:
        """The transcript in tokens: the provider's own count of it where one
        call has been made, plus an estimate of what was added since. A
        provider that states no usage (some local servers) is estimated whole."""
        if self.measured:
            return self.measured + self.unmeasured_chars // CHARS_PER_TOKEN
        chars = sum(len(t.text) + sum(len(o.text) for o in t.outputs)
                    + sum(len(json.dumps(c.input, default=str)) for c in t.calls)
                    for t in self.transcript)
        return chars // CHARS_PER_TOKEN

    def _crowded(self) -> bool:
        return bool(self.window) and self._size() > self.window * COMPACT_AT

    def _elide(self) -> int:
        """Old tool output replaced by a line saying what it was. The calls
        and their pairing stay — every provider refuses a tool call left
        without its result — so only the bulk goes. Returns characters freed."""
        user_turns = [i for i, t in enumerate(self.transcript) if t.outputs]
        names = {c.id: c.name for t in self.transcript for c in t.calls}
        freed = 0
        for index in user_turns[:-KEEP_RECENT_OUTPUTS or None]:
            turn = self.transcript[index]
            outputs = []
            for output in turn.outputs:
                if len(output.text) > 200 and not output.text.startswith("[elided"):
                    note = (f"[elided to fit the context window: {len(output.text):,} characters "
                            f"of {names.get(output.call_id, 'tool')} output — run it again if "
                            "you need it]")
                    freed += len(output.text) - len(note)
                    output = dataclasses.replace(output, text=note)
                outputs.append(output)
            self.transcript[index] = dataclasses.replace(turn, outputs=tuple(outputs))
        if freed and self.measured:
            self.measured = max(0, self.measured - freed // CHARS_PER_TOKEN)
        return freed

    def _history(self) -> str:
        """The transcript as plain text for a summary call: no tool blocks, so
        no provider's pairing rules, and no thinking to replay."""
        names = {c.id: c.name for t in self.transcript for c in t.calls}
        lines = []
        for turn in self.transcript:
            if turn.role == "assistant":
                if turn.text:
                    lines.append(f"ASSISTANT: {turn.text}")
                lines += [f"ASSISTANT called {c.name}({_clip(json.dumps(c.input, default=str), 1000)})"
                          for c in turn.calls]
            elif turn.outputs:
                lines += [f"RESULT of {names.get(o.call_id, 'tool')}"
                          f"{' (error)' if o.is_error else ''}: {_clip(o.text, 4000)}"
                          for o in turn.outputs]
            elif turn.text:
                lines.append(f"USER: {turn.text}")
        text = "\n\n".join(lines)
        limit = int((self.window or 128_000) * SUMMARY_INPUT_SHARE * CHARS_PER_TOKEN)
        if len(text) > limit:
            text = "[the oldest part of the history was dropped to fit]\n\n" + text[-limit:]
        return text

    async def _summarise(self, room: float | None):
        """The history replaced by the model's own summary of it, on the
        original prompt. Billed as the call it is."""
        max_tokens = SUMMARY_MAX_TOKENS
        if room is not None and self.price is not None and self.price["output"] > 0:
            max_tokens = max(1, min(max_tokens, int(room / self.price["output"] * 1_000_000)))
        reply: Reply | None = None
        async with aclosing(self.client.stream(
                self.system, [Turn("user", text=COMPACT_PROMPT + self._history())], [],
                max_tokens)) as events:
            async for event in events:
                if isinstance(event, Reply):
                    reply = event
        if reply is None or not reply.turn.text.strip():
            raise ContextExhausted("the model returned no summary to compact the history to")
        self.calls += 1
        yield self._bill(reply.usage, "compaction")
        first = next((t.text for t in self.transcript if t.role == "user" and t.text), "")
        self.transcript = [Turn("user", text=(
            f"{first}\n\n---\n[Context compacted: the conversation so far was summarised to fit "
            f"the model's context window. Your summary of it:]\n\n{reply.turn.text.strip()}\n\n"
            "Continue the work from here."))]
        self.measured, self.unmeasured_chars = 0, 0

    async def _make_room(self, room: float | None, *, refused: bool, summarised: bool):
        """Elide first; summarise when eliding was not enough — or when the
        provider refused the call as too long and eliding had nothing left."""
        before = self._size()
        freed = self._elide()
        if freed and not refused and not self._crowded():
            yield base.TraceLine({"type": "compacted", "how": "elided", "tokens_before": before,
                                  "tokens_after": self._size()})
            return
        if freed and refused:
            yield base.TraceLine({"type": "compacted", "how": "elided", "tokens_before": before,
                                  "tokens_after": self._size(), "after_refusal": True})
            return
        if summarised:
            raise ContextExhausted("the history does not fit the model's context window even "
                                   "summarised — the task needs a model with a larger window")
        async for bill in self._summarise(room):
            yield bill
        yield base.TraceLine({"type": "compacted", "how": "summarised", "tokens_before": before,
                              "tokens_after": self._size()})

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
        # starts from the prompt. A transcript that ends on a finished reply
        # was not interrupted: the prompt is the other side's next word — the
        # boardroom conversation's next turn (chat_entry), with a call limit
        # of its own.
        if resume is None or not self.transcript:
            self.transcript = [Turn("user", text=prompt)]
        elif self.transcript[-1].role == "assistant" and not self.transcript[-1].calls:
            self.transcript.append(Turn("user", text=prompt))
            self.unmeasured_chars += len(prompt)
            self.calls = 0
        yield base.Session(base.RUN_ID)
        specs = [ToolSpec(t.name, t.description, t.input_schema) for t in self.tools.values()]
        spent = 0.0
        # Whether the provider refused the last call as too long, and whether
        # the history has been summarised since the last call that fit: a
        # second summary of a summary is a loop, not a fix.
        overflowed = summarised = False
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
            if self._crowded() or overflowed:
                async for item in self._make_room(room, refused=overflowed,
                                                  summarised=summarised):
                    if isinstance(item, base.AttemptResult):
                        summarised = True
                        spent += item.cost_usd or 0.0
                    yield item
                overflowed = False
                continue
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
            except Exception as exc:
                # A call that dies after the provider stated its prompt is
                # metered at that prompt. Whether a provider bills a call it
                # failed is its business; the budget assumes it does.
                if stated is not None:
                    yield self._bill(stated, "interrupted")
                if self.client.error_kind(exc) != "context":
                    raise
                # Too long for the window: make room and make the call again.
                yield base.TraceLine({"type": "context_refused", "error": repr(exc)[:500]})
                overflowed = True
                continue
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
            self.measured = (bill.tokens_in + bill.cache_read + bill.cache_creation
                             + bill.tokens_out)
            self.unmeasured_chars = 0
            summarised = False
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
            self.unmeasured_chars += sum(len(o.text) for o in outputs)
            while self.charges:
                charge = self._bill(self.charges.pop(0), "web_search")
                yield charge
                spent += charge.cost_usd or 0.0

    def error_kind(self, exc: BaseException) -> str | None:
        if isinstance(exc, (BudgetReached, Unpriced, ModelRefused, ContextExhausted)):
            return "fatal"
        kind = self.client.error_kind(exc)
        # Reaching the harness at all, a refusal for length was not fixed by
        # making room: retrying it unchanged would be refused the same way.
        return "fatal" if kind == "context" else kind

    def drain(self) -> list[dict]:
        return []
