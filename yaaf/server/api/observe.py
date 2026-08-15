"""Read-only observation API — the chairman's omniscience.

The chairman *speaks* only in the boardroom, but *reads* everything: every
channel, task, run, ledger row, and a random spot-audit sample of evidence and
completions. In M1 this backs the CLI; the SPA consumes the same endpoints."""

from __future__ import annotations

import random
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
    Run,
    RunQueue,
    Suggestion,
    Task,
    TaskStatus,
    Team,
    utcnow,
)
from yaaf.server import policy
from yaaf.server.deps import admin_auth, db

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
    running = session.execute(
        select(func.count(Run.id)).where(
            Run.project_id == project.id, Run.status == "running"
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
        "pending_approvals": pending_approvals,
        "constitution": cons.model_dump(),
        "config": project.config or {},
        "billing_secret_id": project.billing_secret_id,
        "billing_secret_name": _secret_name(session, project.billing_secret_id),
    }


@router.get("/projects/{project_id}/org")
def org(project_id: str, session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    teams = []
    for team in session.execute(select(Team).where(Team.project_id == project.id)).scalars():
        members = policy.team_agents(session, team.id)
        lead = policy.effective_lead(session, team)
        teams.append(
            {
                "id": team.id,
                "name": team.name,
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
    rows = session.execute(
        select(Run)
        .where(Run.project_id == project_id)
        .order_by(Run.created_at.desc())
        .limit(min(limit, 200))
    ).scalars()
    return [
        {
            "id": r.id, "agent_id": r.agent_id, "kind": r.kind, "status": r.status,
            "trigger": r.trigger, "attempt": r.attempt,
            "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cost_usd": r.cost_usd,
            "refusals": r.refusal_count, "summary": r.summary,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        }
        for r in rows
    ]


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
             "evidence": t.completion_evidence, "check_output": t.check_output}
            for t in sample_tasks
        ],
        "note": "POST /admin/evidence/{id}/audit with ok=false to fail a bad sample.",
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
