"""Read-only observation API — the chairman's omniscience.

The chairman *speaks* only in the boardroom, but *reads* everything: every
channel, task, run, ledger row, and a random spot-audit sample of evidence and
completions. In M1 this backs the CLI; the SPA consumes the same endpoints."""

from __future__ import annotations

import hashlib
import random
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from yaaf.core import artifacts
from yaaf.core.constitution import effective, tempo_for, tempo_name
from yaaf.core.models import (
    Agent,
    AgentStatus,
    Approval,
    ApprovalStatus,
    Artifact,
    AuditLog,
    AuditSample,
    AuditSampleStatus,
    Channel,
    ChannelKind,
    Commitment,
    Evidence,
    EvidenceKind,
    Hypothesis,
    LedgerEntry,
    LedgerKind,
    Memory,
    MemoryScope,
    Message,
    Project,
    ProjectMode,
    ProjectStatus,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Review,
    ReviewStatus,
    Role,
    RoleVersion,
    Run,
    RunKind,
    RunQueue,
    RunStatus,
    Secret,
    Suggestion,
    Task,
    TaskEvent,
    TaskStatus,
    Team,
    User,
    VerificationStatus,
    utcnow,
)
from yaaf.runtimes.providers import DEFAULT_PROVIDER, PROVIDERS
from yaaf.server import (
    activity,
    agenda,
    authority,
    briefing,
    file_index,
    metrics,
    policy,
    typing_state,
)
from yaaf.server import asks as ask_lib
from yaaf.server.briefing import reading_member
from yaaf.server.deps import db
from yaaf.server.dispatcher import (
    MODEL_RUN_KINDS,
    activity_state,
    chat_session_live,
    day_start,
    inbox_runs_used,
    unread_messages_for,
)
from yaaf.server.identity import Person, person_auth, signed_in
from yaaf.server.receipt import receipt as receipt_projection
from yaaf.server.rows import approval_row, approval_undo, chairman_time, requirement_name
from yaaf.server.status_line import (
    FAILING_AFTER,
    SPINNING_DAYS,
    SPINNING_RUNS,
    status_line,
)
from yaaf.server.unread import boardroom_unread
from yaaf.server.velocity import (
    commitment_projection,
    countable_scope,
    project_velocity,
    standing_record,
)

router = APIRouter(dependencies=[Depends(signed_in)])


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


def standing(session: Session, person: Person, project: Project) -> dict:
    """What the reader is on this project, for the screen to say and to shape
    its controls by. The server refuses regardless; this is so the page does
    not offer a button whose only outcome is a 403."""
    role = authority.role_of(session, person.id, project)
    level = authority.person_level(session, person, project) or 0
    return {"my_role": role, "can_steer": level >= authority.Level.STEER,
            "can_chair": level >= authority.Level.CHAIR}


def _reachable(session: Session, person: Person | object) -> list[Project]:
    """What this reader may open. `person` is the dependency's answer over HTTP;
    the MCP tools call these rollups as plain functions, where it is still the
    unresolved default, and the reader is whoever that call is bound to — whose
    payload the tool then narrows again to their memberships."""
    reader = person if isinstance(person, Person) else authority.acting_person(session)
    if reader is None:
        return []
    return authority.reachable(session, reader.id, reader.is_admin)


@router.get("/projects", dependencies=[authority.SIGNED_IN])
def projects(session: Session = Depends(db),
             person: Person = Depends(person_auth)) -> list[dict]:
    """The projects this person may open — every one, for the instance admin."""
    return [
        {"id": p.id, "slug": p.slug, "name": p.name, "status": p.status, "goal": p.goal,
         **standing(session, person, p)}
        for p in _reachable(session, person)
    ]


def _awaiting_chairman(session: Session, project: Project, cons) -> dict:
    """The desk's view of policy.awaiting_chairman — same query, shaped for the
    wire. The definition itself lives in policy so the janitor's nags and this
    page can never disagree about what is waiting."""
    waiting = policy.awaiting_chairman(session, project, cons)
    commitment = waiting["commitment"]
    return {
        "plan": len(waiting["plan"]),
        "verdicts": [{"key": r.key, "statement": r.statement, "name": requirement_name(r)}
                     for r in waiting["verdicts"]],
        "commitment": None if commitment is None else {
            "id": commitment.id,
            "deliver_by": commitment.deliver_by.isoformat(),
            "scope_count": len(commitment.scope_snapshot or []),
        },
    }


@router.get("/desk", dependencies=[authority.SIGNED_IN])
def desk(live: bool = False, only: str | None = None, session: Session = Depends(db),
         person: Person = Depends(person_auth)) -> dict:
    """Cross-project rollup: decisions waiting on the chairman, countdowns
    worth watching, and one comparable row per project — in one request, so a
    page answers "what needs me?" without fanning out per project.

    `only` narrows the rows to one project, for a reader that wants one
    project's line without paying for everyone's.

    `live=true` drops the terminal projects (stopped, completed) entirely,
    which is what the Desk wants: a project that is over cannot need anything,
    and every aggregate below is frozen forever on one, so computing them six
    times a minute buys nothing. The Projects page is the archive and asks for
    the default, everything.
    """
    now = utcnow()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)
    soon = now + timedelta(days=7)
    TERMINAL = (ProjectStatus.STOPPED, ProjectStatus.COMPLETED)

    def aware(dt: datetime | None) -> datetime | None:
        if dt is None:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)

    # Only what this reader may open, through the one function every rollup
    # shares — the desk is exactly the payload nobody wrote with the gate in
    # mind, and the day it bypassed it is the day it listed a stranger's work.
    reachable = _reachable(session, person)
    finished = sum(1 for p in reachable if p.status in TERMINAL)
    listing = [p for p in reachable if not (live and p.status in TERMINAL)
               and (only is None or p.id == only)]

    projects_out: list[dict] = []
    for project in listing:
        cons = effective(session, project.id)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        usd, tokens_in, tokens_out, cache_creation, cache_read = session.execute(
            select(
                func.coalesce(func.sum(LedgerEntry.usd), 0.0),
                func.coalesce(func.sum(LedgerEntry.tokens_in), 0),
                func.coalesce(func.sum(LedgerEntry.tokens_out), 0),
                func.coalesce(func.sum(LedgerEntry.cache_creation_tokens), 0),
                func.coalesce(func.sum(LedgerEntry.cache_read_tokens), 0),
            ).where(LedgerEntry.project_id == project.id, LedgerEntry.at >= month_start,
                LedgerEntry.kind != LedgerKind.REVENUE)
        ).one()
        usd_mode = project.billing_mode == "api_key"
        pending = [
            {"id": a.id, "title": a.title, "kind": a.kind, "amount_usd": a.amount_usd,
             "expires_at": aware(a.expires_at).isoformat(), "capacity": a.capacity,
             "undo": approval_undo(a.kind),
             # What silence decides on THIS project; the row said "denied"
             # on every project, including those whose rules approve.
             "if_silent": ("approved" if cons.approval_timeout_decision == "approve"
                           else "declined")}
            for a in session.execute(
                select(Approval)
                .where(Approval.project_id == project.id,
                       Approval.status == ApprovalStatus.PENDING)
                .order_by(Approval.expires_at)
            ).scalars()
        ]
        # Beside the approvals rather than in `awaiting_you`: both are clocked
        # things he answers, neither is gated on the dial, and they belong on
        # the same screen. `default_action` rides along because the honest row
        # is not "answer me" — it is "answer me by then, or this happens",
        # which is what makes ignoring one a real choice instead of a hole.
        pending_asks = [
            {"id": a.id, "title": a.title, "body": a.body,
             "answer_by": aware(a.answer_by).isoformat(),
             "default_action": a.default_action,
             "options": a.options or [], "recommendation": a.recommendation}
            for a in ask_lib.open_asks(session, project.id)
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
        # Consecutive failures, newest first, among the runs that call a
        # model: the retry loop hides one, but three in a row is a project
        # spending and producing nothing, which "Working." would paper over.
        recent = session.execute(
            select(Run.status, Run.error_kind)
            .where(Run.project_id == project.id, Run.kind.in_(MODEL_RUN_KINDS),
                   Run.finished_at.is_not(None))
            .order_by(Run.finished_at.desc()).limit(FAILING_AFTER)
        ).all()
        failed_in_a_row = 0
        for run_status, _ in recent:
            if run_status != RunStatus.FAILED:
                break
            failed_in_a_row += 1
        spinning = _spinning_runs(session, project, now)
        projects_out.append({
            "id": project.id, "slug": project.slug, "name": project.name,
            "status": project.status, "status_reason": project.status_reason,
            "goal": project.goal, "billing_mode": project.billing_mode,
            # Nothing pays for it, so nothing it starts can run. A row that
            # said "Running" here sent the chairman to a project whose first
            # run had already died on the missing key.
            "unpaid": (project.billing_secret_id is None
                       and (project.integrations or {}).get("runtime") != "mock"),
            "budget": {
                "unit": "usd" if usd_mode else "tokens",
                # Token mode must match enforcement (janitor.month_to_date):
                # cache tokens count.
                "spent": (float(usd) if usd_mode
                          else int(tokens_in) + int(tokens_out)
                          + int(cache_creation) + int(cache_read)),
                "cap": cons.monthly_usd_cap if usd_mode else cons.monthly_token_cap,
                "warn_ratio": cons.budget_warn_ratio,
                # The budget is the plan; this times the budget is the wall
                # that pauses the project. A row that reads only the cap
                # cannot say where spending actually stops.
                "hard_stop_multiple": cons.hard_stop_multiple,
            },
            "open_tasks": len(policy.open_top_level_tasks(session, project.id)),
            "queue_ceiling": cons.queue_ceiling,
            # The three things the Supervised preset says the chairman does,
            # each computed only where he actually holds that switch. Where the
            # CEO holds it there is nothing waiting — it decides rather than
            # parking — so an autonomous project reads empty here, and the dial
            # itself rides the project dashboard for anyone who needs it.
            "awaiting_you": _awaiting_chairman(session, project, cons),
            # The reader's own, so each row says which boardroom has news.
            "boardroom_unread": boardroom_unread(session, project),
            "evidence_7d": policy.countable_external_evidence(session, project.id, week_ago),
            # Yesterday, paired: spend on its own is a vanity number, and
            # evidence on its own hides what it cost. Together they are the one
            # question worth asking about a day nobody watched — did it buy
            # anything? — so they travel as a pair or not at all.
            "spend_24h": round(float(session.execute(
                select(func.coalesce(func.sum(LedgerEntry.usd), 0.0)).where(
                    LedgerEntry.project_id == project.id,
                    LedgerEntry.at >= day_ago,
                    LedgerEntry.kind != LedgerKind.REVENUE,
                )
            ).scalar_one()), 4),
            "evidence_24h": policy.countable_external_evidence(
                session, project.id, day_ago),
            "ceo_judgment": policy.ceo_judgment(session, project.id),
            "pending_approvals": pending,
            "pending_asks": pending_asks,
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
        row = projects_out[-1]
        waiting = row["awaiting_you"]
        row["status_line"] = status_line(
            status=project.status, status_reason=project.status_reason,
            unpaid=row["unpaid"], commitment=policy.live_commitment(session, project.id),
            waiting=(len(pending) + len(pending_asks) + (1 if waiting["plan"] else 0)
                     + len(waiting["verdicts"]) + (1 if waiting["commitment"] else 0)),
            open_tasks=row["open_tasks"], runs_running=int(running), last_run_at=last_run_at,
            stall_hours=float(cons.stall_hours), unit=row["budget"]["unit"],
            spent=row["budget"]["spent"], cap=row["budget"]["cap"],
            failed_in_a_row=failed_in_a_row,
            last_error_kind=recent[0][1] if failed_in_a_row else None,
            spinning_runs=spinning)
    # The reader's own "not now"s, so every surface that draws their Inbox
    # leaves out the same rows. Personal: nobody else's Inbox changes.
    from yaaf.server.api import me as me_api

    reader = person if isinstance(person, Person) else authority.acting_person(session)
    snoozed = (me_api.active(session, reader.id, {p.id for p in listing})
               if reader is not None else [])
    return {"as_of": now.isoformat(), "finished": finished, "projects": projects_out,
            "snoozed": snoozed}


def _spinning_runs(session: Session, project: Project, now: datetime) -> int:
    """The week's paid runs, when the week produced nothing at all — no task
    closed, no requirement met, nothing published, no outside proof — and the
    project is old enough to have been expected to. Zero otherwise, which is
    every case the status line must not call waste."""
    since = now - timedelta(days=SPINNING_DAYS)
    created = project.created_at if project.created_at.tzinfo else \
        project.created_at.replace(tzinfo=UTC)
    if created > since:
        return 0
    runs = session.execute(
        select(func.count(Run.id)).where(
            Run.project_id == project.id, Run.kind.in_(MODEL_RUN_KINDS),
            Run.metered.is_(True), Run.created_at >= since)
    ).scalar_one()
    if runs < SPINNING_RUNS:
        return 0
    produced = (
        session.execute(select(func.count(TaskEvent.id)).where(
            TaskEvent.project_id == project.id, TaskEvent.to_status == TaskStatus.DONE,
            TaskEvent.at >= since)).scalar_one()
        + session.execute(select(func.count(RequirementEvent.id)).where(
            RequirementEvent.project_id == project.id,
            RequirementEvent.to_status == RequirementStatus.MET,
            RequirementEvent.at >= since)).scalar_one()
        + session.execute(select(func.count(Artifact.id)).where(
            Artifact.project_id == project.id, Artifact.published_at >= since)).scalar_one()
        + policy.countable_external_evidence(session, project.id, since))
    return 0 if produced else int(runs)


@router.get("/briefing", dependencies=[authority.SIGNED_IN])
def briefing_all(hours: float | None = None, since: str | None = None,
                 session: Session = Depends(db),
                 person: Person = Depends(person_auth)) -> dict:
    """"What happened while I was away", across every live project.

    Each project is read on its OWN window by default — the mark is per
    project, and reading one project's brief must not silently consume
    another's. `hours` or `since` forces one shared window instead, which is
    what a "show me the last 24 hours" control wants.

    Purely a read. The mark moves only through
    `POST /admin/projects/{id}/briefing/seen`, so polling this page can never
    lose the chairman a window he has not looked at yet.
    """
    at = _parse_since(since)
    return briefing.desk_briefing(session, since=at, hours=hours,
                                  projects=_reachable(session, person))


@router.get("/projects/{project_id}/briefing", dependencies=[authority.READ])
def project_briefing(project_id: str, hours: float | None = None,
                     since: str | None = None,
                     session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    at = _parse_since(since)
    if at is None and hours:
        at = utcnow() - timedelta(hours=hours)
    return briefing.project_briefing(session, project, since=at)


#: The plan gate's reasons are written to the CEO, which reads them in its
#: context pack — and they were shown verbatim on the chairman's Roadmap, so a
#: Supervised chairman read that scope was "waiting on him, not on you" beside
#: rows badged "waiting for you". The CEO's words stay as they are; the
#: chairman's screen is told the same fact in the second person.
_CHAIRMAN_WORDING = {
    "the chairman decides what is in scope, so this is waiting on him, not on you.":
        "you decide what is in scope — accept or cut them on the Roadmap.",
}


def _to_chairman(gap: str) -> str:
    for theirs, yours in _CHAIRMAN_WORDING.items():
        gap = gap.replace(theirs, yours)
    return gap


@router.get("/projects/{project_id}/agenda", dependencies=[authority.READ])
def project_agenda(project_id: str, days: int = agenda.DEFAULT_DAYS,
                   session: Session = Depends(db)) -> dict:
    """What this project is scheduled to do next: the wakes, the meeting, the
    rhythm, the clocks that run out, and the promise."""
    project = _project(session, project_id)
    return agenda.project_agenda(session, project, days=days)


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail="`since` must be an ISO datetime, e.g. 2026-08-24T09:00:00Z",
        ) from exc
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _delivery_outstanding(session: Session, project: Project) -> list[str]:
    from yaaf.server.janitor import delivery_outstanding

    return delivery_outstanding(session, project)


def _current_cycle_view(session: Session, project: Project) -> dict | None:
    """The open cycle, with what it is for and whether it will make its date.

    The ordinal and the open date were all this carried, which is why the
    chairman's screen could say "cycle 1, open since Wednesday" beside a memo
    that was not going to be written in time. The goal, the due-by and the
    platform's own arithmetic about reaching it all belong in the same line.
    """
    if project.mode != "operate":
        return None
    from yaaf.server.janitor import next_cycle_roll

    cycle = policy.current_cycle(session, project.id)
    if cycle is None:
        return None
    view = {
        "ordinal": cycle.ordinal,
        "opened_at": cycle.opened_at.isoformat(),
        "goal": cycle.goal,
        "due_at": cycle.due_at.isoformat() if cycle.due_at else None,
        "due_source": cycle.due_source,
        # When this turn closes and is judged — the due-by if the world set one,
        # the constitution's rhythm otherwise. What "on time" means for work
        # that comes round again.
        "rolls_at": next_cycle_roll(
            cycle.opened_at, effective(session, project.id), cycle).isoformat(),
        "capacity": None,
    }
    if cycle.due_at is not None:
        from yaaf.server.capacity import capacity_forecast

        view["capacity"] = capacity_forecast(
            session, project, effective(session, project.id), cycle.due_at)
    return view


@router.get("/projects/{project_id}/flags", dependencies=[authority.READ])
def project_flags(project_id: str, session: Session = Depends(db)) -> dict:
    """What the platform decided the chairman needs to hear, and whether the
    CEO has actually said it.

    `unraised` is the number that matters and the reason this endpoint exists.
    A CEO reporting "solid progress" beside two flags it has not raised is
    self-refuting on the chairman's own screen — which is what makes the
    mechanism trustworthy without asking him to trust it.
    """
    from yaaf.server import chairman_notes
    from yaaf.server import flags as flag_lib

    project = _project(session, project_id)
    rows = flag_lib.open_flags(session, project.id)
    held = chairman_notes.replies_for(session, "flag", [f.id for f in rows])
    return {
        "unraised": sum(1 for f in rows if f.status == "open"),
        "flags": [
            {
                "id": f.id,
                "kind": f.kind,
                "status": f.status,
                "summary": f.summary,
                "arithmetic": f.arithmetic,
                "opened_at": f.opened_at.isoformat() if f.opened_at else None,
                "raised_at": f.raised_at.isoformat() if f.raised_at else None,
                "proposal": f.proposal,
                # Raised is news given; acknowledged is news answered. The desk
                # needs both to tell a decision waiting on him from one he made.
                "acknowledged_at": (
                    f.acknowledged_at.isoformat() if f.acknowledged_at else None),
                "acknowledgement": f.acknowledgement,
                "held_note": held.get(f.id),
            }
            for f in rows
        ],
    }


@router.get("/projects/{project_id}", dependencies=[authority.READ])
def dashboard(project_id: str, session: Session = Depends(db)) -> dict:
    from yaaf.server.api.admin import provider_credentials_view

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
        ).where(LedgerEntry.project_id == project.id, LedgerEntry.at >= month_start,
                LedgerEntry.kind != LedgerKind.REVENUE)
    ).one()
    open_tasks = policy.open_top_level_tasks(session, project.id)
    window = utcnow() - timedelta(hours=cons.drought_hours)
    evidence_count = policy.countable_external_evidence(session, project.id, window)
    # Yesterday, paired — the same pair the desk carries, for the same reason:
    # spend on its own is a vanity number and evidence on its own hides what it
    # cost. The project's own Watch list is where this now lands, so the
    # dashboard has to be able to answer it without the desk rollup.
    day_ago = utcnow() - timedelta(hours=24)
    spend_24h = round(float(session.execute(
        select(func.coalesce(func.sum(LedgerEntry.usd), 0.0)).where(
            LedgerEntry.project_id == project.id,
            LedgerEntry.at >= day_ago,
            LedgerEntry.kind != LedgerKind.REVENUE,
        )
    ).scalar_one()), 4)
    evidence_24h = policy.countable_external_evidence(session, project.id, day_ago)
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
    # Proposed scope is the CEO parked on a decision only the chairman can
    # make. It rides the dashboard so every tab can say so — a CEO report that
    # reads "waiting on R-01 acceptance" never says where accepting happens.
    requirements_proposed = session.execute(
        select(func.count(Requirement.id)).where(
            Requirement.project_id == project.id,
            Requirement.status == RequirementStatus.PROPOSED,
        )
    ).scalar_one()
    # Read off whoever this request is bound to rather than taken as a
    # parameter: the MCP read tools call this handler as a plain function.
    reader = authority.acting_person(session)
    chairman = session.get(User, project.chairman_user_id)
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "status": project.status,
        "status_reason": project.status_reason,
        "goal": project.goal,
        # The rest of the brief, which Settings edits beside the goal.
        "standing_orders": project.standing_orders or [],
        "retired_orders": project.retired_orders or [],
        "brief_confirmed_at": (project.brief_confirmed_at.isoformat()
                               if project.brief_confirmed_at else None),
        **(standing(session, reader, project) if reader is not None else {}),
        "chairman": None if chairman is None else {
            "id": chairman.id, "email": chairman.email,
            "display_name": chairman.display_name},
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
        "spend_24h": spend_24h,
        "evidence_24h": evidence_24h,
        "drought_active": policy.drought_active(session, project.id, cons),
        "memory": _memory_usage(session, project, cons),
        "runs": {"running": running, "queued": queued},
        # Why work is or isn't starting. Computed by the dispatcher's own
        # helpers so the number that explains the silence cannot disagree with
        # the number that caused it.
        "activity": activity_state(session, project, cons),
        "chat_live": chat_session_live(session, project),
        "pending_approvals": pending_approvals,
        "requirements_proposed": requirements_proposed,
        # The reader's, not the project's: None for someone with no seat here.
        "boardroom_unread": boardroom_unread(session, project),
        "ceo_judgment": policy.ceo_judgment(session, project.id),
        "constitution": cons.model_dump(),
        "mode": project.mode,
        # Explore's window; Deliver's completion readiness; Operate's current
        # turn. Mode-shaped facts, one each, so the Overview can show the one
        # that applies without three extra requests.
        "deadline": project.deadline.isoformat() if project.deadline else None,
        "delivery_outstanding": (
            _delivery_outstanding(session, project)
            if project.mode == "deliver" and project.status == "running" else None),
        "cycle": _current_cycle_view(session, project),
        "integrations": project.integrations or {},
        "github_repo": project.github_repo,
        "billing_secret_id": project.billing_secret_id,
        "billing_secret_name": _secret_name(session, project.billing_secret_id),
        # Every credential the project pays with, by provider — the billing
        # one first. Never a value.
        "credentials": provider_credentials_view(session, project),
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


#: The drift between what a self-reporting harness bills and what yaaf's
#: catalog prices the same tokens at, past which the checklist says so; the
#: priced spend it must have seen first, so a few small runs cannot raise it;
#: and how far back it looks.
PRICE_GAP_TOLERANCE = 0.10
PRICE_GAP_MIN_USD = 1.0
PRICE_GAP_RUNS = 200


def price_gaps(session: Session, models: list[str]) -> list[dict]:
    """For each model whose gap is past tolerance: what the harnesses that
    report their own bill charged for it across this install's recent runs,
    against what the catalog prices the same tokens at. Install-wide, because
    the catalog is: a project with every seat on a priced harness has no
    bills of its own to check it by."""
    from yaaf.runtimes.harnesses import HARNESSES

    reporting = [h.name for h in HARNESSES.values() if h.cost == "reported"]
    gaps = []
    for model in models:
        rows = session.execute(
            select(Run.cost_usd, Run.priced_usd)
            .where(Run.model == model, Run.harness.in_(reporting),
                   Run.cost_usd.is_not(None), Run.priced_usd.is_not(None))
            .order_by(Run.finished_at.desc())
            .limit(PRICE_GAP_RUNS)
        ).all()
        reported = sum(r.cost_usd for r in rows)
        priced = sum(r.priced_usd for r in rows)
        if priced < PRICE_GAP_MIN_USD:
            continue
        drift = priced / reported - 1 if reported else 1.0
        if abs(drift) > PRICE_GAP_TOLERANCE:
            gaps.append({"model": model, "runs": len(rows), "reported_usd": reported,
                         "priced_usd": priced, "drift": drift})
    return gaps


@router.get("/projects/{project_id}/health", dependencies=[authority.READ])
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
                                               decrypt(billing.ciphertext), billing.provider)
            add("billing", "fail" if shape_error else "ok",
                shape_error or f"paying with '{billing.name}' ({project.billing_mode})",
                "settings")
    # And what pays each other provider: named, so a chairman reading the
    # checklist sees the whole of who pays for what.
    from yaaf.core import project_credentials
    from yaaf.runtimes.providers import PROVIDERS

    for provider_name, secret in project_credentials.held(session, project).items():
        if secret.id == project.billing_secret_id:
            continue
        seller = PROVIDERS.get(provider_name)
        add(f"billing:{provider_name}", "ok",
            f"paying {seller.label if seller else provider_name} with '{secret.name}'",
            "settings")

    # Seats: every one on a harness that can serve its model, honour its
    # role's grants and pay with this credential. Every door that seats one
    # refuses otherwise; this is for the seat that reached here by another
    # way — a hand edit, a release rolled back past the harness it names —
    # and would otherwise be found by its runs failing at lease.
    from fastapi import HTTPException

    from yaaf.server.api.admin import _project_agents, refuse_unrunnable_seats

    try:
        refuse_unrunnable_seats(session, project, _project_agents(session, project.id))
    except HTTPException as refused:
        add("seats", "fail", f"a seat cannot run — {refused.detail}", "settings")

    # Prices: a seat on a harness yaaf prices itself is metered by the model
    # catalog, so its budget is only as right as the catalog. The bills the
    # self-reporting harnesses send for the same model are the check on it.
    from yaaf.runtimes import catalog
    from yaaf.runtimes.harnesses import HARNESSES
    from yaaf.runtimes.seats import seat_for
    from yaaf.server.contextpack import role_version_for

    metered: dict[str, set[str]] = {}
    for agent in _project_agents(session, project.id):
        seat = seat_for(agent, role_version_for(session, agent))
        harness = HARNESSES.get(seat.harness or "")
        if harness is not None and harness.cost == "priced":
            metered.setdefault(seat.model, set()).add(harness.label)
    for gap in price_gaps(session, sorted(metered)):
        price = catalog.price(gap["model"])
        add(f"price-gap:{gap['model']}", "warn",
            f"yaaf prices {gap['model']} {abs(gap['drift']):.0%} "
            f"{'above' if gap['drift'] > 0 else 'below'} what it is billed at: the "
            f"harnesses that report their own cost charged "
            f"${gap['reported_usd']:.2f} over its last {gap['runs']} runs on this "
            f"install, and the price table puts the same tokens at "
            f"${gap['priced_usd']:.2f}. Seats on "
            f"{', '.join(sorted(metered[gap['model']]))} are metered by that table, so "
            f"their budget is {'over' if gap['drift'] > 0 else 'under'}-counted by as "
            f"much — the prices in yaaf/runtimes/catalog.py"
            f"{f' (as of {price.as_of})' if price else ''} need checking against the "
            "provider's current list.",
            "runs")

    secrets = session.execute(
        select(Secret).where(or_(Secret.project_id == project.id,
                                 Secret.project_id.is_(None)),
                             # The LLM credentials live at platform scope too, and
                             # one named like a data source's secret is not that secret.
                             Secret.llm_mode.is_(None))
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
        oauth_status = (source.get("oauth") or {}).get("status")
        if source.get("auth") == "oauth" and oauth_status != "connected":
            # A stale token may still be mounted, but agents are one expiry
            # away from named auth failures — the fix is a human sign-in, and
            # only the health page can ask for one.
            what = {"pending": "its OAuth sign-in was started but never finished",
                    "needs_reauth": "its OAuth connection expired"}.get(
                oauth_status, "its OAuth connection was never made")
            verb = "Reconnect" if oauth_status == "needs_reauth" else "Connect"
            add(f"data-source:{name}", "fail",
                f"data source '{name}': {what} — press {verb} in Settings", "settings")
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
            # Say what moved, not just that something did. A drift warning with
            # no content is a number nobody acts on: it reports that a newer
            # version exists and gives the reader nothing to decide with, so it
            # sits on the screen for months. each version's `description` is what
            # its author already wrote when publishing it.
            behind = session.execute(
                select(RoleVersion).where(
                    RoleVersion.role_id == role.id,
                    RoleVersion.version > pinned.version,
                ).order_by(RoleVersion.version)
            ).scalars().all()
            changes = "; ".join(
                f"v{v.version}: {(v.description or 'no description').strip()[:160]}"
                for v in behind[:4]
            )
            add(f"role-drift:{team.name}", "warn",
                f"team '{team.name}' runs role '{role.slug}' v{pinned.version}; "
                f"v{latest.version} exists. What changed since — {changes}. "
                "Adopting it repins this team to the current charter.",
                "overview", {"kind": "repin", "team_id": team.id})

    # ── doctrine drift ──────────────────────────────────────────────────────
    #
    # The Founder runs once, so a project keeps the doctrine it was made under
    # for its whole life. Six fixes landed in five days and the desk being
    # watched had been founded before all of them; it would have run nine more
    # months on 26 August's rules with nobody able to see that from anywhere but
    # `git log`.
    #
    # All three checks are arithmetic over rows, at zero tokens. Re-grading the
    # goal prose against the current grammar needs a model and real money, and
    # is deliberately NOT here — these are the structural half, and the
    # structural half is what the FPL desk was actually missing.
    if project.status == ProjectStatus.RUNNING:
        from yaaf.founder import doctrine

        if not doctrine.is_current(project.doctrine_stamp):
            was = project.doctrine_stamp or "before the platform recorded it"
            add("doctrine-drift", "warn",
                f"founded under doctrine {was}; the current doctrine is "
                f"{doctrine.fingerprint()}. What the Founder has learned since is not "
                "reaching this project — the Founder runs once, at creation. Nothing "
                "here is broken; this is a decision for you about whether to carry the "
                "changes across.",
                "overview")

        # A roster with nobody whose job is to disagree. DESIGN_DOCTRINE says
        # plan-adversary survives the cut on purpose and belongs on every
        # Deliver and Operate project, and gives the reason: the CEO wrote the
        # plan and on an autonomous project also accepted it, so it is the only
        # reader of that plan with no distance from it.
        if project.mode in (ProjectMode.DELIVER, ProjectMode.OPERATE):
            categories = {
                role.category
                for role in session.execute(
                    select(Role)
                    .join(RoleVersion, RoleVersion.role_id == Role.id)
                    .join(Team, Team.role_version_id == RoleVersion.id)
                    .where(Team.project_id == project.id)
                ).scalars()
            }
            if "challenge" not in categories:
                # Said for the dial this project is on: a Supervised chairman
                # read that the CEO "on an autonomous project, accepted it too"
                # about a plan he had accepted himself.
                if effective(session, project.id).plan_authority == "ceo":
                    who = ("The CEO wrote it and accepted it too — so it is the only "
                           "reader of that plan with no distance from it.")
                else:
                    who = ("The CEO wrote it and you accept it — so you are its only "
                           "reader with any distance from it.")
                add("no-adversary", "warn",
                    "no seat on this project exists to disagree with the plan. " + who
                    + " Hiring one `challenge` role is the whole fix.",
                    "settings")

        # An Operate project with no standing scope is re-creating the same
        # obligation by hand every cycle. `Recurrence.STANDING` exists for
        # exactly this and names it: expressed as TERMINAL, a project that had
        # shipped 1 of 38 memos looked finished.
        if project.mode == ProjectMode.OPERATE:
            standing = session.execute(
                select(func.count(Requirement.id)).where(
                    Requirement.project_id == project.id,
                    Requirement.recurrence == Recurrence.STANDING,
                )
            ).scalar_one()
            if not standing:
                add("no-standing-scope", "warn",
                    "this project runs on a rhythm and owns no standing requirements, so "
                    "whatever it owes again next cycle is being re-proposed by hand every "
                    "time — and a burn-down of work that recurs reads as nearly finished. "
                    "`recurrence: standing` is what that shape is for.",
                    "plan")

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

    # News the CEO has been told to deliver and has not. Loud on purpose: the
    # whole point of a flag is that nobody has to notice it themselves.
    from yaaf.server import flags as flag_lib

    unraised = flag_lib.unraised_count(session, project.id)
    if unraised:
        add("flags", "warn",
            f"{unraised} flag(s) opened by the platform that your CEO has not yet "
            "raised with you — each one is arithmetic or a failed check, not an "
            "opinion. It is required to bring them to the boardroom with a proposal, "
            "and the reporting cadence does not hold it back from doing so.",
            "boardroom")

    # Questions the CEO put to you and is still waiting on. Not a warning: it
    # asked, it told you when it stops waiting, and it wrote down what it does
    # then, so nothing is stuck. It is here because "he never answered" and "he
    # was never asked" used to look identical from this page.
    pending_asks = ask_lib.open_asks(session, project.id)
    if pending_asks:
        soonest = min(a.answer_by for a in pending_asks)
        add("asks", "ok",
            f"{len(pending_asks)} question(s) from your CEO waiting on you, the first "
            f"stopping {chairman_time(soonest, effective(session, project.id).timezone)}. "
            "Nothing is blocked: "
            "each one names what it will do unanswered, and it will tell you when it "
            "does.",
            "boardroom")

    # Liveness. Every other check on this page asks "is this project set up
    # correctly?"; this one asks "is it working right now?" — the question that
    # had no answer anywhere in the product. A project that has spent its daily
    # runs looks exactly like one with nothing to do on every screen we ship,
    # which is how a hard stop reads as a healthy quiet.
    #
    # The sentence comes from the dispatcher, beside the ladder that decided
    # it. This page used to re-write three of the gates by hand and knew
    # nothing of the rest, so a project held by pacing, a batching window or a
    # leaderless team reported perfect health while doing nothing at all.
    if project.status == "running":
        try:
            live_cons = effective(session, project.id)
        except LookupError:
            # Both loops `continue` past a project in this state, so it is
            # stopped in the most complete way the platform has and there was
            # no trace of it anywhere. Say so.
            add("activity", "fail",
                "this project has no constitution row, so the dispatcher and the "
                "janitor both skip it entirely — nothing will ever run. This is a "
                "broken record, not a setting; it needs restoring from a backup or "
                "the project recreating.", "settings")
            live_cons = None
        if live_cons is not None:
            # Both of these read the constitution, and reading it unguarded is
            # what the `except LookupError` above exists to prevent: a project
            # with no constitution row would 500 this endpoint and pre-empt the
            # one check whose whole job is to report that condition. They run
            # only on a running project, which is right — a paused project's
            # pace is not news.
            rules = live_cons.model_dump()
            on = tempo_name(rules)
            # No bar-card equivalent of the rhythm check below, and that is the
            # answer to the footprint question rather than an omission: the
            # tempo can disagree with something else the chairman wrote down
            # (`cycle_frequency`), and the bar cannot — there is nothing on a
            # project for "how good does this have to be" to contradict. The
            # bar card reports itself in the brief and in the MCP record, where
            # the tempo does.

            # The tempo and the rhythm disagreeing. `cycle_frequency` is
            # amendable and the tempo is not derived again afterwards —
            # deliberately: a platform that silently re-paced a live desk
            # because a rule moved is a worse failure than the one it fixes. So
            # this is a sentence the chairman reads and decides on, the same
            # posture `doctrine-drift` above already takes.
            if project.mode == ProjectMode.OPERATE:
                wanted = tempo_for("operate", rules.get("cycle_frequency"))
                if on is not None and on != wanted:
                    add("tempo-vs-rhythm", "warn",
                        f"this project rolls a {rules['cycle_frequency']} cycle but "
                        f"thinks on the '{on}' tempo, which is what a project of a "
                        f"different rhythm wants — '{wanted}' is the one that matches. "
                        "Nothing is broken and nothing was changed for you; if the "
                        "rhythm moved and the pace did not, this is where you would "
                        "notice.",
                        "settings")

            # The ceiling and the budget describing different projects. Only
            # where the box has actually priced runs: `avg_usd_per_run_box`
            # answers None under five of them, because a fresh install saying
            # nothing is the honest behaviour and a guess here would be quoted
            # back as arithmetic.
            per_run = policy.avg_usd_per_run_box(session)
            if per_run and project.billing_mode == "api_key" and rules["monthly_usd_cap"]:
                # Both lanes. The inbox ceiling is a SEPARATE additive allowance
                # — `daily_runs_used` excludes those runs from the work total on
                # purpose — so a `live` project's real ceiling is 190 a day, not
                # 150, and counting one lane understated this by a quarter. An
                # inbox wake measures about a third of a work run, but
                # `avg_usd_per_run_box` is blended across kinds, so the plain
                # sum is the honest arithmetic available here.
                per_day = (rules["runs_per_project_per_day"]
                           + rules["inbox_runs_per_project_per_day"])
                ceiling = per_day * 30 * per_run
                if ceiling > rules["monthly_usd_cap"]:
                    add("tempo-vs-budget", "warn",
                        f"{per_day} runs a day — {rules['runs_per_project_per_day']} of "
                        f"work plus {rules['inbox_runs_per_project_per_day']} of reading "
                        f"— at the ${per_run:.2f} a run this install has averaged is "
                        f"about ${ceiling:,.0f} a month, against a budget of "
                        f"${rules['monthly_usd_cap']:,.0f}. The budget is a plan rather "
                        "than a wall so neither number is wrong, but they describe "
                        "different projects — one of the two is the one you meant.",
                        "settings")

            act = activity_state(session, project, live_cons)
            brief = act["brief"]
            # Only the briefs that mean *stopped* are findings. Concurrency and
            # busy agents mean the project is working, which is not news.
            if brief["banner"]:
                add("activity",
                    "fail" if brief["tone"] == "critical" else "warn",
                    f"{brief['headline']} {brief['detail']}",
                    "settings")

        dormant = _dormant_seats(session, project)
        if dormant:
            names, _ = _name_maps(session, project.id)
            each = [f"{names.get(agent_id, agent_id)} "
                    f"({'never' if days is None else f'{days} days'})"
                    for agent_id, days in sorted(dormant.items(),
                                                 key=lambda kv: names.get(kv[0], ""))]
            seats = "1 seat hasn't" if len(each) == 1 else f"{len(each)} seats haven't"
            add("dormant-seats", "warn",
                f"{seats} run in over a week: {', '.join(each)}. A seat that never "
                "wakes isn't doing its part, and messages to it pile up unread. Ask "
                "the CEO to give it work or stand it down, or pause it from the Team "
                "page.",
                "team")

    # Connectors: controlled GitHub accounts registered but webhooks unprovisioned.
    if any(str(d).startswith("github:") for d in (project.controlled_domains or [])) \
            and not integrations.get("github_webhook_secret"):
        add("connectors", "warn",
            "controlled GitHub accounts are registered but webhook secrets are not "
            "provisioned — GitHub events cannot become attested evidence yet",
            "settings")

    # Each finding gets a name that outlives its numbers, so a suggestion the
    # reader has put away stays away while "9 days" becomes "10", and comes back
    # the moment it is about something new (another seat, another gate).
    member = reading_member(session, project)
    put_away = (member.health_acks or {}) if member is not None else {}
    for check in checks:
        check["sig"] = health_sig(check["key"], check["detail"])
        ack = put_away.get(check["sig"]) if check["status"] == "warn" else None
        check["acked_at"] = ack.get("at") if ack else None

    return {
        "ok": not any(c["status"] == "fail" for c in checks),
        "all_ok": all(c["status"] == "ok" for c in checks) if checks else True,
        "checks": checks,
    }


def health_sig(key: str, detail: str) -> str:
    """A health finding's identity: its kind and its words, numbers masked.

    The header badge was lit on every project, every day, by advice the
    chairman had already read — "founded under an older doctrine" says in its
    own words that nothing is broken, and it never goes away. Putting one away
    needs a name for it that a count ticking over does not change, and that a
    different finding of the same kind does.
    """
    masked = re.sub(r"\d+(?:[.,]\d+)*", "#", detail)
    return hashlib.sha1(f"{key}|{masked}".encode()).hexdigest()[:12]


@router.get("/projects/{project_id}/plan", dependencies=[authority.READ])
def plan_view(project_id: str, session: Session = Depends(db)) -> dict:
    """Everything the Plan screen draws, in one request.

    One dataset for both views — the stage board and the dependency graph read
    the same rows, so they cannot disagree with each other, and the critical
    path arrives computed rather than being re-derived in the browser where it
    could drift from the refusals' arithmetic.
    """
    from yaaf.core.constitution import effective as effective_constitution

    project = _project(session, project_id)
    if not policy.requires_requirements(project):
        return {"mode": project.mode, "planless": True}

    cons = effective_constitution(session, project.id)
    agents, _ = _name_maps(session, project.id)

    plan = policy.plan_requirement(session, project.id)
    scope = policy.scope_requirements(session, project.id)
    edges = policy.requirement_edges(session, project.id)
    key_of = {r.id: r.key for r in scope}
    if plan is not None:
        key_of[plan.id] = plan.key

    depends_on: dict[str, list[str]] = {}
    for edge in edges:
        depends_on.setdefault(edge.to_requirement_id, []).append(
            key_of.get(edge.from_requirement_id, edge.from_requirement_id))

    tasks_by_requirement: dict[str, list[dict]] = {}
    for task in session.execute(
        select(Task).where(Task.project_id == project.id,
                           Task.requirement_id.is_not(None))
    ).scalars():
        tasks_by_requirement.setdefault(task.requirement_id, []).append({
            "id": task.id,
            "title": task.title,
            "status": task.status,
            "holder": agents.get(task.claimed_by_agent_id),
            "failed_checks": task.check_failures,
        })

    # Runs and dollars actually spent, per requirement — planning runs
    # included, which is the half of spend that used to be unattributable.
    spend = {
        requirement_id: {"runs": int(runs), "usd": round(float(usd or 0), 2)}
        for requirement_id, runs, usd in session.execute(
            select(Run.requirement_id, func.count(Run.id), func.sum(Run.cost_usd))
            .where(Run.project_id == project.id, Run.requirement_id.is_not(None))
            .group_by(Run.requirement_id)
        )
    }

    # The promise and the line toward it. The projection is computed live from
    # the same producer the janitor stamps and the refusals quote; the status
    # is the janitor's (it owns the transitions and the disclosure).
    #
    # Deliver only. An Operate project has no finish line for a promise or a
    # burn-up to point at — its dates are its cycles' — and the rows an Operate
    # project picked up before that was refused are void, not history to draw.
    commitment_row = None if project.mode == ProjectMode.OPERATE else session.execute(
        select(Commitment).where(Commitment.project_id == project.id)
        .order_by(Commitment.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    commitment = None
    burnup = None
    if commitment_row is not None:
        projection = commitment_projection(session, project, cons)
        commitment = {
            "id": commitment_row.id,
            "status": commitment_row.status,
            "deliver_by": commitment_row.deliver_by.isoformat(),
            "projected_done_at": (
                projection["projected_done_at"].isoformat()
                if projection["known"] else None),
            "projection_basis": projection.get("basis") or projection.get("why"),
            "accepted_at": (commitment_row.accepted_at.isoformat()
                            if commitment_row.accepted_at else None),
            "constitution_version": commitment_row.constitution_version,
            "scope_snapshot": commitment_row.scope_snapshot or [],
            "status_reason": commitment_row.status_reason,
            "note": commitment_row.note,
            "met_at": (commitment_row.met_at.isoformat()
                       if commitment_row.met_at else None),
        }
        # How much of the scope has been true, over time — the burn-up's line.
        #
        # Built from `countable_scope`, the same set the pace is measured on,
        # because the solid line and the dashed projection drawn from its tip
        # used to come from two different datasets: the line counted every MET
        # event but R-00, while the pace also dropped standing and metric
        # requirements. One picture, two definitions of the same word.
        #
        # It tracks how many are met AT each instant rather than accumulating
        # met-events, so it goes down when a requirement is reopened. A line
        # that only ever rises cannot represent scope coming untrue, and the
        # old one counted a reopened-then-re-met requirement twice — climbing,
        # in principle, straight past its own ceiling.
        countable = {r.id: r for r in countable_scope(session, project.id)}
        met_points: list[dict] = []
        if countable:
            rows = session.execute(
                select(RequirementEvent.requirement_id, RequirementEvent.at,
                       RequirementEvent.to_status)
                .where(RequirementEvent.project_id == project.id,
                       RequirementEvent.requirement_id.in_(list(countable)))
                .order_by(RequirementEvent.at)
            )
            met_now: set[str] = set()
            for requirement_id, at, to_status in rows:
                was_met = requirement_id in met_now
                if to_status == RequirementStatus.MET:
                    met_now.add(requirement_id)
                else:
                    met_now.discard(requirement_id)
                if (requirement_id in met_now) != was_met:
                    met_points.append({"at": at.isoformat(), "count": len(met_now)})
        burnup = {
            "scope_total": len(countable),
            # Counted here rather than left to callers, who were deriving it as
            # `scope_total - velocity.remaining` — two sets that exclude
            # different things, so the Overview's "N of M settled" ran ahead by
            # the number of open metric requirements.
            "settled": sum(1 for r in countable.values()
                           if r.status == RequirementStatus.MET),
            "met_points": met_points,
        }

    return {
        "mode": project.mode,
        "planless": False,
        "plan": {
            "status": plan.status if plan else None,
            "holds": plan is not None and plan.status == RequirementStatus.MET,
            "outstanding": [_to_chairman(gap)
                            for gap in policy.evaluate_plan(session, project, cons)],
            "arithmetic": policy.plan_arithmetic(session, project, cons),
            "planning_spend": spend.get(plan.id, {"runs": 0, "usd": 0}) if plan else None,
        },
        "commitment": commitment,
        "burnup": burnup,
        "open_stage": policy.open_stage(session, project.id),
        "critical_path": policy.critical_path(session, project.id),
        "velocity": project_velocity(session, project.id),
        # What an Operate project has instead of a burndown: a burndown of work
        # that recurs is a flat line, and the shape that carries the
        # information is which obligation slipped, in which turn.
        "standing": standing_record(session, project.id),
        "requirements": [
            {
                "id": r.id,
                "key": r.key,
                "statement": r.statement,
                "meta": r.meta,
                "recurrence": r.recurrence,
                "cycle_id": r.cycle_id,
                "status": r.status,
                "stage": r.stage,
                "test_kind": (r.acceptance_test or {}).get("type"),
                "acceptance_test": r.acceptance_test,
                # The readings, for a requirement the platform measures. None
                # for every other kind, which is every requirement that existed
                # before metrics did.
                "measurement": (
                    metrics.scoreboard(session, r)
                    if metrics.is_metric(r.acceptance_test) else None
                ),
                "est_runs": r.est_runs,
                "spent": spend.get(r.id, {"runs": 0, "usd": 0}),
                "failure_load": r.failure_load,
                "depends_on": sorted(depends_on.get(r.id, [])),
                "tasks": tasks_by_requirement.get(r.id, []),
                "met_at": r.met_at.isoformat() if r.met_at else None,
                "cut_reason": r.cut_reason,
                "check_output": r.check_output,
                # Split out rather than left for the browser to sniff: the
                # prefix that marks a person's verdict apart from a command's
                # stdout belongs in one place, and it is not the UI.
                "chairman_verdict": policy.rejected_by_chairman(r),
            }
            for r in scope
        ],
    }


def _dormancy(dormant: dict[str, int | None], agent_id: str) -> dict:
    """`dormant`: no run in a week. `dormant_days`: since the last one, or null
    for a seat that never ran — the two cases need different sentences."""
    return {"dormant": agent_id in dormant, "dormant_days": dormant.get(agent_id)}


@router.get("/projects/{project_id}/org", dependencies=[authority.READ])
def org(project_id: str, session: Session = Depends(db)) -> dict:
    project = _project(session, project_id)
    # The same helper the health finding reads, so the Team page can never mark
    # a seat the Overview does not name, or the other way round.
    from yaaf.runtimes import catalog
    from yaaf.runtimes.seats import seat_for
    from yaaf.server.contextpack import role_version_for

    dormant = _dormant_seats(session, project)
    # What each person runs on and has cost this month, so the chart answers
    # "who is expensive?" where the people are rather than on another page.
    month_start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    spent = dict(session.execute(
        select(Run.agent_id, func.coalesce(func.sum(Run.cost_usd), 0.0))
        .where(Run.project_id == project.id, Run.created_at >= month_start)
        .group_by(Run.agent_id)
    ).all())
    total = sum(float(v) for v in spent.values()) or 0.0

    def working(agent) -> dict:
        seat = seat_for(agent, role_version_for(session, agent))
        usd = float(spent.get(agent.id, 0.0))
        return {"model": catalog.label(seat.model), "model_id": seat.model,
                "provider": seat.provider, "month_usd": round(usd, 2),
                "month_share": round(usd / total, 3) if total else 0.0}

    teams = []
    for team in session.execute(select(Team).where(Team.project_id == project.id)).scalars():
        members = policy.team_agents(session, team.id)
        lead = policy.effective_lead(session, team)
        role_version = session.get(RoleVersion, team.role_version_id)
        role = session.get(Role, role_version.role_id) if role_version else None
        # What the team actually runs, spelled out. The overview offered
        # "adopt latest role version" while naming neither the role nor the
        # version pinned, so the one question the card exists to answer —
        # which charter are these people working from — could only be
        # answered by opening the role library and guessing. The slug alone
        # was not enough: it is not what the library shows, and it carries no
        # version.
        latest = session.execute(
            select(RoleVersion).where(RoleVersion.role_id == role.id)
            .order_by(RoleVersion.version.desc()).limit(1)
        ).scalar_one() if role else None
        teams.append(
            {
                "id": team.id,
                "name": team.name,
                "role_slug": role.slug if role else None,
                "role_name": role.name if role else None,
                "role_version": role_version.version if role_version else None,
                # The library's newest, so the reader can see "behind" without
                # cross-referencing the health card.
                "role_latest_version": latest.version if latest else None,
                "lead_agent_id": lead.id if lead else None,
                # The staffing half of the card. `running` is derived rather
                # than stored: a team is stood down by standing its people
                # down, so "is this team working" is always a question about
                # its members.
                "running": any(a.status == AgentStatus.ACTIVE for a in members),
                "stood_down_at": team.stood_down_at.isoformat()
                if team.stood_down_at else None,
                "stand_down_when": team.stand_down_when,
                "agents": [
                    {"id": a.id, "name": a.name, "status": a.status,
                     "stood_down_reason": a.stood_down_reason,
                     **_dormancy(dormant, a.id), **working(a)}
                    for a in members
                ],
            }
        )
    ceo = policy.ceo_of(session, project.id)
    # The CEO's role hangs off the agent rather than a team, and it is a real
    # grant target — a credential scoped to it reaches the CEO like any other.
    # Without the slug here the roster the UI draws would be missing a role the
    # server accepts, which is the sort of gap that sends people back to typing.
    ceo_version = session.get(RoleVersion, ceo.role_version_id) if ceo else None
    ceo_role = session.get(Role, ceo_version.role_id) if ceo_version else None
    return {"ceo": {"id": ceo.id, "name": ceo.name, "status": ceo.status,
                    **_dormancy(dormant, ceo.id), **working(ceo),
                    "role_slug": ceo_role.slug if ceo_role else None,
                    # Named like a team's, for the same reason. No version:
                    # only teams can be repinned, and a version number beside
                    # a seat with no adopt button is a question the product
                    # cannot answer.
                    "role_name": ceo_role.name if ceo_role else None} if ceo else None,
            "teams": teams}


#: A seat with no run in this long is not resting, it is absent. A week is
#: past every cadence the tempo cards ship — the slowest card wakes the CEO
#: daily — so no seat that is doing its job reaches it by schedule alone.
DORMANT_SEAT_DAYS = 7


def _dormant_seats(session: Session, project: Project) -> dict[str, int | None]:
    """{agent_id: whole days since its last run, or None if it never ran}, for
    every active seat that has gone a week without one.

    Measured on five live projects, the long unread waits blamed on the inbox
    were seats like this: one hired and never run, one holding eighteen of its
    project's twenty unread messages. Nothing reported them — an idle seat costs
    nothing, so no ceiling ever fired, and "active" was true the whole time. A
    seat hired inside the window has not had a week to run and is left out.
    """
    now = utcnow()
    cutoff = now - timedelta(days=DORMANT_SEAT_DAYS)
    agents = list(session.execute(
        select(Agent).where(Agent.project_id == project.id,
                            Agent.status == AgentStatus.ACTIVE)
    ).scalars())
    last = dict(session.execute(
        select(Run.agent_id, func.max(Run.created_at))
        .where(Run.project_id == project.id, Run.agent_id.is_not(None))
        .group_by(Run.agent_id)
    ).all())
    def utc(dt: datetime | None) -> datetime | None:
        # SQLite hands timestamps back naive; comparing one to `now` raises.
        return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt

    dormant: dict[str, int | None] = {}
    for agent in agents:
        ran = utc(last.get(agent.id))
        if ran is not None:
            if ran < cutoff:
                dormant[agent.id] = (now - ran).days
        elif utc(agent.created_at) < cutoff:
            dormant[agent.id] = None
    return dormant


def _name_maps(session: Session, project_id: str) -> tuple[dict, dict]:
    """{agent_id: name}, {team_id: name} — the chairman reads names, not ids."""
    agents = {a.id: a.name for a in session.execute(
        select(Agent).where(Agent.project_id == project_id)).scalars()}
    teams = {t.id: t.name for t in session.execute(
        select(Team).where(Team.project_id == project_id)).scalars()}
    return agents, teams


@router.get("/projects/{project_id}/board", dependencies=[authority.READ])
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
            # A gated task is READY and unclaimable, and rendering it as plain
            # "ready" is what let a project sit silent behind a start time
            # nobody could see.
            "not_before": t.not_before.isoformat() if t.not_before else None,
            "hypothesis_id": t.hypothesis_id, "audit_status": t.audit_status,
        }
        for t in rows
    ]


@router.get("/projects/{project_id}/channels", dependencies=[authority.READ])
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


#: Enough to read what is waiting without turning an agent that has fallen far
#: behind into a page of thousands of rows. The count above it stays exact.
INBOX_MESSAGES_PER_AGENT = 50


@router.get("/projects/{project_id}/inbox", dependencies=[authority.READ])
def inbox(project_id: str, session: Session = Depends(db)) -> dict:
    """Every agent's unread messages, newest first, and the reading allowance.

    The banner could say "20 messages unread" and the only place to look was a
    per-channel log with no idea who had read what — so the number was an
    assertion nobody could check. These are the rows the dispatcher counts
    (`unread_messages_for`), so the inbox and the banner agree by construction.
    """
    project = _project(session, project_id)
    cons = effective(session, project.id)
    now = utcnow()
    _, teams = _name_maps(session, project.id)
    agents = list(session.execute(
        select(Agent).where(Agent.project_id == project.id,
                            Agent.status == AgentStatus.ACTIVE)
    ).scalars())

    def where(channel: Channel | None) -> str:
        if channel is None:
            return "a channel"
        if channel.kind == ChannelKind.TEAM:
            return f"{teams.get(channel.team_id) or 'Team'} team room"
        return channel.kind.capitalize()

    seats = []
    for agent in agents:
        unread = sorted(unread_messages_for(session, project, agent),
                        key=lambda pair: (pair[0].created_at, pair[0].id), reverse=True)
        senders = {m.sender_agent_id for m, _ in unread[:INBOX_MESSAGES_PER_AGENT]
                   if m.sender_agent_id}
        names = ({a.id: a.name for a in session.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(senders))).all()}
            if senders else {})
        seats.append({
            "id": agent.id, "name": agent.name, "kind": agent.kind,
            "team_name": teams.get(agent.team_id),
            "unread": len(unread),
            "messages": [
                {"id": m.id, "channel_id": m.channel_id, "channel": where(channel),
                 # Which party wrote it, not only a name: the MCP reader wraps
                 # agent text and leaves the chairman's words as they are.
                 "from_agent": m.sender_agent_id, "from_user": m.sender_user_id,
                 "from_name": ((authority.speaker(session, project, m.sender_user_id)
                                or "chairman") if m.sender_user_id
                               else names.get(m.sender_agent_id)),
                 "body": m.body, "at": m.created_at.isoformat()}
                for m, channel in unread[:INBOX_MESSAGES_PER_AGENT]
            ],
        })
    seats.sort(key=lambda s: (-s["unread"], s["name"]))
    return {
        "unread": sum(s["unread"] for s in seats),
        # The same counts `activity_state` reports, from the same function.
        "reading": {
            "used_today": inbox_runs_used(session, project, day_start(now)),
            "cap_today": cons.inbox_runs_per_project_per_day,
            "used_this_hour": inbox_runs_used(session, project, now - timedelta(hours=1)),
            "cap_this_hour": cons.inbox_runs_per_project_per_hour,
        },
        "agents": seats,
    }


@router.get("/channels/{channel_id}/typing", dependencies=[authority.READ])
def channel_typing(channel_id: str) -> dict:
    """Ephemeral presence for the chat UI — true while the CEO's chat session
    is composing a reply. In-memory, self-expiring, never stored."""
    return {"typing": typing_state.is_typing(channel_id)}


@router.get("/channels/{channel_id}/messages", dependencies=[authority.READ])
def channel_messages(channel_id: str, limit: int = 100, before: str | None = None,
                     session: Session = Depends(db)) -> list[dict]:
    """The newest `limit` messages in a channel, oldest first — and, with
    `before`, the page of `limit` that sits immediately older than that message.

    The cursor is a message id rather than a timestamp, and that is a decision
    rather than a convenience. `created_at` is not unique: two rows written in
    the same instant are ordinary here, and a timestamp cursor then has to pick
    between `<` (which drops the tied sibling — a message gone from the chat
    forever) and `<=` (which hands the boundary row back every time, and never
    advances at all through a tie group bigger than a page). An id is exact, it
    round-trips as an opaque string, and the caller already holds it.

    So the order is `(created_at, id)` in both directions, in the cursor and out
    of it. Ordering by the timestamp alone would let two same-instant rows swap
    places between one page and the next, which shows up as a message the reader
    sees twice or never — precisely at the seam they just scrolled across."""
    query = select(Message).where(Message.channel_id == channel_id)
    if before is not None:
        anchor = session.get(Message, before)
        # A cursor from another channel is refused rather than ignored. The
        # query is channel-scoped either way, so nothing leaks; what a silent
        # fallback would do is return the *newest* page to a caller asking for
        # an older one, so "load older" appears to work while prepending
        # duplicates it already has.
        if anchor is None or anchor.channel_id != channel_id:
            raise HTTPException(status_code=400, detail="unknown cursor")
        query = query.where(
            or_(
                Message.created_at < anchor.created_at,
                and_(Message.created_at == anchor.created_at, Message.id < anchor.id),
            )
        )
    rows = list(
        session.execute(
            query.order_by(Message.created_at.desc(), Message.id.desc())
            .limit(min(limit, 500))
        ).scalars()
    )[::-1]
    # Names for the senders on this page only. This used to be _name_maps,
    # which reads every agent *and* every team in the project and throws the
    # teams away — two full scans on an endpoint the boardroom polls every two
    # seconds.
    sender_ids = {m.sender_agent_id for m in rows if m.sender_agent_id}
    agents = (
        {a.id: a.name for a in session.execute(
            select(Agent.id, Agent.name).where(Agent.id.in_(sender_ids))).all()}
        if sender_ids else {}
    )
    # People by name when there is more than one of them: a co-chair's line read
    # as "chairman" would put words in the chairman's mouth.
    channel = session.get(Channel, channel_id)
    project = session.get(Project, channel.project_id) if channel is not None else None
    people = {uid: (authority.speaker(session, project, uid) if project else None) or "chairman"
              for uid in {m.sender_user_id for m in rows if m.sender_user_id}}
    return [
        {
            "id": m.id, "from_agent": m.sender_agent_id, "from_user": m.sender_user_id,
            "from_name": (people[m.sender_user_id] if m.sender_user_id
                          else agents.get(m.sender_agent_id)),
            "thread_id": m.thread_id, "frozen": m.frozen_at is not None,
            "body": m.body, "at": m.created_at.isoformat(),
        }
        for m in rows
    ]


@router.get("/projects/{project_id}/runs", dependencies=[authority.READ])
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
            "priced_usd": r.priced_usd, "harness": r.harness,
            "refusals": r.refusal_count, "last_refusal": r.last_refusal,
            "last_refusal_at": r.last_refusal_at.isoformat() if r.last_refusal_at else None,
            "last_refusal_cleared_at": (r.last_refusal_cleared_at.isoformat()
                                        if r.last_refusal_cleared_at else None),
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


@router.get("/runs/{run_id}/trace", dependencies=[authority.READ])
def run_trace(run_id: str, session: Session = Depends(db)) -> PlainTextResponse:
    """The run's raw JSONL trace, as stashed in the artifact store at finalize."""
    run = session.get(Run, run_id)
    if run is None or not run.trace_path:
        raise HTTPException(status_code=404, detail="no trace recorded for this run")
    text = _artifact_text(run.trace_path, missing="trace not present in the artifact store")
    return PlainTextResponse(text[-500_000:])


@router.get("/runs/{run_id}/context", dependencies=[authority.READ])
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


@router.get("/evidence/{evidence_id}/snapshot", dependencies=[authority.READ])
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


@router.get("/projects/{project_id}/ledger", dependencies=[authority.READ])
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


@router.get("/projects/{project_id}/spend", dependencies=[authority.READ])
def spend(project_id: str, group_by: str = "model", days: int = 30,
          window: str = "days", session: Session = Depends(db)) -> dict:
    """Spend rollup, summed from the ledger.

    The ledger is where spend is counted. The budget meter reads it, the
    monthly wall reads it, and now so does this — same rows, same exclusion,
    same window when the caller asks for one. So the breakdown adds up to the
    number printed above it.

    It used to sum the run table, which is a different set of numbers: it
    misses spend the runs row never recorded, and it counts a run's tokens
    once where the ledger books a row per turn. Two numbers in one card that
    could not both be true.

    Runs supply the attribution and the ledger supplies the amounts — a run
    that no longer exists still spent its money, so it lands under
    `unattributed` rather than vanishing. Dollars sum non-null usd, so a
    subscription project contributes tokens only; clients switch display on
    billing_mode exactly as the budget meter does. `window=month` matches the
    meter's month-to-date; the default rolling window is for cards that stand
    on their own.
    """
    if group_by not in ("model", "provider", "team", "agent", "kind"):
        raise HTTPException(status_code=400,
                            detail="group_by must be model|provider|team|agent|kind")
    if window not in ("days", "month"):
        raise HTTPException(status_code=400, detail="window must be days|month")
    since = (
        utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if window == "month"
        else utcnow() - timedelta(days=max(1, min(days, 365)))
    )
    entries = list(
        session.execute(
            select(LedgerEntry).where(
                LedgerEntry.project_id == project_id,
                LedgerEntry.at >= since,
                # Revenue is money coming IN — the same exclusion the meter and
                # janitor.month_to_date apply, for the same reason.
                LedgerEntry.kind != LedgerKind.REVENUE,
            )
        ).scalars()
    )
    # The same join the CEO's activity rollup performs, so the chairman's card
    # and the CEO's answer can never resolve a run to a different seat.
    runs, agents, team_names = activity.attribution_index(
        session, {e.run_id for e in entries if e.run_id})

    def key_label(run: Run | None) -> tuple[str, str]:
        if run is None:
            return "unattributed", "unattributed"
        if group_by == "model":
            name = run.model or "unknown"
            return name, name
        if group_by == "provider":
            # A run from before providers were recorded was Anthropic's.
            name = run.provider or DEFAULT_PROVIDER
            seller = PROVIDERS.get(name)
            return name, seller.label if seller else name
        if group_by == "kind":
            return run.kind, run.kind
        agent = agents.get(run.agent_id) if run.agent_id else None
        if agent is None:
            return "unknown", "unknown"
        if group_by == "agent":
            return agent.id, agent.name
        # team: a teamless agent (the CEO) rolls up under its own name.
        if agent.team_id is None:
            return agent.id, agent.name
        return agent.team_id, team_names.get(agent.team_id, "unknown")

    buckets: dict[str, dict] = {}
    # One run can book several ledger rows (a chat session books a row per
    # turn), so runs are counted distinctly — the column says runs, not rows.
    counted: dict[str, set[str]] = {}
    for e in entries:
        run = runs.get(e.run_id) if e.run_id else None
        key, label = key_label(run)
        b = buckets.setdefault(key, {"key": key, "label": label, "runs": 0,
                                     "tokens_in": 0, "tokens_out": 0,
                                     "cache_creation": 0, "cache_read": 0, "usd": 0.0})
        b["tokens_in"] += e.tokens_in
        b["tokens_out"] += e.tokens_out
        b["cache_creation"] += e.cache_creation_tokens
        b["cache_read"] += e.cache_read_tokens
        b["usd"] += e.usd or 0.0
        if e.run_id and e.run_id not in counted.setdefault(key, set()):
            counted[key].add(e.run_id)
            b["runs"] += 1
    out = sorted(
        buckets.values(),
        key=lambda b: (-b["usd"], -(b["tokens_in"] + b["tokens_out"]
                                    + b["cache_creation"] + b["cache_read"])),
    )
    return {"group_by": group_by, "days": days, "window": window,
            "since": since.isoformat(), "rows": out}


@router.get("/projects/{project_id}/activity", dependencies=[authority.READ])
def project_activity(project_id: str, since: str = "today", until: str | None = None,
                     session: Session = Depends(db)) -> dict:
    """Who did the work, in a window the caller names.

    The chairman already had the money half of this at `/spend?group_by=agent`.
    This is the work half — runs, why each one woke, what it produced — and it
    exists in the first place so the CEO can read the same rows as its chairman
    rather than estimating them. Same producer, so the two screens agree.
    """
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="unknown project")
    cons = effective(session, project.id)
    try:
        start, end, label = activity.resolve_window(cons.timezone, since, until)
    except activity.WindowError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"window": label, **activity.roster_activity(session, project, start, end)}
@router.get("/projects/{project_id}/receipt", dependencies=[authority.READ])
def receipt(project_id: str, session: Session = Depends(db)) -> dict:
    """What this project cost, and what the money bought.

    Every other screen answers "is it working right now". This one answers the
    question a stranger asks first, and it answers it in two columns so the
    reader does the division rather than being handed a verdict: metered cost
    on one side, and on the other only the things no agent can move — a
    requirement the platform marked met after running its test, evidence the
    platform fetched and hashed, a close the janitor drew at random.

    Read-only, whole-life, and derived from the same producers the Review tab
    freezes. Safe to paste somewhere public, which is the point of it.
    """
    return receipt_projection(session, _project(session, project_id))


@router.get("/projects/{project_id}/evidence", dependencies=[authority.READ])
def evidence(project_id: str, limit: int = 100, session: Session = Depends(db)) -> list[dict]:
    rows = list(session.execute(
        select(Evidence)
        .where(Evidence.project_id == project_id)
        .order_by(Evidence.created_at.desc())
        .limit(min(limit, 500))
    ).scalars())
    # What each claim was proving, named here rather than joined on the client:
    # the record screen would otherwise have to pull the whole board to print
    # one column, and a claim with no test beside it is not a record of
    # anything.
    titles = {
        t.id: t.title
        for t in session.execute(
            select(Task).where(Task.id.in_([e.task_id for e in rows if e.task_id]))
        ).scalars()
    } if any(e.task_id for e in rows) else {}
    return [
        {
            "id": e.id, "kind": e.kind, "provenance": e.provenance,
            "verification": e.verification, "description": e.description,
            "source_url": e.source_url, "verifier_notes": e.verifier_notes,
            # The task this proves. The dashboard walks evidence -> task ->
            # hypothesis to answer "has this bet been proven?" — a question that
            # otherwise needs the reader to hold the join in their head.
            "task_id": e.task_id,
            "task_title": titles.get(e.task_id),
            # Whether `/observe/evidence/{id}/snapshot` has anything to serve.
            # Without this a reader is offered a link that 404s on every claim
            # the verifier never fetched, which is most internal ones.
            "has_snapshot": bool(e.snapshot_path),
            "at": e.created_at.isoformat(),
        }
        for e in rows
    ]


@router.get("/projects/{project_id}/hypotheses", dependencies=[authority.READ])
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


@router.get("/projects/{project_id}/approvals", dependencies=[authority.READ])
def approvals(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = list(session.execute(
        select(Approval)
        .where(Approval.project_id == project_id)
        .order_by(Approval.created_at.desc())
        .limit(100)
    ).scalars())
    # The outcome an agent filed after spending a grant: the newest per approval.
    outcomes: dict[str, dict] = {}
    if rows:
        for entry in session.execute(
            select(AuditLog)
            .where(AuditLog.project_id == project_id,
                   AuditLog.action == "data_write.outcome",
                   AuditLog.entity_id.in_([a.id for a in rows]))
            .order_by(AuditLog.at.asc())
        ).scalars():
            outcomes[entry.entity_id] = entry.detail or {}
    from yaaf.server import chairman_notes

    held = chairman_notes.replies_for(session, "approval", [a.id for a in rows])
    return [approval_row(a, outcome=outcomes.get(a.id), held_note=held.get(a.id))
            for a in rows]


@router.get("/projects/{project_id}/heads_ups", dependencies=[authority.READ])
def project_heads_ups(project_id: str, session: Session = Depends(db)) -> list[dict]:
    """What he asked to be told: the unnoted ones, then the last week's noted."""
    from yaaf.core.models import HeadsUp
    from yaaf.server import chairman_notes
    from yaaf.server import heads_up as heads

    project = _project(session, project_id)
    since = utcnow() - timedelta(days=7)
    rows = list(session.execute(
        select(HeadsUp).where(HeadsUp.project_id == project.id,
                              or_(HeadsUp.acknowledged_at.is_(None),
                                  HeadsUp.acknowledged_at >= since))
        .order_by(HeadsUp.created_at.desc()).limit(100)
    ).scalars())
    held = chairman_notes.replies_for(session, "heads_up", [h.id for h in rows])
    return [heads.row(h, held.get(h.id)) for h in rows]


@router.get("/projects/{project_id}/asks", dependencies=[authority.READ])
def asks(project_id: str, session: Session = Depends(db)) -> list[dict]:
    """The CEO's questions to the chairman, live and settled.

    Shaped like `/approvals` and read by the same screen, because they are the
    same kind of thing from his side: something clocked, addressed to him, that
    resolves with or without him. The difference is only what happens at the
    clock — an approval decides itself per the constitution, an ask hands back
    to the CEO's own stated default, which is why that sentence travels here.
    """
    from yaaf.core.models import Ask

    rows = session.execute(
        select(Ask)
        .where(Ask.project_id == project_id)
        .order_by(Ask.created_at.desc())
        .limit(100)
    ).scalars()
    return [
        {"id": a.id, "title": a.title, "body": a.body, "status": a.status,
         "asked_by": a.asked_by_agent_id,
         "answer_by": a.answer_by.isoformat(),
         "default_action": a.default_action,
         "asked_in_message_id": a.asked_in_message_id,
         "answered_at": a.answered_at.isoformat() if a.answered_at else None,
         "lapsed_at": a.lapsed_at.isoformat() if a.lapsed_at else None,
         "closed_at": a.closed_at.isoformat() if a.closed_at else None}
        for a in rows
    ]


@router.get("/projects/{project_id}/memories", dependencies=[authority.READ])
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

#: The flat listing's cap. The Deliverables screen no longer reads it — it asks
#: the index a question at a time below — but the MCP reader does, and a model
#: handed fifty thousand paths is not better informed than one handed two.
MAX_FILE_LISTING = 2000
_SKIPPED_DIRS = file_index.SKIPPED_DIRS


def _workspace_root(project: Project) -> Path:
    from yaaf.core.config import settings

    return Path(settings().workspaces_root) / project.slug


def _file_index(project: Project) -> file_index.Snapshot:
    return file_index.snapshot(project.id, _workspace_root(project), f"projects/{project.id}/")


#: Acceptance tests the platform runs itself. An observable is judged by a
#: person or the CEO; it can be met, but it was never machine-checked.
_MACHINE_CHECKED = ("command", "metric")


def _checked(satisfied: list) -> tuple[str | None, str | None]:
    """How the requirements a deliverable claims were settled, as one label.

    The proof lived on another page (Activity → Proof) and the file showed
    only that it "satisfies R-02" — a claim the agent made at publish, which
    says nothing about whether anything checked it. The label never upgrades a
    judgement to a verification: one observable among them makes it "judged".
    """
    if not satisfied:
        return None, None
    keys = ", ".join(r.key for r in satisfied)
    if any(r.status != RequirementStatus.MET for r in satisfied):
        unsettled = ", ".join(r.key for r in satisfied if r.status != RequirementStatus.MET)
        return "pending", f"{unsettled} not settled yet"
    if all((r.acceptance_test or {}).get("type") in _MACHINE_CHECKED for r in satisfied):
        return "verified", f"the platform ran the acceptance test for {keys}"
    return "judged", f"{keys} met by judgement — an observable no script can check"


def _receipt(a: Artifact, reqs: dict) -> dict:
    satisfied = [reqs[rid] for rid in a.requirement_ids or [] if rid in reqs]
    checked, why = _checked(satisfied)
    return {
        "id": a.id, "name": a.name, "version": a.version, "sha256": a.sha256,
        "size": a.size_bytes, "key": a.storage_key,
        "published_at": a.published_at.isoformat(),
        "published_by": a.published_by_agent_id,
        "satisfies": sorted(reqs[rid].key if rid in reqs else rid
                            for rid in a.requirement_ids or []),
        "checked": checked, "checked_why": why,
    }


@router.get("/projects/{project_id}/files", dependencies=[authority.READ])
def project_files(project_id: str, session: Session = Depends(db)) -> dict:
    """Everything the agents have written: the shared workspace (work in
    progress — every sandbox mounts it) and the artifact store (finalized
    deliverables, keyed projects/<project>/<run>/<name>)."""
    project = _project(session, project_id)
    snap = _file_index(project)
    # The index holds the workspace newest first, so this cut keeps this
    # morning's report rather than whatever sorts first by name.
    workspace = [
        {"path": i.path, "size": i.size, "modified": i.at}
        for i in snap.items if i.state == "working"
    ][:MAX_FILE_LISTING]
    rows = sorted(
        ({"key": v.key, "size": v.size, "modified": v.at, "run_id": v.run_id, "name": v.name}
         for i in snap.items if i.state == "published" for v in (i, *i.older)),
        key=lambda r: r["key"],
    )
    # The receipts: published rows with a real hash, a real timestamp, and the
    # requirements each claims to satisfy. The listing above is what the store
    # HOLDS; this is what was formally shipped, and the two can differ.
    reqs = {r.id: r for r in policy.scope_requirements(session, project.id)}
    published = [
        _receipt(a, reqs)
        for a in session.execute(
            select(Artifact).where(Artifact.project_id == project.id)
            .order_by(Artifact.published_at.desc()).limit(200)
        ).scalars()
    ]
    return {"workspace": workspace, "artifacts": rows, "published": published,
            "as_of": snap.as_of.isoformat(),
            # What the cap cost, so the screen can say so instead of implying
            # the project only ever wrote two thousand files.
            "workspace_truncated": snap.found - len(workspace)}


def _receipts(session: Session, project: Project, items: list) -> dict[str, dict]:
    """Receipts for exactly the rows being sent. Reading the newest two hundred
    and matching against those left every older deliverable looking unreceipted
    once a project had published more than that."""
    keys = sorted({v.key for i in items for v in (i, *i.older) if v.key})
    if not keys:
        return {}
    rows: list[Artifact] = []
    for at in range(0, len(keys), 500):
        rows += session.execute(
            select(Artifact).where(Artifact.project_id == project.id,
                                   Artifact.storage_key.in_(keys[at:at + 500]))
        ).scalars().all()
    if not rows:
        return {}
    reqs = {r.id: r for r in policy.scope_requirements(session, project.id)}
    return {a.storage_key: _receipt(a, reqs) for a in rows}


def _file_row(item: file_index.Item, pos: int | None, receipts: dict[str, dict],
              lean: bool = False) -> dict:
    row = {"id": item.id, "name": item.name, "dir": item.dir, "state": item.state,
           "ext": item.ext, "size": item.size, "at": item.at, "path": item.path,
           "key": item.key, "run_id": item.run_id, "version": item.version, "pos": pos}
    if lean:
        return row
    return {**row, "receipt": receipts.get(item.key) if item.key else None,
            "older": [_file_row(o, None, receipts) for o in item.older]}


def _files_query(q: str, state: str, type_: str, since: str, tz: str, sort: str,
                 desc: bool) -> file_index.Query:
    return file_index.Query.of(q=q, state=state, type=type_, since=since, tz=tz,
                               sort=sort, desc=desc)


@router.get("/projects/{project_id}/files/browse", dependencies=[authority.READ])
def project_files_browse(
    project_id: str,
    q: str = "", state: str = "all", type_: str = Query("", alias="type"),
    since: str = "all", tz: str = "UTC", sort: str = "at", desc: bool = True,
    expand: list[str] = Query([]), collapse: list[str] = Query([]),
    more: list[str] = Query([]),
    session: Session = Depends(db),
) -> dict:
    """The Deliverables screen, a screenful at a time: every day folder the
    filters leave standing with its counts, and rows only for the folders that
    are open. `expand`/`collapse` are day keys the reader toggled; `more` is
    `<day>:<rows>` for a folder they asked to see further down."""
    project = _project(session, project_id)
    snap = _file_index(project)
    query = _files_query(q, state, type_, since, tz, sort, desc)
    result = file_index.view(snap, query)
    caps: dict[str, int] = {}
    for spec in more:
        day, _, n = spec.rpartition(":")
        if day and n.isdigit():
            caps[day] = int(n)
    plan = file_index.folders(result, query, set(expand), set(collapse), caps)
    receipts = _receipts(session, project, [
        item for day, is_open, cap in plan if is_open for item in day.items[:cap]])
    return {
        "as_of": snap.as_of.isoformat(),
        "today": result.today,
        "total": result.total,
        "published_total": result.published_total,
        "today_total": result.today_total,
        "matched": result.matched,
        "states": result.states,
        "types": result.types,
        "days": [
            {"key": day.key, "count": len(day.items), "published": day.published,
             "open": is_open, "cap": cap,
             "rows": ([_file_row(item, day.start + n, receipts)
                       for n, item in enumerate(day.items[:cap])] if is_open else None)}
            for day, is_open, cap in plan
        ],
        # What the index could not hold, so the screen can say so rather than
        # imply the project only ever wrote what it lists.
        "working_kept": snap.kept,
        "working_dropped": snap.found - snap.kept,
        "walk_stopped": snap.stopped,
    }


@router.get("/projects/{project_id}/files/browse/rows", dependencies=[authority.READ])
def project_files_rows(
    project_id: str,
    q: str = "", state: str = "all", type_: str = Query("", alias="type"),
    since: str = "all", tz: str = "UTC", sort: str = "at", desc: bool = True,
    offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=250_000),
    lean: bool = False,
    session: Session = Depends(db),
) -> dict:
    """A run of the filtered list in screen order, across day folders: the
    reader stepping to the next file, the Overview's newest three, an export.
    `lean` drops receipts and earlier versions, which an export does not use."""
    project = _project(session, project_id)
    snap = _file_index(project)
    result = file_index.view(snap, _files_query(q, state, type_, since, tz, sort, desc))
    window = result.flat[offset:offset + limit]
    receipts = {} if lean else _receipts(session, project, window)
    return {"as_of": snap.as_of.isoformat(), "matched": result.matched, "offset": offset,
            "rows": [_file_row(item, offset + n, receipts, lean)
                     for n, item in enumerate(window)]}


@router.get("/projects/{project_id}/files/browse/entry", dependencies=[authority.READ])
def project_files_entry(
    project_id: str, id: str,
    q: str = "", state: str = "all", type_: str = Query("", alias="type"),
    since: str = "all", tz: str = "UTC", sort: str = "at", desc: bool = True,
    session: Session = Depends(db),
) -> dict:
    """One file by id — a working file's `w:<path>`, or a storage key, earlier
    versions included — and where it sits under these filters, if it does.
    The Overview links to a deliverable by key; this is how that link lands
    on the file instead of on a list the chairman has to search again."""
    project = _project(session, project_id)
    snap = _file_index(project)
    item = snap.find(id)
    if item is None:
        raise HTTPException(status_code=404, detail="no such file")
    result = file_index.view(snap, _files_query(q, state, type_, since, tz, sort, desc))
    pos = next((n for n, hit in enumerate(result.flat) if hit is item), None)
    receipts = _receipts(session, project, [item])
    return {"as_of": snap.as_of.isoformat(), "matched": result.matched,
            "entry": _file_row(item, pos, receipts)}


def _as_download(body: bytes | Path, name: str) -> Response:
    """Bytes in hand, or a path to stream — either way, a download and never a
    page. `Content-Disposition: attachment` plus `application/octet-stream` is
    what keeps agent-written HTML from rendering on this origin, where the
    chairman's admin token lives; both halves are set here so neither can be
    forgotten by one of the two branches."""
    headers = {"Content-Disposition": f'attachment; filename="{name.replace(chr(34), "")}"'}
    if isinstance(body, Path):
        return FileResponse(body, media_type="application/octet-stream", headers=headers)
    return Response(content=body, media_type="application/octet-stream", headers=headers)


@router.get("/projects/{project_id}/files/content", dependencies=[authority.READ])
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
        # Buffered, and safely so: `artifact_put` refuses anything over 100 MB,
        # so the worst case is one 100 MB read held in memory per download. The
        # workspace below has no such ceiling — it is where agents actually
        # work, and a render lands there at its real size.
        return _as_download(data, artifact.rsplit("/", 1)[-1])

    root = _workspace_root(project).resolve()
    target = (root / path).resolve()
    if (not target.is_relative_to(root)
            or any(part in _SKIPPED_DIRS for part in Path(path).parts)):
        raise HTTPException(status_code=404, detail="no such file")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="no such file")
    # Streamed off disk rather than read into memory. This replaces a 20 MB
    # ceiling that answered 413, which was never a policy about what the
    # chairman may download — it was the size at which `read_bytes()` on a
    # request thread stopped being survivable, and the number was load-bearing
    # for the server rather than for him. It made the video desk's own output
    # unreachable from the page that lists it: its cuts are 40-124 MB each, so
    # every real render 413'd while the notes beside them downloaded fine.
    return _as_download(target, target.name)


@router.get("/projects/{project_id}/suggestions", dependencies=[authority.READ])
def suggestions(project_id: str, session: Session = Depends(db)) -> list[dict]:
    rows = session.execute(
        select(Suggestion)
        .where(Suggestion.project_id == project_id)
        .order_by(Suggestion.created_at.desc())
        .limit(100)
    ).scalars()
    return [{"id": s.id, "by": s.by_agent_id, "text": s.text,
             "consumed": s.consumed_at is not None} for s in rows]


@router.get("/projects/{project_id}/cycles", dependencies=[authority.READ])
def cycles(project_id: str, session: Session = Depends(db)) -> dict:
    """The rhythm's record: every turn, newest first, narratives whole. The
    narratives are the learning loop's storage — clipping them here would
    show the chairman less than his own CEO reads."""
    from yaaf.core.models import Cycle, CycleStatus

    project = _project(session, project_id)
    rows = list(session.execute(
        select(Cycle).where(Cycle.project_id == project.id)
        .order_by(Cycle.ordinal.desc()).limit(50)
    ).scalars())
    def _row(c) -> dict:
        return {"id": c.id, "ordinal": c.ordinal, "status": c.status,
                "goal": c.goal,
                "due_at": c.due_at.isoformat() if c.due_at else None,
                "opened_at": c.opened_at.isoformat(),
                "closed_at": c.closed_at.isoformat() if c.closed_at else None,
                "narrative": c.narrative}

    return {
        "current": next((_row(c) for c in rows if c.status == CycleStatus.OPEN), None),
        "history": [_row(c) for c in rows if c.status != CycleStatus.OPEN],
    }


@router.get("/projects/{project_id}/reviews", dependencies=[authority.READ])
def reviews(project_id: str, session: Session = Depends(db)) -> dict:
    """The standing meeting's record: the open one (if any) and the history,
    newest first. The numbers on each are the janitor's frozen snapshot from
    the moment it opened — the meeting's record does not drift."""
    project = _project(session, project_id)
    rows = list(session.execute(
        select(Review).where(Review.project_id == project.id)
        .order_by(Review.number.desc()).limit(50)
    ).scalars())

    def view(r: Review) -> dict:
        return {
            "id": r.id, "number": r.number, "status": r.status,
            "scheduled_for": r.scheduled_for.isoformat(),
            "opened_at": r.opened_at.isoformat(),
            "numbers": r.numbers,
            "narrative": r.narrative,
            "recommended": r.recommended,
            "narrative_filed_at": (r.narrative_filed_at.isoformat()
                                   if r.narrative_filed_at else None),
            "decisions": r.decisions or [],
            "closed_at": r.closed_at.isoformat() if r.closed_at else None,
        }

    open_review = next((view(r) for r in rows if r.status == ReviewStatus.OPEN), None)
    return {
        "open": open_review,
        "history": [view(r) for r in rows if r.status != ReviewStatus.OPEN],
    }


# Every action an agent takes under the authority dial, and nothing else. The
# keys are the four write paths that exist (agent.py: scope_act, commitment
# self-accept, commitment reset, credential re-scope) — a whitelist rather than
# a prefix match, so a new audit action has to be added here deliberately
# instead of appearing on the chairman's screen by accident.
_SELF_EXERCISE_ACTIONS = {
    "scope.self": "the plan",
    "commitment.self_accept": "the date",
    "commitment.self_move": "the date",
    "credential.scope": "a credential",
}


@router.get("/projects/{project_id}/ceo_decisions", dependencies=[authority.READ])
def ceo_decisions(project_id: str, limit: int = 50, session: Session = Depends(db)) -> list[dict]:
    """What the CEO decided on its own authority — the disclosure half of the
    autonomy dial, as a screen rather than only as an email.

    Autonomy was always audited and always emailed, but it had nowhere to be
    READ: a chairman who had delegated the plan, the date and the verdicts
    could open the dashboard and find no trace of a single decision made in his
    name. The one thing that did show up was the approval the CEO filed when it
    ran out of verbs — so the more autonomous the project, the emptier the
    screen and the more misleading the one row on it.

    Nothing here is pending and nothing expires. It is a record to read, and
    everything in it is reversible from the Plan screen or Settings.
    """
    project = _project(session, project_id)
    rows = session.execute(
        select(AuditLog)
        .where(AuditLog.project_id == project.id,
               AuditLog.action.in_(sorted(_SELF_EXERCISE_ACTIONS)))
        .order_by(AuditLog.at.desc())
        .limit(max(1, min(limit, 200)))
    ).scalars()
    names = {a.id: a.name for a in session.execute(
        select(Agent).where(Agent.project_id == project.id)).scalars()}
    out = []
    for row in rows:
        actor = row.actor.removeprefix("agent:")
        detail = row.detail or {}
        out.append({
            "id": row.id,
            "at": row.at.isoformat(),
            "action": row.action,
            "about": _SELF_EXERCISE_ACTIONS[row.action],
            "by": names.get(actor, "CEO"),
            "what": _self_exercise_summary(row.action, detail),
            "reason": detail.get("reason"),
            "detail": detail,
        })
    return out


def _self_exercise_summary(action: str, detail: dict) -> str:
    """One line a human reads, not a dict they have to decode."""
    if action == "scope.self":
        kind, what = detail.get("kind", "changed"), detail.get("detail")
        return what or f"{kind} on the plan"
    if action == "commitment.self_accept":
        return (f"Signed the delivery date: {(detail.get('deliver_by') or '')[:10]}"
                f" over {len(detail.get('scope') or [])} requirement(s)")
    if action == "commitment.self_move":
        nth = detail.get("reset_count")
        tail = f" (reset #{nth})" if nth and nth > 1 else ""
        return (f"Reset the delivery date: {(detail.get('from') or '')[:10]} → "
                f"{(detail.get('to') or '')[:10]}{tail}")
    if action == "credential.scope":
        was = ", ".join(detail.get("was") or []) or "nobody"
        now = ", ".join(detail.get("now") or []) or "nobody"
        return f"Re-scoped credential {detail.get('name')}: {was} → {now}"
    return action


@router.get("/projects/{project_id}/audit_samples", dependencies=[authority.READ])
def audit_samples(project_id: str, session: Session = Depends(db)) -> dict:
    """The sampled audit's desk: pending draws first, then the recent record.
    Chairman-only by router auth — no agent route serves any of this, because
    a sample an agent can see coming is a sample that audits nothing."""
    project = _project(session, project_id)
    tasks = {
        t.id: t for t in session.execute(
            select(Task).where(Task.project_id == project.id)
        ).scalars()
    }
    rows = list(session.execute(
        select(AuditSample).where(AuditSample.project_id == project.id)
        .order_by(AuditSample.drawn_at.desc()).limit(200)
    ).scalars())

    def view(s: AuditSample) -> dict:
        task = tasks.get(s.task_id)
        return {
            "id": s.id, "task_id": s.task_id, "status": s.status,
            "drawn_at": s.drawn_at.isoformat(),
            "expires_at": s.expires_at.isoformat() if s.expires_at else None,
            "decided_at": s.decided_at.isoformat() if s.decided_at else None,
            "note": s.note,
            "task_title": task.title if task else "(task gone)",
            "test_target": ((task.success_test or {}).get("target", "") if task else ""),
            "claimed_evidence": (task.completion_evidence if task else None),
        }

    pending = [view(s) for s in rows if s.status == AuditSampleStatus.PENDING]
    decided = [view(s) for s in rows if s.status != AuditSampleStatus.PENDING
               and s.status != AuditSampleStatus.SKIPPED]
    considered = len(rows)
    return {
        "pending": pending,
        "recent": decided[:50],
        "coverage": {
            "considered": considered,
            "sampled": len([s for s in rows if s.status != AuditSampleStatus.SKIPPED]),
            "expired_unreviewed": len(
                [s for s in rows if s.status == AuditSampleStatus.EXPIRED]),
        },
    }


# The whole queue is the default, but it is still a page a browser has to
# draw. Above this, the honest offer is the export — and the cut is always
# stated, never silent.
AUDIT_PAGE = 200


@router.get("/projects/{project_id}/spot_audit", dependencies=[authority.READ])
def spot_audit(project_id: str, n: int | None = None,
               session: Session = Depends(db)) -> dict:
    """Everything a human has not judged yet — or, with `n`, a random sample of
    that size. LLM reviewers approve nearly everything put in front of them;
    the chairman is the layer that does not.

    The sample used to be the only mode, and it turned the page into a lottery.
    Five at random, redealt on every verdict, drawn from the most recent fifty:
    a chairman working through a backlog could not tell what he had already
    seen, could not finish, and could not reach anything older than the last
    fifty rows — while the counter above the button faithfully told him he had
    checked 0 of 14. Unjudged rows come first so the queue drains from the top;
    `n` remains for a genuine spot check.
    """
    project = _project(session, project_id)
    evidence = list(
        session.execute(
            select(Evidence)
            .where(Evidence.project_id == project.id)
            .order_by(Evidence.created_at.desc())
        ).scalars()
    )
    done = list(
        session.execute(
            select(Task)
            .where(Task.project_id == project.id, Task.status == TaskStatus.DONE)
            .order_by(Task.updated_at.desc())
        ).scalars()
    )
    # A verdict on evidence writes verification; on a task it writes
    # audit_status. Either way, "nobody has judged this" is what sorts first.
    unjudged_evidence = [e for e in evidence
                         if e.verification == VerificationStatus.PENDING]
    unjudged_done = [t for t in done if not t.audit_status]

    if n is None:
        # sorted() is stable, so this lifts the unjudged rows to the top and
        # leaves both groups in the newest-first order they arrived in.
        sample_evidence = sorted(
            evidence, key=lambda e: e.verification != VerificationStatus.PENDING
        )[:AUDIT_PAGE]
        sample_tasks = sorted(done, key=lambda t: bool(t.audit_status))[:AUDIT_PAGE]
    else:
        # A spot check draws from what has not been judged; handing back a row
        # the chairman already ruled on spends his two minutes on his own
        # answer. Only when there is nothing left unjudged does it re-draw from
        # everything, so pressing the button never returns an empty page.
        pool_e = unjudged_evidence or evidence
        pool_t = unjudged_done or done
        sample_evidence = random.sample(pool_e, min(n, len(pool_e)))
        sample_tasks = random.sample(pool_t, min(n, len(pool_t)))

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
        "sampled": n is not None,
        "unjudged": {"evidence": len(unjudged_evidence), "tasks": len(unjudged_done)},
        "totals": {"evidence": len(evidence), "tasks": len(done)},
        "note": "POST /admin/evidence/{id}/audit or /admin/tasks/{id}/audit "
                "with ok=false to fail a bad sample.",
    }


@router.get("/agents/{agent_id}", dependencies=[authority.READ])
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
