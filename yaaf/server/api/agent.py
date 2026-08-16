"""The agent-facing API — lana's plan.py/memory.py/slack.py, hardened.

Every endpoint authenticates a per-run token and enforces the constitution
through :mod:`yaaf.server.policy`. An agent cannot be talked out of rules it
never held: they live here, server-side, not in its prompt.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import effective
from yaaf.core.db import notify
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    Channel,
    ChannelRead,
    Decision,
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    Memory,
    Message,
    RunKind,
    RunQueue,
    SideEffect,
    SideEffectStatus,
    SuccessTestKind,
    Suggestion,
    Task,
    TaskStatus,
    VerificationStatus,
    utcnow,
)
from yaaf.server import policy, typing_state
from yaaf.server.deps import RunContext, db, run_auth
from yaaf.server.policy import Refusal

router = APIRouter()

WAKE_CHANNEL = "yaaf_wake"


def _task_view(task: Task) -> dict:
    return {
        "id": task.id,
        "team_id": task.team_id,
        "parent_task_id": task.parent_task_id,
        "assignee_agent_id": task.assignee_agent_id,
        "title": task.title,
        "goal": task.goal,
        "success_test": task.success_test,
        "produces_external_evidence": task.produces_external_evidence,
        "status": task.status,
        "blocked_reason": task.blocked_reason,
        "claimed_by_agent_id": task.claimed_by_agent_id,
        "hypothesis_id": task.hypothesis_id,
        "not_before": task.not_before.isoformat() if task.not_before else None,
    }


@router.get("/whoami")
def whoami(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> dict:
    team = None
    if ctx.agent.team_id:
        team = {"id": ctx.agent.team_id, "is_lead": policy.is_lead(session, ctx.agent)}
    return {
        "agent_id": ctx.agent.id,
        "name": ctx.agent.name,
        "kind": ctx.agent.kind,
        "project_id": ctx.project.id,
        "project_status": ctx.project.status,
        "team": team,
        "run_id": ctx.run.id,
    }


@router.get("/context")
def context_pack(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> dict:
    from yaaf.server.contextpack import build_context_pack

    return build_context_pack(session, ctx.run, ctx.agent, ctx.project)


# ── board & tasks ───────────────────────────────────────────────────────────────


@router.get("/board")
def board(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> dict:
    if ctx.agent.kind == "ceo":
        tasks = policy.open_top_level_tasks(session, ctx.project.id)
        done_recent = session.execute(
            select(Task)
            .where(
                Task.project_id == ctx.project.id,
                Task.parent_task_id.is_(None),
                Task.status.in_([TaskStatus.DONE, TaskStatus.KILLED]),
            )
            .order_by(Task.updated_at.desc())
            .limit(10)
        ).scalars()
    else:
        tasks = session.execute(
            select(Task).where(
                Task.team_id == ctx.agent.team_id,
                Task.status.in_(policy.OPEN_TASK_STATUSES),
            )
        ).scalars()
        done_recent = session.execute(
            select(Task)
            .where(
                Task.team_id == ctx.agent.team_id,
                Task.status.in_([TaskStatus.DONE, TaskStatus.KILLED]),
            )
            .order_by(Task.updated_at.desc())
            .limit(10)
        ).scalars()
    return {
        "open": [_task_view(t) for t in tasks],
        "recently_closed": [_task_view(t) for t in done_recent],
    }


class TaskCreate(BaseModel):
    team_id: str
    title: str = Field(min_length=4, max_length=300)
    goal: str = Field(min_length=10, max_length=10_000)
    success_test: dict
    produces_external_evidence: bool = False
    parent_task_id: str | None = None
    assignee_agent_id: str | None = None
    hypothesis_id: str | None = None
    not_before: datetime | None = None


@router.post("/tasks")
def create_task(
    body: TaskCreate, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    policy.check_task_create(
        session,
        cons,
        ctx.project,
        ctx.agent,
        team_id=body.team_id,
        parent_task_id=body.parent_task_id,
        success_test=body.success_test,
        produces_external_evidence=body.produces_external_evidence,
        hypothesis_id=body.hypothesis_id,
    )
    if body.parent_task_id is not None:
        if body.assignee_agent_id is None:
            raise Refusal("A subtask is an assignment: set assignee_agent_id.")
        assignees = {a.id for a in policy.team_agents(session, body.team_id)}
        if body.assignee_agent_id not in assignees:
            raise Refusal("Assignee must be a member of the team.", status_code=422)
    elif body.assignee_agent_id is not None:
        raise Refusal(
            "Team tasks are not assigned to individuals — the team's lead claims them and "
            "decides who does what (including decomposing into assignments). Drop "
            "assignee_agent_id."
        )
    task = Task(
        id=ids.new_id(ids.TASK),
        project_id=ctx.project.id,
        team_id=body.team_id,
        parent_task_id=body.parent_task_id,
        assignee_agent_id=body.assignee_agent_id,
        created_by_agent_id=ctx.agent.id,
        hypothesis_id=body.hypothesis_id,
        title=body.title,
        goal=body.goal,
        success_test=body.success_test,
        produces_external_evidence=body.produces_external_evidence,
        not_before=body.not_before,
    )
    session.add(task)
    policy.record_task_event(session, task, TaskStatus.READY, f"agent:{ctx.agent.id}",
                             from_status=None)
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return _task_view(task)


@router.post("/tasks/{task_id}/claim")
def claim_task(
    task_id: str, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    task = session.get(Task, task_id)
    if task is None or task.project_id != ctx.project.id:
        raise Refusal("Unknown task.", status_code=404)
    policy.check_task_claim(session, ctx.project, ctx.agent, task)
    from_status = task.status
    task.status = TaskStatus.CLAIMED
    task.claimed_by_agent_id = ctx.agent.id
    task.claimed_at = utcnow()
    task.claim_run_id = ctx.run.id
    policy.record_task_event(session, task, TaskStatus.CLAIMED, f"agent:{ctx.agent.id}",
                             from_status=from_status)
    return _task_view(task)


class TaskComplete(BaseModel):
    evidence: str


@router.post("/tasks/{task_id}/complete")
def complete_task(
    task_id: str,
    body: TaskComplete,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    task = session.get(Task, task_id)
    if task is None or task.project_id != ctx.project.id:
        raise Refusal("Unknown task.", status_code=404)
    policy.check_task_complete(session, ctx.project, ctx.agent, task, body.evidence)
    from_status = task.status
    task.completion_evidence = body.evidence.strip()

    if task.success_test.get("type") == SuccessTestKind.COMMAND:
        # The claimant never self-reports a command result: the platform runs it.
        task.status = TaskStatus.CHECKING
        policy.record_task_event(session, task, TaskStatus.CHECKING, f"agent:{ctx.agent.id}",
                                 from_status=from_status)
        session.add(
            RunQueue(
                id=ids.new_id(ids.RUN),
                project_id=ctx.project.id,
                agent_id=None,
                kind=RunKind.SUCCESS_CHECK,
                trigger={"type": "success_check", "task_id": task.id},
                task_id=task.id,
            )
        )
        outcome = "checking"
    else:
        task.status = TaskStatus.DONE
        policy.record_task_event(session, task, TaskStatus.DONE, f"agent:{ctx.agent.id}",
                                 from_status=from_status)
        outcome = "done"
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {"task_id": task.id, "status": outcome}


class TaskBlock(BaseModel):
    reason: str = Field(min_length=10)


@router.post("/tasks/{task_id}/block")
def block_task(
    task_id: str,
    body: TaskBlock,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    task = session.get(Task, task_id)
    if task is None or task.project_id != ctx.project.id:
        raise Refusal("Unknown task.", status_code=404)
    if task.status != TaskStatus.CLAIMED or task.claimed_by_agent_id != ctx.agent.id:
        raise Refusal("Only the claimant blocks a task, and only a claimed one.", 409)
    from_status = task.status
    task.status = TaskStatus.BLOCKED
    task.blocked_reason = body.reason
    task.claimed_by_agent_id = None
    policy.record_task_event(session, task, TaskStatus.BLOCKED, f"agent:{ctx.agent.id}",
                             note=body.reason, from_status=from_status)
    # A block is a planning event: whoever plans this task's level is woken to
    # re-plan now, not at the next heartbeat.
    from yaaf.core.models import Notice, NoticeKind

    if task.parent_task_id is None:
        planner = policy.ceo_of(session, ctx.project.id)
    else:
        parent = session.get(Task, task.parent_task_id)
        planner_id = parent.claimed_by_agent_id if parent else None
        planner = session.get(type(ctx.agent), planner_id) if planner_id else None
    if planner is not None and planner.id != ctx.agent.id:
        session.add(
            Notice(
                id=ids.new_id(ids.NOTICE),
                project_id=ctx.project.id,
                agent_id=planner.id,
                kind=NoticeKind.TASK_BLOCKED,
                body=(
                    f"Task '{task.title}' ({task.id}) was BLOCKED by {ctx.agent.name}: "
                    f"{body.reason[:1500]}\nRe-plan it: change it, kill it, or clear the "
                    "blocker. A blocked task is a question addressed to you."
                ),
            )
        )
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return _task_view(task)


class TaskKill(BaseModel):
    reason: str = Field(min_length=10)


@router.post("/tasks/{task_id}/kill")
def kill_task(
    task_id: str,
    body: TaskKill,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    task = session.get(Task, task_id)
    if task is None or task.project_id != ctx.project.id:
        raise Refusal("Unknown task.", status_code=404)
    policy.check_task_kill(session, ctx.agent, task)
    from_status = task.status
    task.status = TaskStatus.KILLED
    task.blocked_reason = body.reason
    policy.record_task_event(session, task, TaskStatus.KILLED, f"agent:{ctx.agent.id}",
                             note=body.reason, from_status=from_status)
    return _task_view(task)


class SuggestionIn(BaseModel):
    text: str = Field(min_length=10)


@router.post("/suggestions")
def suggest(
    body: SuggestionIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    row = Suggestion(
        id=ids.new_id(ids.SUGGESTION),
        project_id=ctx.project.id,
        by_agent_id=ctx.agent.id,
        text=body.text,
    )
    session.add(row)
    return {"id": row.id, "note": "Recorded for the CEO's next planning cycle. No work created."}


# ── messages ────────────────────────────────────────────────────────────────────


@router.get("/channels")
def channels(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> list[dict]:
    member_ids = policy.agent_channel_ids(session, ctx.agent)
    out = []
    for cid in member_ids:
        channel = session.get(Channel, cid)
        cursor = session.execute(
            select(ChannelRead).where(
                ChannelRead.agent_id == ctx.agent.id, ChannelRead.channel_id == cid
            )
        ).scalar_one_or_none()
        last_read = cursor.last_read_at if cursor else datetime.fromtimestamp(0, tz=utcnow().tzinfo)
        unread = session.execute(
            select(Message.id).where(
                Message.channel_id == cid,
                Message.created_at > last_read,
                or_(Message.sender_agent_id.is_(None), Message.sender_agent_id != ctx.agent.id),
            )
        ).all()
        out.append({"id": cid, "kind": channel.kind, "team_id": channel.team_id,
                    "unread": len(unread)})
    return out


@router.get("/messages")
def read_messages(
    channel_id: str,
    limit: int = 50,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> list[dict]:
    channel = session.get(Channel, channel_id)
    if channel is None or channel.id not in policy.agent_channel_ids(session, ctx.agent):
        raise Refusal("Not a member of that channel.", status_code=403)
    rows = list(
        session.execute(
            select(Message)
            .where(Message.channel_id == channel_id)
            .order_by(Message.created_at.desc())
            .limit(min(limit, 200))
        ).scalars()
    )[::-1]
    # Reading advances the cursor: stateless runs read once, up front.
    cursor = session.execute(
        select(ChannelRead).where(
            ChannelRead.agent_id == ctx.agent.id, ChannelRead.channel_id == channel_id
        )
    ).scalar_one_or_none()
    now = utcnow()
    if cursor is None:
        session.add(ChannelRead(agent_id=ctx.agent.id, channel_id=channel_id, last_read_at=now))
    else:
        cursor.last_read_at = now
    return [
        {
            "id": m.id,
            "from_agent": m.sender_agent_id,
            "from_user": m.sender_user_id,
            "thread_id": m.thread_id,
            "body": m.body,
            "at": m.created_at.isoformat(),
        }
        for m in rows
    ]


class MessagePost(BaseModel):
    channel_id: str
    body: str = Field(min_length=1, max_length=20_000)
    thread_id: str | None = None
    # Break-glass on the chairman's reporting cadence, named rather than
    # implied. Verified where verifiable, recorded either way.
    override: str | None = None


@router.post("/messages")
def post_message(
    body: MessagePost, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    channel = session.get(Channel, body.channel_id)
    if channel is None:
        raise Refusal("Unknown channel.", status_code=404)
    policy.check_agent_post(session, ctx.project, ctx.agent, channel)
    policy.check_thread_not_frozen(session, body.thread_id)
    policy.check_chairman_report(
        session, ctx.project, ctx.agent, channel, ctx.run, body.override)
    message = Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=ctx.project.id,
        channel_id=channel.id,
        sender_agent_id=ctx.agent.id,
        thread_id=body.thread_id,
        body=body.body,
    )
    session.add(message)
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {"id": message.id, "at": message.created_at.isoformat() if message.created_at else None}


# ── chat lane ───────────────────────────────────────────────────────────────────


class TypingIn(BaseModel):
    channel_id: str
    seconds: float = Field(default=15.0, ge=0, le=120)


@router.post("/typing")
def set_typing(
    body: TypingIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Presence, not state: the chat session flips this on while composing a
    reply so the boardroom can show "typing…". Expires by itself."""
    channel = session.get(Channel, body.channel_id)
    if channel is None or channel.id not in policy.agent_channel_ids(session, ctx.agent):
        raise Refusal("Not a member of that channel.", status_code=403)
    typing_state.set_typing(channel.id, body.seconds)
    return {"ok": True}


class ChatUsage(BaseModel):
    tokens_in: int = Field(ge=0)
    tokens_out: int = Field(ge=0)
    # Defaults are load-bearing: chat containers from an older image keep
    # posting 3-field payloads for days after a deploy and must still book.
    cache_creation_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


@router.post("/chat_usage")
def book_chat_usage(
    body: ChatUsage, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Per-turn metering for the resident chat session. Ordinary runs meter at
    finalize; a chat session may live for days, so each turn books immediately —
    the monthly budget gates see chat spend the moment it happens."""
    if ctx.run.kind != RunKind.CHAT_SESSION:
        raise Refusal("Only a chat session books usage this way; runs are metered "
                      "at finalize.", status_code=403)
    ctx.run.tokens_in += body.tokens_in
    ctx.run.tokens_out += body.tokens_out
    ctx.run.cache_creation_tokens += body.cache_creation_tokens
    ctx.run.cache_read_tokens += body.cache_read_tokens
    # Tokens always; dollars only when dollars move (see runner.finalize).
    subscription = ctx.project.billing_mode == "subscription"
    usd = None if subscription else body.cost_usd
    if usd is not None:
        ctx.run.cost_usd = (ctx.run.cost_usd or 0.0) + usd
    if (body.tokens_in or body.tokens_out or body.cache_creation_tokens
            or body.cache_read_tokens or usd):
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER),
            project_id=ctx.project.id,
            kind="model_usage",
            tokens_in=body.tokens_in,
            tokens_out=body.tokens_out,
            cache_creation_tokens=body.cache_creation_tokens,
            cache_read_tokens=body.cache_read_tokens,
            usd=usd,
            run_id=ctx.run.id,
            note="chat_session turn" + (" (subscription: tokens only)" if subscription else ""),
        ))
    return {"ok": True}


# ── memory ──────────────────────────────────────────────────────────────────────

MEMORY_BODY_CHARS = 2_000


class MemoryAdd(BaseModel):
    scope: str
    kind: str = Field(pattern="^(fact|lesson|preference|contact)$")
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=10, max_length=20_000)


@router.post("/memory")
def memory_add(
    body: MemoryAdd, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    team_id = ctx.agent.team_id if body.scope == "team" else None
    policy.check_memory_add(session, cons, ctx.agent, body.scope, team_id)
    row = Memory(
        id=ids.new_id(ids.MEMORY),
        project_id=ctx.project.id,
        scope=body.scope,
        team_id=team_id,
        kind=body.kind,
        subject=body.subject,
        body=body.body,
        created_by_agent_id=ctx.agent.id,
    )
    session.add(row)
    return {"id": row.id}


@router.get("/memory/search")
def memory_search(
    q: str = "",
    scope: str | None = None,
    limit: int = 15,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> list[dict]:
    stmt = select(Memory).where(
        Memory.project_id == ctx.project.id, Memory.superseded_at.is_(None)
    )
    if scope == "project":
        stmt = stmt.where(Memory.scope == "project")
    elif scope == "team":
        stmt = stmt.where(Memory.scope == "team", Memory.team_id == ctx.agent.team_id)
    else:
        stmt = stmt.where(
            or_(Memory.scope == "project", Memory.team_id == ctx.agent.team_id)
        )
    if q.strip():
        if session.get_bind().dialect.name == "postgresql":
            # Matches the GIN index expression from migration 0001 exactly.
            stmt = stmt.where(
                func.to_tsvector("english", Memory.subject + " " + Memory.body).op("@@")(
                    func.plainto_tsquery("english", q.strip())
                )
            )
        else:
            like = f"%{q.strip()}%"
            stmt = stmt.where(or_(Memory.subject.ilike(like), Memory.body.ilike(like)))
    rows = session.execute(stmt.order_by(Memory.created_at.desc()).limit(min(limit, 50))).scalars()
    # Up to 50 full bodies per search is an input-token bill, not a feature:
    # clip each; narrow the query to read a long memory whole.
    return [
        {"id": m.id, "scope": m.scope, "kind": m.kind, "subject": m.subject,
         "body": m.body if len(m.body) <= MEMORY_BODY_CHARS
         else m.body[:MEMORY_BODY_CHARS] + " …[truncated; search a narrower q for the rest]",
         "at": m.created_at.isoformat()}
        for m in rows
    ]


class MemorySupersede(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=10, max_length=20_000)
    kind: str = Field(pattern="^(fact|lesson|preference|contact)$")


@router.post("/memory/{memory_id}/supersede")
def memory_supersede(
    memory_id: str,
    body: MemorySupersede,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    old = session.get(Memory, memory_id)
    if old is None or old.project_id != ctx.project.id or old.superseded_at is not None:
        raise Refusal("Unknown or already superseded memory.", status_code=404)
    if not policy.memory_curator_ok(session, ctx.agent, old):
        raise Refusal(
            "Only the scope's curator supersedes memory: the CEO for project scope, the "
            "team lead for team scope. The janitor never auto-evicts — losing the wrong "
            "memory is worse than a full cap.",
            status_code=403,
        )
    new = Memory(
        id=ids.new_id(ids.MEMORY),
        project_id=old.project_id,
        scope=old.scope,
        team_id=old.team_id,
        kind=body.kind,
        subject=body.subject,
        body=body.body,
        created_by_agent_id=ctx.agent.id,
    )
    session.add(new)
    session.flush()
    old.superseded_by_id = new.id
    old.superseded_at = utcnow()
    return {"id": new.id, "superseded": old.id}


@router.post("/memory/{memory_id}/forget")
def memory_forget(
    memory_id: str, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    old = session.get(Memory, memory_id)
    if old is None or old.project_id != ctx.project.id or old.superseded_at is not None:
        raise Refusal("Unknown or already superseded memory.", status_code=404)
    if not policy.memory_curator_ok(session, ctx.agent, old):
        raise Refusal("Only the scope's curator forgets memory.", status_code=403)
    old.superseded_at = utcnow()
    return {"forgotten": old.id}


# ── evidence, hypotheses, decisions ─────────────────────────────────────────────


class EvidenceAdd(BaseModel):
    kind: str
    description: str = Field(min_length=10)
    source_url: str | None = None
    task_id: str | None = None
    occurred_at: datetime | None = None


@router.post("/evidence")
def evidence_add(
    body: EvidenceAdd, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    policy.check_evidence_add(ctx.project, body.kind, body.source_url)
    row = Evidence(
        id=ids.new_id(ids.EVIDENCE),
        project_id=ctx.project.id,
        task_id=body.task_id,
        created_by_agent_id=ctx.agent.id,
        kind=body.kind,
        provenance=EvidenceProvenance.AGENT_CLAIMED,
        description=body.description,
        source_url=body.source_url,
        verification=(
            VerificationStatus.PENDING
            if body.kind == EvidenceKind.EXTERNAL
            else VerificationStatus.NOT_APPLICABLE
        ),
        occurred_at=body.occurred_at,
    )
    session.add(row)
    notify(session, "yaaf_verify", row.id)
    note = None
    if body.kind == EvidenceKind.EXTERNAL:
        note = (
            "Recorded as agent_claimed/pending. It counts toward drought and the external "
            "floor only after the platform verifies the source — the verifier fetches it, "
            "hashes it, and checks the domain is not one this project controls."
        )
    return {"id": row.id, "verification": row.verification, "note": note}


class HypothesisIn(BaseModel):
    statement: str = Field(min_length=10)
    kill_condition: str = Field(min_length=10)
    kill_date: datetime


@router.post("/hypotheses")
def hypothesis_add(
    body: HypothesisIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    if ctx.agent.kind != "ceo":
        raise Refusal("Only the CEO states hypotheses.", status_code=403)
    if body.kill_date <= utcnow():
        raise Refusal("kill_date must be in the future — a dated, falsifiable condition.")
    row = Hypothesis(
        id=ids.new_id(ids.HYPOTHESIS),
        project_id=ctx.project.id,
        statement=body.statement,
        kill_condition=body.kill_condition,
        kill_date=body.kill_date,
        created_by_agent_id=ctx.agent.id,
    )
    session.add(row)
    return {"id": row.id, "kill_date": body.kill_date.isoformat()}


class HypothesisKill(BaseModel):
    reason: str = Field(min_length=10)


@router.post("/hypotheses/{hypothesis_id}/kill")
def hypothesis_kill(
    hypothesis_id: str,
    body: HypothesisKill,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    if ctx.agent.kind != "ceo":
        raise Refusal("Only the CEO kills hypotheses.", status_code=403)
    row = session.get(Hypothesis, hypothesis_id)
    if row is None or row.project_id != ctx.project.id:
        raise Refusal("Unknown hypothesis.", status_code=404)
    row.status = HypothesisStatus.KILLED
    session.add(
        Decision(
            id=ids.new_id(ids.DECISION),
            project_id=ctx.project.id,
            by_agent_id=ctx.agent.id,
            subject=f"Killed hypothesis {row.id}",
            body=body.reason,
        )
    )
    return {"id": row.id, "status": row.status}


class DecisionIn(BaseModel):
    subject: str = Field(min_length=4, max_length=300)
    body: str = Field(min_length=10)


@router.post("/decisions")
def decision_add(
    body: DecisionIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    row = Decision(
        id=ids.new_id(ids.DECISION),
        project_id=ctx.project.id,
        by_agent_id=ctx.agent.id,
        subject=body.subject,
        body=body.body,
    )
    session.add(row)
    return {"id": row.id}


# ── approvals & spend ───────────────────────────────────────────────────────────


class ApprovalRequest(BaseModel):
    kind: str
    title: str = Field(min_length=4, max_length=300)
    body: str = Field(min_length=10)
    amount_usd: float | None = None


@router.post("/approvals")
def approval_request(
    body: ApprovalRequest, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    from datetime import timedelta

    row = Approval(
        id=ids.new_id(ids.APPROVAL),
        project_id=ctx.project.id,
        kind=body.kind,
        requested_by_agent_id=ctx.agent.id,
        title=body.title,
        body=body.body,
        amount_usd=body.amount_usd,
        expires_at=utcnow() + timedelta(days=cons.approval_timeout_days),
    )
    session.add(row)
    return {
        "id": row.id,
        "expires_at": row.expires_at.isoformat(),
        "note": (
            f"If the chairman does not decide within {cons.approval_timeout_days} days this "
            f"expires as DENIED and you will be woken to re-plan. Frame requests as "
            f"recommendations you would defend, not open questions."
        ),
    }


@router.get("/approvals")
def approval_list(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Approval)
        .where(Approval.project_id == ctx.project.id)
        .order_by(Approval.created_at.desc())
        .limit(30)
    ).scalars()
    return [
        {
            "id": a.id, "kind": a.kind, "title": a.title, "status": a.status,
            "amount_usd": a.amount_usd, "decided_at":
                a.decided_at.isoformat() if a.decided_at else None,
            "decision_note": a.decision_note,
        }
        for a in rows
    ]


class SpendIn(BaseModel):
    usd: float = Field(gt=0)
    note: str = Field(min_length=5)
    approval_id: str | None = None


@router.post("/ledger/spend")
def book_spend(
    body: SpendIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    if body.usd >= cons.approval_threshold_usd:
        approval = session.get(Approval, body.approval_id) if body.approval_id else None
        ok = (
            approval is not None
            and approval.project_id == ctx.project.id
            and approval.status == ApprovalStatus.APPROVED
            and (approval.amount_usd or 0) >= body.usd
        )
        if not ok:
            raise Refusal(
                f"Spends of ${cons.approval_threshold_usd:,.0f} or more need an approved "
                "chairman approval covering the amount. Request one at POST /agent/approvals "
                "with kind='spend' — as a recommendation you would defend.",
                status_code=403,
            )
    row = LedgerEntry(
        id=ids.new_id(ids.LEDGER),
        project_id=ctx.project.id,
        kind="external_spend",
        usd=body.usd,
        run_id=ctx.run.id,
        note=body.note,
    )
    session.add(row)
    return {"id": row.id}


# ── side effects (idempotency ledger) ───────────────────────────────────────────


class SideEffectBegin(BaseModel):
    kind: str = Field(min_length=2, max_length=64)
    idempotency_key: str = Field(min_length=4, max_length=200)
    task_id: str | None = None
    payload: dict = Field(default_factory=dict)


@router.post("/side_effects")
def side_effect_begin(
    body: SideEffectBegin, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    existing = session.execute(
        select(SideEffect).where(
            SideEffect.project_id == ctx.project.id,
            SideEffect.idempotency_key == body.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "id": existing.id,
            "status": existing.status,
            "duplicate": True,
            "result": existing.result,
            "note": (
                "This effect was already recorded (possibly by a prior attempt of this "
                "task). If status is 'completed', DO NOT fire it again."
            ),
        }
    row = SideEffect(
        id=ids.new_id(ids.SIDE_EFFECT),
        project_id=ctx.project.id,
        run_id=ctx.run.id,
        task_id=body.task_id,
        kind=body.kind,
        idempotency_key=body.idempotency_key,
        payload=body.payload,
    )
    session.add(row)
    return {"id": row.id, "status": row.status, "duplicate": False}


class SideEffectFinish(BaseModel):
    ok: bool
    result: dict = Field(default_factory=dict)


@router.post("/side_effects/{effect_id}/finish")
def side_effect_finish(
    effect_id: str,
    body: SideEffectFinish,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    row = session.get(SideEffect, effect_id)
    if row is None or row.project_id != ctx.project.id:
        raise Refusal("Unknown side effect.", status_code=404)
    row.status = SideEffectStatus.COMPLETED if body.ok else SideEffectStatus.FAILED
    row.result = body.result
    row.completed_at = utcnow()
    return {"id": row.id, "status": row.status}


# ── artifacts (durable deliverables) ────────────────────────────────────────────


MAX_ARTIFACT_BYTES = 5 * 1024 * 1024


class ArtifactPut(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str


@router.post("/artifacts")
def artifact_put(body: ArtifactPut, ctx: RunContext = Depends(run_auth)) -> dict:
    """Persist a finished deliverable to the artifact store. The workspace is
    ephemeral; this is where finished work goes to outlive the run. The
    platform picks the location — projects/<project>/<run>/<filename> — so an
    agent names a file, never a path."""
    from yaaf.core.artifacts import store

    filename = body.filename.strip()
    if "/" in filename or "\\" in filename or filename in (".", "..") or "\x00" in filename:
        raise Refusal(
            "filename must be a plain file name — no path separators. The platform "
            "chooses where artifacts live: projects/<project_id>/<run_id>/<filename>.",
            status_code=422,
        )
    try:
        data = base64.b64decode(body.content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise Refusal("content_base64 is not valid base64.", status_code=422) from None
    if len(data) > MAX_ARTIFACT_BYTES:
        raise Refusal(
            f"artifact is {len(data)} bytes; the limit is 5 MB. Store large deliverables "
            "somewhere addressable and record the URL as evidence instead.",
            status_code=413,
        )
    uri = store().put_bytes(f"projects/{ctx.project.id}/{ctx.run.id}/{filename}", data)
    return {"uri": uri}
