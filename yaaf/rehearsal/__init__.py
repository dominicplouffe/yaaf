"""The fire drill: run the whole project for nothing, before you fund it.

The riskiest moment in this product is the one before the first real run. A
chairman writes a goal, hires a crew off a wizard, hands over an API key and a
budget, and goes to bed. What he had until now was a static checklist — is the
billing credential shaped like a credential, is a team on an outdated role
version — and static checks cannot answer the question he is actually asking:
**will this thing work, and what will it cost me?**

So this drives the real platform, at zero dollars.

It is the same machinery CI already uses to run a whole project for $0 — the
mock runtime, the dispatcher, the janitor, the policy engine, every refusal —
pointed at *his* spec instead of a scripted scenario. The README calls the mock
runtime "the test harness, not a demo mode"; that was the right call for a
demo and the wrong one for a preflight, because the thing a preflight needs is
precisely a test harness aimed at your own configuration.

**What it is honest about.** Three things it can tell you, and one it cannot.

* *Does this configuration get off the ground?* A real walk, in order: roles
  resolve, teams hire, the CEO wakes, scope is proposed, the plan gate does its
  arithmetic, the board opens, work is claimed and closed with evidence. Every
  step is a fact about your spec, and every wall is one your own rules built.
* *How often will it stop and wait for you?* The drill plays the chairman and
  counts every time it had to. That number is the autonomy dial made concrete,
  and nothing else in the product could produce it before the money was spent.
* *What will it cost, and what paces it?* In runs, against your caps, with
  `capacity.py`'s four limits — because a daily total is not the only wall, and
  a crew whose critical path runs through one agent's hourly wake ceiling looks
  perfectly healthy at rest.
* *Whether a real model would do the job well.* It cannot. The crew is
  competent and dull by construction. Nothing here is a quality prediction and
  the report says so where a reader will see it.

**What it never does.** It opens no sockets, runs no commands out of a spec
file, touches no real database, and reads no credential. Every acceptance test
the drill's crew writes is an `observable`, so the platform's command lane
never fires; evidence carries no URL, so the verifier never fetches; and its
chairman notification channels are unset for the duration, so the notices a
pretend project raises are logged and never mailed to a real person. It asserts
its own database is a throwaway SQLite file it created and refuses to start
otherwise, which is the guard that makes it safe to invoke from a server.

**The one thing it changes about your rules.** Three constitution values are
relaxed for the duration — the per-agent hourly wake ceiling, the daily run
ceiling, and concurrency — because they are rules that measure *wall-clock
time*, and a drill compresses a fortnight into seconds. Left in force they
would bind in the first tick and report a stall that only the compression
caused. They are relaxed, it is reported that they were, and their real effect
is computed separately from your actual numbers rather than simulated. Every
other rule you wrote is in force exactly as written.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import httpx

log = logging.getLogger("yaaf.rehearsal")

#: How many platform beats the drill will run before it gives up. A dispatch →
#: execute → janitor → chairman cycle is one beat; a healthy project reaches
#: closed work inside a dozen. The ceiling exists so a spec that has found a
#: genuine livelock reports that rather than spinning.
MAX_TICKS = 60

#: The rules the drill lifts, and why each one has to go. Every one of them
#: measures elapsed time, which is the one thing a drill does not have — left
#: in force they bind in the first beat and the drill reports a stall that only
#: the compression caused. Their real effect on the calendar is computed from
#: the chairman's own numbers in the report instead, which is arithmetic rather
#: than simulation and therefore the more trustworthy of the two anyway.
RELAXED = {
    "wakes_per_agent_per_hour": (
        999, "one wake per agent per hour is a rule about clock time; the drill "
             "compresses a fortnight into seconds and would spend every agent's "
             "hour in the first beat"),
    "runs_per_project_per_day": (
        9999, "the daily ceiling is a rule about clock time, and the drill has "
              "no days"),
    "runs_per_project_per_hour": (
        9999, "the trailing-hour ceiling is a rule about clock time, and a drill "
              "spends a fortnight of them in one second"),
    "inbox_runs_per_project_per_hour": (
        9999, "reading the inbox has its own hourly purse, for the same clock "
              "reason and with the same effect on a compressed drill"),
    "max_concurrent_runs": (
        99, "concurrency protects a shared provider rate limit; the drill calls "
            "no provider"),
    "ceo_heartbeat_hours": (
        0.001, "the CEO plans every few HOURS; a drill that waited for the "
               "second heartbeat would end after the first and report a project "
               "that never got past planning"),
    "ceo_heartbeat_always": (
        True, "an idle planning wake is suppressed to save a full-price run, and "
              "a drill's runs are free — suppressing them here would only hide "
              "whether the board ever opens"),
}

#: The dispatcher batches notices and agent chatter for minutes before it will
#: buy a run for them. Same clock problem as the rules above, but it lives in
#: module constants rather than the constitution, so it is patched rather than
#: amended — safe because the drill owns its whole process.
COALESCE_PATCHES = {
    "NOTICE_COALESCE_MINUTES": 0.0,
    "MESSAGE_COALESCE_MINUTES": 0.0,
}


class RehearsalError(RuntimeError):
    """The drill could not be set up — a bad spec, a missing role, a database
    it refuses to touch. Distinct from a drill that ran and found problems,
    which is a successful drill with a bad report."""


# ── isolation ───────────────────────────────────────────────────────────────────


def _isolate(directory: Path):
    """Point this process at a throwaway SQLite file, prove it, and hand back
    the undo.

    The engine in :mod:`yaaf.core.db` is a process-global. Two consequences,
    both handled here. A drill run inside the live API process would re-point
    the *server* at its own scratch database — which is why the admin route
    shells out to a subprocess, and why this asserts rather than trusts that
    the database it is about to build in is the file it just made. And a drill
    that borrowed the global without giving it back would leave every later
    caller in the process talking to a directory that no longer exists — which
    is why it returns `restore` and the caller runs it in a `finally`.
    """
    from yaaf.core.db import engine, swap_engine
    from yaaf.core.models import Base

    path = directory / "rehearsal.db"
    restore = swap_engine(f"sqlite:///{path}")
    live = str(engine().url)
    if not live.startswith("sqlite") or str(path) not in live:
        restore()
        raise RehearsalError(
            "the rehearsal refused to run: its database is not the throwaway "
            f"file it created ({live}). This is the guard that keeps a drill "
            "away from a real deployment.")
    Base.metadata.create_all(engine())
    return restore


def _isolate_state_roots(directory: Path):
    """Point the workspace and artifact roots inside the drill's temp dir, and
    hand back the undo.

    The dispatcher materializes a job, and materializing a job prepares a
    workspace — `runner/daemon._prepare_workspace` mkdirs
    `<workspaces_root>/<slug>` before any agent runs. The drill goes down that
    same real path, on purpose: running the actual dispatcher is the point.
    But the root it wrote into was the live one, so a drill that promised to
    create nothing left a directory named after a project that does not exist
    — and, when the spec's slug matched a real project, reached into that
    project's own workspace. A GitHub-backed spec would have gone further and
    cloned into it.

    It also could not run where the drill is actually invoked from: the API
    container mounts the workspaces volume read-only, exactly because the API
    has no business writing agent workspaces, so the dashboard's fire drill
    died on `OSError: [Errno 30] Read-only file system` before it could report
    anything at all. Both halves are the same bug, and the fix is the one the
    database already had — the drill works in a directory it created and takes
    to the grave.

    `settings()` is an lru_cache over an env-read object, so the cache is
    cleared on the way in and again on the way out; the environment goes back
    exactly as it was, missing keys included.
    """
    from yaaf.core.config import settings

    roots = {"YAAF_WORKSPACES_ROOT": directory / "workspaces",
             "YAAF_ARTIFACTS_ROOT": directory / "artifacts"}
    previous = {name: os.environ.get(name) for name in roots}
    for name, path in roots.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
    settings.cache_clear()

    def restore() -> None:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        settings.cache_clear()

    live = settings()
    if str(directory) not in live.workspaces_root:
        # Put the environment back before refusing, the way the engine guard
        # does: a drill that bailed out holding the roots would leave the rest
        # of this process writing somewhere it never chose.
        restore()
        raise RehearsalError(
            "the rehearsal refused to run: its workspace root is not the "
            f"throwaway directory it created ({live.workspaces_root}). This is "
            "the guard that keeps a drill away from a real deployment.")

    return restore


def _patch_workspace_clone():
    """Stop a GitHub-backed spec cloning its repo, and hand back the undo.

    Preparing a workspace clones the project's repo on first touch, and the
    drill goes down that same real path — so a spec carrying `github_repo`
    made the fire drill shell out to `git clone` and reach the network, while
    its own report said it opens no socket. Isolating the workspace root moved
    where that clone would land; it did not stop it happening. The repo is not
    what a drill is testing: the scripted crew writes no code, so an empty
    directory and a checkout run the same project.
    """
    from yaaf.runner import daemon as daemon_module

    previous = daemon_module._clone_workspace
    daemon_module._clone_workspace = lambda *args, **kwargs: None

    def restore() -> None:
        daemon_module._clone_workspace = previous

    return restore


def _mute_chairman_notifications():
    """Unset the outbound notification channels, and hand back the undo.

    The chairman a drill invents is `rehearsal@localhost`, and the notices a
    project raises are real: the janitor nags for a verdict, the policy engine
    reports a budget pause, a commitment goes at risk. Every one of those calls
    `chairman_notify`, which reads YAAF_SMTP_HOST and YAAF_CHAIRMAN_WEBHOOK_URL
    out of the environment — and the drill inherits the server's environment.
    So a deployment with mail configured sent a real email, from a real
    account, for every notice of a project that does not exist, and the
    batching windows the drill sets to zero were the one thing that would have
    held them back. The webhook half is worse: an outbound POST, from the drill
    whose own report says it opens no socket.

    Unset rather than stubbed, because unset is a state the product already
    supports and documents — `notify` logs the notice and delivers nothing. The
    whole real notification path still runs, exactly as the drill runs every
    other path for real; only the transport has nowhere to go. Same
    env-and-cache discipline as the state roots, including proving it took.
    """
    from yaaf.core.config import settings

    silenced = {"YAAF_SMTP_HOST": "", "YAAF_CHAIRMAN_WEBHOOK_URL": ""}
    previous = {name: os.environ.get(name) for name in silenced}
    os.environ.update(silenced)
    settings.cache_clear()

    def restore() -> None:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        settings.cache_clear()

    live = settings()
    if live.smtp_host or live.chairman_webhook_url:
        restore()
        raise RehearsalError(
            "the rehearsal refused to run: its chairman notifications still "
            "have somewhere to go. This is the guard that keeps a drill from "
            "mailing a real person about a project that does not exist.")

    return restore


def _patch_coalescing():
    """Lift the dispatcher's batching windows, and hand back the undo.

    These are module constants rather than constitution rules, so they cannot
    be amended — they are patched. Which makes giving them back mandatory: a
    drill that left them at zero would change how every OTHER caller in the
    process paces its wakes, and in a test session that is a whole suite
    quietly measuring the wrong thing. Same discipline as the engine.
    """
    from yaaf.server import dispatcher as dispatcher_module

    previous = {name: getattr(dispatcher_module, name) for name in COALESCE_PATCHES}
    for name, value in COALESCE_PATCHES.items():
        setattr(dispatcher_module, name, value)

    def restore() -> None:
        for name, value in previous.items():
            setattr(dispatcher_module, name, value)

    return restore


def _client() -> httpx.Client:
    """An in-process client over the real app. No socket is ever opened.

    Starlette's `TestClient` rather than `httpx.ASGITransport`: the latter is
    async-only, and every caller here — the dispatcher, the janitor, the mock
    crew — is synchronous by the project's own "one fewer concurrency model to
    get wrong" rule. `TestClient` is a plain `httpx.Client` subclass that
    drives the ASGI app over a portal, which is exactly the shape the mock
    backend already accepts, and it ships with starlette rather than being a
    test-only dependency.
    """
    from starlette.testclient import TestClient

    from yaaf.core.config import settings
    from yaaf.server.app import create_app

    client = TestClient(create_app(), base_url="http://rehearsal")
    client.headers.update({"Authorization": f"Bearer {settings().admin_token}"})
    return client


# ── the spec ────────────────────────────────────────────────────────────────────


def _split_spec(spec: dict) -> tuple[dict, list[dict]]:
    """A setup file is a superset of a project spec: the same object under a
    `project` key, plus `roles` and `secrets`. Take both shapes, and never the
    secrets — a drill has no business decrypting anything."""
    if "project" in spec and isinstance(spec["project"], dict):
        return dict(spec["project"]), list(spec.get("roles") or [])
    return dict(spec), list(spec.get("roles") or [])


def _seed_roles(client: httpx.Client, roles: list[dict], roles_dir: Path | None,
                needed: set[str]) -> dict:
    """Load every role the spec names. Definitions carried by the setup file
    win; anything still missing is looked for in the checkout's library, which
    is what makes `yaaf rehearse examples/…` work on a machine that has never
    deployed anything."""
    loaded: set[str] = set()
    unresolved: list[str] = []
    for role in roles:
        body = dict(role)
        if client.post("/admin/roles", json=body).status_code == 200:
            loaded.add(body.get("slug", ""))
    if roles_dir is not None and roles_dir.is_dir():
        for path in sorted(roles_dir.glob("*.json")):
            try:
                body = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if body.get("slug") in loaded or body.get("slug") not in needed:
                continue
            if client.post("/admin/roles", json=body).status_code == 200:
                loaded.add(body["slug"])
    for slug in sorted(needed - loaded):
        unresolved.append(slug)
    return {"loaded": sorted(loaded), "unresolved": unresolved}


def _roles_named(project: dict) -> set[str]:
    named = {project.get("ceo_role_slug") or "ceo"}
    for team in project.get("teams") or []:
        if team.get("role_slug"):
            named.add(team["role_slug"])
    return named


# ── the chairman the drill plays ────────────────────────────────────────────────


def _be_the_chairman(client: httpx.Client, project_id: str, tick: int,
                     interventions: list[dict]) -> int:
    """Do what the platform is waiting on a human for, and count it.

    Recording this is half the point of the exercise. "How much will this
    actually need me?" is the question the autonomy dial answers in prose and
    nothing in the product could answer in numbers — and it can only be
    answered by running the project, because the dial decides which verbs park
    and the work decides how often they come up.

    The drill answers generously: it accepts scope, signs the date, approves
    spending and holds observable closes. A stricter chairman would produce a
    shorter drill and a less informative report; the count, not the verdict, is
    the output.
    """
    acted = 0

    def note(kind: str, detail: str) -> None:
        nonlocal acted
        acted += 1
        interventions.append({"kind": kind, "detail": detail, "tick": tick})

    waiting = client.get("/observe/desk?live=true").json()
    row = next((p for p in waiting["projects"] if p["id"] == project_id), None)
    if row is None:
        return 0

    for approval in row["pending_approvals"]:
        response = client.post(f"/admin/approvals/{approval['id']}/decide",
                               json={"approve": True, "note": "rehearsal: approved"})
        if response.status_code == 200:
            note("approval", f"{approval['kind']}: {approval['title'][:70]}")

    if row["awaiting_you"]["plan"]:
        plan = client.get(f"/observe/projects/{project_id}/plan").json()
        for requirement in plan.get("requirements", []):
            if requirement["status"] != "proposed":
                continue
            if client.post(f"/admin/requirements/{requirement['id']}/accept",
                           json={}).status_code == 200:
                note("accept scope", f"{requirement['key']}: "
                                     f"{requirement['statement'][:60]}")

    commitment = row["awaiting_you"]["commitment"]
    if commitment is not None and client.post(
            f"/admin/commitments/{commitment['id']}/accept",
            json={"note": "rehearsal: signed the projection"}).status_code == 200:
        note("sign the date", f"delivering by {commitment['deliver_by'][:10]}")

    for verdict in row["awaiting_you"]["verdicts"]:
        plan = client.get(f"/observe/projects/{project_id}/plan").json()
        target = next((r for r in plan.get("requirements", [])
                       if r["key"] == verdict["key"]), None)
        if target is None:
            continue
        if client.post(f"/admin/requirements/{target['id']}/verdict",
                       json={"ok": True, "note": "rehearsal: held"}).status_code == 200:
            note("judge an observable", f"{verdict['key']} — no script can check this")

    if row["frozen_threads"]:
        note("unfreeze a thread",
             f"{row['frozen_threads']} thread(s) the pair breaker froze — only you "
             "can release them")
    return acted


# ── one beat of the platform ────────────────────────────────────────────────────


def _tick(client: httpx.Client) -> int:
    """Dispatch wakes, execute what was leased, then enforce.

    The janitor's own sweep is used minus one step: `verify_pending_evidence`
    fetches evidence source URLs over the network, and a drill that reached the
    internet would be neither offline nor repeatable. Nothing the drill's crew
    files carries a URL, so the step has nothing to do anyway — but calling the
    project sweep directly is the difference between "does not need the network"
    and "would use it if something asked".
    """
    from sqlalchemy import select

    from yaaf.core.db import db_session
    from yaaf.core.models import Project
    from yaaf.runner.backends import mock as mock_backend
    from yaaf.runner.daemon import execute_job, lease_and_prepare
    from yaaf.server.dispatcher import dispatch_once
    from yaaf.server.janitor import _janitor_project

    with db_session() as session:
        dispatch_once(session)
    # One at a time, and re-leased each round. `lease_and_prepare` assembles
    # every pack BEFORE any of them executes, so a batch of five hands four
    # agents a board that the first one has already changed — they then act on
    # a stale pack and are refused for it. Production rarely sees that (the
    # concurrency cap is 3 and the dispatcher gates per agent) and the drill
    # relaxed the cap, so serialising here is what keeps the walls the drill
    # reports the chairman's rather than the harness's.
    jobs = 0
    while True:
        leased = lease_and_prepare(limit=1)
        if not leased:
            break
        for job in leased:
            backend = (mock_backend.MockBackend(client=client)
                       if job.kind == "agent_run" else None)
            execute_job(job, backend=backend)
            jobs += 1
    with db_session() as session:
        for project in session.execute(select(Project)).scalars():
            _janitor_project(session, project)
    return jobs


# ── the drill ───────────────────────────────────────────────────────────────────


def rehearse(spec: dict, *, days: int = 30, usd_per_run: float | None = None,
             max_ticks: int = MAX_TICKS, roles_dir: Path | None = None) -> dict:
    """Run `spec` as a project for nothing, and report what happened."""
    from yaaf.rehearsal import crew, report
    from yaaf.runner.backends import mock as mock_backend

    project_spec, spec_roles = _split_spec(spec)
    if not project_spec.get("goal"):
        raise RehearsalError("the spec has no goal; that is the one thing the CEO "
                             "plans from and the drill cannot invent it")
    if roles_dir is None:
        candidate = Path.cwd() / "roles"
        roles_dir = candidate if candidate.is_dir() else None

    with tempfile.TemporaryDirectory(prefix="yaaf-rehearsal-") as tmp:
        restore_engine = _isolate(Path(tmp))
        restore_state = _isolate_state_roots(Path(tmp))
        restore_clone = _patch_workspace_clone()
        restore_notify = _mute_chairman_notifications()
        restore_pacing = _patch_coalescing()
        client = _client()
        try:
            return _run(client, project_spec, spec_roles, roles_dir, days,
                        usd_per_run, max_ticks, crew, report, mock_backend)
        finally:
            mock_backend.clear_registry()
            client.close()
            # Always, and before the directory goes. Every global the drill
            # borrowed goes back: one left pointing at a deleted temp file, or
            # one left with its batching window at zero, is a bug that surfaces
            # in whatever unrelated code runs next.
            restore_pacing()
            restore_notify()
            restore_clone()
            restore_state()
            restore_engine()


def _run(client, project_spec, spec_roles, roles_dir, days, usd_per_run,
         max_ticks, crew, report, mock_backend) -> dict:
    steps: list[dict] = []
    interventions: list[dict] = []

    def step(key: str, ok: bool, detail: str, tick: int | None = None) -> None:
        steps.append({"key": key, "ok": ok, "detail": detail, "tick": tick})

    roles = _seed_roles(client, spec_roles, roles_dir, _roles_named(project_spec))
    if roles["unresolved"]:
        step("roles", False,
             f"no definition for {', '.join(roles['unresolved'])} — the spec does "
             "not carry it and it is not in this checkout's roles/ library")
        return report.build(client, None, project_spec, steps, interventions,
                            days, usd_per_run, roles, 0)
    step("roles", True, f"{len(roles['loaded'])} role(s) resolved")

    email = project_spec.get("chairman_email") or "rehearsal@localhost"
    client.post("/admin/users", json={"email": email, "display_name": "Rehearsal"})

    body = dict(project_spec)
    body["chairman_email"] = email
    body["slug"] = (body.get("slug") or "rehearsal")[:60]
    # The runtime is the whole point: scripted agents against the real API.
    integrations = dict(body.get("integrations") or {})
    integrations["runtime"] = "mock"
    # Slack and data sources reach the outside world. A drill does neither.
    integrations.pop("slack", None)
    integrations.pop("mcp_servers", None)
    body["integrations"] = integrations

    created = client.post("/admin/projects", json=body)
    if created.status_code != 200:
        detail = created.json().get("detail", created.text)
        step("created", False, f"the platform refused this spec: {detail}")
        return report.build(client, None, project_spec, steps, interventions,
                            days, usd_per_run, roles, 0)
    project = created.json()
    project_id = project["id"]
    agents = 1 + sum(len(t["agents"]) for t in project["teams"])
    step("created", True,
         f"{project_spec.get('mode', 'explore')} · {len(project['teams'])} team(s), "
         f"{agents} agent(s)")

    # The user's own constitution is what ran a moment ago; from here the three
    # clock rules step aside, on the record, as an ordinary amendment.
    relaxed = client.post(f"/admin/projects/{project_id}/amendments", json={
        "reason": "rehearsal: the drill has no wall-clock time, so the rules "
                  "that measure it would bind on the first beat",
        "changes": {name: value for name, (value, _) in RELAXED.items()},
    })
    step("relaxed", relaxed.status_code == 200,
         f"{len(RELAXED)} clock rules lifted for the drill; every other rule you "
         "wrote is in force")

    started = client.post(f"/admin/projects/{project_id}/start")
    if started.status_code != 200:
        detail = started.json().get("detail", started.text)
        step("started", False, f"it would not start: {detail}")
        return report.build(client, project_id, project_spec, steps, interventions,
                            days, usd_per_run, roles, 0)
    step("started", True, "the project is running")

    # Every refusal the crew collects, kept rather than discarded. `AgentApi`
    # records the 403/409/422 it hit and the runner throws it away; a drill's
    # single most useful output is the wall a project keeps walking into, so
    # the collector wraps the crew rather than the crew doing its own logging.
    refusals: list[dict] = []

    # Deliver and Operate own requirements; Explore does not. The drill knows
    # which from the spec, so it tells the crew rather than making it infer —
    # see the note in `crew.ceo`.
    scope_project = project_spec.get("mode", "explore") in ("deliver", "operate")

    def collect(api, job) -> str:
        before = len(api.refusals)
        try:
            return crew.dispatch(api, job, scope_project=scope_project)
        finally:
            for hit in api.refusals[before:]:
                refusals.append({**hit, "agent": job.context_pack["you"]["name"]})

    mock_backend.clear_registry()
    mock_backend.register("*", collect)

    ticks = 0
    idle = 0
    for ticks in range(1, max_ticks + 1):
        worked = _tick(client)
        acted = _be_the_chairman(client, project_id, ticks, interventions)
        report.observe(client, project_id, ticks, steps)
        # Two beats with nobody working and nothing waiting on the chairman is a
        # project that has said everything it has to say.
        idle = idle + 1 if (worked == 0 and acted == 0) else 0
        if idle >= 2:
            break

    return report.build(client, project_id, project_spec, steps, interventions,
                        days, usd_per_run, roles, ticks, refusals)


def load_spec(path: str | Path) -> dict:
    raw = Path(path).read_text()
    try:
        spec = json.loads(raw)
    except ValueError as exc:
        raise RehearsalError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict):
        raise RehearsalError(f"{path} should hold a JSON object")
    return spec


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def main(argv: list[str] | None = None) -> int:
    """`python -m yaaf.rehearsal <spec.json>` — the drill in its own process,
    printing JSON. This is the entry point the API route shells out to, because
    the drill re-points a process-global database engine and must never do that
    inside a server."""
    import argparse

    from yaaf.rehearsal import report

    parser = argparse.ArgumentParser(prog="yaaf.rehearsal")
    parser.add_argument("spec")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--usd-per-run", type=float, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = rehearse(load_spec(args.spec), days=args.days,
                          usd_per_run=args.usd_per_run)
    except RehearsalError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "at": _stamp()})
              if args.json else f"rehearsal: {exc}")
        return 2
    print(json.dumps(result, indent=2, default=str) if args.json
          else report.render_text(result))
    return 0 if result["ok"] else 1


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())


__all__: list[str] = ["MAX_TICKS", "RELAXED", "RehearsalError", "load_spec",
                      "main", "rehearse"]
