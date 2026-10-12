"""`yaaf spend-report`: where the model budget actually went.

Unlike every other command (HTTP against the API), this opens the server's
database directly via yaaf.core.db, so it needs the server's DB env vars
(YAAF_DATABASE_URL — see yaaf.core.config), not YAAF_API/YAAF_ADMIN_TOKEN.
Run it on the box that hosts the server, or point YAAF_DATABASE_URL at the
same Postgres.

Dollars (cost_usd) are trustworthy. tokens_in is known-short for runs recorded
before the metering fix (cache tokens were dropped); the report prints token
columns anyway, with a caveat in the header.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import typer
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    AgentKind,
    LedgerEntry,
    LedgerKind,
    Project,
    Run,
    RunKind,
    RunStatus,
    Task,
    TaskStatus,
    Team,
)
from yaaf.runtimes.seats import seat_for
from yaaf.server.contextpack import role_version_for
from yaaf.server.dispatcher import heartbeat_only

TOKENS_CAVEAT = "input tokens exclude cache tokens for runs recorded before the metering fix"
NO_DOLLARS = "(subscription — no dollar figure)"
WASTE_STATUSES = {RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
                  RunStatus.TERMINATED_REFUSALS}


# ── aggregation helpers ─────────────────────────────────────────────────────────
# A bucket is [runs, tokens_in, tokens_out, usd, saw_any_usd]. Rows come from
# explicit-column selects (see _fetch_runs), never full entities, so the report
# works whether or not the metering migration has reached the database.


def _tokens_in(row) -> int:
    """The full prompt where the metering fix has landed: cache tokens live in
    their own columns and belong in the input total. Absent columns count 0."""
    return ((row.tokens_in or 0) + (getattr(row, "cache_creation_tokens", 0) or 0)
            + (getattr(row, "cache_read_tokens", 0) or 0))


def _add(buckets: dict, key, run) -> None:
    bucket = buckets.setdefault(key, [0, 0, 0, 0.0, False])
    bucket[0] += 1
    bucket[1] += _tokens_in(run)
    bucket[2] += run.tokens_out or 0
    if run.cost_usd is not None:
        bucket[3] += run.cost_usd
        bucket[4] = True


def _sum_runs(runs: list) -> list:
    buckets: dict = {}
    for run in runs:
        _add(buckets, "total", run)
    return buckets.get("total", [0, 0, 0, 0.0, False])


def _by_spend(buckets: dict) -> list:
    return sorted(buckets.items(), key=lambda kv: (-kv[1][3], -(kv[1][1] + kv[1][2])))


# ── formatting helpers ──────────────────────────────────────────────────────────


def _n(value: int) -> str:
    return f"{value:,}"


def _usd(bucket: list) -> str:
    return f"${bucket[3]:,.2f}" if bucket[4] else "—"


def _money_line(bucket: list, subscription: bool) -> str:
    """A headline dollar figure — the place the subscription caveat belongs."""
    if bucket[4]:
        return f"${bucket[3]:,.2f}"
    return NO_DOLLARS if subscription else "$0.00"


def _row(labels: list[str], bucket: list) -> list[str]:
    return [*labels, _n(bucket[0]), _n(bucket[1]), _n(bucket[2]), _usd(bucket)]


def _trunc(text: str, width: int = 60) -> str:
    return text if len(text) <= width else text[: width - 1] + "…"


def _table(headers: list[str], rows: list[list[str]], left_cols: int = 1) -> str:
    """Plain aligned columns: labels left, counts and dollars right."""
    if not rows:
        return "  (nothing in this window)"
    widths = [len(header) for header in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: list[str]) -> str:
        cells = [cell.ljust(widths[i]) if i < left_cols else cell.rjust(widths[i])
                 for i, cell in enumerate(row)]
        return ("  " + "  ".join(cells)).rstrip()

    lines = [fmt(headers), "  " + "  ".join("-" * width for width in widths)]
    lines.extend(fmt(row) for row in rows)
    return "\n".join(lines)


def _heading(title: str) -> None:
    typer.echo(f"\n{title}")
    typer.echo("─" * len(title))


# ── attribution ─────────────────────────────────────────────────────────────────


def _agent_model(session: Session, agent: Agent) -> str:
    """The runner's own model pick (yaaf.runtimes.seats), not a copy of it."""
    return seat_for(agent, role_version_for(session, agent)).model


def _run_model(run, model_by_agent: dict[str, str]) -> tuple[str, bool]:
    """(model label, attributed?). runs.model may not exist yet (migration in
    flight) or be NULL for historical rows — fall back to the agent's role."""
    recorded = getattr(run, "model", None)
    if recorded:
        return recorded, False
    if run.agent_id is None:
        return "(no model — platform check)", False
    return model_by_agent.get(run.agent_id, "(unknown)"), True


def _resolve(session: Session, ref: str) -> Project:
    """Accept a project id or slug."""
    if ref.startswith("proj_"):
        found = session.get(Project, ref)
        if found is not None:
            return found
    found = session.execute(select(Project).where(Project.slug == ref)).scalar_one_or_none()
    if found is None:
        typer.echo(f"no project with slug or id {ref!r}")
        raise typer.Exit(1)
    return found


# ── fetching ────────────────────────────────────────────────────────────────────
# The metering migration (runs.model/effort, cache token columns) may or may
# not have reached this database, and the ORM may be older or newer than it.
# Select explicit columns, gated on what BOTH the ORM and the live table have,
# instead of full entities that would demand every mapped column to exist.


def _live_columns(session: Session, table: str) -> set[str]:
    return {column["name"] for column in sa_inspect(session.get_bind()).get_columns(table)}


def _optional(model, names: list[str], live: set[str]) -> list:
    return [getattr(model, name) for name in names
            if name in live and hasattr(model, name)]


def _fetch_runs(session: Session, project_id: str, since: datetime) -> list:
    live = _live_columns(session, "runs")
    columns = [Run.id, Run.agent_id, Run.kind, Run.trigger, Run.task_id, Run.attempt,
               Run.status, Run.tokens_in, Run.tokens_out, Run.cost_usd]
    columns += _optional(Run, ["cache_creation_tokens", "cache_read_tokens", "model"], live)
    return list(session.execute(
        select(*columns).where(Run.project_id == project_id, Run.created_at >= since)
    ).all())


def _fetch_ledger(session: Session, project_id: str, since: datetime) -> list:
    live = _live_columns(session, "ledger")
    columns = [LedgerEntry.kind, LedgerEntry.tokens_in, LedgerEntry.tokens_out,
               LedgerEntry.usd, LedgerEntry.note, LedgerEntry.at]
    columns += _optional(LedgerEntry, ["cache_creation_tokens", "cache_read_tokens"], live)
    return list(session.execute(
        select(*columns).where(LedgerEntry.project_id == project_id, LedgerEntry.at >= since)
    ).all())


# ── the report ──────────────────────────────────────────────────────────────────


def _report(session: Session, project: Project, since: datetime, window: str) -> None:
    subscription = project.billing_mode != "api_key"
    runs = _fetch_runs(session, project.id, since)
    ledger = _fetch_ledger(session, project.id, since)
    agents = {agent.id: agent for agent in session.execute(
        select(Agent).where(Agent.project_id == project.id)).scalars()}
    teams = {team.id: team for team in session.execute(
        select(Team).where(Team.project_id == project.id)).scalars()}

    total = _sum_runs(runs)
    typer.echo(f"Project: {project.name} ({project.slug}) — billing: {project.billing_mode}")
    typer.echo(f"Window:  {window}")
    typer.echo(f"Runs: {_n(total[0])} · tokens in/out: {_n(total[1])} / {_n(total[2])}"
               f" · run spend: {_money_line(total, subscription)}")
    typer.echo(f"Note: {TOKENS_CAVEAT}.")
    if not runs and not ledger:
        typer.echo("\nNothing recorded in this window.")
        return

    # 1. Spend by model.
    model_by_agent = {agent_id: _agent_model(session, agent)
                      for agent_id, agent in agents.items()}
    model_buckets: dict = {}
    attributed = 0
    for run in runs:
        model, was_attributed = _run_model(run, model_by_agent)
        attributed += was_attributed
        _add(model_buckets, model, run)
    _heading("Spend by model")
    rows = [_row([model], bucket) for model, bucket in _by_spend(model_buckets)]
    rows.append(_row(["TOTAL"], total))
    typer.echo(_table(["model", "runs", "tokens in", "tokens out", "usd"], rows))
    if attributed:
        typer.echo(f"  ({_n(attributed)} runs predate runs.model; attributed via the agent's "
                   "role version)")

    # 2. Spend by team, and by agent.
    team_buckets: dict = {}
    agent_buckets: dict = {}
    for run in runs:
        agent = agents.get(run.agent_id) if run.agent_id else None
        if agent is None:
            team_label, agent_label = "(platform)", "(success checks)"
        elif agent.kind == AgentKind.CEO or not agent.team_id:
            team_label, agent_label = "(CEO — no team)", agent.name
        else:
            team = teams.get(agent.team_id)
            team_label = team.name if team else "(unknown team)"
            agent_label = agent.name
        _add(team_buckets, team_label, run)
        _add(agent_buckets, (agent_label, team_label), run)
    _heading("Spend by team")
    typer.echo(_table(["team", "runs", "tokens in", "tokens out", "usd"],
                      [_row([team], bucket) for team, bucket in _by_spend(team_buckets)]))
    _heading("Spend by agent")
    typer.echo(_table(["agent", "team", "runs", "tokens in", "tokens out", "usd"],
                      [_row([name, team], bucket)
                       for (name, team), bucket in _by_spend(agent_buckets)],
                      left_cols=2))

    # 3. Kind × status, with the waste statuses called out.
    kind_status: dict = {}
    for run in runs:
        _add(kind_status, (run.kind, run.status), run)
    _heading("Spend by run kind × status")
    rows = []
    for (kind, status), bucket in sorted(kind_status.items()):
        marker = "← waste" if status in WASTE_STATUSES else ""
        rows.append([kind, status, _n(bucket[0]), _n(bucket[1]), _n(bucket[2]),
                     _usd(bucket), marker])
    typer.echo(_table(["kind", "status", "runs", "tokens in", "tokens out", "usd", ""],
                      rows, left_cols=2))
    wasted = _sum_runs([run for run in runs if run.status in WASTE_STATUSES])
    if wasted[0]:
        if total[4] and total[3]:
            share = f"{wasted[3] / total[3]:.0%} of run spend"
        elif total[1] + total[2]:
            share = f"{(wasted[1] + wasted[2]) / (total[1] + total[2]):.0%} of tokens"
        else:
            share = "share unknown"
        typer.echo(f"  waste (failed/reaped/killed/terminated_refusals): {_n(wasted[0])} runs, "
                   f"{_money_line(wasted, subscription)} — {share}")
    else:
        typer.echo("  no failed/reaped/killed/terminated_refusals runs in this window")

    # 4. Heartbeat waste: the model woke on a timer, not because anything happened.
    heartbeat_runs = [run for run in runs if heartbeat_only(run.trigger)]
    _heading("Heartbeat waste (woken only by heartbeat/daily cadences)")
    if heartbeat_runs:
        no_task = _sum_runs([run for run in heartbeat_runs if run.task_id is None])
        with_task = _sum_runs([run for run in heartbeat_runs if run.task_id is not None])
        typer.echo(_table(["outcome", "runs", "tokens in", "tokens out", "usd"],
                          [_row(["woke, planned nothing (no task)"], no_task),
                           _row(["woke with a task attached"], with_task)]))
        hb_total = _sum_runs(heartbeat_runs)
        if total[4] and total[3] and hb_total[4]:
            typer.echo(f"  heartbeat wakes cost {_money_line(hb_total, subscription)} — "
                       f"{hb_total[3] / total[3]:.0%} of run spend")
    else:
        typer.echo("  none — every run in this window had a non-heartbeat trigger")

    # 5. Retry amplification.
    retries = [run for run in runs if (run.attempt or 1) > 1]
    _heading("Retry amplification (attempt > 1)")
    if retries:
        retry_buckets: dict = {}
        for run in retries:
            _add(retry_buckets, run.status, run)
        rows = [_row([status], bucket) for status, bucket in _by_spend(retry_buckets)]
        rows.append(_row(["TOTAL"], _sum_runs(retries)))
        typer.echo(_table(["final status", "runs", "tokens in", "tokens out", "usd"], rows))
    else:
        typer.echo("  none — no retried runs in this window")

    # 6. Cost per completed task.
    done_tasks = {task.id: task for task in session.execute(
        select(Task).where(Task.project_id == project.id, Task.status == TaskStatus.DONE)
    ).scalars()}
    task_buckets: dict = {}
    for run in runs:
        if run.task_id in done_tasks:
            _add(task_buckets, run.task_id, run)
    _heading("Cost per completed task")
    if task_buckets:
        done_spend = _sum_runs([run for run in runs if run.task_id in done_tasks])
        count = len(task_buckets)
        if done_spend[4]:
            average = f"avg ${done_spend[3] / count:,.2f}/task"
        else:
            average = f"avg {_n((done_spend[1] + done_spend[2]) // count)} tokens/task"
        typer.echo(f"  {count} done tasks with runs in this window · "
                   f"run spend {_money_line(done_spend, subscription)} · {average}")
        top = _by_spend(task_buckets)[:10]
        typer.echo(_table(["top 10 most expensive", "runs", "tokens in", "tokens out", "usd"],
                          [_row([_trunc(done_tasks[task_id].title)], bucket)
                           for task_id, bucket in top]))
    else:
        typer.echo("  no done tasks with run spend in this window")

    # 7. Chat lane vs work lane.
    chat = _sum_runs([run for run in runs if run.kind == RunKind.CHAT_SESSION])
    work = _sum_runs([run for run in runs if run.kind != RunKind.CHAT_SESSION])
    _heading("Chat lane vs work lane")
    typer.echo(_table(["lane", "runs", "tokens in", "tokens out", "usd"],
                      [_row(["chat (chat_session)"], chat),
                       _row(["work (agent_run + success_check)"], work)]))

    # 8. External spend + reconciliation against the ledger.
    _heading("External spend & ledger reconciliation")
    external = [entry for entry in ledger if entry.kind == LedgerKind.EXTERNAL_SPEND]
    if external:
        external_usd = sum(entry.usd or 0.0 for entry in external)
        typer.echo(f"  {len(external)} external-spend entries · total ${external_usd:,.2f}")
        for entry in external[:10]:
            amount = f"${entry.usd:,.2f}" if entry.usd is not None else "—"
            typer.echo(f"    {entry.at:%Y-%m-%d}  {amount:>10}  {_trunc(entry.note or '')}")
        if len(external) > 10:
            typer.echo(f"    … and {len(external) - 10} more")
    else:
        typer.echo("  no external-spend ledger entries in this window")
    ledger_tokens_in = sum(_tokens_in(entry) for entry in ledger)
    ledger_tokens_out = sum(entry.tokens_out or 0 for entry in ledger)
    typer.echo(f"  ledger tokens in/out: {_n(ledger_tokens_in)} / {_n(ledger_tokens_out)} "
               f"vs runs: {_n(total[1])} / {_n(total[2])}")
    if subscription:
        typer.echo(f"  reconciliation: {NO_DOLLARS}")
    else:
        by_kind = {kind: sum(entry.usd or 0.0 for entry in ledger if entry.kind == kind)
                   for kind in (LedgerKind.MODEL_USAGE, LedgerKind.EXTERNAL_SPEND, LedgerKind.INFRA)}
        ledger_usd = sum(entry.usd or 0.0 for entry in ledger)
        typer.echo(f"  reconciliation: ledger usd ${ledger_usd:,.2f} "
                   f"(model_usage ${by_kind['model_usage']:,.2f} + "
                   f"external ${by_kind['external_spend']:,.2f} + "
                   f"infra ${by_kind['infra']:,.2f}) "
                   f"vs run cost_usd ${total[3]:,.2f} — difference ${ledger_usd - total[3]:+,.2f}")


# ── the command ─────────────────────────────────────────────────────────────────


def spend_report(
    project: str = typer.Argument(None, help="Project slug or id (or use --all-projects)"),
    days: int = typer.Option(None, "--days",
                             help="Look back N days; default: the current calendar month"),
    all_projects: bool = typer.Option(False, "--all-projects",
                                      help="Report on every project instead of one"),
) -> None:
    """Analyze LLM spend straight from the database (no API involved).

    Needs the server's DB env vars (YAAF_DATABASE_URL), not
    YAAF_API/YAAF_ADMIN_TOKEN — run it where the server runs, or point
    YAAF_DATABASE_URL at the same Postgres.
    """
    if all_projects and project:
        typer.echo("give a project or --all-projects, not both")
        raise typer.Exit(1)
    if not all_projects and not project:
        typer.echo("give a project slug/id, or --all-projects")
        raise typer.Exit(1)
    now = datetime.now(UTC)
    if days is not None:
        since = now - timedelta(days=days)
        window = f"last {days} days (since {since:%Y-%m-%d %H:%M} UTC)"
    else:
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        window = f"{now:%B %Y} month-to-date (since {since:%Y-%m-%d} UTC)"
    with db_session() as session:
        if all_projects:
            targets = list(session.execute(select(Project).order_by(Project.slug)).scalars())
            if not targets:
                typer.echo("no projects in the database")
                raise typer.Exit(1)
        else:
            targets = [_resolve(session, project)]
        for index, target in enumerate(targets):
            if index:
                typer.echo("\n" + "=" * 78 + "\n")
            _report(session, target, since, window)
