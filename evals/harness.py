"""The golden-transcript eval harness (PLAN §"Agent runtime").

A scenario is a YAML file naming a project spec, a set of scripted behaviors
(dotted refs into :mod:`evals.behaviors`), a tick count, and declarative
expectations. The harness builds a fresh SQLite database, brings up the real
FastAPI app, provisions the project through the real admin API, registers the
behaviors with the MockModel backend, and runs N dispatch → lease → execute →
janitor ticks — the exact loop production runs, at $0 in model tokens.

While the scenario runs, every agent-API interaction is recorded into a
TRANSCRIPT: an ordered JSONL of (method, path, request body, response status,
response body), plus run boundaries and platform-executed success checks. The
recording is a thin adapter wrapped around the in-process TestClient that
:class:`~yaaf.runner.backends.mock.AgentApi` talks through — real requests,
real responses, real refusals; nothing is mocked.

Transcripts are compared against recorded goldens after NORMALIZING away
nondeterminism: entity ids (``prefix_hex``, see :mod:`yaaf.core.ids`) become
stable per-prefix placeholders in order of first appearance, ISO timestamps
become ``<TS>``, and temp paths become ``<TMP>``. A diff is a regression.

Goldens are recorded on SQLite with the mock backend only — that is what keeps
them deterministic and free.
"""

from __future__ import annotations

import difflib
import importlib
import json
import os
import re
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select


def _bootstrap_env() -> None:
    """Replicate tests/conftest.py: this must land before any yaaf import
    resolves settings. All setdefault, so under pytest the conftest wins."""
    os.environ.setdefault("YAAF_SANDBOX_MODE", "subprocess")
    os.environ.setdefault("YAAF_ALLOW_HOST_SUCCESS_TESTS", "1")
    os.environ.setdefault("YAAF_MASTER_KEY", "kkP1p6Rt0DZ2rWv1w5Zq3o8y6bT4uY9cQ1eS7gJ0aXk=")
    os.environ.setdefault("YAAF_WORKSPACES_ROOT", tempfile.mkdtemp(prefix="yaaf-eval-ws-"))
    os.environ.setdefault("YAAF_ARTIFACTS_ROOT", tempfile.mkdtemp(prefix="yaaf-eval-art-"))


_bootstrap_env()

from fastapi.testclient import TestClient  # noqa: E402

from yaaf.core import ids  # noqa: E402
from yaaf.core.config import settings  # noqa: E402

settings.cache_clear()

from yaaf.core.constitution import effective  # noqa: E402
from yaaf.core.db import db_session, engine, reset_for_tests  # noqa: E402
from yaaf.core.models import (  # noqa: E402
    Base,
    Channel,
    ChannelKind,
    Hypothesis,
    Message,
    Project,
    Task,
    User,
    utcnow,
)
from yaaf.runner.backends import mock as mock_backend  # noqa: E402
from yaaf.runner.daemon import execute_job, lease_and_prepare  # noqa: E402
from yaaf.server.dispatcher import (  # noqa: E402
    MESSAGE_COALESCE_MINUTES,
    MODEL_RUN_KINDS,
    dispatch_once,
)
from yaaf.server.janitor import janitor_once  # noqa: E402
from yaaf.server.policy import (  # noqa: E402
    OPEN_TASK_STATUSES,
    Refusal,
    validate_success_test,
)

EVALS_DIR = Path(__file__).resolve().parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
GOLDEN_DIR = EVALS_DIR / "golden"

ROLE_MIN = {
    "description": "eval role",
    "system_prompt": "You are a scripted eval agent.",
    "tier": "standard",
}


class HarnessError(RuntimeError):
    """Provisioning or scenario plumbing failed — not a regression, a bug."""


# ── scenarios ───────────────────────────────────────────────────────────────────


@dataclass
class Scenario:
    name: str
    description: str
    project: dict
    behaviors: dict[str, str]  # agent name -> dotted behavior ref
    ticks: int
    expectations: dict
    path: Path | None = None


def load_scenario(path: Path | str) -> Scenario:
    path = Path(path)
    raw = yaml.safe_load(path.read_text())
    for key in ("name", "project", "behaviors", "ticks"):
        if key not in raw:
            raise HarnessError(f"{path}: scenario is missing required key '{key}'")
    return Scenario(
        name=raw["name"],
        description=raw.get("description", ""),
        project=raw["project"],
        behaviors=raw["behaviors"],
        ticks=int(raw["ticks"]),
        expectations=raw.get("expectations", {}),
        path=path,
    )


def list_scenarios() -> list[Path]:
    return sorted(SCENARIOS_DIR.glob("*.yaml"))


def golden_path(scenario: Scenario) -> Path:
    return GOLDEN_DIR / f"{scenario.name}.jsonl"


def _resolve_behavior(dotted: str):
    module_name, _, attr = dotted.rpartition(".")
    if not module_name:
        raise HarnessError(f"behavior ref '{dotted}' is not a dotted path")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise HarnessError(f"behavior '{attr}' not found in {module_name}") from exc


# ── transcript recording ────────────────────────────────────────────────────────


class RecordingClient:
    """A thin recording adapter around the in-process TestClient.

    MockBackend injects a client into AgentApi, and AgentApi funnels every
    call through ``client.request(...)``. Wrapping that one method captures
    the complete agent-API conversation — real requests, real responses, real
    refusals — without mocking anything. AgentApi never closes an injected
    client, so this adapter needs nothing but ``request``.
    """

    def __init__(self, inner, transcript: list[dict], agent: str):
        self._inner = inner
        self._transcript = transcript
        self._agent = agent

    def request(self, method: str, url, **kwargs):
        response = self._inner.request(method, url, **kwargs)
        try:
            body: Any = response.json()
        except Exception:
            body = response.text[:2000]
        params = kwargs.get("params")
        self._transcript.append(
            {
                "event": "api",
                "agent": self._agent,
                "method": str(method).upper(),
                "path": str(url),
                "params": dict(params) if params else None,
                "request": kwargs.get("json"),
                "status": response.status_code,
                "response": body,
            }
        )
        return response


# ── provisioning (real admin API) ───────────────────────────────────────────────


def _admin_headers() -> dict:
    """The scenario's chairman, signed in: an account in the eval's throwaway
    database and a personal token for it, as tests/conftest.py seeds one."""
    from yaaf.server import identity

    with db_session() as session:
        user = identity.find_user(session, "chair@example.com")
        if user is None:
            user = User(id=ids.new_id(ids.USER), email="chair@example.com",
                        display_name="Chair", is_admin=True)
            session.add(user)
        _, plaintext = identity.mint_token(session, user, "eval", days=1)
    return {"Authorization": f"Bearer {plaintext}"}


def _expect_ok(response, what: str) -> dict:
    if response.status_code != 200:
        raise HarnessError(f"provisioning failed ({what}): {response.status_code} {response.text}")
    return response.json()


def _provision(client: TestClient, scenario: Scenario) -> dict:
    admin = _admin_headers()
    spec = scenario.project
    teams = spec.get("teams") or []
    _expect_ok(
        client.post(
            "/admin/users",
            json={"email": "chair@example.com", "display_name": "Chair"},
            headers=admin,
        ),
        "create chairman",
    )
    _expect_ok(
        client.post(
            "/admin/roles",
            json={"slug": "ceo", "name": "Ceo", "is_ceo_role": True, **ROLE_MIN},
            headers=admin,
        ),
        "seed ceo role",
    )
    for slug in sorted({t.get("role_slug", "worker") for t in teams} | {"worker"}):
        _expect_ok(
            client.post(
                "/admin/roles",
                json={"slug": slug, "name": slug.title(), "is_ceo_role": False, **ROLE_MIN},
                headers=admin,
            ),
            f"seed role {slug}",
        )
    body = {
        "slug": spec.get("slug", scenario.name.replace("_", "")[:24]),
        "name": spec.get("slug", scenario.name).title(),
        "goal": spec.get("goal", "Exercise the platform under scripted behaviors."),
        "chairman_email": "chair@example.com",
        "ceo_role_slug": "ceo",
        "ceo_name": "CEO",
        "constitution": spec.get("constitution") or {},
        "integrations": spec.get("integrations") or spec.get("config") or {},
        "teams": teams,
    }
    project = _expect_ok(client.post("/admin/projects", json=body, headers=admin), "create project")
    _expect_ok(
        client.post(f"/admin/projects/{project['id']}/start", headers=admin), "start project"
    )
    return project


def _backdate(project_id: str, hours: float) -> None:
    """Make the project look older, so drought grace periods can be escaped
    (same trick as tests/conftest.backdate_project)."""
    with db_session() as session:
        project = session.get(Project, project_id)
        project.created_at = utcnow() - timedelta(hours=hours)


# ── the tick loop ───────────────────────────────────────────────────────────────


def _age_agent_chatter() -> None:
    """Evals compress minutes into ticks: a team message posted last beat is,
    in production time, minutes old by the next one. Without this shift the
    dispatcher's message coalescing would keep scenario agents asleep for the
    whole run. Boardroom and chairman messages are left alone — they wake
    instantly in production too, and the cadence scenarios depend on their
    real timestamps."""
    shift = timedelta(minutes=MESSAGE_COALESCE_MINUTES + 1)
    with db_session() as session:
        boardrooms = {
            c.id for c in session.execute(
                select(Channel).where(Channel.kind == ChannelKind.BOARDROOM)
            ).scalars()
        }
        for message in session.execute(
            select(Message).where(Message.sender_agent_id.is_not(None))
        ).scalars():
            if message.channel_id not in boardrooms:
                message.created_at = message.created_at - shift


def _tick(client: TestClient, transcript: list[dict], tick_no: int) -> int:
    """One platform beat, exactly as tests/mockmodel/test_e2e.py drives it:
    dispatch wakes, execute leased jobs, run the janitor."""
    _age_agent_chatter()
    with db_session() as session:
        dispatch_once(session)
    jobs = lease_and_prepare(limit=16)
    transcript.append({"event": "tick", "n": tick_no, "jobs": len(jobs)})
    for job in jobs:
        # Both lanes are the agent thinking; only a platform check is not. An
        # inbox run testing false here fell through to the check branch and ran
        # with no behavior attached, so an agent that woke to read its mail
        # silently did nothing at all.
        if job.kind in MODEL_RUN_KINDS:
            agent_name = job.context_pack.get("you", {}).get("name", "?")
            transcript.append(
                {"event": "run_start", "agent": agent_name, "kind": job.kind,
                 "trigger": job.trigger}
            )
            recorder = RecordingClient(client, transcript, agent_name)
            result = execute_job(job, backend=mock_backend.MockBackend(client=recorder))
            transcript.append(
                {"event": "run_end", "agent": agent_name, "ok": result.ok,
                 "summary": result.summary}
            )
        else:
            result = execute_job(job)  # success checks: platform-executed, never an agent
            transcript.append(
                {
                    "event": "success_check",
                    "task_id": job.task_id,
                    "exit_code": result.check_exit_code,
                    "output": (result.check_output or "").strip(),
                }
            )
    with db_session() as session:
        janitor_once(session)
    return len(jobs)


# ── running a scenario ──────────────────────────────────────────────────────────


@dataclass
class ScenarioResult:
    scenario: Scenario
    project_id: str
    transcript: list[dict]
    normalized: list[str]
    failures: list[str]


def run_scenario(scenario: Scenario) -> ScenarioResult:
    """Fresh DB, real app, scripted behaviors, N ticks, recorded transcript."""
    workdir = Path(tempfile.mkdtemp(prefix=f"yaaf-eval-{scenario.name}-"))
    os.environ["YAAF_WORKSPACES_ROOT"] = str(workdir / "workspaces")
    os.environ["YAAF_ARTIFACTS_ROOT"] = str(workdir / "artifacts")
    settings.cache_clear()
    reset_for_tests(f"sqlite:///{workdir}/eval.db")
    Base.metadata.create_all(engine())

    mock_backend.clear_registry()
    for agent_name, dotted in scenario.behaviors.items():
        mock_backend.register(agent_name, _resolve_behavior(dotted))

    transcript: list[dict] = []
    try:
        from yaaf.server.app import create_app

        with TestClient(create_app()) as client:
            project = _provision(client, scenario)
            backdate_hours = float(scenario.project.get("backdate_hours") or 0)
            if backdate_hours:
                _backdate(project["id"], backdate_hours)
            for tick_no in range(1, scenario.ticks + 1):
                _tick(client, transcript, tick_no)
        failures = check_expectations(scenario, transcript, project["id"])
    finally:
        mock_backend.clear_registry()

    normalized = normalize_transcript(transcript, scrub=(str(workdir),))
    return ScenarioResult(
        scenario=scenario,
        project_id=project["id"],
        transcript=transcript,
        normalized=normalized,
        failures=failures,
    )


# ── normalization ───────────────────────────────────────────────────────────────

_ID_PREFIXES = sorted(
    {value for key, value in vars(ids).items() if key.isupper() and isinstance(value, str)},
    key=len,
    reverse=True,
)
_ID_RE = re.compile(r"\b(" + "|".join(_ID_PREFIXES) + r")_([0-9a-f]{20,32})\b")
# Seconds are optional: agent-facing prose rounds. `/agent/asks` answers with
# "if he says nothing by 2026-09-08T15:33+00:00", and a normalizer that only
# matched second-precision left that wall clock in the golden — a transcript
# that diffs against itself one minute later.
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)


def normalize_transcript(transcript: list[dict], scrub: tuple[str, ...] = ()) -> list[str]:
    """Serialize the transcript to JSONL with nondeterminism removed:

    - entity ids (``task_018f...``) -> stable per-prefix placeholders
      (``task_0001``) in order of first appearance
    - ISO timestamps -> ``<TS>``
    - the scenario's temp directories -> ``<TMP>``

    Identical scripted runs normalize to identical lines; anything that
    changes the *shape* of the conversation still shows through.
    """
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    def _sub_id(match: re.Match) -> str:
        token = match.group(0)
        if token not in mapping:
            prefix = match.group(1)
            counters[prefix] = counters.get(prefix, 0) + 1
            mapping[token] = f"{prefix}_{counters[prefix]:04d}"
        return mapping[token]

    lines: list[str] = []
    for entry in transcript:
        line = json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str)
        for path_str in scrub:
            line = line.replace(path_str, "<TMP>")
        line = _ID_RE.sub(_sub_id, line)
        line = _TS_RE.sub("<TS>", line)
        lines.append(line)
    return lines


# ── declarative expectations ────────────────────────────────────────────────────


def _check_bound(failures: list[str], label: str, actual: int, bound) -> None:
    """`bound` is an exact int, or a {min:..,max:..} dict."""
    if isinstance(bound, dict):
        low, high = bound.get("min"), bound.get("max")
        if low is not None and actual < low:
            failures.append(f"{label}: {actual} < min {low}")
        if high is not None and actual > high:
            failures.append(f"{label}: {actual} > max {high}")
    elif actual != bound:
        failures.append(f"{label}: expected {bound}, got {actual}")


def _transcript_refusals(transcript: list[dict]) -> list[dict]:
    """Refused agent calls: any 4xx whose body carries a structured error,
    which is exactly how the app's Refusal handler answers."""
    refusals = []
    for entry in transcript:
        if entry.get("event") != "api" or entry["status"] < 400:
            continue
        response = entry.get("response")
        if isinstance(response, dict) and "error" in response:
            refusals.append(
                {
                    "agent": entry["agent"],
                    "path": _ID_RE.sub("{id}", entry["path"]),
                    "status": entry["status"],
                    "error": response["error"],
                }
            )
    return refusals


#: Walls that refuse MORE OF A PERMITTED THING, rather than refusing a wrong
#: thing. Hitting one is a project asking for capacity it has not got; it is
#: not misconduct. The platform counted both as the same event for a long
#: time, which is how "an agent probing a ceiling" and "an agent misbehaving"
#: became one signal — and a scenario could then only ever pin reaching as
#: failure. Splitting them is what lets `reaches` be pinned separately from
#: `refusals` without un-pinning either.
REACHING_MARKERS = (
    "Queue ceiling:",
    "Subtask ceiling:",
    "One item at a time:",
)


def _is_reach(error: str) -> bool:
    return any(marker in error for marker in REACHING_MARKERS)


def _check_refusal_block(
    failures: list[str], label: str, rows: list[dict], want: dict
) -> None:
    """`refusals` and `reaches` are counted identically and differ only in what
    they mean, so they share one checker."""
    if not want:
        return
    if "total" in want:
        _check_bound(failures, f"{label}.total", len(rows), want["total"])
    by_path = Counter(r["path"] for r in rows)
    for path, bound in (want.get("by_path") or {}).items():
        _check_bound(failures, f"{label}.by_path[{path}]", by_path.get(path, 0), bound)
    joined = "\n".join(r["error"] for r in rows)
    for needle in want.get("substrings") or []:
        if needle not in joined:
            failures.append(f"{label}.substrings: no entry mentions {needle!r}")


def _ok_posts(transcript: list[dict], path: str) -> list[dict]:
    """Agent POSTs to one endpoint that the platform actually accepted."""
    return [
        e for e in transcript
        if e.get("event") == "api"
        and e.get("method") == "POST"
        and 200 <= e["status"] < 300
        and _ID_RE.sub("{id}", e["path"]) == path
    ]


def check_expectations(
    scenario: Scenario, transcript: list[dict], project_id: str
) -> list[str]:
    """Check the scenario's declarative expectations against the transcript
    and the final database state. Returns a list of failures (empty = pass)."""
    expected = scenario.expectations or {}
    failures: list[str] = []

    refused = _transcript_refusals(transcript)
    _check_refusal_block(
        failures, "refusals",
        [r for r in refused if not _is_reach(r["error"])],
        expected.get("refusals", {}),
    )
    _check_refusal_block(
        failures, "reaches",
        [r for r in refused if _is_reach(r["error"])],
        expected.get("reaches", {}),
    )

    with db_session() as session:
        cons = effective(session, project_id)
        tasks = [
            {
                "id": t.id,
                "parent": t.parent_task_id,
                "status": str(t.status),
                "success_test": t.success_test,
            }
            for t in session.query(Task).filter(Task.project_id == project_id).all()
        ]
        hypothesis_count = (
            session.query(Hypothesis).filter(Hypothesis.project_id == project_id).count()
        )
        # An agent-authored message in the boardroom is the CEO speaking to the
        # chairman. Counted from the record rather than the transcript because
        # what matters to a floor is that it LANDED, not that it was attempted.
        chairman_messages = (
            session.query(Message)
            .join(Channel, Message.channel_id == Channel.id)
            .filter(
                Message.project_id == project_id,
                Message.sender_agent_id.isnot(None),
                Channel.kind == ChannelKind.BOARDROOM,
            )
            .count()
        )

    want_tasks = expected.get("tasks", {})
    if "total" in want_tasks:
        _check_bound(failures, "tasks.total", len(tasks), want_tasks["total"])
    if "status_counts" in want_tasks:
        actual_counts = dict(Counter(t["status"] for t in tasks))
        if actual_counts != dict(want_tasks["status_counts"]):
            failures.append(
                f"tasks.status_counts: expected {dict(want_tasks['status_counts'])}, "
                f"got {actual_counts}"
            )
    if want_tasks.get("within_ceilings"):
        open_statuses = {str(s) for s in OPEN_TASK_STATUSES}
        open_top = [t for t in tasks if t["parent"] is None and t["status"] in open_statuses]
        if len(open_top) > cons.queue_ceiling:
            failures.append(
                f"tasks.within_ceilings: {len(open_top)} open top-level tasks exceed "
                f"queue_ceiling {cons.queue_ceiling}"
            )
        per_parent = Counter(
            t["parent"] for t in tasks if t["parent"] and t["status"] in open_statuses
        )
        for parent, count in per_parent.items():
            if count > cons.subtask_ceiling:
                failures.append(
                    f"tasks.within_ceilings: {count} open subtasks under {parent} exceed "
                    f"subtask_ceiling {cons.subtask_ceiling}"
                )
    if want_tasks.get("structural_success_tests"):
        for task in tasks:
            try:
                validate_success_test(cons, task["success_test"] or {})
            except Refusal as refusal:
                failures.append(
                    f"tasks.structural_success_tests: task {task['id']} has a success test "
                    f"the constitution would refuse: {refusal.message}"
                )

    want_hypotheses = expected.get("hypotheses", {})
    if "total" in want_hypotheses:
        _check_bound(failures, "hypotheses.total", hypothesis_count, want_hypotheses["total"])

    # ── floors ──────────────────────────────────────────────────────────────
    #
    # Everything above this line can only be tripped by a project doing too
    # much: more refusals than allowed, more tasks than the ceiling, a success
    # test too vague to check. A scenario whose CEO did NOTHING satisfies all
    # of it — `refusals.total: 0` is what silence looks like too. These two
    # blocks are the other direction, and they are the reason house rule 1
    # says an automatic state change may point either way: a floor is pinned
    # the same way a ceiling is, and fails the same build.

    want_escalation = expected.get("escalation", {})
    if "chairman_messages" in want_escalation:
        _check_bound(failures, "escalation.chairman_messages",
                     chairman_messages, want_escalation["chairman_messages"])
    if "asks_opened" in want_escalation:
        _check_bound(failures, "escalation.asks_opened",
                     len(_ok_posts(transcript, "/agent/asks")), want_escalation["asks_opened"])
    if "flags_raised" in want_escalation:
        _check_bound(failures, "escalation.flags_raised",
                     len(_ok_posts(transcript, "/agent/flags/raise")),
                     want_escalation["flags_raised"])

    want_activity = expected.get("activity", {})
    if "tasks_created" in want_activity:
        _check_bound(failures, "activity.tasks_created",
                     len(_ok_posts(transcript, "/agent/tasks")), want_activity["tasks_created"])
    if "runs_used" in want_activity:
        runs = sum(1 for e in transcript if e.get("event") == "run_start")
        _check_bound(failures, "activity.runs_used", runs, want_activity["runs_used"])

    return failures


# ── goldens & diffs ─────────────────────────────────────────────────────────────


def write_golden(scenario: Scenario, normalized: list[str]) -> Path:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    path = golden_path(scenario)
    path.write_text("\n".join(normalized) + ("\n" if normalized else ""))
    return path


def read_golden(scenario: Scenario) -> list[str] | None:
    path = golden_path(scenario)
    if not path.exists():
        return None
    return path.read_text().splitlines()


def unified_diff(golden: list[str], current: list[str], name: str) -> list[str]:
    return list(
        difflib.unified_diff(
            golden,
            current,
            fromfile=f"golden/{name}.jsonl",
            tofile=f"current/{name}.jsonl",
            lineterm="",
        )
    )
