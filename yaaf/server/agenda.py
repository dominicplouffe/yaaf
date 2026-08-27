"""The agenda: what this company is going to do, before it does it.

The desk answers "what breaks if I close this tab" — ranked by time to
consequence, which is the right question when something is wrong. Nothing
anywhere answered the flatter one a chairman asks about a thing that runs
without him: *what is it going to do this week?* An autonomous system that
will not show you its schedule is a haunted one, and every fact needed to draw
it was already in the rows — the wake times, the meeting, the rhythm, the kill
dates, the promise.

Two shapes, because two things are genuinely different:

**Rhythms** recur. A daily wake at 09:00 is one fact with many occurrences, and
listing fourteen of them would bury the four dated events that actually matter.
So a rhythm carries its `every` sentence, its true `next` firing, and the local
days it touches — enough to paint a strip without pretending each tick is news.

**Events** happen once, on a date, and most of them have a consequence
attached: an approval auto-decides, a bet's work blocks, a promise comes due.

Everything is read in the constitution's one timezone, the same one the
dispatcher reads — a calendar that disagreed with the wake it is drawing would
be worse than no calendar.

What is deliberately *not* here: the date the budget's burn rate lands on the
cap. That arithmetic exists on the desk, and a second copy of it here would be
a second definition to drift. The agenda draws dates the platform holds, and
says so when one is missing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc, effective, parse_hhmm
from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Approval,
    ApprovalStatus,
    AuditSample,
    AuditSampleStatus,
    Commitment,
    CommitmentStatus,
    Heartbeat,
    Hypothesis,
    HypothesisStatus,
    Project,
    ProjectMode,
    ProjectStatus,
    Review,
    ReviewStatus,
    Team,
    utcnow,
)

#: How far ahead the strip looks by default. Two weeks is one review interval
#: plus slack, which is the span over which a chairman's plans are still soft.
DEFAULT_DAYS = 14
MAX_DAYS = 90


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _zone(cons: ConstitutionDoc) -> ZoneInfo:
    try:
        return ZoneInfo(cons.timezone)
    except Exception:  # pragma: no cover - the rule validates this on write
        return ZoneInfo("UTC")


def _local_day(at: datetime, tz: ZoneInfo) -> str:
    return at.astimezone(tz).date().isoformat()


def _event(at: datetime, kind: str, label: str, tz: ZoneInfo, *,
           detail: str | None = None, tone: str = "flat",
           where: str | None = None) -> dict:
    """One dated thing.

    `tone` says what kind of date it is, and the vocabulary is small on
    purpose: `consequence` resolves without the chairman, `deadline` is a
    promise coming due, `meeting` wants him in the room, `projection` is the
    platform's arithmetic rather than a commitment anybody made. A strip that
    drew all four identically would be a strip that says nothing.
    """
    at = _aware(at)
    return {
        "at": at.isoformat(),
        "day": _local_day(at, tz),
        "kind": kind,
        "label": label,
        "detail": detail,
        "tone": tone,
        "where": where,
    }


def _rhythm(kind: str, label: str, every: str, nxt: datetime | None,
            horizon: datetime, tz: ZoneInfo, *, detail: str | None = None,
            daily: bool = True) -> dict:
    """A recurring wake, reduced to what a fourteen-day strip can show: the
    sentence, the true next firing, and which local days it lands on."""
    days: list[str] = []
    if nxt is not None and nxt <= horizon:
        if daily:
            cursor = nxt.astimezone(tz).date()
            last = horizon.astimezone(tz).date()
            while cursor <= last:
                days.append(cursor.isoformat())
                cursor += timedelta(days=1)
        else:
            days.append(_local_day(nxt, tz))
    return {
        "kind": kind,
        "label": label,
        "detail": detail,
        "every": every,
        "next": nxt.isoformat() if nxt else None,
        "days": days,
    }


def _next_daily(session: Session, agent: Agent, raw: str, tz: ZoneInfo,
                now: datetime, beat_name: str) -> datetime | None:
    """When this wall-clock wake fires next, read exactly the way the
    dispatcher decides it has fired: once per local day, at or after the time,
    deduped on the agent's heartbeat row. A calendar that computed it a second
    way would eventually draw a wake the dispatcher was not going to run."""
    when = parse_hhmm(raw)
    if when is None:
        return None
    local_now = now.astimezone(tz)
    target = local_now.replace(hour=when.hour, minute=when.minute,
                               second=0, microsecond=0)
    beat = session.execute(
        select(Heartbeat).where(Heartbeat.agent_id == agent.id,
                                Heartbeat.name == beat_name)
    ).scalar_one_or_none()
    fired_today = (
        beat is not None
        and _aware(beat.last_fired_at).astimezone(tz).date() >= local_now.date()
    )
    if local_now >= target and not fired_today:
        # The time has passed and today's has not fired: the next janitor tick
        # runs it. "Due now" is the honest answer, not tomorrow.
        return now
    if local_now < target:
        return target.astimezone(UTC)
    return (target + timedelta(days=1)).astimezone(UTC)


def _wake_rhythms(session: Session, project: Project, cons: ConstitutionDoc,
                  now: datetime, horizon: datetime, tz: ZoneInfo) -> list[dict]:
    rhythms: list[dict] = []
    agents = list(session.execute(
        select(Agent).where(Agent.project_id == project.id,
                            Agent.status == AgentStatus.ACTIVE)
    ).scalars())
    teams = {
        t.id: t.name
        for t in session.execute(
            select(Team).where(Team.project_id == project.id)
        ).scalars()
    }
    ceo = next((a for a in agents if a.kind == AgentKind.CEO), None)

    if ceo is not None and cons.ceo_daily_at:
        rhythms.append(_rhythm(
            "ceo_daily", f"{ceo.name} — daily wake",
            f"every day at {cons.ceo_daily_at} ({cons.timezone})",
            _next_daily(session, ceo, cons.ceo_daily_at, tz, now, "daily"),
            horizon, tz, detail="the standup: the CEO plans on a wall clock",
        ))

    for agent in sorted(agents, key=lambda a: a.name):
        if not agent.daily_at:
            continue
        where = teams.get(agent.team_id) if agent.team_id else "no team"
        rhythms.append(_rhythm(
            "agent_daily", f"{agent.name} — daily wake",
            f"every day at {agent.daily_at} ({cons.timezone})",
            _next_daily(session, agent, agent.daily_at, tz, now, "agent_daily"),
            horizon, tz, detail=where,
        ))

    if ceo is not None and cons.ceo_heartbeat_hours:
        beat = session.execute(
            select(Heartbeat).where(Heartbeat.agent_id == ceo.id,
                                    Heartbeat.name == "planning")
        ).scalar_one_or_none()
        hours = float(cons.ceo_heartbeat_hours)
        nxt = (max(now, _aware(beat.last_fired_at) + timedelta(hours=hours))
               if beat is not None else now)
        idle_note = (
            "skipped when the board is empty and no bet is live"
            if not cons.ceo_heartbeat_always
            else "fires even with an empty board"
        )
        rhythms.append(_rhythm(
            "ceo_planning", f"{ceo.name} — planning wake",
            f"every {hours:g}h", nxt, horizon, tz, detail=idle_note,
        ))
    return rhythms


def _review_event(session: Session, project: Project, cons: ConstitutionDoc,
                  tz: ZoneInfo) -> tuple[dict | None, str | None]:
    """The standing meeting, on the janitor's own arithmetic. Returns the event
    or the reason there isn't one — an absence a chairman can read beats a
    calendar that is simply blank where his weekly meeting should be."""
    latest = session.execute(
        select(Review).where(Review.project_id == project.id)
        .order_by(Review.number.desc()).limit(1)
    ).scalar_one_or_none()
    if latest is not None and latest.status == ReviewStatus.OPEN:
        return None, (f"Review #{latest.number} is open and unattended — the next one "
                      "is not scheduled until you close it.")
    interval = timedelta(days=cons.review_interval_days)
    if latest is None:
        due = _aware(project.created_at) + interval
        number = 1
    else:
        due = max(_aware(latest.scheduled_for) + interval,
                  _aware(latest.closed_at or latest.opened_at))
        number = latest.number + 1
    return _event(
        due, "review", f"Review #{number}", tz,
        detail=("opened by the janitor with zero tokens, whatever the budget and "
                "whatever is on the board"),
        tone="meeting", where="review",
    ), None


def _cycle_event(session: Session, project: Project, cons: ConstitutionDoc,
                 tz: ZoneInfo) -> dict | None:
    if project.mode != ProjectMode.OPERATE:
        return None
    from yaaf.server import policy
    from yaaf.server.janitor import next_cycle_roll

    cycle = policy.current_cycle(session, project.id)
    if cycle is None:
        return None
    rolls_at = next_cycle_roll(_aware(cycle.opened_at), cons, cycle)
    source = ("the date the world set for this cycle" if cycle.due_at
              else f"the {cons.cycle_frequency} rhythm")
    return _event(
        rolls_at, "cycle_roll", f"Cycle {cycle.ordinal} rolls", tz,
        detail=(f"{source} — unfinished scope carries forward and the plan gate "
                "reopens"),
        tone="deadline", where="plan",
    )


def _commitment_events(session: Session, project: Project, tz: ZoneInfo) -> list[dict]:
    commitment = session.execute(
        select(Commitment).where(
            Commitment.project_id == project.id,
            Commitment.status.in_([CommitmentStatus.ACCEPTED, CommitmentStatus.AT_RISK]),
        ).order_by(Commitment.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if commitment is None:
        return []
    out = [_event(
        commitment.deliver_by, "deliver_by", "Promised delivery", tz,
        detail=(f"{len(commitment.scope_snapshot or [])} requirement(s) were in scope "
                "when you signed"),
        tone="deadline", where="plan",
    )]
    projected = _aware(commitment.projected_done_at)
    if projected is not None:
        late = projected > _aware(commitment.deliver_by)
        out.append(_event(
            projected, "projected", "Projected delivery", tz,
            detail=("the platform's own projection from measured pace" +
                    (" — later than the promise, which is why scope is frozen"
                     if late else " — inside the promise")),
            tone="projection", where="plan",
        ))
    return out


def _clock_events(session: Session, project: Project, tz: ZoneInfo,
                  horizon: datetime, now: datetime) -> list[dict]:
    """The things that resolve themselves. Same population the desk ranks — it
    asks which is soonest, this one asks which day it lands on."""
    out: list[dict] = []
    for approval in session.execute(
        select(Approval).where(
            Approval.project_id == project.id,
            Approval.status == ApprovalStatus.PENDING,
        ).order_by(Approval.expires_at)
    ).scalars():
        expires = _aware(approval.expires_at)
        if expires > horizon:
            continue
        out.append(_event(
            expires, "approval_expiry", f"“{approval.title[:70]}” auto-decides", tz,
            detail=f"{approval.kind.replace('_', ' ')} — silence is a decision here",
            tone="consequence", where="records/decisions",
        ))
    for bet in session.execute(
        select(Hypothesis).where(
            Hypothesis.project_id == project.id,
            Hypothesis.status == HypothesisStatus.ACTIVE,
        ).order_by(Hypothesis.kill_date)
    ).scalars():
        kill = _aware(bet.kill_date)
        if kill > horizon:
            continue
        out.append(_event(
            kill, "kill_date", f"Bet dies: “{bet.statement[:70]}”", tz,
            detail="its work blocks unless you extend it, once, with a reason",
            tone="consequence", where="work",
        ))
    samples = list(session.execute(
        select(AuditSample).where(
            AuditSample.project_id == project.id,
            AuditSample.status == AuditSampleStatus.PENDING,
            AuditSample.expires_at.is_not(None),
        ).order_by(AuditSample.expires_at)
    ).scalars())
    in_window = [s for s in samples if _aware(s.expires_at) <= horizon]
    if in_window:
        soonest = _aware(in_window[0].expires_at)
        out.append(_event(
            soonest, "audit_expiry",
            f"{len(in_window)} spot-check(s) expire unreviewed", tz,
            detail="nothing blocks — those closes just stand unaudited",
            tone="consequence", where="work",
        ))
    if project.mode == ProjectMode.EXPLORE and project.deadline is not None:
        deadline = _aware(project.deadline)
        if now <= deadline <= horizon:
            out.append(_event(
                deadline, "explore_deadline", "The deadline you set", tz,
                detail="nothing pauses itself — you are told, and the judgment is yours",
                tone="deadline", where="plan",
            ))
    return out


def _budget_reset_event(now: datetime, horizon: datetime, tz: ZoneInfo) -> dict | None:
    """The budget is measured month-to-date, so the 1st is a real event on this
    company's calendar: the meter goes back to zero and a company paused for
    an exhausted cap becomes affordable again — though not restarted, which
    stays the chairman's decision."""
    year, month = now.year, now.month
    nxt = datetime(year + (month // 12), (month % 12) + 1, 1, tzinfo=UTC)
    if nxt > horizon:
        return None
    return _event(
        nxt, "budget_reset", "Monthly budget resets", tz,
        detail="the spend meter returns to zero; a paused company still waits for you",
        tone="flat", where="settings",
    )


def project_agenda(session: Session, project: Project,
                   cons: ConstitutionDoc | None = None,
                   days: int = DEFAULT_DAYS) -> dict:
    """Everything this company is scheduled to do in the next `days`."""
    cons = cons or effective(session, project.id)
    days = max(1, min(int(days), MAX_DAYS))
    now = utcnow()
    horizon = now + timedelta(days=days)
    tz = _zone(cons)

    notes: list[str] = []
    events: list[dict] = []
    rhythms: list[dict] = []

    # A company that is not running has no schedule to draw — the dispatcher
    # will not wake anybody and the janitor's rhythms do not fire against a
    # terminal project. Saying that plainly beats drawing a week of wakes that
    # are never going to happen.
    live = project.status == ProjectStatus.RUNNING
    if not live:
        notes.append(
            f"This company is {project.status}, so nothing below is scheduled to run. "
            + (project.status_reason or "")
        )
    else:
        rhythms = _wake_rhythms(session, project, cons, now, horizon, tz)
        review, why_not = _review_event(session, project, cons, tz)
        if review is not None and datetime.fromisoformat(review["at"]) <= horizon:
            events.append(review)
        elif review is not None:
            notes.append(
                f"The next review is {review['at'][:10]}, past this window.")
        if why_not:
            notes.append(why_not)
        cycle = _cycle_event(session, project, cons, tz)
        if cycle is not None and datetime.fromisoformat(cycle["at"]) <= horizon:
            events.append(cycle)
        events += [
            event for event in _commitment_events(session, project, tz)
            if datetime.fromisoformat(event["at"]) <= horizon
        ]
        events += _clock_events(session, project, tz, horizon, now)
        reset = _budget_reset_event(now, horizon, tz)
        if reset is not None:
            events.append(reset)
        if not cons.ceo_daily_at and not any(r["kind"] == "agent_daily" for r in rhythms):
            notes.append(
                "Nobody is on a wall-clock wake. The CEO still plans on its heartbeat; "
                "a daily time is how you get a standup.")

    events.sort(key=lambda e: e["at"])
    first = now.astimezone(tz).date()
    return {
        "as_of": now.isoformat(),
        "days": days,
        "horizon": horizon.isoformat(),
        "timezone": cons.timezone,
        "first_day": first.isoformat(),
        "day_list": [(first + timedelta(days=i)).isoformat() for i in range(days)],
        "live": live,
        "rhythms": rhythms,
        "events": events,
        "notes": notes,
    }
