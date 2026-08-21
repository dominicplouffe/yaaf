"""The agent-facing API — lana's plan.py/memory.py/slack.py, hardened.

Every endpoint authenticates a per-run token and enforces the constitution
through :mod:`yaaf.server.policy`. An agent cannot be talked out of rules it
never held: they live here, server-side, not in its prompt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import effective, propose_amendment
from yaaf.core.db import notify
from yaaf.core.models import (
    Account,
    AgentKind,
    Approval,
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
    # whether a row exists in a company it cannot see.
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


class RequirementPropose(BaseModel):
    statement: str = Field(min_length=10, max_length=2000)
    acceptance_test: dict
    stage: int = Field(default=1, ge=1, le=50)
    est_runs: int | None = Field(default=None, ge=1, le=10_000)
    depends_on: list[str] = Field(default_factory=list)


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
    # scope is refused before affordability is even asked.
    policy.check_scope_within_commitment(session, ctx.project, cons)
    policy.check_plan_affordable(session, ctx.project, cons, body.est_runs or 0)
    cycle = policy.current_cycle(session, ctx.project.id)
    requirement = Requirement(
        id=ids.new_id(ids.REQUIREMENT),
        project_id=ctx.project.id,
        cycle_id=cycle.id if cycle is not None else None,
        key=policy.next_requirement_key(session, ctx.project.id),
        statement=body.statement.strip(),
        acceptance_test=body.acceptance_test,
        status=RequirementStatus.PROPOSED,
        stage=body.stage,
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
        "note": ("Proposed, not scope. The chairman accepts requirements; until he does, "
                 "no work may be created for this one."),
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
# company LACKS becomes a need: a structured ask that lands where the chairman
# actually is — the boardroom (which the Slack bridge mirrors when connected)
# plus his out-of-band notification — and is fulfilled in one motion on his
# side (value stored, roles scoped, need approved). A capability the company
# HAS is delegated by whoever holds capability_authority: the chairman's
# tickbox by default, the CEO's own verb when the dial grants it.


def _boardroom_system_line(session: Session, project_id: str, body: str) -> None:
    """A platform-authored boardroom line: no sender, so it spends no cadence
    and reads the same in the dashboard and in Slack (the bridge mirrors the
    boardroom) — the one channel that reaches the chairman wherever he is."""
    boardroom = session.execute(
        select(Channel).where(Channel.project_id == project_id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalar_one_or_none()
    if boardroom is not None:
        session.add(Message(
            id=ids.new_id(ids.MESSAGE),
            project_id=project_id,
            channel_id=boardroom.id,
            body=body,
        ))


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
    """Record an identity the company created in the world. Record-only, and
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
            "note": "On the registry. The chairman sees everything the company "
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
    chairman_notify(
        session, ctx.project, f"Human needed: {body.what[:120]}",
        approval.body + "\n\nAnswer on the Decisions screen — your note travels "
        "back verbatim and wakes the agent. This is the one step autonomy never "
        "takes from you: the platform hands over security challenges, it never "
        "defeats them.",
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
    chairman_notify(
        session, ctx.project, f"The company needs: {body.title}",
        "\n".join(detail_lines) + "\n\nFulfill or decline it on the Decisions screen "
        "— fulfilling stores the credential and scopes the roles in one motion.",
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
    """Names and scopes only, service credentials only — what the company
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
        # everyone it did — the worst kind of success.
        policy.check_roles_exist(session, body.add_roles)
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
