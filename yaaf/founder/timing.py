"""Where a Founder turn's time goes.

A drafting turn takes minutes, and the step list on the page cannot say why:
a step starts when a tool *runs*, which is after the model has finished
writing that tool's input. "Writing the draft" times the validation of a
bundle, not the minute the model spent producing it — so the slow part lived
in the gaps between steps, unlabelled.

This records the whole turn instead: how long the CLI took to start, each model
call with its tokens and the tools it asked for, each tool's own run (the
goal grade with its own model calls inside), and the SDK's own count of time
spent in the API. One log line per turn, and the same breakdown on the turn in
the transcript, so a slow turn can be read after the fact.

Nothing here changes what a turn does. It is carried in a context variable
rather than through every signature, because the code it observes — the SDK
loop, the provider loop, the tool handlers and the grader's own model run —
is reached through layers that have no reason to know about it.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("yaaf.founder")

_current: contextvars.ContextVar[Turn | None] = contextvars.ContextVar(
    "founder_turn_timing", default=None)


@dataclass
class Call:
    """One model call: what it cost in time and tokens, and what it asked for."""
    seconds: float = 0.0
    input: int = 0
    cache_read: int = 0
    cache_write: int = 0
    output: int = 0
    #: Characters of thinking the model showed. A proxy, not a count: some
    #: providers summarise thinking, and its tokens are inside `output`.
    thinking_chars: int = 0
    text_chars: int = 0
    tools: list[str] = field(default_factory=list)
    #: When the call began: the end of whatever came before it.
    began: float = field(default=0.0, repr=False)

    def as_dict(self) -> dict:
        return {"seconds": round(self.seconds, 1), "input": self.input,
                "cache_read": self.cache_read, "cache_write": self.cache_write,
                "output": self.output, "thinking_chars": self.thinking_chars,
                "text_chars": self.text_chars, "tools": self.tools}


@dataclass
class Tool:
    name: str
    seconds: float = 0.0
    #: A tool that runs a model of its own (the goal grade) reports it here.
    inner: Turn | None = None

    def as_dict(self) -> dict:
        out: dict = {"name": self.name, "seconds": round(self.seconds, 1)}
        if self.inner is not None:
            out["model"] = self.inner.as_dict()
        return out


@dataclass
class Turn:
    started: float = field(default_factory=time.monotonic)
    ended: float | None = None
    #: Seconds from starting the CLI to its first message; None off the SDK.
    cli_start: float | None = None
    calls: list[Call] = field(default_factory=list)
    tool_runs: list[Tool] = field(default_factory=list)
    #: What the SDK itself says it spent inside the API, when it says.
    api_seconds: float | None = None
    #: The last moment something finished — a model call or a tool. A model
    #: call is timed from here to its last block, because the CLI hands over a
    #: message only once it is written.
    _mark: float | None = None
    _open: dict = field(default_factory=dict)

    # ── what the loops report ─────────────────────────────────────────────

    def seen(self) -> None:
        """Any message from the CLI. The first one ends its start-up."""
        now = time.monotonic()
        if self.cli_start is None and self._mark is None:
            self.cli_start = now - self.started
            self._mark = now

    def model_message(self, message_id: str | None, usage: dict | None, *,
                      tools=(), thinking_chars: int = 0, text_chars: int = 0) -> None:
        """One assistant message, or one block of one — the CLI sends a
        multi-block reply as several messages sharing an id and a usage."""
        now = time.monotonic()
        if self._mark is None:
            self._mark = self.started
        key = message_id or f"anon-{len(self.calls)}"
        call = self._open.get(key)
        if call is None:
            call = Call(began=self._mark)
            self.calls.append(call)
            self._open = {key: call}
            _usage(call, usage)
        call.seconds = now - call.began
        call.tools += list(tools)
        call.thinking_chars += thinking_chars
        call.text_chars += text_chars
        self._mark = now

    def reply(self, seconds: float, usage, *, tools=(), thinking_chars: int = 0,
              text_chars: int = 0) -> None:
        """One finished call from the provider loop, which times its own."""
        call = Call(seconds=seconds, tools=list(tools), thinking_chars=thinking_chars,
                    text_chars=text_chars)
        call.input = int(getattr(usage, "input", 0) or 0)
        call.output = int(getattr(usage, "output", 0) or 0)
        call.cache_read = int(getattr(usage, "cache_read", 0) or 0)
        call.cache_write = int(getattr(usage, "cache_write", 0) or 0)
        self.calls.append(call)
        self._mark = time.monotonic()

    def result(self, duration_api_ms) -> None:
        if duration_api_ms:
            self.api_seconds = (self.api_seconds or 0.0) + duration_api_ms / 1000

    @contextlib.contextmanager
    def tool(self, name: str):
        """Time one tool. Anything it runs a model for is timed inside it."""
        run = Tool(name)
        began = time.monotonic()
        token = _current.set(Turn())
        try:
            yield run
        finally:
            inner = _current.get()
            _current.reset(token)
            run.seconds = time.monotonic() - began
            if inner is not None and inner.calls:
                inner.ended = time.monotonic()
                run.inner = inner
            self.tool_runs.append(run)
            self._mark = time.monotonic()
            self._open = {}

    # ── what it says ──────────────────────────────────────────────────────

    def total(self) -> float:
        return (self.ended or time.monotonic()) - self.started

    def model_seconds(self) -> float:
        return sum(c.seconds for c in self.calls)

    def tool_seconds(self) -> float:
        return sum(t.seconds for t in self.tool_runs)

    def tokens(self) -> dict:
        def add(turn: Turn, out: dict) -> dict:
            for c in turn.calls:
                for k in ("input", "cache_read", "cache_write", "output", "thinking_chars"):
                    out[k] += getattr(c, k)
            for t in turn.tool_runs:
                if t.inner is not None:
                    add(t.inner, out)
            return out
        return add(self, dict.fromkeys(
            ("input", "cache_read", "cache_write", "output", "thinking_chars"), 0))

    def as_dict(self) -> dict:
        return {
            "seconds": round(self.total(), 1),
            "cli_start_seconds": None if self.cli_start is None else round(self.cli_start, 1),
            "model_seconds": round(self.model_seconds(), 1),
            "tool_seconds": round(self.tool_seconds(), 1),
            "api_seconds": None if self.api_seconds is None else round(self.api_seconds, 1),
            "tokens": self.tokens(),
            "calls": [c.as_dict() for c in self.calls],
            "tools": [t.as_dict() for t in self.tool_runs],
        }

    def line(self) -> str:
        return line_of(self.as_dict())


def line_of(timing: dict) -> str:
    """A turn's breakdown in one line: where the seconds went, then the
    slowest calls and what each asked for. Reads `Turn.as_dict()`, so the
    server's log and anything holding a stored turn say the same thing."""
    tokens = timing.get("tokens") or {}
    calls = timing.get("calls") or []
    parts = [f"founder turn {timing.get('seconds', 0):.0f}s:"]
    if timing.get("cli_start_seconds") is not None:
        parts.append(f"cli start {timing['cli_start_seconds']:.0f}s ·")
    read = tokens.get("input", 0) + tokens.get("cache_read", 0) + tokens.get("cache_write", 0)
    parts.append(f"model {timing.get('model_seconds', 0):.0f}s ({len(calls)} calls, "
                 f"{_k(read)} in [{_k(tokens.get('cache_read', 0))} cached] / "
                 f"{_k(tokens.get('output', 0))} out, "
                 f"~{_k(tokens.get('thinking_chars', 0) // 4)} thinking) ·")
    by_tool: dict[str, float] = {}
    for run in timing.get("tools") or []:
        by_tool[run["name"]] = by_tool.get(run["name"], 0.0) + run["seconds"]
    parts.append(" · ".join(f"{name} {secs:.0f}s" for name, secs in
                            sorted(by_tool.items(), key=lambda kv: -kv[1])) or "no tools")
    slow = sorted(calls, key=lambda c: -c["seconds"])[:3]
    if slow:
        parts.append("| slowest calls: " + ", ".join(
            f"{c['seconds']:.0f}s/{_k(c['output'])} out→{'+'.join(c['tools']) or 'reply'}"
            for c in slow))
    return " ".join(parts)


def _usage(call: Call, usage: dict | None) -> None:
    usage = usage or {}
    call.input = int(usage.get("input_tokens") or 0)
    call.cache_read = int(usage.get("cache_read_input_tokens") or 0)
    call.cache_write = int(usage.get("cache_creation_input_tokens") or 0)
    call.output = int(usage.get("output_tokens") or 0)


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def current() -> Turn | None:
    return _current.get()


@contextlib.contextmanager
def turn(recorder: Turn | None = None):
    """Time everything run inside this block as one Founder turn."""
    recorder = recorder if recorder is not None else Turn()
    token = _current.set(recorder)
    try:
        yield recorder
    finally:
        _current.reset(token)
        recorder.ended = time.monotonic()


@contextlib.contextmanager
def tool(name: str):
    """Time a tool on the current turn — a no-op outside one."""
    recorder = _current.get()
    if recorder is None:
        yield None
        return
    with recorder.tool(name) as run:
        yield run
