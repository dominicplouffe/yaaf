"""Can this model do yaaf's work? A check an administrator runs on a credential
(docs/PROVIDERS.md §8).

`yaaf smoke` proves a key, an address and a wire with one call. It cannot say
whether the model behind them can *drive an agent*: follow an instruction to
the letter, fill a nested tool schema, read a tool's result and use it, keep
going across a dozen calls, recover when a tool refuses. Those are what a seat
does all day, and a model that answers a ping can still fail every one of them
— which a chairman otherwise finds out from a project that quietly gets
nothing done.

So the check is two batteries, both spoken through the wire a seat on the
credential runs (yaaf.runtimes.wire), both graded mechanically — never by
another model, whose verdict would be one more thing to trust:

- **Probes** — one capability each: exact instructions, structured tool
  arguments, a tool result used, parallel calls, the effort dial, search.
- **Tasks** — five small pieces of agent work over an in-memory workspace
  (aggregate a paginated tool, find a fact across files, edit a config, recover
  from a refused write, follow an output format exactly). Nothing a model
  writes is executed: every grader reads state.

It costs the credential's own money — tens of calls per model — which is
stated before it runs, metered as it runs, and recorded with the result.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from yaaf.runtimes import wire

#: The most calls one task may make before it is failed as lost.
TASK_CALLS = 14
#: What one call may write. Enough for any answer these ask for.
CALL_TOKENS = 2048
SYSTEM = ("You are an autonomous agent completing a task with the tools you are given. "
          "Use the tools; do not ask questions; when the task says to submit, call submit.")


@dataclass
class Outcome:
    """One probe or task, graded."""
    name: str
    kind: str  # "probe" | "task"
    ok: bool | None  # None: not applicable to this model, and why is `detail`
    detail: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    web_searches: int = 0
    cost_usd: float | None = None  # what the provider stated; else priced by the caller
    latency_ms: int = 0

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class _Meter:
    calls: int = 0
    usage: list[wire.Usage] = field(default_factory=list)
    started: float = field(default_factory=time.monotonic)

    def outcome(self, name: str, kind: str, ok: bool | None, detail: str) -> Outcome:
        stated = [u.cost_usd for u in self.usage]
        return Outcome(
            name=name, kind=kind, ok=ok, detail=detail, calls=self.calls,
            input_tokens=sum(u.input for u in self.usage),
            output_tokens=sum(u.output for u in self.usage),
            cache_read=sum(u.cache_read for u in self.usage),
            web_searches=sum(u.web_searches for u in self.usage),
            cost_usd=sum(stated) if stated and all(c is not None for c in stated) else None,
            latency_ms=int((time.monotonic() - self.started) * 1000))


@dataclass(frozen=True)
class Target:
    """What is checked: the wire, the model by the provider's own name, the
    lowest rung of its effort dial (None: it has none) and whether the
    provider's own API searches."""
    endpoint: wire.Endpoint
    model: str
    effort: str | None
    search: bool
    transport: httpx.AsyncBaseTransport | None = None


async def _ask(target: Target, meter: _Meter, transcript: list[wire.Turn],
               tools: list[wire.ToolSpec], *, effort: str | None = None,
               web_search: bool = False) -> wire.Reply:
    try:
        replies = [r async for r in wire.call(target.endpoint, target.model, SYSTEM,
                                              transcript, tools, CALL_TOKENS, effort=effort,
                                              web_search=web_search,
                                              transport=target.transport)]
    except Exception as exc:
        # The calls already made were paid for: the step's failure carries
        # them, so the check is priced at what it spent, not at zero.
        exc.meter = meter  # type: ignore[attr-defined]
        raise
    reply = replies[-1] if replies else None
    if reply is None:
        raise wire.WireError(0, {"error": {"message": "no reply"}}, "transient")
    meter.calls += 1
    meter.usage.append(reply.usage)
    return reply


def _tool(name: str, description: str, properties: dict, required: list[str]) -> wire.ToolSpec:
    return wire.ToolSpec(name, description, {"type": "object", "properties": properties,
                                             "required": required})


# ── probes: one capability each ────────────────────────────────────────────────


async def probe_instructions(target: Target) -> Outcome:
    meter = _Meter()
    reply = await _ask(target, meter, [wire.Turn("user", text=(
        "Reply with exactly this text and nothing else: CHECK-4417"))], [])
    said = reply.turn.text.strip().strip("`\"'.")
    ok = said == "CHECK-4417"
    return meter.outcome("instructions", "probe", ok,
                         "replied exactly as told" if ok else f"replied {said[:120]!r}")


TICKET = _tool("create_ticket", "File a support ticket.", {
    "title": {"type": "string"},
    "priority": {"type": "string", "enum": ["low", "normal", "urgent"]},
    "tags": {"type": "array", "items": {"type": "string"}, "minItems": 1},
    "assignee": {"type": "object", "properties": {"name": {"type": "string"},
                                                  "team": {"type": "string"}},
                 "required": ["name", "team"]},
}, ["title", "priority", "tags", "assignee"])


async def probe_tool_arguments(target: Target) -> Outcome:
    meter = _Meter()
    reply = await _ask(target, meter, [wire.Turn("user", text=(
        "File a ticket: the login page returns HTTP 500 for every user. It is urgent. Tag it "
        "auth and outage. Assign it to Priya on the platform team."))], [TICKET])
    calls = [c for c in reply.turn.calls if c.name == "create_ticket"]
    if not calls:
        return meter.outcome("tool_arguments", "probe", False, "did not call the tool")
    args = calls[0].input
    assignee = args.get("assignee") if isinstance(args.get("assignee"), dict) else {}
    tags = {str(t).lower() for t in args.get("tags") or [] if isinstance(t, str)}
    wrong = [what for what, fine in (
        ("priority", args.get("priority") == "urgent"),
        ("tags", {"auth", "outage"} <= tags),
        ("assignee.name", "priya" in str(assignee.get("name", "")).lower()),
        ("assignee.team", "platform" in str(assignee.get("team", "")).lower()),
        ("title", bool(str(args.get("title", "")).strip()))) if not fine]
    return meter.outcome("tool_arguments", "probe", not wrong,
                         "filled a nested schema correctly" if not wrong
                         else f"wrong {', '.join(wrong)}: {json.dumps(args)[:300]}")


ORDER = _tool("lookup_order", "Look up an order by its id.",
              {"order_id": {"type": "string"}}, ["order_id"])


async def probe_tool_result(target: Target) -> Outcome:
    meter = _Meter()
    transcript = [wire.Turn("user", text="Where is order A-1182? Give me its tracking number.")]
    reply = await _ask(target, meter, transcript, [ORDER])
    calls = [c for c in reply.turn.calls if c.name == "lookup_order"]
    if not calls:
        return meter.outcome("tool_result", "probe", False, "did not look the order up")
    transcript += [reply.turn, wire.Turn("user", outputs=tuple(
        wire.ToolOutput(c.id, json.dumps({"order_id": "A-1182", "status": "shipped",
                                          "carrier": "DHL", "tracking": "JD014600003829"}))
        for c in calls))]
    answer = await _ask(target, meter, transcript, [ORDER])
    ok = "JD014600003829" in answer.turn.text
    return meter.outcome("tool_result", "probe", ok,
                         "used the tool's result in its answer" if ok
                         else f"answered without it: {answer.turn.text[:200]!r}")


WEATHER = _tool("get_weather", "Current weather for one city.",
                {"city": {"type": "string"}}, ["city"])
_TEMPERATURES = {"paris": "14°C, cloudy", "tokyo": "22°C, clear", "lima": "19°C, mist"}


async def probe_parallel(target: Target) -> Outcome:
    meter = _Meter()
    transcript = [wire.Turn("user", text=(
        "What is the weather in Paris, Tokyo and Lima right now? Look up all three, then "
        "answer with each city's temperature."))]
    asked: set[str] = set()
    widest = 0
    for _ in range(4):
        reply = await _ask(target, meter, transcript, [WEATHER])
        calls = [c for c in reply.turn.calls if c.name == "get_weather"]
        if not calls:
            break
        widest = max(widest, len(calls))
        outputs = []
        for c in calls:
            city = str(c.input.get("city", "")).split(",")[0].strip().lower()
            asked.add(city)
            outputs.append(wire.ToolOutput(c.id, _TEMPERATURES.get(city, "unknown city")))
        transcript += [reply.turn, wire.Turn("user", outputs=tuple(outputs))]
    else:
        return meter.outcome("parallel", "probe", False, "kept calling after every lookup")
    answered = all(t.split("°")[0] in reply.turn.text for t in _TEMPERATURES.values())
    if not (set(_TEMPERATURES) <= asked and answered):
        return meter.outcome("parallel", "probe", False,
                             f"looked up {sorted(asked)}; answer {reply.turn.text[:160]!r}")
    return meter.outcome("parallel", "probe", True,
                         "called the tool for all three at once" if widest >= 3
                         else "got all three, one call at a time (slower, still correct)")


async def probe_effort(target: Target) -> Outcome:
    if target.effort is None:
        return Outcome("effort", "probe", None, "this model has no effort dial to turn")
    meter = _Meter()
    reply = await _ask(target, meter, [wire.Turn("user", text="Reply with the word: ready")],
                       [], effort=target.effort)
    ok = "ready" in reply.turn.text.lower()
    return meter.outcome("effort", "probe", ok, f"took effort {target.effort!r}" if ok
                         else f"took the dial but replied {reply.turn.text[:120]!r}")


async def probe_search(target: Target) -> Outcome:
    if not target.search:
        return Outcome("search", "probe", None,
                       "this provider's API does not search — web roles search through "
                       "the install's backend (Settings → Web search)")
    meter = _Meter()
    question = ("Search the web: what is the current stable version of the Python "
                "programming language? Answer with the version number.")
    if target.endpoint.protocol == "gemini":
        text, sources, usage = await wire.grounded_search(target.endpoint, target.model,
                                                          question, transport=target.transport)
        meter.calls, meter.usage = 1, [usage]
        ok = bool(sources)
        return meter.outcome("search", "probe", ok, f"grounded on {len(sources)} pages" if ok
                             else "answered without searching")
    reply = await _ask(target, meter, [wire.Turn("user", text=question)], [], web_search=True)
    ok = reply.usage.web_searches > 0
    return meter.outcome("search", "probe", ok, "searched with its own server tool" if ok
                         else f"answered without searching: {reply.turn.text[:120]!r}")


PROBES: tuple[Callable, ...] = (probe_instructions, probe_tool_arguments, probe_tool_result,
                                probe_parallel, probe_effort, probe_search)


# ── tasks: small pieces of agent work, graded on state ─────────────────────────


@dataclass
class Workspace:
    files: dict[str, str]
    read_only: tuple[str, ...] = ()
    submitted: str | None = None
    extra: dict[str, Callable[[dict], str]] = field(default_factory=dict)

    def tools(self) -> list[wire.ToolSpec]:
        base = [
            _tool("list_files", "List every file in the workspace.", {}, []),
            _tool("read_file", "Read a file.", {"path": {"type": "string"}}, ["path"]),
            _tool("write_file", "Create or overwrite a file.",
                  {"path": {"type": "string"}, "content": {"type": "string"}},
                  ["path", "content"]),
            _tool("submit", "Submit your final answer. Ends the task.",
                  {"answer": {"type": "string"}}, ["answer"]),
        ]
        return base + [spec for spec in EXTRA_TOOLS if spec.name in self.extra]

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        path = str(args.get("path", "")).strip().lstrip("/").removeprefix("./")
        if name == "list_files":
            return "\n".join(sorted(self.files)) or "(empty)", False
        if name == "read_file":
            if path not in self.files:
                return f"no such file: {path}", True
            return self.files[path], False
        if name == "write_file":
            if any(path.startswith(prefix) for prefix in self.read_only):
                return (f"refused: {path.split('/')[0]}/ is read-only — write reports under "
                        "out/ instead"), True
            self.files[path] = str(args.get("content", ""))
            return f"wrote {len(self.files[path])} characters to {path}", False
        if name == "submit":
            self.submitted = str(args.get("answer", ""))
            return "submitted", False
        if name in self.extra:
            return self.extra[name](args), False
        return f"no tool named {name}", True


EXTRA_TOOLS = (
    _tool("list_orders", "One page of the order book, oldest first. Pages start at 1; a "
          "page with no orders is past the end.", {"page": {"type": "integer"}}, ["page"]),
)


@dataclass(frozen=True)
class Task:
    name: str
    prompt: str
    setup: Callable[[], Workspace]
    grade: Callable[[Workspace], tuple[bool, str]]


def _orders() -> list[dict]:
    statuses = ["paid", "refunded", "paid", "shipped", "refunded", "paid", "cancelled",
                "refunded", "paid", "shipped", "paid", "refunded", "paid", "paid",
                "refunded", "shipped", "paid", "paid", "refunded", "paid", "cancelled",
                "paid"]
    return [{"id": f"O-{1000 + i}", "status": s, "amount": round(12.5 + (i * 7.25) % 90, 2)}
            for i, s in enumerate(statuses)]


def _aggregate_setup() -> Workspace:
    orders = _orders()

    def page(args: dict) -> str:
        try:
            number = int(args.get("page", 1))
        except (TypeError, ValueError):
            return "page must be a whole number"
        chunk = orders[(number - 1) * 6:number * 6] if number >= 1 else []
        return json.dumps({"page": number, "orders": chunk})

    return Workspace(files={}, extra={"list_orders": page})


def _aggregate_grade(ws: Workspace) -> tuple[bool, str]:
    refunded = [o for o in _orders() if o["status"] == "refunded"]
    count, total = len(refunded), round(sum(o["amount"] for o in refunded), 2)
    said = ws.submitted or ""
    numbers = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", said.replace(",", ""))]
    ok = count in numbers and any(abs(n - total) < 0.011 for n in numbers)
    return ok, (f"counted {count} refunds totalling {total}" if ok
                else f"submitted {said[:160]!r}; expected {count} refunds totalling {total}")


_NOTES = {
    "notes/2026-09-02.md": "Standup. Billing migration slipped a week. Ola on call.",
    "notes/2026-09-09.md": "Retro. Agreed to freeze schema changes on Fridays.",
    "notes/2026-09-16.md": "Incident INC-2291: checkout timeouts for 40 minutes. "
                           "On call: Teodora Vance. Root cause: connection pool exhausted.",
    "notes/2026-09-23.md": "Incident INC-2290 follow-up done. On call: Marek Ilić.",
    "notes/2026-09-30.md": "Planning. INC-2291 postmortem scheduled for Thursday.",
    "README.md": "Meeting notes, one file per week.",
}


def _find_grade(ws: Workspace) -> tuple[bool, str]:
    said = (ws.submitted or "").lower()
    ok = "teodora" in said and "2026-09-16" in said
    return ok, ("named the file and the person on call" if ok
                else f"submitted {said[:160]!r}; expected notes/2026-09-16.md and Teodora Vance")


_CONFIG = {"service": "ledger", "replicas": 1, "regions": ["us-east-1"], "debug": True,
           "timeout_s": 30}
_CONFIG_README = ("# Release checklist\n\nBefore the release, change config.json:\n"
                  "1. Run three replicas.\n2. Add the region eu-west-1 (keep the existing one).\n"
                  "3. Remove the debug setting entirely.\nLeave everything else exactly as it "
                  "is. The file must stay valid JSON.\n")


def _config_grade(ws: Workspace) -> tuple[bool, str]:
    try:
        config = json.loads(ws.files.get("config.json", ""))
    except ValueError:
        return False, "config.json is no longer valid JSON"
    expected = {"service": "ledger", "replicas": 3, "regions": ["us-east-1", "eu-west-1"],
                "timeout_s": 30}
    regions_ok = isinstance(config.get("regions"), list) and \
        sorted(config["regions"]) == sorted(expected["regions"])
    ok = regions_ok and {k: v for k, v in config.items() if k != "regions"} == \
        {k: v for k, v in expected.items() if k != "regions"}
    return ok, ("made exactly the three changes" if ok
                else f"config.json is now {json.dumps(config)[:240]}")


def _recover_grade(ws: Workspace) -> tuple[bool, str]:
    report = ws.files.get("out/report.md", "")
    bullets = [line for line in report.splitlines() if re.match(r"\s*[-*] \S", line)]
    ok = report.lstrip().startswith("# Weekly report") and len(bullets) == 3
    if "out/report.md" not in ws.files:
        return False, "never wrote out/report.md after the refusal"
    return ok, ("recovered from the refused write and met the format" if ok
                else f"out/report.md does not match the format: {report[:200]!r}")


_TEAM = ("# Team\n\n- Ines Okafor — design\n- Bram de Wit — backend\n- Chen Alvarez — data\n"
         "- Aiko Brandt — frontend\n- Dmitri Nakamura — infrastructure\n")
_SORTED = ["Chen Alvarez", "Aiko Brandt", "Dmitri Nakamura", "Ines Okafor", "Bram de Wit"]


def _format_grade(ws: Workspace) -> tuple[bool, str]:
    written = ws.files.get("out/names.txt")
    if written is None:
        return False, "never wrote out/names.txt"
    lines = [line.strip() for line in written.strip().splitlines()]
    ok = lines == _SORTED
    return ok, ("listed the names exactly as specified" if ok
                else f"wrote {lines!r}; expected {_SORTED!r}")


TASKS: tuple[Task, ...] = (
    Task("aggregate",
         "Using list_orders, go through the whole order book and work out how many orders "
         "are refunded and the total amount refunded. Submit both numbers.",
         _aggregate_setup, _aggregate_grade),
    Task("find",
         "Somewhere in the workspace notes there is the incident INC-2291. Find which notes "
         "file records the incident itself and who was on call. Submit the file's path and "
         "the person's full name.",
         lambda: Workspace(files=dict(_NOTES)), _find_grade),
    Task("edit_config",
         "Follow the instructions in README.md to update config.json, then submit 'done'.",
         lambda: Workspace(files={"README.md": _CONFIG_README,
                                  "config.json": json.dumps(_CONFIG, indent=2)}),
         _config_grade),
    Task("recover",
         "Write this week's report to reports/weekly.md: a first line '# Weekly report' then "
         "exactly three bullet points ('- ') summarising notes.md. Then submit 'done'.",
         lambda: Workspace(files={"notes.md": "Shipped search. Fixed the export bug. Hired "
                                              "a designer. Planned Q4."},
                           read_only=("reports/",)),
         _recover_grade),
    Task("format",
         "Read team.md and write out/names.txt: the team members' full names, sorted by "
         "surname (the last word of the name, except that 'de Wit' sorts under W), one per "
         "line, nothing else on any line. Then submit 'done'.",
         lambda: Workspace(files={"team.md": _TEAM}), _format_grade),
)


async def run_task(target: Target, task: Task) -> Outcome:
    meter = _Meter()
    ws = task.setup()
    tools = ws.tools()
    transcript = [wire.Turn("user", text=task.prompt)]
    while meter.calls < TASK_CALLS and ws.submitted is None:
        reply = await _ask(target, meter, transcript, tools)
        transcript.append(reply.turn)
        if not reply.turn.calls:
            if reply.stop == "max_tokens":
                transcript.append(wire.Turn("user", text="Continue."))
                continue
            break
        outputs = []
        for call in reply.turn.calls:
            text, failed = ws.run(call.name, call.input if isinstance(call.input, dict) else {})
            outputs.append(wire.ToolOutput(call.id, text, failed))
        transcript.append(wire.Turn("user", outputs=tuple(outputs)))
    if ws.submitted is None and meter.calls >= TASK_CALLS:
        return meter.outcome(task.name, "task", False,
                             f"did not finish within {TASK_CALLS} calls")
    if ws.submitted is None:
        return meter.outcome(task.name, "task", False, "stopped without submitting")
    ok, detail = task.grade(ws)
    return meter.outcome(task.name, "task", ok, detail)


# ── the run ────────────────────────────────────────────────────────────────────


#: Roughly what one model's check spends, in tokens, for the estimate shown
#: before it runs: measured over the batteries above, rounded up.
ESTIMATE_TOKENS = {"input": 60_000, "output": 6_000}


def plan(tasks: bool) -> list[tuple[str, str, Callable]]:
    """What a check runs, in order: (name, kind, coroutine factory)."""
    steps: list[tuple[str, str, Callable]] = [
        (p.__name__.removeprefix("probe_"), "probe", p) for p in PROBES]
    if tasks:
        steps += [(t.name, "task", functools.partial(_task_step, t)) for t in TASKS]
    return steps


async def _task_step(task: Task, target: Target) -> Outcome:
    return await run_task(target, task)


def run_step(target: Target, name: str, kind: str, step: Callable) -> Outcome:
    """One step, its failures recorded rather than raised: a refused call is a
    finding about the credential, and the check goes on to the next step —
    unless the key itself is dead, which the caller stops on."""
    started = time.monotonic()
    try:
        return asyncio.run(step(target))
    except wire.WireError as exc:
        detail = f"the provider refused a call ({exc.kind}): {str(exc)[:300]}"
        failure: Exception = exc
    except (httpx.HTTPError, ValueError) as exc:
        detail = f"the call failed: {exc!r}"[:300]
        failure = exc
    meter = getattr(failure, "meter", None)
    outcome = meter.outcome(name, kind, False, detail) if meter is not None \
        else Outcome(name, kind, False, detail)
    outcome.latency_ms = int((time.monotonic() - started) * 1000)
    return outcome


def verdict(outcomes: list[dict]) -> tuple[str, str]:
    """`passed`, `partial` or `failed`, and the one line a screen shows."""
    graded = [o for o in outcomes if o["ok"] is not None]
    passed = [o for o in graded if o["ok"]]
    tasks = [o for o in graded if o["kind"] == "task"]
    tasks_passed = [o for o in tasks if o["ok"]]
    probes = [o for o in graded if o["kind"] == "probe"]
    probes_passed = [o for o in probes if o["ok"]]
    line = f"{len(probes_passed)}/{len(probes)} probes"
    if tasks:
        line += f", {len(tasks_passed)}/{len(tasks)} tasks"
    if not graded or not passed:
        return "failed", line
    if len(passed) == len(graded):
        return "passed", line
    # The probes are what every seat leans on: a model that cannot fill a tool
    # schema or read a result back is not one to hire, whatever else it did.
    core = {"tool_arguments", "tool_result"}
    if any(o["name"] in core and not o["ok"] for o in probes):
        return "failed", line
    return "partial", line
