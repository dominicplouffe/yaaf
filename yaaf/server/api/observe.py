"""Read-only observation API — the chairman's omniscience.

The chairman *speaks* only in the boardroom, but *reads* everything: every
channel, task, run, ledger row, and a random spot-audit sample of evidence and
completions. In M1 this backs the CLI; the SPA consumes the same endpoints."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import artifacts
from yaaf.core.constitution import effective
from yaaf.core.models import (
    Agent,
    Approval,
    ApprovalStatus,
    Channel,
    Evidence,
    EvidenceKind,
    Hypothesis,
    LedgerEntry,
    Memory,
    MemoryScope,
    Message,
    Project,
    Role,
    RoleVersion,
    Run,
    RunKind,
    RunQueue,
    Secret,
    Suggestion,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    VerificationStatus,
    utcnow,
)
from yaaf.server import policy, typing_state
from yaaf.server.deps import admin_auth, db
from yaaf.server.dispatcher import (
    activity_state,
    chat_session_live,
    daily_runs_used,
    day_start,
)

router = APIRouter(dependencies=[Depends(admin_auth)])


def _secret_name(session: Session, secret_id: str | None) -> str | None:
    if not secret_id:
        return None
    from yaaf.core.models import Secret

    secret = session.get(Secret, secret_id)
    return secret.name if secret else None


def _project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    return project


def _memory_usage(session: Session, project: Project, cons) -> dict:
    """Occupancy per curated scope, counted exactly the way the refusal counts
    it — a number here that disagreed with the one an agent hits would be worse
    than showing none at all."""
    scopes = [{
        "scope": "project",
        "team_id": None,
        "name": "Project memory",
        "live": policy.live_memory_count(session, MemoryScope.PROJECT, project.id),
        "cap": cons.memory_cap_project,
    }]
    for team in session.execute(
        select(Team).where(Team.project_id == project.id).order_by(Team.name)
    ).scalars():
        lead = policy.effective_lead(session, team)
        scopes.append({
            "scope": "team",
            "team_id": team.id,
            "name": team.name,
            "live": policy.live_memory_count(session, MemoryScope.TEAM, project.id, team.id),
            "cap": cons.memory_cap_team,
            "curator": lead.name if lead else None,
        })
    return {"warn_ratio": cons.memory_warn_ratio, "scopes": scopes}


def _last_countable_evidence_at(session: Session, project_id: str) -> datetime | None:
    """When the outside world last verifiably acted — the drought clock's anchor.
    Same countability rule as policy: external AND (verified or attested)."""
    at = session.execute(
        select(func.max(Evidence.created_at)).where(
            Evidence.project_id == project_id,
            Evidence.kind == EvidenceKind.EXTERNAL,
            Evidence.verification.in_(
                [VerificationStatus.VERIFIED, VerificationStatus.NOT_APPLICABLE]
            ),
        )
    ).scalar_one()
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at


@router.get("/projects")
def projects(session: Session = Depends(db)) -> list[dict]:
    return [
        {"id": p.id, "slug": p.slug, "name": p.name, "status": p.status, "goal": p.goal}
        for p in session.execute(select(Project).order_by(Project.created_at)).scalars()
    ]


@router.get("/desk")
def desk(session: Session = Depends(db)) -> dict:
    """Cross-project attention for the landing page: decisions waiting on the
    chairman, countdowns worth watching, what happened while they were away,
    and one comparable row per project — in one request, so the Desk answers
    "what needs me?" without the UI fanning out per project."""
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    soon = now + timedelta(days=7)

    def aware(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    away = {"since": day_ago.isoformat(), "tasks_completed": 0,
            "evidence_verified": 0, "usd_spent": 0.0, "runs": 0, "refusal_storms": 0}
    projects_out: list[dict] = []
    for project in session.execute(select(Project).order_by(Project.created_at)).scalars():
        cons = effective(session, project.id)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usd, tokens_in, tokens_out, cache_creation, cache_read = session.execute(
            select(
                func.coalesce(func.sum(LedgerEntry.usd), 0.0),
                func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
                func.coalesce(func.sum(LedgerEntry.tokens_out), 0),
                func.coalesce(func.sum(LedgerEntry.cache_creation_tokens), 0),
                func.coalesce(func.sum(LedgerEntry.cache_read_tokens), 0),
            ).where(LedgerEntry.project_id == project.id, LedgerEntry.at >= month_start)
        ).one()
        usd_mode = project.billing_mode == "api_key"
        pending = [
            {"id": a.id, "title": a.title, "kind": a.kind, "amount_usd": a.amount_usd,
             "expires_at": aware(a.expires_at).isoformat()}
            for a in session.execute(
                select(Approval)
                .where(Approval.project_id == project.id,
                       Approval.status == ApprovalStatus.PENDING)
                .order_by(Approval.expires_at)
            ).scalars()
        ]
        hyps = list(
            session.execute(
                select(Hypothesis).where(Hypothesis.project_id == project.id)
            ).scalars()
        )
        expired_bets = [
            {"id": h.id, "statement": h.statement,
             "kill_date": aware(h.kill_date).isoformat(),
             "blocked_tasks": session.execute(
                 select(func.count(Task.id)).where(
                     Task.hypothesis_id == h.id, Task.status == TaskStatus.BLOCKED
                 )
             ).scalar_one()}
            for h in hyps
            if h.status == "expired" and not h.extended_once
        ]
        kill_dates_soon = [
            {"id": h.id, "statement": h.statement,
             "kill_date": aware(h.kill_date).isoformat()}
            for h in hyps
            if h.status == "active" and aware(h.kill_date) <= soon
        ]
        completed = session.execute(
            select(func.count(Task.id)).where(
                Task.project_id == project.id, Task.status == TaskStatus.DONE
            )
        ).scalar_one()
        audited = session.execute(
            select(func.count(Task.id)).where(
                Task.project_id == project.id, Task.status == TaskStatus.DONE,
                Task.audit_status.is_not(None),
            )
        ).scalar_one()
        frozen_threads = session.execute(
            select(func.count(Message.id))
            .join(Channel, Channel.id == Message.channel_id)
            .where(Channel.project_id == project.id, Message.frozen_at.is_not(None))
        ).scalar_one()
        running = session.execute(
            select(func.count(Run.id)).where(
                Run.project_id == project.id, Run.status == "running",
                Run.kind != RunKind.CHAT_SESSION,
            )
        ).scalar_one()
        last_run_at = aware(
            session.execute(
                select(func.max(Run.started_at)).where(Run.project_id == project.id)
            ).scalar_one()
        )
        projects_out.append({
            "id": project.id, "slug": project.slug, "name": project.name,
            "status": project.status, "status_reason": project.status_reason,
            "goal": project.goal, "billing_mode": project.billing_mode,
            "budget": {
                "unit": "usd" if usd_mode else "tokens",
                # Token mode must match enforcement (janitor.month_to_date):
                # cache tokens count.
                "spent": (float(usd) if usd_mode
                          else int(tokens_in) + int(tokens_out)
                          + int(cache_creation) + int(cache_read)),
                "cap": cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap,
                "warn_ratio": cons.budget_warn_ratio,
            },
            "open_tasks": len(policy.open_top_level_tasks(session, project.id)),
            "queue_ceiling": cons.queue_ceiling,
            # Only the daily-run gate, not the full activity_state: this loop
            # already runs a dozen aggregates per project, and walking every
            # agent of every project would make the landing page the most
            # expensive request we serve. It is also the gate that actually
            # stops companies — the rest need the project page.
            "runs_today": {
                "used": daily_runs_used(session, project, day_start(now)),
                "cap": cons.runs_per_project_per_day,
            },
            "evidence_7d": policy.countable_external_evidence(session, project.id, week_ago),
            "ceo_judgment": policy.ceo_judgment(session, project.id),
            "pending_approvals": pending,
            "expired_bets": expired_bets,
            "kill_dates_soon": kill_dates_soon,
            "audit": {"completed": int(completed), "audited": int(audited),
                      "unaudited": int(completed) - int(audited)},
            "drought": {
                "active": policy.drought_active(session, project.id, cons),
                "window_hours": cons.drought_hours,
                "applies": aware(project.created_at)
                <= now - timedelta(hours=cons.drought_hours),
                "last_countable_at": (
                    at.isoformat()
                    if (at := _last_countable_evidence_at(session, project.id))
                    else None
                ),
            },
            "frozen_threads": int(frozen_threads),
            "runs_running": int(running),
            "last_run_at": last_run_at.isoformat() if last_run_at else None,
        })
        away["tasks_completed"] += int(
            session.execute(
                select(func.count(TaskEvent.id)).where(
                    TaskEvent.project_id == project.id,
                    TaskEvent.to_status == TaskStatus.DONE,
                    TaskEvent.at >= day_ago,
                )
            ).scalar_one()
        )
        away["evidence_verified"] += policy.countable_external_evidence(
            session, project.id, day_ago
        )
        away["usd_spent"] += float(
            session.execute(
                select(func.coalesce(func.sum(LedgerEntry.usd), 0.0)).where(
                    LedgerEntry.project_id == project.id, LedgerEntry.at >= day_ago
                )
            ).scalar_one()
        )
        away["runs"] += int(
            session.execute(
                select(func.count(Run.id)).where(
                    Run.project_id == project.id, Run.started_at >= day_ago
                )
            ).scalar_one()
        )
        away["refusal_storms"] += int(
            session.execute(
                select(func.count(Run.id)).where(
                    Run.project_id == project.id,
                    Run.status == "terminated_refusals",
                    Run.started_at >= day_ago,
                )
            ).scalar_one()
        )
    away["usd_spent"] = round(away["usd_spent"], 4)
    return {"as_of": now.isoformat(), "away": away, "projects": projects_out}


@router.get("/projects/{project_id}")
def dashboard(project_id: str, session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    cons = effective(session, project.id)
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usd, tokens_in, tokens_out, cache_creation, cache_read = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.usd), 0.0),
            func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
            func.coalesce(func.sum(LedgerEntry.tokens_out), 0),
            func.coalesce(func.sum(LedgerEntry.cache_creation_tokens), 0),
            func.coalesce(func.sum(LedgerEntry.cache_read_tokens), 0),
        ).where(LedgerEntry.project_id == project.id, LedgerEntry.at >= month_start)
    ).one()
    open_tasks = policy.open_top_level_tasks(session, project.id)
    window = utcnow() - timedelta(hours=cons.drought_hours)
    evidence_count = policy.countable_external_evidence(session, project.id, window)
    # The resident chat session is presence, not work — counting it here would
    # pin "working" indicators on forever. It surfaces as chat_live instead.
    running = session.execute(
        select(func.count(Run.id)).where(
            Run.project_id == project.id, Run.status == "running",
            Run.kind != RunKind.CHAT_SESSION,
        )
    ).scalar_one()
    queued = session.execute(
        select(func.count(RunQueue.id)).where(RunQueue.project_id == project.id)
    ).scalar_one()
    pending_approvals = session.execute(
        select(func.count(Approval.id)).where(
            Approval.project_id == project.id, Approval.status == ApprovalStatus.PENDING
        )
    ).scalar_one()
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "status": project.status,
        "status_reason": project.status_reason,
        "goal": project.goal,
        "billing_mode": project.billing_mode,
        "month_to_date": {"usd": float(usd), "tokens_in": int(tokens_in),
                          "tokens_out": int(tokens_out),
                          "cache_creation": int(cache_creation),
                          "cache_read": int(cache_read)},
        "caps": {"monthly_usd_cap": cons.monthly_usd_cap,
                 "monthly_token_cap": cons.monthly_token_cap},
        "open_tasks": len(open_tasks),
        "queue_ceiling": cons.queue_ceiling,
        "external_evidence_in_window": evidence_count,
        "drought_active": policy.drought_active(session, project.id, cons),
        "memory": _memory_usage(session, project, cons),
        "runs": {"running": running, "queued": queued},
        # Why work is or isn't starting. Computed by the dispatcher's own
        # helpers so the number that explains the silence cannot disagree with
        # the number that caused it.
        "activity": activity_state(session, project, cons),
        "chat_live": chat_session_live(session, project),
        "pending_approvals": pending_approvals,
        "ceo_judgment": policy.ceo_judgment(session, project.id),
        "constitution": cons.model_dump(),
        "mode": project.mode,
        "integrations": project.integrations or {},
        "github_repo": project.github_repo,
        "billing_secret_id": project.billing_secret_id,
        "billing_secret_name": _secret_name(session, project.billing_secret_id),
        # Drought-clock anchors: the UI counts toward the refusal, not just
        # reporting it after the fact.
        "created_at": (
            project.created_at if project.created_at.tzinfo
            else project.created_at.replace(tzinfo=UTC)
        ).isoformat(),
        "last_countable_evidence_at": (
            at.isoformat() if (at := _last_countable_evidence_at(session, project.id)) else None
        ),
    }


@router.get("/projects/{project_id}/health")
def project_health(project_id: str, session: Session = Depends(db)) -> dict:
    """Every way this project's configuration can be silently wrong, checked
    server-side with a plain-language fix per finding. All green = the UI
    hides the card. This is the answer to 'is my project set up right?' —
    the project tells you, instead of failing quietly at 9am."""
    from sqlalchemy import or_

    from yaaf.core.crypto import decrypt
    from yaaf.server.api.admin import _billing_shape_error

    project = _project(session, project_id)
    integrations = project.integrations or {}
    checks: list[dict] = []

    def add(key: str, status: str, detail: str, where: str | None = None,
            action: dict | None = None) -> None:
        checks.append({"key": key, "status": status, "detail": detail,
                       "where": where, "action": action})

    # Billing: attached, and the credential matches the mode.
    if not project.billing_secret_id:
        add("billing", "fail", "no billing credential attached — agents cannot run",
            "settings")
    else:
        billing = session.get(Secret, project.billing_secret_id)
        if billing is None:
            add("billing", "fail", "the attached billing credential no longer exists",
                "settings")
        else:
            shape_error = _billing_shape_error(project.billing_mode,
                                               decrypt(billing.ciphertext))
            add("billing", "fail" if shape_error else "ok",
                shape_error or f"paying with '{billing.name}' ({project.billing_mode})",
                "settings")

    secrets = session.execute(
        select(Secret).where(or_(Secret.project_id == project.id,
                                 Secret.project_id.is_(None)))
    ).scalars().all()
    secrets_by_name = {s.name: s for s in secrets}

    # Project data sources: secrets present, granted, and last check green.
    for source in integrations.get("mcp_servers", []):
        name = source.get("name", "?")
        url_secret = secrets_by_name.get(source.get("url_secret", ""))
        if url_secret is None:
            add(f"data-source:{name}", "fail",
                f"data source '{name}': its URL credential is missing — re-save it",
                "settings")
            continue
        ungranted = [r for r in source.get("roles", [])
                     if r not in (url_secret.role_scopes or [])]
        if ungranted:
            add(f"data-source:{name}", "fail",
                f"data source '{name}': credential not granted to role(s) "
                f"{', '.join(ungranted)} — re-save it", "settings")
            continue
        last = source.get("last_check") or {}
        if not last:
            add(f"data-source:{name}", "warn",
                f"data source '{name}': never tested — run its check", "settings")
        elif last.get("ok"):
            add(f"data-source:{name}", "ok",
                f"data source '{name}': {last.get('detail', 'reachable')}", "settings")
        else:
            add(f"data-source:{name}", "fail",
                f"data source '{name}': last check failed — {last.get('detail', '')}",
                "settings")

    # Teams: no role-version drift. (Roles never own data sources — the whole
    # role-source failure class was removed with the concept.)
    for team in session.execute(
        select(Team).where(Team.project_id == project.id)
    ).scalars():
        pinned = session.get(RoleVersion, team.role_version_id)
        role = session.get(Role, pinned.role_id) if pinned else None
        if pinned is None or role is None:
            continue
        latest = session.execute(
            select(RoleVersion).where(RoleVersion.role_id == role.id)
            .order_by(RoleVersion.version.desc()).limit(1)
        ).scalar_one()
        if latest.version > pinned.version:
            add(f"role-drift:{team.name}", "warn",
                f"team '{team.name}' runs role '{role.slug}' v{pinned.version}; "
                f"v{latest.version} exists — adopt it when ready",
                "overview", {"kind": "repin", "team_id": team.id})

    # Slack: config present implies its secrets exist.
    slack = integrations.get("slack") or {}
    if slack:
        missing = [slack.get(k) for k in ("bot_token_secret", "signing_secret",
                                          "app_token_secret")
                   if slack.get(k) and slack.get(k) not in secrets_by_name]
        detail = "Slack bridge configured"
        if slack.get("app_token_secret") and not missing:
            detail = "Slack bridge configured; socket mode on — chat from Slack works"
        add("slack", "fail" if missing else "ok",
            ("Slack config references missing secret(s): " + ", ".join(missing))
            if missing else detail, "settings")

    # The chat lane: a running project with billing should have its CEO chat
    # session live (the supervisor restarts it within seconds; a persistent
    # absence means the runner is down or the session is boot-looping).
    chat_on = ((project.integrations or {}).get("chat") or {}).get("enabled", True)
    if project.status == "running" and project.billing_secret_id is not None and chat_on:
        if chat_session_live(session, project):
            add("chat", "ok", "CEO chat session is live — the boardroom answers "
                "in seconds", None)
        else:
            add("chat", "warn",
                "CEO chat session is not live — boardroom replies fall back to "
                "queued runs (minutes). It starts within seconds of the runner "
                "seeing the project; if this persists, check the runner's logs.",
                "runs")

    # Liveness. Every other check on this page asks "is this project set up
    # correctly?"; this one asks "is it working right now?" — the question that
    # had no answer anywhere in the product. A company that has spent its daily
    # runs looks exactly like one with nothing to do on every screen we ship,
    # which is how a hard stop reads as a healthy quiet.
    #
    # Only the two gates that mean *stopped* warn here. Hitting the concurrency
    # cap or finding every agent busy means the company is working, which is not
    # a finding.
    if project.status == "running":
        live_cons = effective(session, project.id)
        act = activity_state(session, project, live_cons)
        waiting = act["waiting"]
        held_up = (f"{waiting['ready_tasks']} task(s) and {waiting['notices']} "
                   f"notice(s) are waiting behind it")
        if act["blocked_by"] == "daily_runs":
            daily = act["daily_runs"]
            add("activity", "warn",
                f"out of runs for today — {daily['used']} of {daily['cap']} used, "
                f"and nothing will start until {daily['resets_at'][11:16]} UTC. "
                f"{held_up}. Raise 'Runs per day, whole project' if this is too "
                "tight, or leave it and the work resumes on its own.",
                "settings")
        elif act["blocked_by"] == "pacing":
            # Distinct from the daily ceiling on purpose: this one clears
            # within the hour on its own, and telling the two apart is the
            # difference between "wait" and "raise a limit".
            pace = act["pacing"]
            frees = pace["frees_at"]
            when = (f"the next run may start at {frees[11:16]} UTC"
                    if frees else "runs free up as the trailing hour advances")
            add("activity", "warn",
                f"pacing itself — {pace['used']} of {pace['cap']} runs used in the last "
                f"hour, so {when}. {held_up}. This is the brake that stops a whole day's "
                "runs going before lunch; it clears on its own and usually needs nothing "
                "from you.",
                "settings")
        elif act["blocked_by"] == "wake_ceiling":
            # Name the moment. "As the hour rolls forward" is unfalsifiable, so
            # a throttle that clears on its own read as a permanent fault.
            frees = act["wake_ceiling"]["frees_at"]
            when = (f"the next agent is free to wake at {frees[11:16]} UTC"
                    if frees else "slots free as the trailing hour advances")
            add("activity", "warn",
                "every agent with work to do has hit its hourly wake ceiling "
                f"({live_cons.wakes_per_agent_per_hour}/hour) — {when}. {held_up}. "
                "This clears itself; raise 'Wakes per agent per hour' only if the "
                "cap is tighter than you meant it to be.",
                "settings")

    # Connectors: controlled GitHub accounts registered but webhooks unprovisioned.
    if any(str(d).startswith("github:") for d in (project.controlled_domains or [])) \
            and not integrations.get("github_webhook_secret"):
        add("connectors", "warn",
            "controlled GitHub accounts are registered but webhook secrets are not "
            "provisioned — GitHub events cannot become attested evidence yet",
            "settings")

    return {
        "ok": not any(c["status"] == "fail" for c in checks),
        "all_ok": all(c["status"] == "ok" for c in checks) if checks else True,
        "checks": checks,
    }


@router.get("/projects/{project_id}/org")
def org(project_id: str, session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    teams = []
    for team in session.execute(select(Team).where(Team.project_id == project.id)).scalars():
        members = policy.team_agents(session, team.id)
        lead = policy.effective_lead(session, team)
        role_version = session.get(RoleVersion, team.role_version_id)
        role = session.get(Role, role_version.role_id) if role_version else None
        teams.append(
            {
                "id": team.id,
                "name": team.name,
                "role_slug": role.slug if role else None,
                "lead_agent_id": lead.id if lead else None,
                "agents": [
                    {"id": a.id, "name": a.name, "status": a.status} for a in members
                ],
            }
        )
    ceo = policy.ceo_of(session, project.id)
    return {"ceo": {"id": ceo.id, "name": ceo.name, "status": ceo.status} if ceo else None,
            "teams": teams}


def _name_maps(session: Session, project_id: str) -> tuple[dict, dict]:
    """{agent_id: name}, {team_id: name} — the chairman reads names, not ids."""
    agents = {a.id: a.name for a in session.execute(
        select(Agent).where(Agent.project_id == project_id)).scalars()}
    teams = {t.id: t.name for t in session.execute(
        select(Team).where(Team.project_id == project_id)).scalars()}
    return agents, teams


@router.get("/projects/{project_id}/board")
def board(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _project(session, project_id)
    agents, teams = _name_maps(session, project.id)
    rows = session.execute(
        select(Task).where(Task.project_id == project.id).order_by(Task.created_at.desc()).limit(200)
    ).scalars()
    return [
        {
            "id": t.id, "title": t.title, "status": t.status, "team_id": t.team_id,
            "team_name": teams.get(t.team_id),
            "parent_task_id": t.parent_task_id, "assignee": t.assignee_agent_id,
            "claimed_by": t.claimed_by_agent_id,
            "holder_name": agents.get(t.claimed_by_agent_id or t.assignee_agent_id),
            "external": t.produces_external_evidence,
            "success_test": t.success_test, "evidence": t.completion_evidence,
            "check_output": t.check_output, "blocked_reason": t.blocked_reason,
            "hypothesis_id": t.hypothesis_id, "audit_status": t.audit_status,
        }
        for t in rows
    ]


@router.get("/projects/{project_id}/channels")
def channels(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _project(session, project_id)
    _, teams = _name_maps(session, project.id)
    return [
        {"id": c.id, "kind": c.kind, "team_id": c.team_id,
         "team_name": teams.get(c.team_id)}
        for c in session.execute(
            select(Channel).where(Channel.project_id == project.id)
        ).scalars()
    ]


@router.get("/channels/{channel_id}/typing")
def channel_typing(channel_id: str) -> dict:
    """Ephemeral presence for the chat UI — true while the CEO's chat session
    is composing a reply. In-memory, self-expiring, never stored."""
    return {"typing": typing_state.is_typing(channel_id)}


@router.get("/channels/{channel_id}/messages")
def channel_messages(channel_id: str, limit: int = 100, session: Session = Depends(db)) -> list[dict]:
    channel = session.get(Channel, channel_id)
    agents = ({} if channel is None
              else _name_maps(session, channel.project_id)[0])
    rows = list(
        session.execute(
            select(Message)
            .where(Message.channel_id == channel_id)
            .order_by(Message.created_at.desc())
            .limit(min(limit, 500))
        ).scalars()
    )[::-1]
    return [
        {
            "id": m.id, "from_agent": m.sender_agent_id, "from_user": m.sender_user_id,
            "from_name": ("chairman" if m.sender_user_id
                          else agents.get(m.sender_agent_id)),
            "thread_id": m.thread_id, "frozen": m.frozen_at is not None,
            "body": m.body, "at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.get("/projects/{project_id}/runs")
def runs(project_id: str, limit: int = 50, session: Session = Depends(db)) -> list[dict]:
    rows = list(
        session.execute(
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(Run.created_at.desc())
            .limit(min(limit, 200))
        ).scalars()
    )
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    agents = {
        a.id: a
        for a in session.execute(select(Agent).where(Agent.id.in_(agent_ids))).scalars()
    } if agent_ids else {}
    team_ids = {a.team_id for a in agents.values() if a.team_id}
    team_names = {
        t.id: t.name
        for t in session.execute(select(Team).where(Team.id.in_(team_ids))).scalars()
    } if team_ids else {}

    def who(r: Run) -> dict:
        agent = agents.get(r.agent_id) if r.agent_id else None
        return {
            "agent_name": agent.name if agent else None,
            "team_name": team_names.get(agent.team_id) if agent and agent.team_id else None,
        }

    return [
        {
            "id": r.id, "agent_id": r.agent_id, **who(r), "kind": r.kind, "status": r.status,
            "trigger": r.trigger, "attempt": r.attempt,
            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
            "cache_creation": r.cache_creation_tokens, "cache_read": r.cache_read_tokens,
            "model": r.model, "effort": r.effort, "cost_usd": r.cost_usd,
            "refusals": r.refusal_count, "last_refusal": r.last_refusal,
            "summary": r.summary,
            "has_trace": r.trace_path is not None,
            "has_context": r.context_path is not None,
            # A resident chat session is meant to run for hours, so "running"
            # alone cannot tell alive from hung. The heartbeat can.
            "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]


def _artifact_text(path_or_uri: str, missing: str) -> str:
    """Rows written since the artifact store hold a store uri (file:// or
    s3://); older rows hold a plain filesystem path. Serve both."""
    try:
        if artifacts.is_store_uri(path_or_uri):
            data = artifacts.read_uri(path_or_uri)
        else:  # pre-store row: a plain path on this host
            path = Path(path_or_uri)
            if not path.exists():
                raise FileNotFoundError(path_or_uri)
            data = path.read_bytes()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=missing) from None
    return data.decode(errors="replace")


@router.get("/runs/{run_id}/trace")
def run_trace(run_id: str, session: Session = Depends(db)) -> PlainTextResponse:
    """The run's raw JSONL trace, as stashed in the artifact store at finalize."""
    run = session.get(Run, run_id)
    if run is None or not run.trace_path:
        raise HTTPException(status_code=404, detail="no trace recorded for this run")
    text = _artifact_text(run.trace_path, missing="trace not present in the artifact store")
    return PlainTextResponse(text[-500_000:])


@router.get("/runs/{run_id}/context")
def run_context(run_id: str, session: Session = Depends(db)) -> PlainTextResponse:
    """What the run actually sent to the model — the staged system prompt and
    context pack, stashed in the artifact store as one JSON document."""
    run = session.get(Run, run_id)
    if run is None or not run.context_path:
        raise HTTPException(status_code=404, detail="no context recorded for this run")
    text = _artifact_text(
        run.context_path, missing="context not present in the artifact store"
    )
    return PlainTextResponse(text, media_type="application/json")


@router.get("/evidence/{evidence_id}/snapshot")
def evidence_snapshot(evidence_id: str, session: Session = Depends(db)) -> PlainTextResponse:
    """The verifier's snapshot of the evidence source, fetched and stored at
    verification time. The sha256 on the row is the authoritative record; this
    is the human-readable copy for spot audits."""
    evidence_row = session.get(Evidence, evidence_id)
    if evidence_row is None or not evidence_row.snapshot_path:
        raise HTTPException(status_code=404, detail="no snapshot recorded for this evidence")
    text = _artifact_text(
        evidence_row.snapshot_path, missing="snapshot not present in the artifact store"
    )
    return PlainTextResponse(text)


@router.get("/projects/{project_id}/ledger")
def ledger(project_id: str, limit: int = 100, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.project_id == project_id)
        .order_by(LedgerEntry.at.desc())
        .limit(min(limit, 500))
    ).scalars()
    return [
        {"id": e.id, "kind": e.kind, "tokens_in": e.tokens_in, "tokens_out": e.tokens_out,
         "cache_creation": e.cache_creation_tokens, "cache_read": e.cache_read_tokens,
         "usd": e.usd, "run_id": e.run_id, "note": e.note, "at": e.at.isoformat()}
        for e in rows
    ]


@router.get("/projects/{project_id}/spend")
def spend(project_id: str, group_by: str = "model", days: int = 30,
          session: Session = Depends(db)) -> dict:
    """Spend rollup over recent runs. Dollars sum non-null cost_usd, so a
    subscription project contributes tokens only — clients switch display on
    billing_mode exactly as the budget meter does."""
    if group_by not in ("model", "team", "agent", "kind"):
        raise HTTPException(status_code=400, detail="group_by must be model|team|agent|kind")
    since = utcnow() - timedelta(days=max(1, min(days, 365)))
    rows = list(
        session.execute(
            select(Run).where(Run.project_id == project_id, Run.created_at >= since)
        ).scalars()
    )
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    agents = {
        a.id: a
        for a in session.execute(select(Agent).where(Agent.id.in_(agent_ids))).scalars()
    } if agent_ids else {}
    team_ids = {a.team_id for a in agents.values() if a.team_id}
    team_names = {
        t.id: t.name
        for t in session.execute(select(Team).where(Team.id.in_(team_ids))).scalars()
    } if team_ids else {}

    def key_label(r: Run) -> tuple[str, str]:
        if group_by == "model":
            name = r.model or "unknown"
            return name, name
        if group_by == "kind":
            return r.kind, r.kind
        agent = agents.get(r.agent_id) if r.agent_id else None
        if agent is None:
            return "unknown", "unknown"
        if group_by == "agent":
            return agent.id, agent.name
        # team: a teamless agent (the CEO) rolls up under its own name.
        if agent.team_id is None:
            return agent.id, agent.name
        return agent.team_id, team_names.get(agent.team_id, "unknown")

    buckets: dict[str, dict] = {}
    for r in rows:
        key, label = key_label(r)
        b = buckets.setdefault(key, {"key": key, "label": label, "runs": 0,
                                     "tokens_in": 0, "tokens_out": 0,
                                     "cache_creation": 0, "cache_read": 0, "usd": 0.0})
        b["runs"] += 1
        b["tokens_in"] += r.tokens_in
        b["tokens_out"] += r.tokens_out
        b["cache_creation"] += r.cache_creation_tokens
        b["cache_read"] += r.cache_read_tokens
        b["usd"] += r.cost_usd or 0.0
    out = sorted(
        buckets.values(),
        key=lambda b: (-b["usd"], -(b["tokens_in"] + b["tokens_out"]
                                    + b["cache_creation"] + b["cache_read"])),
    )
    return {"group_by": group_by, "days": days, "rows": out}


@router.get("/projects/{project_id}/evidence")
def evidence(project_id: str, limit: int = 100, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Evidence)
        .where(Evidence.project_id == project_id)
        .order_by(Evidence.created_at.desc())
        .limit(min(limit, 500))
    ).scalars()
    return [
        {
            "id": e.id, "kind": e.kind, "provenance": e.provenance,
            "verification": e.verification, "description": e.description,
            "source_url": e.source_url, "verifier_notes": e.verifier_notes,
            # The task this proves. The dashboard walks evidence -> task ->
            # hypothesis to answer "has this bet been proven?" — a question that
            # otherwise needs the reader to hold the join in their head.
            "task_id": e.task_id,
            "at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.get("/projects/{project_id}/hypotheses")
def hypotheses(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Hypothesis).where(Hypothesis.project_id == project_id)
    ).scalars()
    return [
        {"id": h.id, "statement": h.statement, "kill_condition": h.kill_condition,
         "kill_date": h.kill_date.isoformat(), "status": h.status,
         "extended_once": h.extended_once}
        for h in rows
    ]


@router.get("/projects/{project_id}/approvals")
def approvals(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Approval)
        .where(Approval.project_id == project_id)
        .order_by(Approval.created_at.desc())
        .limit(100)
    ).scalars()
    return [
        {"id": a.id, "kind": a.kind, "title": a.title, "body": a.body,
         "amount_usd": a.amount_usd, "status": a.status,
         "requested_by": a.requested_by_agent_id,
         "expires_at": a.expires_at.isoformat(),
         "decided_at": a.decided_at.isoformat() if a.decided_at else None,
         "decision_note": a.decision_note}
        for a in rows
    ]


@router.get("/projects/{project_id}/memories")
def memories(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Memory)
        .where(Memory.project_id == project_id, Memory.superseded_at.is_(None))
        .order_by(Memory.created_at.desc())
    ).scalars()
    return [
        {"id": m.id, "scope": m.scope, "team_id": m.team_id, "kind": m.kind,
         "subject": m.subject, "body": m.body, "at": m.created_at.isoformat()}
        for m in rows
    ]


# ── files: the workspace (work in progress) and the artifact store ─────────────

MAX_FILE_LISTING = 2000
MAX_SERVED_BYTES = 20 * 1024 * 1024
# Platform plumbing, not agent work: staging (tokens, prompts, traces) and git
# internals never show in the chairman's Files view.
_SKIPPED_DIRS = {".git", ".yaaf"}


def _workspace_root(project: Project) -> Path:
    from yaaf.core.config import settings

    return Path(settings().workspaces_root) / project.slug


@router.get("/projects/{project_id}/files")
def project_files(project_id: str, session: Session = Depends(db)) -> dict:
    """Everything the agents have written: the shared workspace (work in
    progress — every sandbox mounts it) and the artifact store (finalized
    deliverables, keyed projects/<project>/<run>/<name>)."""
    project = _project(session, project_id)
    workspace: list[dict] = []
    root = _workspace_root(project)
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part in _SKIPPED_DIRS for part in rel.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            workspace.append({
                "path": str(rel), "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            })
            if len(workspace) >= MAX_FILE_LISTING:
                break
    rows = []
    for entry in artifacts.store().list_keys(f"projects/{project.id}/"):
        parts = entry["key"].split("/")
        rows.append({**entry,
                     "run_id": parts[2] if len(parts) > 3 else None,
                     "name": parts[-1]})
    return {"workspace": workspace, "artifacts": rows}


@router.get("/projects/{project_id}/files/content")
def project_file_content(
    project_id: str, path: str = "", artifact: str = "",
    session: Session = Depends(db),
) -> Response:
    """One file's bytes, ALWAYS as a download (octet-stream) — agent-written
    HTML must never render on this origin, where the chairman's admin token
    lives. Untrusted content + that token + a browser is the lethal
    combination; the disposition header is the leg removed."""
    project = _project(session, project_id)
    if bool(path) == bool(artifact):
        raise HTTPException(status_code=422, detail="pass exactly one of ?path= or ?artifact=")
    if artifact:
        # Keys are validated against THIS project's prefix — the store holds
        # every project's deliverables. ".." would satisfy the prefix check
        # and still resolve into another project's keys; refuse it outright.
        if (not artifact.startswith(f"projects/{project.id}/")
                or ".." in artifact.split("/")):
            raise HTTPException(status_code=404, detail="no such artifact")
        try:
            data = artifacts.store().get_bytes(artifact)
        except (FileNotFoundError, ValueError):
            raise HTTPException(status_code=404, detail="no such artifact") from None
        name = artifact.rsplit("/", 1)[-1]
    else:
        root = _workspace_root(project).resolve()
        target = (root / path).resolve()
        if (not target.is_relative_to(root)
                or any(part in _SKIPPED_DIRS for part in Path(path).parts)):
            raise HTTPException(status_code=404, detail="no such file")
        if not target.is_file():
            raise HTTPException(status_code=404, detail="no such file")
        if target.stat().st_size > MAX_SERVED_BYTES:
            raise HTTPException(status_code=413, detail="file exceeds the 20MB serving limit")
        data = target.read_bytes()
        name = target.name
    safe_name = name.replace('"', "")
    return Response(
        content=data, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )


@router.get("/projects/{project_id}/suggestions")
def suggestions(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Suggestion)
        .where(Suggestion.project_id == project_id)
        .order_by(Suggestion.created_at.desc())
        .limit(100)
    ).scalars()
    return [{"id": s.id, "by": s.by_agent_id, "text": s.text,
             "consumed": s.consumed_at is not None} for s in rows]


@router.get("/projects/{project_id}/spot_audit")
def spot_audit(project_id: str, n: int = 5, session: Session = Depends(db)) -> dict:
    """A random sample of recent evidence and completed tasks for human audit.
    LLM reviewers approve nearly everything put in front of them; the chairman
    is the layer that does not."""
    project = _project(session, project_id)
    recent_evidence = list(
        session.execute(
            select(Evidence)
            .where(Evidence.project_id == project.id)
            .order_by(Evidence.created_at.desc())
            .limit(50)
        ).scalars()
    )
    recent_done = list(
        session.execute(
            select(Task)
            .where(Task.project_id == project.id, Task.status == TaskStatus.DONE)
            .order_by(Task.updated_at.desc())
            .limit(50)
        ).scalars()
    )
    sample_evidence = random.sample(recent_evidence, min(n, len(recent_evidence)))
    sample_tasks = random.sample(recent_done, min(n, len(recent_done)))
    return {
        "evidence": [
            {"id": e.id, "kind": e.kind, "provenance": e.provenance,
             "verification": e.verification, "description": e.description,
             "source_url": e.source_url}
            for e in sample_evidence
        ],
        "completed_tasks": [
            {"id": t.id, "title": t.title, "success_test": t.success_test,
             "evidence": t.completion_evidence, "check_output": t.check_output,
             "audit_status": t.audit_status, "audit_note": t.audit_note}
            for t in sample_tasks
        ],
        "note": "POST /admin/evidence/{id}/audit or /admin/tasks/{id}/audit "
                "with ok=false to fail a bad sample.",
    }


@router.get("/agents/{agent_id}")
def agent_detail(agent_id: str, session: Session = Depends(db)) -> dict:
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    recent = session.execute(
        select(Run).where(Run.agent_id == agent.id).order_by(Run.created_at.desc()).limit(20)
    ).scalars()
    return {
        "id": agent.id, "name": agent.name, "kind": agent.kind, "team_id": agent.team_id,
        "status": agent.status,
        "recent_runs": [
            {"id": r.id, "status": r.status, "trigger": r.trigger,
             "tokens_out": r.tokens_out, "summary": r.summary}
            for r in recent
        ],
    }
