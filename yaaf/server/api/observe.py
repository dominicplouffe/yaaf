"""Read-only observation API — the chairman's omniscience.

The chairman *speaks* only in the boardroom, but *reads* everything: every
channel, task, run, ledger row, and a random spot-audit sample of evidence and
completions. In M1 this backs the CLI; the SPA consumes the same endpoints."""

from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
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
    Hypothesis,
    LedgerEntry,
    Memory,
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
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy, typing_state
from yaaf.server.deps import admin_auth, db
from yaaf.server.dispatcher import chat_session_live

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


@router.get("/projects")
def projects(session: Session = Depends(db)) -> list[dict]:
    return [
        {"id": p.id, "slug": p.slug, "name": p.name, "status": p.status, "goal": p.goal}
        for p in session.execute(select(Project).order_by(Project.created_at)).scalars()
    ]


@router.get("/projects/{project_id}")
def dashboard(project_id: str, session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    cons = effective(session, project.id)
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usd, tokens_in, tokens_out = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.usd), 0.0),
            func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
            func.coalesce(func.sum(LedgerEntry.tokens_out), 0),
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
        "name": project.name,
        "status": project.status,
        "status_reason": project.status_reason,
        "goal": project.goal,
        "billing_mode": project.billing_mode,
        "month_to_date": {"usd": float(usd), "tokens_in": int(tokens_in),
                          "tokens_out": int(tokens_out)},
        "caps": {"monthly_usd_cap": cons.monthly_usd_cap,
                 "monthly_token_cap": cons.monthly_token_cap},
        "open_tasks": len(open_tasks),
        "queue_ceiling": cons.queue_ceiling,
        "external_evidence_in_window": evidence_count,
        "drought_active": policy.drought_active(session, project.id, cons),
        "runs": {"running": running, "queued": queued},
        "chat_live": chat_session_live(session, project),
        "pending_approvals": pending_approvals,
        "ceo_judgment": policy.ceo_judgment(session, project.id),
        "constitution": cons.model_dump(),
        "config": project.config or {},
        "github_repo": project.github_repo,
        "billing_secret_id": project.billing_secret_id,
        "billing_secret_name": _secret_name(session, project.billing_secret_id),
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
    config = project.config or {}
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
    for source in config.get("mcp_servers", []):
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
    slack = config.get("slack") or {}
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
    chat_on = ((project.config or {}).get("chat") or {}).get("enabled", True)
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

    # Connectors: controlled GitHub accounts registered but webhooks unprovisioned.
    if any(str(d).startswith("github:") for d in (project.controlled_domains or [])) \
            and not config.get("github_webhook_secret"):
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


@router.get("/projects/{project_id}/board")
def board(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _project(session, project_id)
    rows = session.execute(
        select(Task).where(Task.project_id == project.id).order_by(Task.created_at.desc()).limit(200)
    ).scalars()
    return [
        {
            "id": t.id, "title": t.title, "status": t.status, "team_id": t.team_id,
            "parent_task_id": t.parent_task_id, "assignee": t.assignee_agent_id,
            "claimed_by": t.claimed_by_agent_id,
            "external": t.produces_external_evidence,
            "success_test": t.success_test, "evidence": t.completion_evidence,
            "check_output": t.check_output, "blocked_reason": t.blocked_reason,
        }
        for t in rows
    ]


@router.get("/projects/{project_id}/channels")
def channels(project_id: str, session: Session = Depends(db)) -> list[dict]:
    project = _project(session, project_id)
    return [
        {"id": c.id, "kind": c.kind, "team_id": c.team_id}
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
            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
            "refusals": r.refusal_count, "summary": r.summary,
            "has_trace": r.trace_path is not None,
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
         "usd": e.usd, "note": e.note, "at": e.at.isoformat()}
        for e in rows
    ]


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
         "expires_at": a.expires_at.isoformat()}
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
         "subject": m.subject, "body": m.body}
        for m in rows
    ]


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
