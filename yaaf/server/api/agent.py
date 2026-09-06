"""The agent-facing API — lana's plan.py/memory.py/slack.py, hardened.

Every endpoint authenticates a per-run token and enforces the constitution
through :mod:`yaaf.server.policy`. An agent cannot be talked out of rules it
never held: they live here, server-side, not in its prompt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import effective, propose_amendment
from yaaf.core.db import notify
from yaaf.core.models import (
    Account,
    AgentKind,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    AuditLog,
    Channel,
    ChannelKind,
    ChannelRead,
    Commitment,
    CommitmentStatus,
    Decision,
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    Hypothesis,
    HypothesisStatus,
    LedgerEntry,
    LedgerKind,
    Memory,
    Message,
    ProjectMode,
    Recurrence,
    Requirement,
    RequirementEdge,
    RequirementStatus,
    Review,
    ReviewStatus,
    RunKind,
    RunQueue,
    Secret,
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
from yaaf.server.notify import chairman_notify
from yaaf.server.policy import Refusal
from yaaf.server.rows import approval_row

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
        # What this work is FOR. Outside Explore a task must name a requirement,
        # and there was no read anywhere that returned one — so the only way to
        # supply the mandatory field was to guess an id and eat the 404.
        "requirement_id": task.requirement_id,
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
        # Own team, plus anything anywhere waiting on a read this agent can
        # give. A reviewer sits on its own team by design, so a board filtered
        # to team_id showed it nothing — it was woken for work it could not
        # see, which is worse than not being woken at all.
        visible = Task.team_id == ctx.agent.team_id
        if policy.is_review_seat(session, ctx.agent):
            visible = or_(visible, and_(
                Task.status == TaskStatus.REVIEW,
                Task.claimed_by_agent_id != ctx.agent.id,
            ))
        tasks = session.execute(
            select(Task).where(
                Task.project_id == ctx.project.id,
                visible,
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
    requirement_id: str | None = None
    not_before: datetime | None = None


@router.post("/tasks")
def create_task(
    body: TaskCreate, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    requirement = None
    if body.requirement_id is not None:
        requirement = session.get(Requirement, body.requirement_id)
        if requirement is None or requirement.project_id != ctx.project.id:
            raise Refusal("Unknown requirement.", status_code=404)
    # A subtask inherits what its parent is for: a lead decomposing work cannot
    # quietly point half of it at something else.
    if requirement is None and body.parent_task_id is not None:
        parent = session.get(Task, body.parent_task_id)
        if parent is not None and parent.requirement_id:
            requirement = session.get(Requirement, parent.requirement_id)
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
        requirement=requirement,
        not_before=body.not_before,
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
        requirement_id=requirement.id if requirement else None,
        title=body.title,
        goal=body.goal,
        success_test=body.success_test,
        produces_external_evidence=body.produces_external_evidence,
        not_before=body.not_before,
    )
    session.add(task)
    policy.record_task_event(session, task, TaskStatus.READY, f"agent:{ctx.agent.id}",
                             from_status=None)
    # Accepted scope becomes live scope the moment work exists for it, so the
    # burn-up and the review can tell "nobody has started" from "in flight".
    if requirement is not None and requirement.status == RequirementStatus.PENDING:
        policy.record_requirement_event(session, requirement, RequirementStatus.IN_PROGRESS,
                                        f"agent:{ctx.agent.id}", note=f"work created: {task.id}")
        requirement.status = RequirementStatus.IN_PROGRESS
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
    elif policy.review_is_owed(session, ctx.project, task):
        # An observable test used to close on the claimant's own word, and 80%
        # of those closes were never sampled. Where the chairman asked for an
        # independent read, the task now waits for somebody who did not write
        # it instead.
        task.status = TaskStatus.REVIEW
        policy.record_task_event(session, task, TaskStatus.REVIEW, f"agent:{ctx.agent.id}",
                                 from_status=from_status)
        outcome = "review"
    else:
        task.status = TaskStatus.DONE
        policy.record_task_event(session, task, TaskStatus.DONE, f"agent:{ctx.agent.id}",
                                 from_status=from_status)
        outcome = "done"
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {"task_id": task.id, "status": outcome}


class TaskReview(BaseModel):
    ok: bool
    notes: str = Field(min_length=10)

    @field_validator("notes")
    @classmethod
    def _not_blank(cls, raw: str) -> str:
        # min_length counts spaces. "          " is ten characters and no reasons.
        if len(raw.strip()) < 10:
            raise ValueError("say what you found; whitespace is not a review")
        return raw.strip()


@router.post("/tasks/{task_id}/review")
def review_task(
    task_id: str,
    body: TaskReview,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    """A verdict on somebody else's finished work.

    `ok` closes the task. Not-ok hands it back to its claimant with the
    notes, which are an ordinary outcome and not an escalation — returning
    work costs a run and is supposed to. `notes` is required either way:
    "it is fine" teaches the writer nothing and makes the pass unauditable.
    """
    task = session.get(Task, task_id)
    if task is None or task.project_id != ctx.project.id:
        raise Refusal("Unknown task.", status_code=404)
    policy.check_task_review(session, ctx.project, ctx.agent, task)
    from_status = task.status
    note = body.notes.strip()
    if body.ok:
        task.status = TaskStatus.DONE
        policy.record_task_event(session, task, TaskStatus.DONE, f"agent:{ctx.agent.id}",
                                 note=f"reviewed: {note[:500]}", from_status=from_status)
        outcome = "done"
    else:
        # A returned task costs two runs a cycle — the author's and the
        # reader's — so an unbounded loop is a project that spends its day
        # disagreeing with itself. Reuses the counter and the ceiling the
        # command-check loop already has, for the same reason: past it, this is
        # not a disagreement the two of them will settle by trying again.
        task.check_failures = (task.check_failures or 0) + 1
        cap = effective(session, ctx.project.id).task_check_failures_before_block
        if cap and task.check_failures >= cap:
            task.status = TaskStatus.BLOCKED
            task.blocked_reason = (
                f"Returned by review {task.check_failures} times; the last reason was: "
                f"{note[:300]}. Two agents disagreeing this often is a question for "
                "whoever planned the work, not another round.")
            policy.record_task_event(session, task, TaskStatus.BLOCKED,
                                     f"agent:{ctx.agent.id}",
                                     note=f"returned {task.check_failures}x",
                                     from_status=from_status)
            policy.notice_review_returned(session, task, ctx.agent, note, blocked=True)
            outcome = "blocked"
        else:
            # Back to the person who wrote it, still theirs, with the reasons.
            task.status = TaskStatus.CLAIMED
            task.blocked_reason = None
            policy.record_task_event(session, task, TaskStatus.CLAIMED,
                                     f"agent:{ctx.agent.id}",
                                     note=f"returned: {note[:500]}",
                                     from_status=from_status)
            policy.notice_review_returned(session, task, ctx.agent, note)
            outcome = "returned"
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

    planner = policy.planner_of(session, task)
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


# ── chairman directives ─────────────────────────────────────────────────────────


class DirectiveIn(BaseModel):
    """A standing instruction the chairman gave in the boardroom, filed by the
    CEO — as policy (changes) or as an executed plan order (action) — instead
    of as a memory."""

    from_message_id: str
    changes: dict | None = None
    action: dict | None = None
    # Capped below the 4000-char reason columns so the citation suffix the
    # platform appends can never push a valid request over a wall mid-write.
    reason: str = Field(min_length=10, max_length=3800)


@router.post("/directives")
def file_directive(
    body: DirectiveIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Turn something the chairman said into something the platform does.

    Two shapes. `changes` files reporting-cadence policy (the original gap: on
    16 Aug he wrote "change your updates to every 4 hours" and nothing
    structural happened). `action` executes a plan order — the chairman writes
    "cut R-07" or "reopen R-04, it wasn't actually done" in the boardroom, and
    the CEO carries it out through the same machinery as the chairman's own
    buttons, instead of the order waiting for him to click it himself.

    The platform cannot check what a message *meant*, so it checks what it can
    and makes the rest loud: the citation must be a real, recent boardroom
    message from the chairman himself; the authority exercised is his, proven
    by that citation; and every filing is audited, disclosed, and echoed back
    to him in the boardroom where a misreading is one sentence away from
    correction — and every scope verb is reversible.
    """
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Only the CEO files directives — nobody else speaks to the chairman.",
                      status_code=403)
    if (body.changes is None) == (body.action is None):
        raise Refusal("A directive carries exactly one of `changes` (a cadence rule) or "
                      "`action` (a plan order).", status_code=422)
    if body.action is not None:
        return _file_scope_directive(body, ctx, session)
    policy.check_directive_scope(body.changes)
    cited = policy.check_directive_citation(session, ctx.project, body.from_message_id)
    try:
        amendments = propose_amendment(
            session, ctx.project.id, ctx.project.chairman_user_id,
            f"{body.reason} [filed by the CEO from the chairman's boardroom message "
            f"{cited.id} at {cited.created_at.isoformat(timespec='minutes')}]",
            body.changes,
        )
    except Exception as exc:
        raise Refusal(f"That is not a valid change to the constitution: {exc}") from exc
    if not amendments:
        raise Refusal("That directive changes nothing — the rule already reads that way.")
    _audit_directive(session, ctx, cited, body.changes)
    policy.confirm_directive(session, ctx.project, ctx.agent, cited, body.changes)
    return {
        "amendments": [{"id": a.id, "effective_at": a.effective_at.isoformat()}
                       for a in amendments],
        "in_force": True,
        "note": ("Filed and in force now. It is recorded, and "
                 "the chairman has been told in the boardroom so he can correct you if you "
                 "read him wrong. Stop carrying this rule in memory; it is policy."),
    }


def _requirement_by_key(session: Session, project_id: str, key: str) -> Requirement:
    row = session.execute(
        select(Requirement).where(
            Requirement.project_id == project_id, Requirement.key == key.strip().upper()
        )
    ).scalar_one_or_none()
    if row is None:
        raise Refusal(f"No requirement {key} in this project.", status_code=404)
    return row


def _execute_scope_action(session: Session, ctx: RunContext, action: dict,
                          reason: str, actor: str, by_user: str | None,
                          notify_ceo: bool) -> tuple[dict, str]:
    """ONE implementation of every scope verb, whoever's authority carries it.

    Both the directive path (the chairman's cited word) and the self-exercise
    path (the dial) land here, on the apply_* helpers directly — so a Refusal
    reaches the app-level handler and counts against the run's refusal cap on
    both paths, field validation happened in policy (typed, ranged, same walls
    as the chairman's own routes), and adding a verb is one branch plus one
    entry in the policy tables. Reasons are length-capped at the request
    models, so nothing here can outgrow a column."""
    # Local import: admin imports this module at load; one-way at import time.
    from yaaf.server.api import admin as admin_api

    kind = action["kind"]
    project = ctx.project
    row = _requirement_by_key(session, project.id, action["requirement_key"])
    if kind == "accept":
        result = admin_api.apply_accept(session, project, row, actor,
                                        action.get("stage"), reason,
                                        by_user=by_user, notify=notify_ceo)
        detail = f"accepted {row.key} into scope (stage {result['stage']})"
    elif kind == "cut":
        result = admin_api.apply_cut(session, project, row, actor, reason,
                                     notify=notify_ceo)
        detail = f"cut {row.key}"
    elif kind == "reopen":
        result = admin_api.apply_reopen(session, project, row, actor, reason,
                                        notify=notify_ceo)
        detail = f"reopened {row.key} (was {result['was']})"
    elif kind == "restage":
        result = admin_api.apply_restage(session, project, row, actor,
                                         action["stage"], reason, notify=notify_ceo)
        detail = f"moved {row.key} to stage {result['stage']} (was {result['was']})"
    elif kind == "reestimate":
        result = admin_api.apply_reestimate(session, project, row, actor,
                                            action["est_runs"], reason,
                                            notify=notify_ceo)
        detail = (f"reestimated {row.key} at {result['est_runs']} runs "
                  f"(was {result['was']}; scored estimate stays "
                  f"{result['est_runs_first']})")
    elif kind == "reword":
        result = admin_api.apply_reword(session, project, row, actor,
                                        action["statement"], reason,
                                        notify=notify_ceo)
        detail = f"reworded {row.key}"
    elif kind == "verdict":
        result = admin_api.apply_verdict(session, project, row, actor,
                                         bool(action["ok"]), reason,
                                         notify=notify_ceo)
        detail = (f"judged {row.key} {'MET' if action['ok'] else 'NOT met'} "
                  "(unverifiable observable, CEO judgment)")
    else:  # unsequence
        source = _requirement_by_key(session, project.id, action["depends_on_key"])
        result = admin_api.apply_edge_remove(session, project, actor,
                                             source.id, row.id, reason,
                                             notify=notify_ceo)
        detail = f"removed the dependency {source.key} → {row.key}"
    return result, detail


def _file_scope_directive(body: DirectiveIn, ctx: RunContext, session: Session) -> dict:
    """Execute a plan order the chairman gave in the boardroom, on his cited
    authority — the same apply helpers his own buttons drive."""
    action = body.action or {}
    policy.check_directive_action(action)
    cited = policy.check_directive_citation(
        session, ctx.project, body.from_message_id, for_rules=False)
    reason = (f"{body.reason} [chairman's order, boardroom message {cited.id} at "
              f"{cited.created_at.isoformat(timespec='minutes')}]")
    result, detail = _execute_scope_action(
        session, ctx, action, reason,
        actor=f"user:{ctx.project.chairman_user_id}",
        by_user=ctx.project.chairman_user_id,
        notify_ceo=True,
    )
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=ctx.project.id,
            actor=f"agent:{ctx.agent.id}",
            action="scope.directive",
            entity="message",
            entity_id=cited.id,
            detail={"action": action, "detail": detail,
                    "cited_at": cited.created_at.isoformat()},
        )
    )
    policy.confirm_directive(session, ctx.project, ctx.agent, cited, action_detail=detail)
    return {
        "executed": detail,
        "result": result,
        "note": ("Executed on the chairman's cited authority and echoed back to him in "
                 "the boardroom, threaded to his message. If you read him wrong, he will "
                 "say so — every scope verb is reversible."),
    }


# ── self-held scope authority ──────────────────────────────────────────────────


class ScopeActIn(BaseModel):
    action: dict
    reason: str = Field(min_length=10, max_length=3800)


@router.post("/scope")
def scope_act(
    body: ScopeActIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Exercise a scope verb the constitution's authority dial grants the CEO.

    Under the default dial every verb here is a 403 — the chairman holds them
    all, and the CEO's paths are proposing, or executing his boardroom orders
    as directives. When the chairman turns `plan_authority`, `scope_authority`
    or `verdict_policy` to the CEO, the same verbs run through the same apply
    helpers as his own buttons — the only differences are the actor on the
    record and where the disclosure goes: every self-exercise lands in the
    audit log and is sent to the chairman, so autonomy is never quiet.
    """
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Scope authority, where granted at all, is granted to the CEO.",
                      status_code=403)
    cons = effective(session, ctx.project.id)
    policy.check_self_scope_action(cons, body.action)
    result, detail = _execute_scope_action(
        session, ctx, body.action,
        f"{body.reason} [self-exercised under the constitution's authority dial]",
        actor=f"agent:{ctx.agent.id}",
        by_user=None,
        notify_ceo=False,
    )
    _disclose_self_action(session, ctx, body.action["kind"], detail, body.reason)
    return {
        "executed": detail,
        "result": result,
        "note": ("Done under the authority the constitution grants you. It is on the "
                 "record and the chairman has been told — he can reverse any of it, "
                 "and turning the dial back is one amendment. Spend this authority "
                 "the way you would want it audited."),
    }


def _disclose_self_action(session: Session, ctx: RunContext, kind: str,
                          detail: str, reason: str) -> None:
    """Autonomy is never quiet: every self-exercise is audited and sent to the
    chairman out-of-band. Self-judged verdicts in particular are disclosed
    every single time — stronger than sampling, and the reason `ceo_judgment`
    does not bend 'nothing is quietly trusted'."""
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=ctx.project.id,
            actor=f"agent:{ctx.agent.id}",
            action="scope.self",
            entity="project",
            entity_id=ctx.project.id,
            detail={"kind": kind, "detail": detail, "reason": reason},
        )
    )
    chairman_notify(
        session, ctx.project,
        f"CEO decision: {detail}",
        f"{detail}\n\nIts reason: {reason}\n\nExercised under the authority dial you "
        "set in the constitution. Everything here is reversible — reopen what was "
        "cut, cut what was accepted — and the dial itself is yours to turn back.",
    )


def _audit_directive(session: Session, ctx: RunContext, cited: Message, changes: dict) -> None:
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=ctx.project.id,
            actor=f"agent:{ctx.agent.id}",
            action="constitution.directive",
            entity="message",
            entity_id=cited.id,
            detail={"changes": changes, "cited_at": cited.created_at.isoformat()},
        )
    )


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
            kind=LedgerKind.MODEL_USAGE,
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
    include_superseded: bool = False,
    memory_id: str | None = None,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> list[dict]:
    # Compaction is only safe because it is reversible: folded-in entries stay
    # in the table and stay findable. `include_superseded` is how a curator
    # fetches back something it folded too eagerly — without it, "nothing is
    # ever deleted" would be a claim the agent has no way to act on.
    stmt = select(Memory).where(Memory.project_id == ctx.project.id)
    if memory_id:
        # Fetch-one for the memory-tool-shaped view: an id is an exact address,
        # and making it ride the text search would cap it at the search limit.
        stmt = stmt.where(Memory.id == memory_id)
    if not include_superseded:
        stmt = stmt.where(Memory.superseded_at.is_(None))
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
         "at": m.created_at.isoformat(),
         **({"superseded_by": m.superseded_by_id, "superseded_at": m.superseded_at.isoformat()}
            if m.superseded_at is not None else {})}
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


class MemoryCompact(BaseModel):
    # Capped below every default cap on purpose: folding a theme into one entry
    # is curation, folding the entire store into one entry is the auto-eviction
    # this subsystem refuses to do — just performed by hand.
    replaces: list[str] = Field(min_length=2, max_length=20)
    subject: str = Field(min_length=3, max_length=300)
    body: str = Field(min_length=10, max_length=20_000)
    kind: str = Field(pattern="^(fact|lesson|preference|contact)$")


@router.post("/memory/compact")
def memory_compact(
    body: MemoryCompact, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Fold several entries into one. The compaction verb: supersede is 1→1 and
    frees no slot, forget drops one and keeps nothing. This keeps what the
    entries *meant* while giving back all but one of their slots — and because
    every folded-in row still points at its replacement, the fold is auditable
    and can be read back with ?include_superseded=true."""
    ids_wanted = list(dict.fromkeys(body.replaces))  # order-preserving dedup
    if len(ids_wanted) < 2:
        raise Refusal(
            "Compaction folds two or more entries into one. To replace a single entry "
            "use memory_supersede; to drop one use memory_forget.",
        )
    # Scoped to this project in the query, not checked afterwards: two different
    # answers for "no such id" and "another project's id" would tell an agent
    # whether a row exists in a project it cannot see.
    rows = list(
        session.execute(
            select(Memory).where(
                Memory.id.in_(ids_wanted), Memory.project_id == ctx.project.id
            )
        ).scalars()
    )
    found = {m.id: m for m in rows}
    missing = [mid for mid in ids_wanted if mid not in found]
    if missing:
        raise Refusal(f"Unknown memory: {', '.join(missing)}.", status_code=404)
    stale = [m.id for m in rows if m.superseded_at is not None]
    if stale:
        raise Refusal(
            f"Already superseded: {', '.join(stale)}. "
            "Search again — another run may have curated since you read them.",
            status_code=404,
        )
    scopes = {(m.scope, m.team_id) for m in rows}
    if len(scopes) > 1:
        raise Refusal(
            "Every entry in one compaction must share a scope — project memory and a "
            "team's memory are curated by different agents and capped separately.",
        )
    scope, team_id = scopes.pop()
    if not policy.memory_curator_ok(session, ctx.agent, rows[0]):
        raise Refusal(
            "Only the scope's curator compacts memory: the CEO for project scope, the "
            "team lead for team scope. The janitor never auto-evicts — losing the wrong "
            "memory is worse than a full cap.",
            status_code=403,
        )
    new = Memory(
        id=ids.new_id(ids.MEMORY),
        project_id=ctx.project.id,
        scope=scope,
        team_id=team_id,
        kind=body.kind,
        subject=body.subject,
        body=body.body,
        created_by_agent_id=ctx.agent.id,
    )
    session.add(new)
    session.flush()
    now = utcnow()
    for row in rows:
        row.superseded_by_id = new.id
        row.superseded_at = now
    cons = effective(session, ctx.project.id)
    return {
        "id": new.id,
        "folded": [m.id for m in rows],
        "freed": len(rows) - 1,
        "live": policy.live_memory_count(session, scope, ctx.project.id, team_id),
        "cap": policy.memory_cap_for(cons, scope),
    }


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


# ── requirements: the CEO proposes, the chairman accepts ────────────────────────
#
# Note what is absent: there is no route here that writes `met`. That is the
# strongest available form of "done is never self-graded" — not a rule an agent
# could argue with, but a verb it was never given. The platform runs the
# acceptance test and writes the result itself.


class RequirementMeta(BaseModel):
    """What the project's owner would call this requirement. Not checked by the
    platform; shown by whoever presents the plan to the person who hired it."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=400)
    cadence: str | None = Field(default=None, pattern="^(daily|weekly)$")
    due: str | None = Field(default=None, max_length=40)
    lands: str | None = Field(default=None, pattern="^(report|memo|note|table)$")


class RequirementPropose(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)
    acceptance_test: dict
    meta: RequirementMeta | None = None
    stage: int = Field(default=1, ge=1, le=50)
    est_runs: int | None = Field(default=None, ge=1, le=10_000)
    depends_on: list[str] = Field(default_factory=list)
    # Met once, or owed again every cycle. Operate only — Deliver and Explore
    # have an end, and an obligation that never closes there would be a
    # requirement the project could never satisfy.
    recurrence: str = Field(default=Recurrence.TERMINAL,
                            pattern="^(terminal|standing)$")


@router.post("/requirements")
def propose_requirement(
    body: RequirementPropose, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    policy.check_requirement_propose(cons, ctx.project, ctx.agent, body.acceptance_test)
    if policy.requires_requirements(ctx.project) and not body.est_runs:
        raise Refusal(
            "This project plans before it builds, so a requirement needs est_runs: your "
            "honest guess at how many runs it will take. It is checked against the budget "
            "now and against what the work actually costs later — a guess you stand "
            "behind is worth more here than a small number.",
            status_code=422,
        )
    # The commitment's guarantee: while the promised date does not hold, new
    # scope is refused before affordability is even asked. And its other half —
    # a project that already delivered may not grow itself back open, which the
    # commitment check misses because a promise that was KEPT is neither at risk
    # nor breached.
    policy.check_scope_within_commitment(session, ctx.project, cons)
    policy.check_scope_after_delivery(session, ctx.project)
    policy.check_plan_affordable(session, ctx.project, cons, body.est_runs or 0)
    if (body.recurrence == Recurrence.STANDING
            and ctx.project.mode != ProjectMode.OPERATE):
        raise Refusal(
            "A standing requirement is owed again every cycle, and only Operate runs "
            f"on cycles — this project is {ctx.project.mode}. Here a requirement is a "
            "thing that must be true at the end, so one that never closed would be one "
            "the project could never satisfy.",
            status_code=422,
        )
    cycle = policy.current_cycle(session, ctx.project.id)
    requirement = Requirement(
        id=ids.new_id(ids.REQUIREMENT),
        project_id=ctx.project.id,
        cycle_id=cycle.id if cycle is not None else None,
        key=policy.next_requirement_key(session, ctx.project.id),
        statement=body.statement.strip(),
        acceptance_test=body.acceptance_test,
        meta=body.meta.model_dump(mode="json") if body.meta else None,
        status=RequirementStatus.PROPOSED,
        stage=body.stage,
        recurrence=body.recurrence,
        est_runs=body.est_runs,
        est_runs_first=body.est_runs,
        proposed_by_agent_id=ctx.agent.id,
    )
    session.add(requirement)
    session.flush()
    policy.record_requirement_event(session, requirement, RequirementStatus.PROPOSED,
                                    f"agent:{ctx.agent.id}", from_status=None)
    for source_id in body.depends_on:
        source = session.get(Requirement, source_id)
        if source is None or source.project_id != ctx.project.id:
            raise Refusal(f"Unknown requirement in depends_on: {source_id}", status_code=404)
        policy.check_requirement_edge(session, ctx.project, source, requirement)
        session.add(RequirementEdge(
            id=ids.new_id(ids.REQUIREMENT_EDGE),
            project_id=ctx.project.id,
            from_requirement_id=source.id,
            to_requirement_id=requirement.id,
        ))
    return {
        "requirement_id": requirement.id,
        "key": requirement.key,
        "status": requirement.status,
        "recurrence": requirement.recurrence,
        "note": ("Proposed, not scope. The chairman accepts requirements; until he does, "
                 "no work may be created for this one.")
        + (" Standing: he accepts this obligation ONCE and it is owed every cycle "
           "from then on. Each roll records whether it was honoured, and a cycle "
           "that closes without it raises a flag — so the pattern is a fact rather "
           "than a claim. It never counts toward delivery pace: an obligation met "
           "on its rhythm is not progress toward an end."
           if requirement.recurrence == Recurrence.STANDING else ""),
    }


class RequirementDependency(BaseModel):
    requirement_id: str
    depends_on_requirement_id: str


@router.post("/requirement_edges")
def add_requirement_edge(
    body: RequirementDependency,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Only the CEO sequences requirements.", status_code=403)
    target = session.get(Requirement, body.requirement_id)
    source = session.get(Requirement, body.depends_on_requirement_id)
    if target is None or source is None:
        raise Refusal("Unknown requirement.", status_code=404)
    policy.check_requirement_edge(session, ctx.project, source, target)
    session.add(RequirementEdge(
        id=ids.new_id(ids.REQUIREMENT_EDGE),
        project_id=ctx.project.id,
        from_requirement_id=source.id,
        to_requirement_id=target.id,
    ))
    return {"ok": True, "note": f"{target.key} now waits for {source.key}."}


# ── the commitment: the CEO asks, the platform computes, the chairman decides ──
#
# Note what is absent, again: there is no date field in the proposal — extra
# fields are a validation error, so a smuggled deliver_by dies at the schema —
# and no agent route accepts, moves or closes a commitment. The projection is
# computed by the platform, the transitions are the janitor's, and the date is
# the chairman's.


class CommitmentPropose(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=2000)


@router.post("/commitments")
def propose_commitment(
    body: CommitmentPropose, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    cons = effective(session, ctx.project.id)
    projection = policy.check_commitment_propose(session, ctx.project, cons, ctx.agent)
    projected = projection["projected_done_at"]
    # A promise is day-shaped — "by the 30th", never "by 14:07:22" — so the
    # proposed date is the end of the projected day. It also keeps a promise
    # accepted at exactly the projection from reading as late one tick later,
    # when the sliding projection has moved past it by seconds.
    deliver_by = projected.replace(hour=23, minute=59, second=59, microsecond=0)
    commitment = Commitment(
        id=ids.new_id(ids.COMMITMENT),
        project_id=ctx.project.id,
        status=CommitmentStatus.PROPOSED,
        deliver_by=deliver_by,
        proposed_by_agent_id=ctx.agent.id,
        note=(body.note or "").strip() or None,
        projected_done_at=projected,
        projection={"method": projection["method"], "note": projection["basis"]},
    )
    session.add(commitment)
    session.flush()
    if cons.commitment_authority == "ceo":
        # The dial holds the date's signature too. Self-acceptance is only ever
        # adopting the platform's own projection — the CEO still cannot write a
        # date — so what the grant really covers is the *commitment to it*.
        # Same freeze as the chairman's accept, disclosed the same loud way.
        from yaaf.server.api import admin as admin_api

        commitment.status = CommitmentStatus.ACCEPTED
        commitment.accepted_at = utcnow()
        admin_api._freeze_commitment_basis(session, ctx.project, commitment, cons)
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
            actor=f"agent:{ctx.agent.id}", action="commitment.self_accept",
            entity="commitment", entity_id=commitment.id,
            detail={"deliver_by": commitment.deliver_by.isoformat(),
                    "scope": commitment.scope_snapshot},
        ))
        chairman_notify(
            session, ctx.project,
            f"CEO committed to {commitment.deliver_by:%Y-%m-%d}",
            f"The CEO committed to delivering {len(commitment.scope_snapshot)} "
            f"requirement(s) by {commitment.deliver_by:%Y-%m-%d} — the platform's own "
            "projection, which the CEO cannot write, only adopt. From here the "
            "projection recomputes continuously; a slip freezes new scope and emails "
            "you, exactly as if you had signed it yourself. Move or decline it any "
            "time — those buttons never close.",
        )
        return {
            "commitment_id": commitment.id,
            "status": commitment.status,
            "deliver_by": deliver_by.isoformat(),
            "basis": projection["basis"],
            "note": ("Committed. The constitution grants you the signature, so the "
                     "platform's projected date is now the promise — it is frozen, "
                     "audited, and the chairman has been told. A slip freezes new "
                     "scope on you the same as on anyone."),
        }
    return {
        "commitment_id": commitment.id,
        "status": commitment.status,
        "deliver_by": deliver_by.isoformat(),
        "basis": projection["basis"],
        "note": ("Proposed to the chairman. The date is the platform's own projection "
                 "— you did not write it and cannot. He accepts it (with this date or "
                 "his own), or declines; nothing binds until he does."),
    }


class FlagRaiseIn(BaseModel):
    flag_id: str
    # The boardroom message in which it was actually said. The flag closes
    # because the chairman was told, and this is the proof.
    message_id: str
    proposal: str = Field(min_length=20, max_length=4000)


@router.get("/flags")
def list_flags(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> dict:
    """What the platform has decided the chairman needs to hear."""
    from yaaf.server import flags as flag_lib

    rows = flag_lib.flags_for_context(session, ctx.project.id)
    unraised = sum(1 for f in rows if not f["raised"])
    return {
        "flags": rows,
        "unraised": unraised,
        "note": (
            "Each of these was opened by arithmetic or a failed check, not by anyone's "
            "judgement about whether it matters. Raise it in the boardroom with the "
            "numbers and a proposal, then close it with flag_raise. The chairman can "
            "see how many are unraised, so saying nothing is not a quiet option."
            if unraised else "Nothing outstanding."
        ),
    }


@router.post("/flags/raise")
def raise_flag(
    body: FlagRaiseIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Close a flag by having told the chairman, with what you propose doing.

    A flag cannot be closed by going quiet, and it cannot be closed by an alarm
    either. The proposal is required because a flag without one is anxiety
    forwarded to his desk: what a CEO adds over a monitoring alert is the
    sentence after the bad news — what broke, the arithmetic, the ways out, the
    one you would pick, and what you need from him.

    Closing it dismissively is legitimate and expected. "Failed on one order,
    the warehouse API was down four minutes, it self-healed, no action needed"
    is this verb working exactly as intended. What is no longer available is
    leaving it open while reporting that everything is fine.
    """
    from yaaf.core.models import Flag, FlagStatus

    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal(
            "Only the CEO speaks to the chairman. If you can see something he should "
            "know, tell the CEO — a suggestion survives your run.",
            status_code=403,
        )
    flag = session.get(Flag, body.flag_id)
    if flag is None or flag.project_id != ctx.project.id:
        raise Refusal("No such flag.", status_code=404)
    if flag.status != FlagStatus.OPEN:
        raise Refusal(
            f"That flag is already {flag.status}. One telling is enough.",
            status_code=409,
        )

    message = session.get(Message, body.message_id)
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == ctx.project.id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if (message is None or boardroom is None or message.channel_id != boardroom.id
            or message.sender_agent_id != ctx.agent.id):
        raise Refusal(
            "message_id must be a boardroom message you posted. The flag closes "
            "because the chairman was told; the message is the proof, and without it "
            "this verb would just be a way of marking bad news as handled.",
            status_code=422,
        )

    flag.status = FlagStatus.RAISED
    flag.raised_at = utcnow()
    flag.raised_in_message_id = message.id
    flag.proposal = body.proposal.strip()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="flag.raised",
        entity="flag", entity_id=flag.id,
        detail={"kind": flag.kind, "message_id": message.id,
                "proposal": flag.proposal[:500]},
    ))
    return {
        "flag_id": flag.id,
        "status": flag.status,
        "note": "Raised. He has it, with what you propose — that is the job.",
    }


class OverrunAccountIn(BaseModel):
    overrun_id: str
    # What the extra bought, and whether you would spend it again. Long minimum
    # on purpose: "we went over" is not an account, and a month past its plan is
    # exactly where a short answer is worth nothing.
    account: str = Field(min_length=80, max_length=8000)
    # What the budget should be, in the project's own unit — or null for "leave
    # it". Asking for a number here rather than accepting a shrug is the point:
    # the budget only ever converges on reality if somebody proposes the
    # correction from the side that watched the money go.
    recommend_budget: float | None = Field(default=None, ge=0)


@router.post("/overrun_account")
def overrun_account(
    body: OverrunAccountIn, ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db)
) -> dict:
    """Say what the extra spend bought. The move that did not exist before.

    A project used to have two options when the money ran short: stay under
    the number, or be paused by it. Neither of those is what a person does. A
    person goes over when the work needs it and reports — and then the two of
    them decide whether the number was wrong or the work was.

    Note what this verb deliberately cannot touch. The breakdown is the
    platform's, computed from the ledger; you explain it, you do not restate
    it. And the recommendation is a recommendation: raising the budget is the
    chairman's, because a CEO that could raise its own budget has no budget.
    """
    from yaaf.core.models import BudgetOverrun

    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal(
            "The month's spend is the CEO's to account for. If you have something "
            "the CEO should know about what the work cost, send a suggestion.",
            status_code=403,
        )
    overrun = session.get(BudgetOverrun, body.overrun_id)
    if overrun is None or overrun.project_id != ctx.project.id:
        raise Refusal("No such overrun on this project.", status_code=404)
    if overrun.ceo_account is not None:
        raise Refusal(
            "That overrun already has your account on it. If the picture has "
            "changed materially, say so in the boardroom — rewriting the record "
            "after the fact is how an account stops being one.",
            status_code=409,
        )
    overrun.ceo_account = body.account.strip()
    overrun.accounted_at = utcnow()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="overrun.accounted",
        entity="budget_overrun", entity_id=overrun.id,
        detail={"recommend_budget": body.recommend_budget,
                "account": overrun.ceo_account[:500]},
    ))
    if body.recommend_budget is not None:
        overrun.breakdown = {**(overrun.breakdown or {}),
                             "ceo_recommended_budget": body.recommend_budget}
    return {
        "overrun_id": overrun.id,
        "note": (
            "Filed. It is on his desk with the platform's own breakdown beside it, "
            "and he decides whether the number was wrong or the work was — both are "
            "ordinary answers. Raise the flag as well, so he reads it where he "
            "reads everything else."
        ),
    }


class CycleGoalIn(BaseModel):
    goal: str = Field(min_length=10, max_length=4000)
    # A deadline the world imposes. Optional: many rhythms are just a rhythm.
    due_at: datetime | None = None
    # Where the date came from. Required WITH a date, never without one.
    due_source_kind: str | None = Field(default=None, pattern="^(calendar|data)$")
    due_source_url: str | None = Field(default=None, max_length=2000)
    due_source_note: str | None = Field(default=None, max_length=2000)
    due_source_fetched_at: datetime | None = None
    due_source_sha256: str | None = Field(default=None, max_length=64)


@router.post("/cycle_goal")
def set_cycle_goal(
    body: CycleGoalIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """What this turn of the rhythm is for, and when its work is actually due.

    A deadline the outside world imposes is a FACT, not a promise — the
    gameweek deadline, the carrier's cutoff, the filing date. The rule that the
    CEO cannot write a date governs promises against scope, and this is not
    one: nothing here says the work WILL be done, only when the world stops
    accepting it. So the CEO may record it, and must say where it came from.

    Whether the work will be done by then stays where it belongs — in
    `capacity_forecast`, computed by the same code that decides whether a run
    may start, and reported whether or not it flatters anyone.

    Before this, an Operate cycle had a number and a narrative but no goal and
    no due-by, so both lived in prose in a chat message. Nothing could check
    them, count them, or notice when one was about to be missed.
    """
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal(
            "The cycle's goal is the whole project's, so the CEO sets it. If you "
            "believe this cycle is aimed at the wrong thing, say so to the CEO — a "
            "suggestion survives your run.",
            status_code=403,
        )
    if ctx.project.mode != ProjectMode.OPERATE:
        raise Refusal(
            f"Cycles belong to Operate projects; this one is {ctx.project.mode}. "
            "Deliver promises a date through a commitment, and Explore has a window "
            "rather than a rhythm.",
            status_code=422,
        )
    cycle = policy.current_cycle(session, ctx.project.id)
    if cycle is None:
        raise Refusal("No cycle is open yet; the janitor opens one within a tick.",
                      status_code=409)

    source = None
    if body.due_at is not None:
        if body.due_source_kind is None:
            raise Refusal(
                "A due date needs its provenance. You may record a deadline because a "
                "deadline is a fact you discovered, not a date you wrote — and a fact "
                "cites its source. Pass due_source_kind='data' with the URL you read "
                "it from, or 'calendar' when it follows from the rhythm itself.",
                status_code=422,
            )
        if body.due_source_kind == "data" and not body.due_source_url:
            raise Refusal(
                "due_source_kind='data' means you read this date somewhere. Name the "
                "URL, so the next cycle can check whether it moved.",
                status_code=422,
            )
        source = {
            "kind": body.due_source_kind,
            "url": body.due_source_url,
            "note": body.due_source_note,
            "fetched_at": (body.due_source_fetched_at.isoformat()
                           if body.due_source_fetched_at else None),
            "snapshot_sha256": body.due_source_sha256,
            "recorded_by": ctx.agent.id,
            "recorded_at": utcnow().isoformat(),
        }

    cycle.goal = body.goal.strip()
    if body.due_at is not None:
        cycle.due_at = body.due_at
        cycle.due_source = source

    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="cycle.goal_set",
        entity="cycle", entity_id=cycle.id,
        detail={"goal": cycle.goal[:500],
                "due_at": cycle.due_at.isoformat() if cycle.due_at else None,
                "due_source": cycle.due_source},
    ))

    forecast = None
    if cycle.due_at is not None:
        from yaaf.server.capacity import capacity_forecast

        forecast = capacity_forecast(
            session, ctx.project, effective(session, ctx.project.id), cycle.due_at)

    return {
        "cycle": cycle.ordinal,
        "goal": cycle.goal,
        "due_at": cycle.due_at.isoformat() if cycle.due_at else None,
        "rolls_on_due_at": cycle.due_at is not None,
        "capacity": forecast,
        "note": (
            "Recorded. The cycle now rolls on this date rather than the "
            "constitution's rhythm."
            if cycle.due_at is not None else
            "Recorded. No due date, so this cycle rolls on the constitution's rhythm."
        ) + (
            " The capacity block is the platform's own arithmetic on whether the open "
            "work fits before then — if `feasible` is false, that is a fact to take to "
            "the chairman now, with the numbers and what you propose cutting or raising."
            if forecast is not None else ""
        ),
    }


class CommitmentReset(BaseModel):
    reason: str = Field(min_length=10, max_length=4000)


@router.post("/commitments/reset")
def reset_commitment(
    body: CommitmentReset, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Reset a promise that stopped holding, to the platform's own projection.

    The counterpart of the chairman's Move button, held by whoever
    `commitment_authority` says. It exists because the alternative was worse:
    an autonomous CEO whose date had slipped could cut scope and could finish
    work, but if the honest answer was "this date cannot hold" its only
    remaining move was to file an approval — putting a decision the chairman
    had explicitly delegated back on his desk, with a default-deny clock on it.

    What does NOT change is the rule under the rule: the CEO cannot write a
    date. The new `deliver_by` is the end of the day the platform currently
    projects, computed by the same producer that stamps a proposal. A reset
    therefore buys no slack that the measured pace has not already spent — it
    can only stop a promise from being a number nobody believes.
    """
    cons = effective(session, ctx.project.id)
    commitment, projection = policy.check_commitment_reset(
        session, ctx.project, cons, ctx.agent)
    old = commitment.deliver_by
    projected = projection["projected_done_at"]
    # Day-shaped, exactly as a proposal is: a promise is "by the 30th", never
    # "by 14:07:22", and an end-of-day date cannot read as late one tick after
    # the sliding projection catches up with it.
    commitment.deliver_by = projected.replace(hour=23, minute=59, second=59, microsecond=0)
    resets = policy.commitment_resets(session, commitment) + 1
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="commitment.self_move",
        entity="commitment", entity_id=commitment.id,
        detail={"from": old.isoformat(), "to": commitment.deliver_by.isoformat(),
                "reason": body.reason, "basis": projection.get("basis"),
                "reset_count": resets},
    ))
    # The count travels with the disclosure on purpose. One reset is a project
    # adapting to what it learned; the fourth in a fortnight is a date nobody
    # should believe, and the chairman should not have to count them himself to
    # find that out.
    tally = ("This is the first time this promise has been reset."
             if resets == 1 else
             f"This promise has now been reset {resets} times.")
    chairman_notify(
        session, ctx.project,
        f"CEO reset the delivery date to {commitment.deliver_by:%Y-%m-%d}",
        f"{old:%Y-%m-%d} → {commitment.deliver_by:%Y-%m-%d}\n\nIts reason: {body.reason}"
        f"\n\nThe new date is the platform's own projection ({projection.get('basis')}) "
        "— the CEO cannot write a date, only adopt the arithmetic's. "
        f"{tally}\n\nExercised under commitment_authority: ceo. Move it yourself, cut "
        "scope, or turn the dial back — those buttons never close.",
    )
    # Re-walk the status now, through the same producer the janitor uses, so a
    # freeze lifts (or persists) on the spot rather than on the next tick.
    from yaaf.server import janitor

    janitor.check_commitments(session, ctx.project, cons)
    return {
        "commitment_id": commitment.id,
        "status": commitment.status,
        "was": old.isoformat(),
        "deliver_by": commitment.deliver_by.isoformat(),
        "basis": projection.get("basis"),
        "resets": resets,
        "note": ("Reset to the platform's projection and disclosed to the chairman in "
                 "full. The scope freeze lifts only if the arithmetic now fits inside "
                 "the new date. Resetting is not free: he can see how many times you "
                 "have done it, and a promise reset repeatedly is a plan problem you "
                 "should be naming rather than a date problem you keep solving."),
    }


# ── the review: the CEO's half of the meeting ───────────────────────────────────


class ReviewNarrativeIn(BaseModel):
    narrative: str = Field(min_length=50, max_length=20_000)
    recommended: str | None = Field(default=None, max_length=10_000)


@router.post("/review_narrative")
def file_review_narrative(
    body: ReviewNarrativeIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """The variance narrative, into the open review. Note the shape of the
    verb: the CEO explains the numbers, it does not produce them — the
    platform's snapshot was frozen when the janitor opened the meeting, so
    there is nothing here for a narrative to massage."""
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal(
            "The review narrative is the CEO's. If you have variance to explain, "
            "tell the CEO — your channel for that is a message or a suggestion.",
            status_code=403,
        )
    review = session.execute(
        select(Review).where(Review.project_id == ctx.project.id,
                             Review.status == ReviewStatus.OPEN)
    ).scalar_one_or_none()
    if review is None:
        raise Refusal(
            "No review is open. The janitor opens them on the constitution's "
            "cadence; you will be woken when one is.",
            status_code=409,
        )
    if review.narrative is not None:
        raise Refusal(
            f"Review #{review.number} already has your narrative. The meeting is "
            "with the chairman now — his decisions arrive as notices.",
            status_code=409,
        )
    review.narrative = body.narrative.strip()
    review.recommended = (body.recommended or "").strip() or None
    review.narrative_run_id = ctx.run.id
    review.narrative_filed_at = utcnow()
    chairman_notify(
        session, ctx.project, f"review #{review.number}: the CEO's narrative is in",
        body.narrative[:2000]
        + (f"\n\nRecommends: {review.recommended[:1000]}" if review.recommended else "")
        + "\n\nTake the meeting on the Review tab.",
    )
    return {"review_number": review.number,
            "note": ("Filed. The chairman decides from here — cut, add, reword, "
                     "move the date, raise the budget, or accept the slip. Each "
                     "decision reaches you as a notice with his reason.")}


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
    policy.check_evidence_add(session, ctx.project, body.kind, body.source_url,
                              body.task_id)
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



def _file_approval(session: Session, ctx: RunContext, kind: str, title: str,
                   body: str, amount_usd: float | None = None) -> Approval:
    """ONE way an agent puts something on the chairman's desk: same id, same
    expiry arithmetic, same default-deny clock, whichever lane filed it."""
    cons = effective(session, ctx.project.id)
    row = Approval(
        id=ids.new_id(ids.APPROVAL),
        project_id=ctx.project.id,
        kind=kind,
        requested_by_agent_id=ctx.agent.id,
        title=title,
        body=body,
        amount_usd=amount_usd,
        expires_at=utcnow() + timedelta(days=cons.approval_timeout_days),
    )
    session.add(row)
    return row


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
    # An approval is a blocker on a human's desk with a default-deny clock on
    # it. Filing one for something the dial already put in the CEO's hands is
    # the one way an autonomous project can still stall on its chairman.
    policy.check_approval_request(cons, body.kind)
    row = _file_approval(session, ctx, body.kind, body.title, body.body,
                         body.amount_usd)
    return {
        "id": row.id,
        "expires_at": row.expires_at.isoformat(),
        "note": (
            f"If the chairman does not decide within {cons.approval_timeout_days} days this "
            f"expires as DENIED and you will be woken to re-plan. Frame requests as "
            f"recommendations you would defend, not open questions."
        ),
    }


# ── asks: a question to him that cannot go quiet ────────────────────────────────


class AskIn(BaseModel):
    title: str = Field(min_length=4, max_length=300)
    body: str = Field(min_length=10)
    answer_by_hours: float = Field(gt=0)
    # Long enough that "decide later" does not fit. The value of this field is
    # that it is written before the answer is known; a placeholder would make
    # the whole mechanism ceremonial.
    default_action: str = Field(min_length=20, max_length=2000)


class AskCloseIn(BaseModel):
    ask_id: str
    message_id: str


@router.post("/asks")
def ask_chairman(
    body: AskIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Put a question to the chairman, with the moment you stop waiting.

    This is deliberately not a status, so the reporting cadence never sees it.
    A cadence exists to stop a CEO narrating at him; it was never meant to stop
    one asking him something the next move depends on — and when it did, the
    project sat for six hours on a finished deliverable while his desk said
    nothing was waiting.

    It does not block. Keep working: the whole point of `default_action` is
    that the project has somewhere to go if he says nothing at all.
    """
    from yaaf.core.models import Ask

    cons = effective(session, ctx.project.id)
    policy.check_ask(session, ctx.project, ctx.agent, cons, body.answer_by_hours)
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == ctx.project.id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if boardroom is None:
        raise Refusal("This project has no boardroom.", status_code=404)

    answer_by = utcnow() + timedelta(hours=body.answer_by_hours)
    message = Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=ctx.project.id,
        channel_id=boardroom.id,
        sender_agent_id=ctx.agent.id,
        body=(f"**{body.title}**\n\n{body.body}\n\n"
              f"_Answer by {answer_by.isoformat(timespec='minutes')}. "
              f"If I have not heard from you: {body.default_action}_"),
    )
    session.add(message)
    row = Ask(
        id=ids.new_id(ids.ASK),
        project_id=ctx.project.id,
        asked_by_agent_id=ctx.agent.id,
        title=body.title,
        body=body.body,
        answer_by=answer_by,
        default_action=body.default_action.strip(),
        asked_in_message_id=message.id,
    )
    session.add(row)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="ask.opened",
        entity="ask", entity_id=row.id,
        detail={"title": body.title, "answer_by": answer_by.isoformat(),
                "default_action": row.default_action[:500]},
    ))
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {
        "ask_id": row.id,
        "message_id": message.id,
        "answer_by": answer_by.isoformat(),
        "note": (
            "Asked, and on his desk until he answers. You are not blocked — keep "
            f"working. If he says nothing by {answer_by.isoformat(timespec='minutes')} "
            "you will be woken to do exactly what you just wrote down, and to tell him "
            "you did."
        ),
    }


@router.post("/asks/close")
def close_ask(
    body: AskCloseIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Close an ask by having acted on it and said so in the boardroom.

    Same shape as raising a flag, for the same reason: the row exists to make
    silence visible, so it cannot be cleared by more silence. An ask he
    answered closes when the CEO says what it did with the answer; an ask that
    lapsed closes when the CEO says it took the default it named.
    """
    from yaaf.core.models import Ask, AskStatus
    from yaaf.server import asks as ask_lib

    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Only the CEO speaks to the chairman.", status_code=403)
    ask = session.get(Ask, body.ask_id)
    if ask is None or ask.project_id != ctx.project.id:
        raise Refusal("No such ask.", status_code=404)
    if ask.status == AskStatus.OPEN:
        raise Refusal(
            "That question is still open — he has not answered and its window has not "
            "closed. Closing it now would be deciding on his behalf while still claiming "
            "to have asked. Act on your own authority and say so, or wait.",
            status_code=409,
        )
    if ask.status not in ask_lib.OWED_STATUSES:
        raise Refusal(f"That ask is already {ask.status}.", status_code=409)

    message = session.get(Message, body.message_id)
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == ctx.project.id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if (message is None or boardroom is None or message.channel_id != boardroom.id
            or message.sender_agent_id != ctx.agent.id):
        raise Refusal(
            "message_id must be a boardroom message you posted. The ask closes because "
            "he was told what you did about it; the message is the proof, and without "
            "it this verb would just be a way of marking his question as handled.",
            status_code=422,
        )

    was = ask.status
    ask.status = AskStatus.CLOSED
    ask.closed_at = utcnow()
    ask.closed_in_message_id = message.id
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="ask.closed",
        entity="ask", entity_id=ask.id,
        detail={"was": was, "message_id": message.id},
    ))
    return {"ask_id": ask.id, "status": ask.status,
            "note": "Closed. He knows what happened, which is the whole of the job."}


@router.get("/asks")
def ask_list(ctx: RunContext = Depends(run_auth), session: Session = Depends(db)) -> list[dict]:
    from yaaf.server import asks as ask_lib

    return ask_lib.asks_for_context(session, ctx.project.id, ctx.agent.id)


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


# ── approved writes ─────────────────────────────────────────────────────────────
#
# A data source can name the tools it may be called with (`mcp_servers[].tools`).
# Anything else is refused in the sandbox — and these two endpoints are the only
# way past that refusal, because the rule that lets a call through has to live
# where an agent cannot argue with it.
#
# The shape is one call, one approval. The chairman decides on a rendered call
# with its literal arguments; the platform stores their fingerprint; spending
# checks the fingerprint again and burns the approval. So an approval obtained
# for one change cannot be replayed on a bigger one, or on a second one.


def _arguments_digest(arguments: dict | None) -> str:
    """The fingerprint the chairman's decision is bound to.

    Canonical: sorted keys, no incidental whitespace. Computed HERE and never
    accepted from a caller — a digest an agent supplies is a digest an agent
    chooses.
    """
    canonical = json.dumps(arguments or {}, sort_keys=True, separators=(",", ":"),
                           default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _project_data_source(project, name: str) -> dict | None:
    for source in (project.integrations or {}).get("mcp_servers", []):
        if source.get("name") == name:
            return source
    return None


class DisclosedChange(BaseModel):
    """One before → after, on one thing: a campaign's daily budget, a keyword's bid."""

    entity: str = Field(min_length=1, max_length=200)
    field: str = Field(min_length=1, max_length=80)
    before: str | float | int | bool | None = None
    after: str | float | int | bool | None = None


class DisclosedEvidence(BaseModel):
    """The rows the ask rests on, with what a person needs to reconcile them."""

    columns: list[str] = Field(min_length=1, max_length=30)
    rows: list[list[str | float | int | bool | None]] = Field(default_factory=list, max_length=200)
    period_start: str | None = Field(default=None, max_length=40)
    period_end: str | None = Field(default=None, max_length=40)
    currency: str | None = Field(default=None, max_length=8)
    as_of: str | None = Field(default=None, max_length=40)


class DisclosedEstimate(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=80)
    basis: str = Field(default="", max_length=1000)


class Disclosure(BaseModel):
    """What the chairman needs to decide well. Bounded, because it is stored on
    the approval row and rendered to a person; an agent that wants to show
    ten thousand rows shows the two hundred that matter."""

    category: str | None = Field(default=None, max_length=64)
    change_set: list[DisclosedChange] = Field(default_factory=list, max_length=500)
    scope_counts: dict[str, int] = Field(default_factory=dict)
    evidence: DisclosedEvidence | None = None
    estimate: DisclosedEstimate | None = None
    reversible: bool | None = None
    counter_case: str = Field(default="", max_length=4000)
    spend_affected_usd_month: float | None = None


class DataWriteRequest(BaseModel):
    source: str = Field(min_length=1, max_length=64)
    tool: str = Field(min_length=1, max_length=128)
    arguments: dict = Field(default_factory=dict)
    why: str = Field(default="", max_length=4000)
    # Prose for the chairman, never part of the grant: the digest binds the
    # arguments, and two requests that differ only here spend identically.
    disclosure: Disclosure | None = None


@router.post("/data_writes")
def data_write_request(
    body: DataWriteRequest, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """File ONE call for the chairman to approve, fingerprint and all."""
    source = _project_data_source(ctx.project, body.source)
    if source is None:
        raise Refusal(
            f"{body.source!r} is not one of this project's data sources.", status_code=404)
    allowlist = source.get("tools")
    if allowlist is None:
        raise Refusal(
            f"{body.source}.{body.tool} needs no approval — this source has no allowlist. "
            "Call it with data_fetch.")
    if body.tool in allowlist:
        raise Refusal(
            f"{body.source}.{body.tool} is already yours to call. Use data_fetch; an "
            "approval here would put a decision on the chairman's desk that changes "
            "nothing.")

    cons = effective(session, ctx.project.id)
    rendered = json.dumps(body.arguments or {}, indent=2, sort_keys=True, default=str)
    row = _file_approval(
        session, ctx, ApprovalKind.TOOL_REQUEST,
        f"Run {body.source}.{body.tool} on your account",
        (f"{body.why}\n\n" if body.why else "")
        + f"This would call `{body.tool}` on your connected {body.source} account "
          f"with exactly these arguments:\n\n```json\n"
          f"{rendered[:4000]}\n```\n\n"
          "Approving authorises this one call and nothing else — the arguments above "
          "are part of what you are approving, and a different call needs a new "
          "approval.",
    )
    row.grant = {"source": body.source, "tool": body.tool,
                 "arguments_sha256": _arguments_digest(body.arguments)}
    row.disclosure = body.disclosure.model_dump(mode="json") if body.disclosure else None
    session.flush()
    chairman_notify(
        session, ctx.project,
        f"Approve a change to your {body.source} account?",
        f"{row.title}\n\n{row.body}",
        kind="approval_requested", data={"approval": approval_row(row)},
    )
    return {
        "id": row.id,
        "expires_at": row.expires_at.isoformat(),
        "note": (
            f"Filed. Unanswered within {cons.approval_timeout_days} days this expires as "
            "DENIED and you are woken to re-plan. When it is approved, call data_fetch "
            "with approval_id and the SAME arguments — the approval is bound to them, and "
            "it is good for one call."
        ),
    }


class DataWriteSpend(BaseModel):
    source: str
    tool: str
    arguments: dict = Field(default_factory=dict)


@router.post("/data_writes/{approval_id}/spend")
def data_write_spend(
    approval_id: str, body: DataWriteSpend,
    ctx: RunContext = Depends(run_auth), session: Session = Depends(db),
) -> dict:
    """Burn an approved grant for exactly the call it was approved for.

    Every refusal here is written to be read by the agent that hit it, because
    it arrives as its tool output — it should say what to do next, not merely
    that something was denied.
    """
    approval = session.get(Approval, approval_id)
    if approval is None or approval.project_id != ctx.project.id:
        raise Refusal(f"{approval_id!r} is not an approval on this project.", status_code=404)
    grant = approval.grant or {}
    if not grant:
        raise Refusal(
            f"Approval {approval_id} carries no grant, so it authorises no call. File the "
            "call with data_write_request, which records what is being approved.")
    if approval.status != ApprovalStatus.APPROVED:
        raise Refusal(
            f"Approval {approval_id} is {approval.status}, not approved."
            + (" It expired undecided, which the constitution reads as no — re-plan around"
               " it or file it again with a reason that answers why it went unanswered."
               if approval.status == ApprovalStatus.EXPIRED else ""),
            status_code=409)
    if approval.spent_at is not None:
        raise Refusal(
            f"Approval {approval_id} was already spent at "
            f"{approval.spent_at.isoformat()}. One approval buys one call — file another.",
            status_code=409)
    if grant.get("source") != body.source or grant.get("tool") != body.tool:
        raise Refusal(
            f"Approval {approval_id} authorises {grant.get('source')}.{grant.get('tool')}, "
            f"not {body.source}.{body.tool}.", status_code=409)
    if grant.get("arguments_sha256") != _arguments_digest(body.arguments):
        raise Refusal(
            f"These are not the arguments approval {approval_id} was approved for. The "
            "chairman decided on a specific call, so changing the arguments voids it — "
            "file the call you actually intend with data_write_request.", status_code=409)

    approval.spent_at = utcnow()
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=approval.project_id,
            actor=f"agent:{ctx.agent.id}",
            action="data_write.spend",
            entity="approval",
            entity_id=approval.id,
            detail={"source": body.source, "tool": body.tool,
                    "arguments_sha256": grant.get("arguments_sha256")},
        )
    )
    return {"id": approval.id, "spent": True}


class ObservedEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=80)
    basis: str = Field(default="", max_length=500)
    as_of: str | None = Field(default=None, max_length=40)


class DataWriteOutcome(BaseModel):
    ok: bool
    observed: ObservedEffect | None = None
    note: str = Field(default="", max_length=2000)


@router.post("/data_writes/{approval_id}/outcome")
def data_write_outcome(
    approval_id: str, body: DataWriteOutcome,
    ctx: RunContext = Depends(run_auth), session: Session = Depends(db),
) -> dict:
    """What a spent grant actually did, filed after the fact.

    The disclosure said what the call would do and `spent_at` says it was made;
    this is the only record of what came of it. Refused before the spend: an
    outcome for a call that has not happened is a forecast wearing a receipt.
    """
    approval = session.get(Approval, approval_id)
    if approval is None or approval.project_id != ctx.project.id:
        raise Refusal(f"{approval_id!r} is not an approval on this project.", status_code=404)
    if approval.spent_at is None:
        raise Refusal(
            f"Approval {approval_id} has not been spent, so it has no outcome yet. Make "
            "the call with data_fetch and approval_id first, then file what it did.",
            status_code=409)
    detail = body.model_dump(mode="json")
    detail["filed_at"] = utcnow().isoformat()
    session.add(
        AuditLog(
            id=ids.new_id(ids.AUDIT),
            project_id=approval.project_id,
            actor=f"agent:{ctx.agent.id}",
            action="data_write.outcome",
            entity="approval",
            entity_id=approval.id,
            detail=detail,
        )
    )
    return {"id": approval.id, "outcome": detail}


class SpendIn(BaseModel):
    usd: float = Field(gt=0)
    note: str = Field(min_length=5)
    approval_id: str | None = None


@router.post("/ledger/spend")
def book_spend(
    body: SpendIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Book a real-world spend made with raw tooling. The approval lines are
    the same as the purchase actuator's — they were yours to clear BEFORE
    paying — but the monthly wall is not enforced here: this money already
    moved, and refusing the record would un-report real spending, which is
    worse than any overshoot. An overshoot is booked AND disclosed loudly."""
    cons = effective(session, ctx.project.id)
    approval = session.get(Approval, body.approval_id) if body.approval_id else None
    committed = policy.check_external_spend(session, ctx.project, cons, body.usd,
                                            approval, enforce_wall=False)
    row = LedgerEntry(
        id=ids.new_id(ids.LEDGER),
        project_id=ctx.project.id,
        kind=LedgerKind.EXTERNAL_SPEND,
        usd=body.usd,
        run_id=ctx.run.id,
        note=body.note,
    )
    session.add(row)
    cap = cons.external_monthly_usd_cap
    if cap > 0 and committed + body.usd > cap:
        session.add(AuditLog(
            id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
            actor=f"agent:{ctx.agent.id}", action="spend.over_envelope",
            entity="ledger", entity_id=row.id,
            detail={"usd": body.usd, "committed_after": committed + body.usd,
                    "cap": cap, "note": body.note[:300]},
        ))
        chairman_notify(
            session, ctx.project, "Real-world spend crossed the envelope",
            f"${body.usd:,.2f} was spent with raw tooling and booked, putting the "
            f"month at ${committed + body.usd:,.2f} of the ${cap:,.0f} envelope. "
            "Booked rather than refused — the money had already moved, and an "
            "unrecorded spend is worse than an overshot one. Further purchases are "
            "walled; the spend itself is on the ledger and the audit.",
        )
        return {"id": row.id,
                "warning": (f"Booked, and the month is now over the "
                            f"${cap:,.0f} envelope. The chairman has been told. "
                            "Clear purchases with purchase_begin BEFORE paying — "
                            "that is where the wall can still save you.")}
    return {"id": row.id}


# ── needs and credential delegation ─────────────────────────────────────────────
#
# Credentials are capabilities (CEO Charter, stage D). A capability the
# project LACKS becomes a need: a structured ask that lands where the chairman
# actually is — the boardroom (which the Slack bridge mirrors when connected)
# plus his out-of-band notification — and is fulfilled in one motion on his
# side (value stored, roles scoped, need approved). A capability the project
# HAS is delegated by whoever holds capability_authority: the chairman's
# tickbox by default, the CEO's own verb when the dial grants it.


def _boardroom_system_line(session: Session, project_id: str, body: str) -> None:
    """A platform-authored boardroom line. The body lives in `notify` now — the
    janitor needs the same channel to say a delivery is waiting on the chairman."""
    from yaaf.server.notify import boardroom_line

    boardroom_line(session, project_id, body)


class AccountIn(BaseModel):
    service: str = Field(min_length=2, max_length=200)
    username: str = Field(min_length=1, max_length=200)
    owner: str = Field(default="project", pattern="^(project|chairman)$")
    credential_name: str | None = Field(default=None, max_length=120)
    url: str | None = Field(default=None, max_length=500)
    requirement_id: str | None = None
    note: str | None = Field(default=None, max_length=4000)


@router.post("/accounts")
def account_record(
    body: AccountIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Record an identity the project created in the world. Record-only, and
    REQUIRED discipline rather than machinery: an account nobody wrote down is
    an orphan the owner discovers on a bill. Store the credential as a secret
    (or file it in your assist/need) and link its NAME here — the registry
    links, it never holds."""
    existing = session.execute(
        select(Account).where(Account.project_id == ctx.project.id,
                              Account.service == body.service.strip(),
                              Account.username == body.username.strip())
    ).scalar_one_or_none()
    if existing is not None:
        return {"account_id": existing.id, "duplicate": True,
                "note": "Already on the registry."}
    row = Account(
        id=ids.new_id(ids.ACCOUNT),
        project_id=ctx.project.id,
        service=body.service.strip(),
        username=body.username.strip(),
        owner=body.owner,
        credential_name=body.credential_name,
        url=body.url,
        requirement_id=body.requirement_id,
        note=body.note,
        created_by_agent_id=ctx.agent.id,
    )
    session.add(row)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="account.record",
        entity="account", entity_id=row.id,
        detail={"service": row.service, "username": row.username,
                "owner": row.owner},
    ))
    return {"account_id": row.id, "duplicate": False,
            "note": "On the registry. The chairman sees everything the project "
                    "answers to, which is what makes winding down a reading "
                    "instead of an archaeology."}


@router.get("/accounts")
def accounts_list(
    ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> list[dict]:
    rows = session.execute(
        select(Account).where(Account.project_id == ctx.project.id)
        .order_by(Account.created_at)
    ).scalars()
    return [{"service": a.service, "username": a.username, "owner": a.owner,
             "credential_name": a.credential_name, "url": a.url,
             "note": a.note} for a in rows]


class AssistIn(BaseModel):
    what: str = Field(min_length=10, max_length=2000)
    # Where the human picks up: a URL, "the SMS sent to your phone", a form.
    where: str | None = Field(default=None, max_length=500)


@router.post("/assists")
def assist_request(
    body: AssistIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """The human-assist protocol: a step only a human can do.

    A captcha, a 2FA code, a KYC form, a signature — the platform never tries
    to defeat a security challenge at any autonomy level; it hands it over.
    ANY agent may file one (a worker mid-signup should not relay a captcha
    through the CEO), and it rides the approval machinery: it nags, it
    expires default-deny, and the chairman's answer — the code, the 'done',
    the 'no' — arrives in the decision note, which wakes you. Save your
    session state to the workspace BEFORE ending the run (playwright
    storage_state, cookies, the URL you were on): the workspace persists, so
    the next run resumes where this one stopped."""
    approval = _file_approval(
        session, ctx, "assist", f"Assist: {body.what[:120]}",
        body.what + (f"\nWhere: {body.where}" if body.where else ""))
    _boardroom_system_line(
        session, ctx.project.id,
        f"🤝 ASSIST NEEDED ({ctx.agent.name}): {body.what[:400]}"
        + (f"\nWhere: {body.where}" if body.where else "")
        + "\nAnswer on the Decisions screen — your note travels back verbatim "
          "(a code, a 'done', a 'no') and wakes the agent.")
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="assist.request",
        entity="approval", entity_id=approval.id,
        detail={"what": body.what[:300], "where": body.where},
    ))
    session.flush()
    chairman_notify(
        session, ctx.project, f"Human needed: {body.what[:120]}",
        approval.body + "\n\nAnswer on the Decisions screen — your note travels "
        "back verbatim and wakes the agent. This is the one step autonomy never "
        "takes from you: the platform hands over security challenges, it never "
        "defeats them.",
        kind="approval_requested", data={"approval": approval_row(approval)},
    )
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {
        "assist_id": approval.id,
        "expires_at": approval.expires_at.isoformat(),
        "note": ("Filed and sent to the chairman where he reads. Save your session "
                 "state to the workspace now (cookies, storage state, the URL), "
                 "record what you were doing, and end the run — his answer wakes "
                 "you, and the workspace persists."),
    }


class NeedIn(BaseModel):
    title: str = Field(min_length=4, max_length=200)
    why: str = Field(min_length=10, max_length=4000)
    est_usd_per_month: float | None = Field(default=None, ge=0)
    suggested_secret_name: str | None = Field(default=None, max_length=120)
    roles: list[str] = Field(default_factory=list)


#: Dials a project may put a case for more of. Deliberately only the ones that
#: govern HOW MUCH WORK IT MAY DO — throughput, queue depth, the month's money.
#:
#: The mirror of `directive_file`, which is restricted to rules that *limit what
#: the CEO asks of the chairman*, on the stated grounds that "an agent filing
#: its own constitution is how a project votes itself a bigger budget". That
#: reasoning is right and it left a hole: it made asking impossible as well as
#: taking. This list is the asking half, and nothing on it changes by filing —
#: every one still needs the chairman's signature.
#:
#: Not here, on purpose: everything in Autonomy (who decides what is not
#: capacity), the quality bar, verdict policy and the audit rates. A project
#: arguing for a lower bar on itself is not a capacity request.
REQUESTABLE_CAPACITY = {
    "runs_per_project_per_day",
    "runs_per_project_per_hour",
    "inbox_runs_per_project_per_day",
    "max_concurrent_runs",
    "reserved_governance_runs",
    "queue_ceiling",
    "subtask_ceiling",
    "monthly_usd_cap",
    "monthly_token_cap",
}

#: The one requestable dial that is not this project's to spend. Asking for it
#: has to say so out loud, because the chairman granting it is taking headroom
#: from every other project on the box.
SHARED_CAPACITY = {"max_concurrent_runs"}


class CapacityIn(BaseModel):
    rule: str
    to: float
    # The arithmetic, and it is required. A capacity request without it is a
    # wish, and the difference between this verb and complaining in the
    # boardroom is that this one carries the numbers to decide on.
    buys: str = Field(min_length=20, max_length=2000)
    costs: str = Field(min_length=10, max_length=2000)
    without: str = Field(min_length=20, max_length=2000)


@router.post("/capacity")
def capacity_request(
    body: CapacityIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Put a case to the chairman for more of a capacity dial.

    Every "raise it" instruction in the platform used to point at a verb that
    could only describe the problem. The CEO on the desk that prompted this had
    the diagnosis exactly right — "the platform also rations runs... that's what
    actually paces us now" — and then: "if we ever want genuinely more runs in a
    day, that's a separate dial and I'd come to you with what it buys before
    asking." There was no route by which it could come.

    It does not change the dial. It rides the same desk, clock and default-deny
    as every other approval, and it does not block: the project keeps working at
    the cap it has while he decides, which is the whole reason this is not an
    `owner_decision`.
    """

    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal(
            "Only the CEO argues for the project's capacity. If you are short of "
            "room, say so to the CEO — a suggestion survives your run.",
            status_code=403,
        )
    if body.rule not in REQUESTABLE_CAPACITY:
        raise Refusal(
            f"{body.rule!r} is not a capacity dial. You may put a case for "
            f"{', '.join(sorted(REQUESTABLE_CAPACITY))} — the rules that govern how "
            "much work this project may do. Rules about who decides, what the bar is, "
            "or how often you are audited are the chairman's judgement about the "
            "project, not its throughput, and arguing for a lower bar on yourself is "
            "not a capacity request.",
            status_code=422,
        )
    cons = effective(session, ctx.project.id)
    current = getattr(cons, body.rule)
    if body.to <= current:
        raise Refusal(
            f"{body.rule} is already {current} and you asked for {body.to}. This verb is "
            "for arguing you need MORE room. To hold yourself to less, file it as a "
            "directive — that one you may set yourself.",
            status_code=422,
        )

    shared = body.rule in SHARED_CAPACITY
    detail = [
        f"Raise {body.rule} from {current} to {body.to:g}.",
        f"What it buys: {body.buys}",
        f"What it costs: {body.costs}",
        f"What happens without it: {body.without}",
    ]
    if shared:
        detail.append(
            "NOTE: this dial protects the provider rate limit every project on this "
            "box shares. Granting it takes headroom from the others, which is not "
            "true of the rest of this list."
        )
    approval = _file_approval(
        session, ctx, ApprovalKind.CAPACITY,
        f"Capacity: {body.rule} {current} -> {body.to:g}", "\n".join(detail))
    _boardroom_system_line(
        session, ctx.project.id,
        f"📈 CAPACITY: raise {body.rule} from {current} to {body.to:g}"
        f"{' (shared across the box)' if shared else ''} — {body.buys[:400]}\n"
        f"Decide it on the Decisions screen. Unanswered, it expires to no in "
        f"{cons.approval_timeout_days} days and the cap stays where it is.")
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="capacity.request",
        entity="approval", entity_id=approval.id,
        detail={"rule": body.rule, "from": current, "to": body.to, "shared": shared},
    ))
    session.flush()
    chairman_notify(
        session, ctx.project, f"The project is asking for more {body.rule}",
        f"{body.buys[:300]}",
    )
    return {
        "approval_id": approval.id,
        "rule": body.rule,
        "from": current,
        "to": body.to,
        "note": (
            "Filed, with the arithmetic. You are not blocked and the cap has not "
            "moved: keep working at the number you have. If he says nothing it "
            "expires to no, which is the same as today — so plan for today's cap and "
            "treat a yes as upside."
        ),
    }


@router.post("/needs")
def need_request(
    body: NeedIn, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """File a need: 'I need access to AWS — give me a key.'

    Rides the approval machinery (kind=tool_request: it nags, it expires
    default-deny, the chairman decides), and additionally lands as a boardroom
    line so it reaches him wherever he reads — the dashboard, Slack, any
    connected messenger. Fulfilling it stores the credential and scopes the
    roles in one motion on his side."""
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Needs go to the chairman, and only the CEO speaks to him. "
                      "Tell the CEO what you are missing — a suggestion survives.",
                      status_code=403)
    cons = effective(session, ctx.project.id)
    detail_lines = [body.why]
    if body.suggested_secret_name:
        detail_lines.append(f"Suggested credential name: {body.suggested_secret_name}")
    if body.roles:
        detail_lines.append(f"For roles: {', '.join(body.roles)}")
    if body.est_usd_per_month is not None:
        detail_lines.append(f"Estimated cost: ~${body.est_usd_per_month:,.2f}/month")
    approval = _file_approval(
        session, ctx, "tool_request", f"Need: {body.title}",
        "\n".join(detail_lines), body.est_usd_per_month)
    cost = (f" (~${body.est_usd_per_month:,.0f}/mo)"
            if body.est_usd_per_month is not None else "")
    _boardroom_system_line(
        session, ctx.project.id,
        f"📋 NEED: {body.title}{cost} — {body.why[:400]}\n"
        f"Fulfill it on the Decisions screen (one motion: value stored, "
        f"roles scoped, need approved), or decline it there. Unanswered, "
        f"it expires to no in {cons.approval_timeout_days} days.")
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="need.request",
        entity="approval", entity_id=approval.id,
        detail={"title": body.title, "roles": body.roles,
                "est_usd_per_month": body.est_usd_per_month},
    ))
    session.flush()
    chairman_notify(
        session, ctx.project, f"The project needs: {body.title}",
        "\n".join(detail_lines) + "\n\nFulfill or decline it on the Decisions screen "
        "— fulfilling stores the credential and scopes the roles in one motion.",
        kind="approval_requested", data={"approval": approval_row(approval)},
    )
    notify(session, WAKE_CHANNEL, ctx.project.id)
    return {
        "need_id": approval.id,
        "expires_at": approval.expires_at.isoformat(),
        "note": (f"Filed and sent to the chairman where he reads. Unanswered, it "
                 f"expires to DENIED in {cons.approval_timeout_days} days — plan the "
                 "work that does not depend on it rather than waiting."),
    }


@router.get("/credentials")
def credentials_list(
    ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> list[dict]:
    """Names and scopes only, service credentials only — what the project
    holds and who may use it. Values never ride any agent surface, and the
    billing credential is not listed at all."""
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("The credential roster is the CEO's to read.", status_code=403)
    rows = session.execute(
        select(Secret).where(Secret.project_id == ctx.project.id,
                             Secret.kind == "service").order_by(Secret.name)
    ).scalars()
    return [{"name": s.name, "role_scopes": s.role_scopes or []} for s in rows]


class CredentialScope(BaseModel):
    add_roles: list[str] = Field(default_factory=list)
    remove_roles: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=10)


@router.post("/credentials/{name}/scope")
def credential_scope(
    name: str,
    body: CredentialScope,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    """Delegate a capability: re-scope an existing credential across the
    team's roles — the chairman's tickbox, exercised by the CEO where the
    dial grants it. Values never move; only who receives them at run time.
    Creating a credential is always the chairman's — he holds the values."""
    if ctx.agent.kind != AgentKind.CEO:
        raise Refusal("Delegation, where granted at all, is granted to the CEO.",
                      status_code=403)
    cons = effective(session, ctx.project.id)
    if cons.capability_authority != "ceo":
        raise Refusal(
            "The constitution holds capability_authority = 'chairman': scoping "
            "credentials is his tickbox on this project. File a need, or ask him in "
            "the boardroom — his order executes as a directive. The dial itself is "
            "his amendment to turn, and yours to recommend.",
            status_code=403,
        )
    if not body.add_roles and not body.remove_roles:
        raise Refusal("Nothing to change.", status_code=422)
    if body.add_roles:
        # A grant to a role that does not exist grants nothing while telling
        # everyone it did — the worst kind of success. Which is equally true of
        # a role that exists in the library but is on nobody's team here.
        policy.check_roles_on_project(session, ctx.project.id, body.add_roles)
    row = session.execute(
        select(Secret).where(Secret.project_id == ctx.project.id, Secret.name == name)
    ).scalar_one_or_none()
    if row is None or row.kind != "service":
        raise Refusal(f"No service credential named {name!r} on this project.",
                      status_code=404)
    scopes = list(row.role_scopes or [])
    was = list(scopes)
    for slug in body.add_roles:
        if slug not in scopes:
            scopes.append(slug)
    scopes = [s for s in scopes if s not in body.remove_roles]
    row.role_scopes = scopes
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="credential.scope",
        entity="secret", entity_id=row.id,
        detail={"name": name, "was": was, "now": scopes, "reason": body.reason},
    ))
    chairman_notify(
        session, ctx.project, f"CEO re-scoped credential {name}",
        f"{name}: {', '.join(was) or '(nobody)'} → {', '.join(scopes) or '(nobody)'}\n"
        f"Its reason: {body.reason}\nExercised under capability_authority: ceo. "
        "Effective on each role's next run; reverse it any time in Settings.",
    )
    return {"name": name, "role_scopes": scopes, "was": was,
            "note": "Effective on each granted role's next run. On the record, and "
                    "the chairman has been told."}


# ── the purchase actuator ───────────────────────────────────────────────────────
#
# The convenient path for real-world buying (CEO Charter, stage C). A purchase
# that runs through here gets its bookkeeping for free and atomically: the
# envelope is checked BEFORE money moves, the side-effect row makes a
# crash-retry unable to buy twice, and finishing books the ledger and keeps
# the receipt. It is never the only path — a stored card is a credential like
# any other, usable by any tool the CEO points at a checkout — but a purchase
# made outside it must still be booked with spend_book, against the same
# envelope, and an unbooked purchase is exactly what the spot-audit catches.


class PurchaseBegin(BaseModel):
    vendor: str = Field(min_length=2, max_length=200)
    description: str = Field(min_length=5, max_length=2000)
    usd: float = Field(gt=0)
    idempotency_key: str = Field(min_length=4, max_length=200)
    requirement_id: str | None = None
    approval_id: str | None = None


@router.post("/purchases")
def purchase_begin(
    body: PurchaseBegin, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Clear a purchase against the envelope and record it before paying.

    Call this BEFORE money moves. A refusal here costs nothing; the same
    refusal discovered after paying costs the money and an apology."""
    cons = effective(session, ctx.project.id)
    existing = session.execute(
        select(SideEffect).where(
            SideEffect.project_id == ctx.project.id,
            SideEffect.idempotency_key == body.idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return {
            "purchase_id": existing.id,
            "status": existing.status,
            "duplicate": True,
            "result": existing.result,
            "note": ("This purchase was already recorded (possibly by a prior attempt). "
                     "If status is 'completed', the money already moved — DO NOT pay "
                     "again. If 'recorded', a prior attempt died mid-purchase: check "
                     "with the vendor before retrying."),
        }
    approval = session.get(Approval, body.approval_id) if body.approval_id else None
    committed = policy.check_external_spend(session, ctx.project, cons, body.usd,
                                            approval)
    row = SideEffect(
        id=ids.new_id(ids.SIDE_EFFECT),
        project_id=ctx.project.id,
        run_id=ctx.run.id,
        kind="purchase",
        idempotency_key=body.idempotency_key,
        payload={"vendor": body.vendor, "description": body.description,
                 "usd": body.usd, "requirement_id": body.requirement_id,
                 "approval_id": body.approval_id},
    )
    session.add(row)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="purchase.begin",
        entity="side_effect", entity_id=row.id,
        detail={"vendor": body.vendor, "usd": body.usd,
                "description": body.description[:300]},
    ))
    # `committed` came back from the envelope check — no second ledger scan.
    # This purchase now reserves the envelope too, until finish closes it.
    remaining = (cons.external_monthly_usd_cap - committed - body.usd
                 if cons.external_monthly_usd_cap > 0 else None)
    return {
        "purchase_id": row.id,
        "status": row.status,
        "duplicate": False,
        "cleared_usd": body.usd,
        "envelope_remaining_after": round(remaining, 2) if remaining is not None else None,
        "note": ("Cleared. Pay with whatever tool fits, then call purchase_finish with "
                 "the receipt — the ledger entry is written there, not here, so a "
                 "purchase that falls through costs nothing on the books."),
    }


class PurchaseFinish(BaseModel):
    ok: bool
    # The receipt is the point: what was bought, from whom, for how much, in
    # the vendor's own words. Ten characters of "it worked" is not a receipt.
    receipt: str = Field(min_length=10, max_length=8000)
    external_ref: str | None = Field(default=None, max_length=300)


@router.post("/purchases/{purchase_id}/finish")
def purchase_finish(
    purchase_id: str,
    body: PurchaseFinish,
    ctx: RunContext = Depends(run_auth),
    session: Session = Depends(db),
) -> dict:
    row = session.get(SideEffect, purchase_id)
    if row is None or row.project_id != ctx.project.id or row.kind != "purchase":
        raise Refusal("Unknown purchase.", status_code=404)
    if row.status != SideEffectStatus.RECORDED:
        return {
            "purchase_id": row.id,
            "status": row.status,
            "duplicate": True,
            "note": "This purchase is already finished; the books did not change.",
        }
    usd = float((row.payload or {}).get("usd") or 0)
    if body.ok:
        # Money moved: book it even if the envelope shifted since begin — a
        # refusal here would un-record real spending, which is worse than any
        # overshoot. The overshoot itself shows in the meter and the audit.
        ledger = LedgerEntry(
            id=ids.new_id(ids.LEDGER),
            project_id=ctx.project.id,
            kind=LedgerKind.EXTERNAL_SPEND,
            usd=usd,
            run_id=ctx.run.id,
            note=(f"purchase: {(row.payload or {}).get('vendor', '?')} — "
                  f"{(row.payload or {}).get('description', '')[:200]}"
                  + (f" [ref {body.external_ref}]" if body.external_ref else "")),
        )
        session.add(ledger)
        row.status = SideEffectStatus.COMPLETED
        row.result = {"receipt": body.receipt, "external_ref": body.external_ref,
                      "ledger_id": ledger.id}
    else:
        row.status = SideEffectStatus.FAILED
        row.result = {"receipt": body.receipt, "external_ref": body.external_ref}
    row.completed_at = utcnow()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=ctx.project.id,
        actor=f"agent:{ctx.agent.id}", action="purchase.finish",
        entity="side_effect", entity_id=row.id,
        detail={"ok": body.ok, "usd": usd,
                "vendor": (row.payload or {}).get("vendor"),
                "receipt": body.receipt[:500]},
    ))
    return {"purchase_id": row.id, "status": row.status,
            "booked_usd": usd if body.ok else 0.0}


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
# One read returns a slice, not the file. A deliverable can be 5 MB; a run's
# context cannot. offset pages through the rest, and the sha256 on every
# response is of the WHOLE file — so a verifier reading one page can still
# state exactly which bytes it read a page of.
MAX_ARTIFACT_READ_BYTES = 64 * 1024


class ArtifactPut(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str
    # Requirement ids or keys this deliverable claims to satisfy. A claim,
    # not a verdict — met is still only ever written by the platform.
    satisfies: list[str] = Field(default_factory=list, max_length=20)


def _artifact_key(ctx: RunContext, ref: str) -> str:
    """Resolve what an agent hands us — a key from ``artifact_list``, or a uri it
    read off a message, an evidence row, or a task completion — to a key inside
    THIS project. The store holds every project's deliverables; a run token is
    not a passport to all of them."""
    from yaaf.core.artifacts import is_store_uri, key_for_uri

    ref = (ref or "").strip()
    if not ref:
        raise Refusal("pass ref — an artifact key or the uri that was cited.", 422)
    key = key_for_uri(ref) if is_store_uri(ref) else ref
    # ".." satisfies a prefix check and still resolves out of the project.
    if not key or not key.startswith(f"projects/{ctx.project.id}/") or ".." in key.split("/"):
        raise Refusal(
            f"{ref!r} is not one of this project's artifacts. artifact_list shows "
            "the ones that are.",
            status_code=404,
        )
    return key


@router.post("/artifacts")
def artifact_put(
    body: ArtifactPut, ctx: RunContext = Depends(run_auth), session: Session = Depends(db)
) -> dict:
    """Persist a finished deliverable to the artifact store, AND as a row.

    The workspace persists with the project as shared working state; the
    artifact store is the shipped, versioned record — where finished work goes
    to be served, audited and read by others. The platform picks the location —
    projects/<project>/<run>/
    <filename> — so an agent names a file, never a path. The row is the
    receipt: sha256 and version are computed here, published_at is a real
    timestamp instead of a filesystem mtime a restore would rewrite, and the
    requirements it claims to satisfy are validated against this project."""
    from yaaf.core.artifacts import store
    from yaaf.core.models import Artifact

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
    # Satisfies: keys ("R-03") or ids, resolved and validated — an id from
    # another project or thin air is refused, not stored as decoration.
    satisfied: list[Requirement] = []
    if body.satisfies:
        scope = {r.id: r for r in policy.scope_requirements(session, ctx.project.id)}
        by_key = {r.key: r for r in scope.values()}
        for ref in body.satisfies:
            requirement = scope.get(ref) or by_key.get(ref)
            if requirement is None:
                raise Refusal(
                    f"satisfies names {ref!r}, which is not a requirement of this "
                    "project. Cite real scope or cite nothing — a deliverable that "
                    "claims to satisfy an invented requirement satisfies less than "
                    "one that claims nothing.",
                    status_code=404,
                )
            satisfied.append(requirement)
    elif ctx.run.requirement_id:
        stamped = session.get(Requirement, ctx.run.requirement_id)
        if stamped is not None and stamped.key != policy.PLAN_KEY:
            satisfied.append(stamped)

    key = f"projects/{ctx.project.id}/{ctx.run.id}/{filename}"
    uri = store().put_bytes(key, data)
    digest = hashlib.sha256(data).hexdigest()
    version = 1 + (session.execute(
        select(func.max(Artifact.version)).where(
            Artifact.project_id == ctx.project.id, Artifact.name == filename)
    ).scalar_one() or 0)
    row = Artifact(
        id=ids.new_id(ids.ARTIFACT),
        project_id=ctx.project.id,
        name=filename,
        version=version,
        sha256=digest,
        size_bytes=len(data),
        storage_key=key,
        uri=uri,
        published_by_agent_id=ctx.agent.id if ctx.agent else None,
        run_id=ctx.run.id,
        requirement_ids=[r.id for r in satisfied],
    )
    session.add(row)
    session.flush()
    chairman_notify(
        session, ctx.project, f"deliverable published: {filename} v{version}",
        f"{filename} (v{version}, {len(data)} bytes) is in the artifact store"
        + (f", satisfying {', '.join(r.key for r in satisfied)}." if satisfied else "."),
        kind="deliverable_published",
        data={"deliverable": {"id": row.id, "name": filename, "version": version,
                              "key": key, "size": len(data),
                              "published_at": row.published_at.isoformat() if row.published_at else None,
                              "satisfies": [r.key for r in satisfied]}},
    )
    # The digest is returned at write time so the author can record what it
    # shipped and a later reader can prove the store still holds those bytes.
    return {"uri": uri, "key": key, "size": len(data), "sha256": digest,
            "artifact_id": row.id, "version": version,
            "satisfies": [r.key for r in satisfied]}


@router.get("/artifacts")
def artifact_list(ctx: RunContext = Depends(run_auth)) -> dict:
    """Every deliverable this project has actually shipped. A workspace file is
    what someone was working on; this is what shipped, and the two can differ —
    ``artifact_put`` takes bytes an agent hands it, not a copy of a file."""
    from yaaf.core.artifacts import store

    rows = []
    for entry in store().list_keys(f"projects/{ctx.project.id}/"):
        parts = entry["key"].split("/")
        rows.append({**entry,
                     "run_id": parts[2] if len(parts) > 3 else None,
                     "name": parts[-1]})
    return {"artifacts": rows}


@router.get("/artifacts/content")
def artifact_get(
    ref: str = "", offset: int = 0, limit: int = MAX_ARTIFACT_READ_BYTES,
    ctx: RunContext = Depends(run_auth),
) -> dict:
    """A shipped artifact's bytes, read back out of the store.

    Without this an artifact uri is unfalsifiable: the sandbox mounts only the
    workspace, so the ``file://`` path in a citation resolves to nothing and a
    non-author has no way to check what a deliverable contains.
    """
    from yaaf.core.artifacts import store

    if offset < 0 or limit < 1:
        raise Refusal("offset must be 0 or more and limit at least 1.", status_code=422)
    key = _artifact_key(ctx, ref)
    try:
        data = store().get_bytes(key)
    except (FileNotFoundError, ValueError, OSError):
        # Deliberately an answer, not a Refusal. Every Refusal counts against
        # the run's refusal cap and revokes the token at it — so refusing here
        # would spend a verifier's budget on precisely the finding it was sent
        # to produce, and kill the run that checked most carefully.
        return {
            "key": key, "exists": False, "size": 0,
            "detail": "nothing is stored under this key. A uri cited in a message, "
                      "an evidence row, or a task completion is a claim that bytes "
                      "were shipped — not proof that they were.",
        }

    chunk = data[offset:offset + min(limit, MAX_ARTIFACT_READ_BYTES)]
    out = {
        "key": key,
        "exists": True,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),  # of the whole file, not the slice
        "offset": offset,
        "returned": len(chunk),
        "truncated": offset + len(chunk) < len(data),
    }
    try:
        data.decode()
    except UnicodeDecodeError:
        out["content_base64"] = base64.b64encode(chunk).decode()
    else:
        # Text stays text even when the slice lands mid-character: one
        # replacement char at the seam beats base64-ing a readable file.
        out["text"] = chunk.decode(errors="replace")
    return out
