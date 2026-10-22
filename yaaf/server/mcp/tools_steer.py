"""The chairman's levers, reachable by a model — under three constraints.

Every tool here calls the same `admin` handler the browser calls, so the rules
each verb enforces are enforced identically: a refusal in the dashboard is a
refusal here, and neither can be argued with. What this layer adds is the three
things a browser does not need.

**A written reason.** Most of these already demand one; the rest demand one
here, because a decision a model made on the chairman's behalf is exactly the
kind you will want explained at the next review.

**A preview first.** Every irreversible verb is two-phase (`confirm.py`), so a
model cannot be talked into a decision inside a single tool call by something
it read in a channel.

**A name that survives.** Writes are attributed `user:<id> via mcp:<client>`
for good.

One verb is deliberately missing. `POST /admin/approvals/{id}/fulfill` supplies
a credential *value* to satisfy an agent's need — it would mean pasting a
secret into a model's context to fulfil a request an agent made. That stays in
the browser, where the chairman is the only reader.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Agent,
    Approval,
    AuditLog,
    Commitment,
    Evidence,
    Flag,
    HeadsUp,
    Message,
    Requirement,
    Review,
    Task,
    Team,
)
from yaaf.server.api import admin
from yaaf.server.mcp import confirm
from yaaf.server.mcp.auth import Principal, ToolRefusal, resolve_project
from yaaf.server.mcp.registry import register, required
from yaaf.server.mcp.tools_read import DETAIL_ARG, PROJECT_ARG, _owned

REASON_ARG = {"type": "string",
              "description": "Why. Recorded, audited, and read back at the next review"}
CONFIRM_ARG = {"type": "string",
               "description": "The token from this tool's own preview. Omit to preview"}


def _schema(required_args: list[str], **properties) -> dict:
    return {
        "type": "object",
        "properties": {"project": PROJECT_ARG, "confirm": CONFIRM_ARG,
                       "detail": DETAIL_ARG, **properties},
        "required": ["project", *required_args],
        "additionalProperties": False,
    }


def _call(principal: Principal, fn, **kwargs):
    """Run an admin handler as this client, with its refusals passed through."""
    with confirm.attributed(principal):
        try:
            return fn(**kwargs)
        except HTTPException as exc:
            raise ToolRefusal(str(exc.detail)) from None


def _record(session: Session, principal: Principal, project_id: str | None,
            tool: str, arguments: dict, outcome: dict) -> dict:
    """One audit row per steer call, whatever the handler underneath records.

    Belt as well as braces: the handlers write their own rows, but the claim
    that *every* write through this surface is attributable cannot rest on
    each of them remembering to. This row is written here, once, for all of
    them, and `test_mcp_attribution.py` is what keeps it honest.
    """
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT),
        project_id=project_id,
        actor=confirm.actor(principal),
        action=f"mcp.{tool}",
        entity="mcp_client",
        entity_id=principal.client_id,
        detail={"arguments": {k: v for k, v in arguments.items() if k != "confirm"},
                "outcome": outcome},
    ))
    return outcome


def _reason(arguments: dict) -> str:
    reason = required(arguments, "reason")
    if len(reason) < 5:
        raise ToolRefusal("a reason of five characters is not a reason")
    return reason


def _when(value) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ToolRefusal("deliver_by must be an ISO 8601 datetime") from None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# ── the boardroom ───────────────────────────────────────────────────────────────


@register(
    "boardroom_say",
    scope="steer",
    description=(
        "Speak to the CEO in the boardroom — the chairman's only channel, and the "
        "highest-value write here. Wakes the CEO, which costs a metered run against "
        "the project's budget."
    ),
    input_schema={
        "type": "object",
        "properties": {"project": PROJECT_ARG,
                       "body": {"type": "string", "description": "What to say"},
                       "thread_id": {"type": "string", "description": "Reply in a thread"}},
        "required": ["project", "body"],
        "additionalProperties": False,
    },
)
def boardroom_say(principal: Principal, session: Session, arguments: dict) -> dict:
    """Not confirm-gated, deliberately: speech is reversible, and a preview step
    on every sentence would make the one tool a chairman uses constantly cost
    two calls. What it says is recorded either way."""
    project = resolve_project(session, principal, required(arguments, "project"))
    body = required(arguments, "body")
    result = _call(principal, admin.boardroom_post, project_id=project.id,
                   body=admin.BoardroomPost(body=body, thread_id=arguments.get("thread_id")),
                   session=session)
    return _record(session, principal, project.id, "boardroom_say",
                   {"project": project.slug, "body": body},
                   {"posted": result["id"], "project": project.slug})


# ── approvals ───────────────────────────────────────────────────────────────────


@register(
    "approval_decide",
    scope="steer",
    description=(
        "Approve or deny a pending approval. Approving a capacity request raises the "
        "dial at once, to `to` if given. A note is held until the CEO answers it. "
        "Fulfilling a credential need is not here "
        "on purpose — that means handing over a secret value, and it stays in the "
        "browser where the chairman is the only reader."
    ),
    input_schema=_schema(["approval", "approve"],
                         approval={"type": "string", "description": "Approval id"},
                         approve={"type": "boolean"},
                         note={"type": "string"},
                         to={"type": "number",
                             "description": "capacity only: the number to raise it to"}),
)
def approval_decide(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    approval = _owned(session, project, Approval,
                      str(arguments.get("approval") or ""), "approval")
    approve = arguments.get("approve")
    if not isinstance(approve, bool):
        raise ToolRefusal("`approve` must be true or false")

    preview = confirm.gate(session, principal, "approval_decide", arguments, {
        "verb": "approve" if approve else "deny",
        "approval": approval.id, "title": approval.title,
        "amount_usd": approval.amount_usd, "project": project.slug,
    })
    if preview:
        return preview

    outcome = _call(principal, admin.decide_approval, approval_id=approval.id,
                    body=admin.ApprovalDecision(
                        approve=approve, note=str(arguments.get("note") or ""),
                        to=arguments.get("to") if isinstance(arguments.get("to"),
                                                             (int, float)) else None),
                    session=session)
    return _record(session, principal, project.id, "approval_decide", arguments, outcome)


# ── scope ───────────────────────────────────────────────────────────────────────


REQUIREMENT_VERBS = ("accept", "cut", "verdict", "reopen", "restage", "reestimate",
                     "reword", "unsequence")


@register(
    "requirement_decide",
    scope="steer",
    description=(
        "The scope verbs: accept a proposed requirement, cut one, judge an unverifiable "
        "close (verdict), reopen, restage, reestimate, reword, or unsequence a "
        "dependency. Every one needs a reason and a confirmation."
    ),
    input_schema=_schema(["verb"],
                         verb={"type": "string", "enum": list(REQUIREMENT_VERBS)},
                         requirement={"type": "string", "description": "Requirement id"},
                         reason=REASON_ARG,
                         ok={"type": "boolean", "description": "verdict: was it met"},
                         stage={"type": "integer"},
                         est_runs={"type": "integer"},
                         statement={"type": "string", "description": "reword: the new text"},
                         from_requirement={"type": "string", "description": "unsequence"},
                         to_requirement={"type": "string", "description": "unsequence"}),
)
def requirement_decide(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    verb = required(arguments, "verb")
    if verb not in REQUIREMENT_VERBS:
        raise ToolRefusal(f"unknown verb {verb!r}")

    if verb == "unsequence":
        source = _owned(session, project, Requirement,
                        str(arguments.get("from_requirement") or ""), "requirement")
        target = _owned(session, project, Requirement,
                        str(arguments.get("to_requirement") or ""), "requirement")
        reason = _reason(arguments)
        preview = confirm.gate(session, principal, "requirement_decide", arguments, {
            "verb": verb, "from": source.key, "to": target.key, "project": project.slug})
        if preview:
            return preview
        outcome = _call(principal, admin.remove_requirement_edge,
                        body=admin.EdgeRemove(from_requirement_id=source.id,
                                              to_requirement_id=target.id, reason=reason),
                        session=session)
        return _record(session, principal, project.id, "requirement_decide",
                       arguments, outcome)

    row = _owned(session, project, Requirement,
                 str(arguments.get("requirement") or ""), "requirement")
    preview = confirm.gate(session, principal, "requirement_decide", arguments, {
        "verb": verb, "requirement": row.key, "statement": row.statement,
        "status": row.status, "project": project.slug,
        # Said out loud in the preview, because it is the one verb on this
        # surface that writes "met" on work no script could check.
        **({"warning": "This is the platform writing a verdict on an unverifiable "
                       "close. Nothing else on this surface decides truth."}
           if verb == "verdict" else {}),
    })
    if preview:
        return preview

    handler, body = {
        "accept": (admin.accept_requirement, lambda: admin.RequirementDecision(
            stage=arguments.get("stage"), note=str(arguments.get("reason") or ""))),
        "cut": (admin.cut_requirement, lambda: admin.RequirementCut(reason=_reason(arguments))),
        "verdict": (admin.requirement_verdict, lambda: admin.RequirementVerdict(
            ok=bool(arguments.get("ok")), note=_reason(arguments))),
        "reopen": (admin.reopen_requirement,
                   lambda: admin.RequirementReopen(reason=_reason(arguments))),
        "restage": (admin.restage_requirement, lambda: admin.RequirementRestage(
            stage=int(arguments.get("stage") or 0), reason=_reason(arguments))),
        "reestimate": (admin.reestimate_requirement, lambda: admin.RequirementReestimate(
            est_runs=int(arguments.get("est_runs") or 0), reason=_reason(arguments))),
        "reword": (admin.reword_requirement, lambda: admin.RequirementReword(
            statement=str(arguments.get("statement") or ""), reason=_reason(arguments))),
    }[verb]
    outcome = _call(principal, handler, requirement_id=row.id, body=body(), session=session)
    return _record(session, principal, project.id, "requirement_decide", arguments, outcome)


# ── the promise ─────────────────────────────────────────────────────────────────


@register(
    "commitment_decide",
    scope="steer",
    description=(
        "Sign, decline or move the delivery date. Accepting adopts the platform's own "
        "projection; moving is the chairman's alone, and needs a reason."
    ),
    input_schema=_schema(["verb"],
                         verb={"type": "string", "enum": ["accept", "decline", "move"]},
                         commitment={"type": "string", "description": "Commitment id"},
                         deliver_by={"type": "string", "description": "ISO 8601, for move"},
                         reason=REASON_ARG),
)
def commitment_decide(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    row = _owned(session, project, Commitment,
                 str(arguments.get("commitment") or ""), "commitment")
    verb = required(arguments, "verb")

    preview = confirm.gate(session, principal, "commitment_decide", arguments, {
        "verb": verb, "commitment": row.id, "project": project.slug,
        "currently": row.deliver_by.isoformat() if row.deliver_by else None,
        "status": row.status,
        **({"moving_to": str(arguments.get("deliver_by"))} if verb == "move" else {}),
    })
    if preview:
        return preview

    if verb == "accept":
        body = admin.CommitmentAccept(
            deliver_by=_when(arguments["deliver_by"]) if arguments.get("deliver_by") else None,
            note=str(arguments.get("reason") or ""))
        outcome = _call(principal, admin.accept_commitment, commitment_id=row.id,
                        body=body, session=session)
    elif verb == "decline":
        outcome = _call(principal, admin.decline_commitment, commitment_id=row.id,
                        body=admin.CommitmentDecline(reason=_reason(arguments)),
                        session=session)
    elif verb == "move":
        outcome = _call(principal, admin.move_commitment, commitment_id=row.id,
                        body=admin.CommitmentMove(deliver_by=_when(arguments.get("deliver_by")),
                                                  reason=_reason(arguments)),
                        session=session)
    else:
        raise ToolRefusal(f"unknown verb {verb!r}")
    return _record(session, principal, project.id, "commitment_decide", arguments, outcome)


# ── review ──────────────────────────────────────────────────────────────────────


@register(
    "review_decide",
    scope="steer",
    description=(
        "Work an open review: pull a lever (cut, add, reword, move_date, raise_budget, "
        "accept_slip) or close it. The two honest ways out of a commitment at risk."
    ),
    input_schema=_schema(["verb"],
                         verb={"type": "string", "enum": ["lever", "close"]},
                         review={"type": "string", "description": "Review id"},
                         kind={"type": "string",
                               "enum": ["cut", "add", "reword", "move_date",
                                        "raise_budget", "accept_slip"]},
                         requirement={"type": "string"},
                         statement={"type": "string"},
                         stage={"type": "integer"},
                         est_runs={"type": "integer"},
                         deliver_by={"type": "string", "description": "ISO 8601"},
                         amount={"type": "number"},
                         reason=REASON_ARG),
)
def review_decide(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    row = _owned(session, project, Review, str(arguments.get("review") or ""), "review")
    verb = required(arguments, "verb")

    preview = confirm.gate(session, principal, "review_decide", arguments, {
        "verb": verb, "review": row.id, "project": project.slug,
        "lever": arguments.get("kind"), "status": row.status})
    if preview:
        return preview

    if verb == "close":
        outcome = _call(principal, admin.close_review, review_id=row.id,
                        body=admin.ReviewClose(note=str(arguments.get("reason") or "")),
                        session=session)
    elif verb == "lever":
        requirement = arguments.get("requirement")
        if requirement:
            requirement = _owned(session, project, Requirement,
                                 str(requirement), "requirement").id
        outcome = _call(principal, admin.pull_review_lever, review_id=row.id,
                        body=admin.ReviewLeverIn(
                            kind=required(arguments, "kind"), reason=_reason(arguments),
                            requirement_id=requirement,
                            statement=arguments.get("statement"),
                            stage=int(arguments.get("stage") or 1),
                            est_runs=arguments.get("est_runs"),
                            deliver_by=(_when(arguments["deliver_by"])
                                        if arguments.get("deliver_by") else None),
                            amount=arguments.get("amount")),
                        session=session)
    else:
        raise ToolRefusal(f"unknown verb {verb!r}")
    return _record(session, principal, project.id, "review_decide", arguments, outcome)


# ── proof ───────────────────────────────────────────────────────────────────────


@register(
    "audit_decide",
    scope="steer",
    description=(
        "Record your verdict on a spot check: a sampled item, one evidence row, or one "
        "completed task. This is the chairman grading the work, so it is never "
        "self-graded by the party that did it."
    ),
    input_schema=_schema(["verb", "ok"],
                         verb={"type": "string", "enum": ["sample", "evidence", "task"]},
                         id={"type": "string", "description": "The sample, evidence or task id"},
                         ok={"type": "boolean"},
                         reason=REASON_ARG),
)
def audit_decide(principal: Principal, session: Session, arguments: dict) -> dict:
    from yaaf.core.models import AuditSample

    project = resolve_project(session, principal, required(arguments, "project"))
    verb = required(arguments, "verb")
    model, handler, body_class, key = {
        "sample": (AuditSample, admin.decide_audit_sample, admin.AuditSampleDecision,
                   "sample_id"),
        "evidence": (Evidence, admin.audit_evidence, admin.EvidenceAudit, "evidence_id"),
        "task": (Task, admin.audit_task, admin.TaskAudit, "task_id"),
    }[verb]
    row = _owned(session, project, model, str(arguments.get("id") or ""), verb)
    ok = arguments.get("ok")
    if not isinstance(ok, bool):
        raise ToolRefusal("`ok` must be true or false")

    preview = confirm.gate(session, principal, "audit_decide", arguments, {
        "verb": verb, "id": row.id, "verdict": "ok" if ok else "not ok",
        "project": project.slug})
    if preview:
        return preview

    outcome = _call(principal, handler, **{key: row.id},
                    body=body_class(ok=ok, note=str(arguments.get("reason") or "")),
                    session=session)
    return _record(session, principal, project.id, "audit_decide", arguments, outcome)


@register(
    "flag_ack",
    scope="steer",
    description=(
        "Answer a flag the CEO raised: the platform opened it from arithmetic, the CEO "
        "brought it to the boardroom with a proposal, and this is you saying you have read "
        "it and what you think. A flag whose condition was a moment — a missed cycle, a "
        "month over budget — closes here, because nothing else will ever close it. One "
        "whose condition is still true stays open under your note: what clears it is the "
        "arithmetic turning."
    ),
    input_schema=_schema(["flag"],
                         flag={"type": "string", "description": "The flag id"},
                         reason=REASON_ARG),
)
def flag_ack(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    flag = _owned(session, project, Flag, str(arguments.get("flag") or ""), "flag")

    preview = confirm.gate(session, principal, "flag_ack", arguments, {
        "flag": flag.id, "kind": flag.kind, "project": project.slug})
    if preview:
        return preview

    outcome = _call(principal, admin.acknowledge_flag, flag_id=flag.id,
                    body=admin.FlagAck(note=str(arguments.get("reason") or "")),
                    session=session)
    return _record(session, principal, project.id, "flag_ack", arguments, outcome)


@register(
    "heads_up_ack",
    scope="steer",
    description=(
        "Mark noted something the chairman asked to be told — a heads-up the CEO sent "
        "because a standing order in the brief says to. Nothing waits on it. A note, if "
        "given, is held until the CEO answers it."
    ),
    input_schema=_schema(["heads_up"],
                         heads_up={"type": "string", "description": "The heads-up id"},
                         reason=REASON_ARG),
)
def heads_up_ack(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    row = _owned(session, project, HeadsUp, str(arguments.get("heads_up") or ""), "heads-up")

    preview = confirm.gate(session, principal, "heads_up_ack", arguments, {
        "heads_up": row.id, "title": row.title, "project": project.slug})
    if preview:
        return preview

    outcome = _call(principal, admin.acknowledge_heads_up, heads_up_id=row.id,
                    body=admin.HeadsUpAck(note=str(arguments.get("reason") or "")),
                    session=session)
    return _record(session, principal, project.id, "heads_up_ack", arguments, outcome)


# ── lifecycle ───────────────────────────────────────────────────────────────────


LIFECYCLE_VERBS = ("start", "pause", "stop", "complete", "pause_agent", "resume_agent",
                   "stand_down", "recall", "set_schedule", "unfreeze_thread",
                   "briefing_seen")


@register(
    "lifecycle",
    scope="steer",
    description=(
        "Run the project's switches: start, pause, stop or complete it, pause or "
        "resume one agent, stand a whole team down or recall it, set an agent's daily "
        "wake, unfreeze a frozen thread, or mark a briefing read. "
        "stand_down/recall are the STAFFING verbs and take `team`: a role whose job is "
        "finished stops costing wake-ups, keeps its channel and history, and comes "
        "back whole. pause_agent is the different thing — an intervention on one "
        "person, which recall deliberately will not undo. "
        "Everything but briefing_seen needs a confirmation."
    ),
    input_schema=_schema(["verb"],
                         verb={"type": "string", "enum": list(LIFECYCLE_VERBS)},
                         agent={"type": "string", "description": "Agent id, for agent verbs"},
                         team={"type": "string",
                               "description": "Team id, for stand_down and recall"},
                         message={"type": "string", "description": "unfreeze_thread: message id"},
                         daily_at={"type": "string", "description": "set_schedule: HH:MM, or empty"},
                         reason=REASON_ARG),
)
def lifecycle(principal: Principal, session: Session, arguments: dict) -> dict:
    project = resolve_project(session, principal, required(arguments, "project"))
    verb = required(arguments, "verb")
    if verb not in LIFECYCLE_VERBS:
        raise ToolRefusal(f"unknown verb {verb!r}")
    reason = str(arguments.get("reason") or "")

    if verb != "briefing_seen":
        preview = confirm.gate(session, principal, "lifecycle", arguments, {
            "verb": verb, "project": project.slug, "status_now": project.status,
            "agent": arguments.get("agent"), "team": arguments.get("team"),
            "reason": reason})
        if preview:
            return preview

    if verb == "start":
        outcome = _call(principal, admin.start_project, project_id=project.id, session=session)
    elif verb == "pause":
        outcome = _call(principal, admin.pause_project, project_id=project.id,
                        body=admin.LifecycleReason(reason=reason), session=session)
    elif verb == "stop":
        outcome = _call(principal, admin.stop_project, project_id=project.id,
                        body=admin.LifecycleReason(reason=reason), session=session)
    elif verb == "complete":
        outcome = _call(principal, admin.complete_project, project_id=project.id,
                        session=session)
    elif verb in ("pause_agent", "resume_agent"):
        agent = _owned(session, project, Agent, str(arguments.get("agent") or ""), "agent")
        outcome = _call(principal, admin.pause_agent, agent_id=agent.id,
                        body=admin.AgentPause(paused=verb == "pause_agent"), session=session)
    elif verb in ("stand_down", "recall"):
        team = _owned(session, project, Team, str(arguments.get("team") or ""), "team")
        fn = admin.stand_down_team if verb == "stand_down" else admin.recall_team
        outcome = _call(principal, fn, team_id=team.id,
                        body=admin.TeamStandDown(
                            reason=reason or "No reason given by the chairman."),
                        session=session)
    elif verb == "set_schedule":
        agent = _owned(session, project, Agent, str(arguments.get("agent") or ""), "agent")
        outcome = _call(principal, admin.set_agent_schedule, agent_id=agent.id,
                        body=admin.AgentSchedule(daily_at=arguments.get("daily_at") or None),
                        session=session)
    elif verb == "unfreeze_thread":
        message = _owned(session, project, Message,
                         str(arguments.get("message") or ""), "message")
        outcome = _call(principal, admin.unfreeze_thread, message_id=message.id,
                        session=session)
    else:
        outcome = _call(principal, admin.briefing_seen, project_id=project.id,
                        body=None, session=session)
    return _record(session, principal, project.id, "lifecycle", arguments, outcome)
