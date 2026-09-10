"""Flags: the platform's obligation on the CEO to speak.

Every other mechanism between the CEO and its chairman is a brake. The cadence
refuses an unprompted status between slots. `may_post_status_now` is a
permission. Nothing in the platform ever required the CEO to say anything — so
the failure that prompted all of this was not really a failure of information.
The CEO had written "run ration is the binding constraint, not the analysis" in
its own run summary three hours before it told its chairman "solid progress",
and the platform had handed it the exact sentence to quote.

Better numbers alone would not have changed that sentence. What changes it is a
flag: opened by arithmetic or a failed test rather than by the CEO's judgement,
overriding the cadence brake, closable only by being said out loud with a
proposal — and, while it is not, visible to the chairman as unraised. That last
property is what makes the mechanism trustworthy without asking anyone to trust
it: when it stops working, the silence shows.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Cycle,
    CycleStatus,
    Flag,
    FlagKind,
    FlagStatus,
    Project,
    ProjectMode,
    Recurrence,
    Requirement,
    RequirementCycleResult,
    RequirementStatus,
    utcnow,
)
from yaaf.server import metrics, policy

log = logging.getLogger("yaaf.flags")

#: Flags whose condition is a moment rather than a state — a cycle that already
#: rolled cannot un-roll, and a month that went over budget stays gone over —
#: so `evaluate` never resolves them. What closes them is the chairman
#: answering, which is why `acknowledge` exists: for years the docstring here
#: said "only the CEO raising them closes them", but raising sets RAISED, and
#: RAISED is precisely the state his desk drew. Nothing ever closed them.
HISTORICAL_KINDS = (FlagKind.CYCLE_MISSED, FlagKind.STANDING_MISSED)

#: The moments, for acknowledgement's purposes. `budget_overrun` is not in
#: HISTORICAL_KINDS because `evaluate` never sees it at all — it is opened from
#: the janitor's budget sweep against one month's overrun record — but it is
#: the same shape of news: that month is over, and no arithmetic will ever
#: un-spend it.
ACK_CLOSES = (*HISTORICAL_KINDS, FlagKind.BUDGET_OVERRUN)

#: Gates that mean a WALL stopped the work, as opposed to the project having
#: scheduled its own board shut. `dispatcher.activity_state` names each of
#: these; anything outside this set is the project's own doing or the
#: chairman's, and is not an excuse for an idle desk.
CAP_GATES = ("daily_runs", "pacing", "concurrency", "inbox_runs",
             "wake_ceiling", "governance_reserve")


def _scoreline(cycle) -> str:
    """What the cycle actually did, in the flag that makes the CEO explain it.

    Every FlagKind is supposed to be arithmetic. This one carried the ordinal,
    the timestamp and the goal — and no numbers, so "missed" read the same
    whether nine of ten requirements landed or none did. The CEO on the desk
    that prompted this re-explained that distinction to its chairman six times
    in five days, by hand, out of its own run allowance. The numbers were always
    on the row; they were just never in the sentence.
    """
    card = cycle.scorecard or {}
    if not card.get("accepted"):
        return "."
    met, accepted = card.get("met", 0), card["accepted"]
    carried = ", ".join(card.get("carried_keys") or []) or "none"
    return (f": {met} of {accepted} met, {card.get('carried', 0)} carried forward "
            f"({carried}).")


def open_flag(session: Session, project: Project, kind: str, summary: str,
              arithmetic: dict | None = None, subject_id: str | None = None) -> Flag | None:
    """Open one, unless this exact condition is already flagged.

    Deduped on (kind, subject, live) because the trip conditions are recomputed
    every janitor tick: a capacity forecast that stays negative for six hours
    is one piece of news, not seventy-two.
    """
    live = session.execute(
        select(Flag).where(
            Flag.project_id == project.id,
            Flag.kind == kind,
            Flag.subject_id == subject_id,
            Flag.status.in_([FlagStatus.OPEN, FlagStatus.RAISED]),
        )
    ).scalars().first()
    if live is not None:
        # Keep the arithmetic current even on a flag already raised: if the
        # chairman asks again, the numbers should be today's.
        live.summary = summary
        if arithmetic is not None:
            live.arithmetic = arithmetic
        return None
    if kind in HISTORICAL_KINDS and _already_answered(session, project, kind, subject_id):
        # A historical condition is a moment, and `acknowledge` says the
        # chairman's answer is the end of it — but "the end of it" only held
        # until the next janitor tick, because the dedupe above looks at live
        # flags and his answer had just made this one resolved. So the card he
        # had just cleared came straight back, every tick, for a cycle whose
        # arithmetic can never turn. He could clear it again; the second
        # attempt was an HTTP 500 (see 0039).
        #
        # A state condition is different and still reopens: a forecast that
        # goes bad again is new news, not the same news twice.
        return None
    flag = Flag(
        id=ids.new_id(ids.FLAG),
        project_id=project.id,
        kind=kind,
        status=FlagStatus.OPEN,
        subject_id=subject_id,
        summary=summary[:4000],
        arithmetic=arithmetic,
    )
    session.add(flag)
    log.info("flag opened for %s: %s", project.slug, kind)
    return flag


def _already_answered(session: Session, project: Project, kind: str,
                      subject_id: str | None) -> bool:
    """Has this exact piece of historical news already been closed once?

    Only asked for `HISTORICAL_KINDS`, where the subject is a moment that
    happened — cycle 7 was missed, this obligation failed on that date. Once
    that has been flagged and closed, flagging it again is telling the chairman
    something he has already answered.
    """
    return session.execute(
        select(Flag.id).where(
            Flag.project_id == project.id,
            Flag.kind == kind,
            Flag.subject_id == subject_id,
            Flag.status == FlagStatus.RESOLVED,
        ).limit(1)
    ).scalar_one_or_none() is not None


def resolve_subject(session: Session, project_id: str, kind: str,
                    subject_id: str | None, why: str) -> int:
    """Close every live flag for one condition, whatever kind it is.

    The guard against resolving a historical kind lives in `_resolve`, which is
    what `evaluate` calls — because there the question is "has the arithmetic
    turned?", and a missed cycle's arithmetic never turns. A caller that owns
    the subject's whole lifecycle is answering a different question: a spend
    window's flag is about that window, and when the window closes there is no
    condition left to be true.
    """
    closed = 0
    for flag in session.execute(
        select(Flag).where(
            Flag.project_id == project_id,
            Flag.kind == kind,
            Flag.subject_id == subject_id,
            Flag.status.in_([FlagStatus.OPEN, FlagStatus.RAISED]),
        )
    ).scalars():
        flag.status = FlagStatus.RESOLVED
        flag.resolution = why
        flag.closed_at = utcnow()
        closed += 1
    return closed


def _resolve(session: Session, project: Project, kind: str,
             subject_id: str | None, why: str) -> None:
    """Close a flag whose condition has cleared.

    Only ever for conditions that are a state. A missed cycle stays flagged
    until someone says something about it — the news does not expire because
    the next cycle went well.
    """
    if kind in HISTORICAL_KINDS:
        return
    resolve_subject(session, project.id, kind, subject_id, why)


def _last_cycle_results(session: Session, project: Project):
    """Each standing requirement paired with how it went in the last cycle that
    judged it. Nothing to say about one that has never closed a cycle yet."""
    standing = list(session.execute(
        select(Requirement).where(
            Requirement.project_id == project.id,
            Requirement.recurrence == Recurrence.STANDING)
    ).scalars())
    if not standing:
        return []
    ordinals = {
        c.id: c.ordinal for c in session.execute(
            select(Cycle).where(Cycle.project_id == project.id)).scalars()
    }
    out = []
    for requirement in standing:
        results = [
            r for r in session.execute(
                select(RequirementCycleResult).where(
                    RequirementCycleResult.requirement_id == requirement.id)
            ).scalars()
            if r.cycle_id in ordinals
        ]
        if not results:
            continue
        latest = max(results, key=lambda r: ordinals[r.cycle_id])
        out.append((requirement, latest, ordinals[latest.cycle_id]))
    return out


def evaluate(session: Session, project: Project, cons: ConstitutionDoc) -> int:
    """Recompute every trip condition. Returns how many flags were opened."""
    from yaaf.server.capacity import capacity_forecast
    from yaaf.server.dispatcher import activity_state

    opened = 0

    # 1. A standing obligation the project owed last cycle and did not deliver.
    #    There is no "blocked" status to read: what makes this knowable is the
    #    per-cycle record, which is the whole reason it exists. A memo owed 38
    #    times and shipped once used to be one row reading MET.
    for requirement, result, ordinal in _last_cycle_results(session, project):
        if result.status == RequirementStatus.MET:
            continue
        if open_flag(
            session, project, FlagKind.STANDING_MISSED,
            f"{requirement.key} is owed every cycle and cycle {ordinal} "
            f"closed without it: {requirement.statement[:300]}",
            arithmetic={"requirement": requirement.key,
                        "cycle": ordinal,
                        "status_at_close": result.status,
                        "failure_load": requirement.failure_load,
                        "check_output": (result.check_output or "")[:1000]},
            subject_id=result.id,
        ):
            opened += 1

    # 2. A cycle that rolled with scope still open. Cycles never block on a
    #    failure — stopping the rhythm because last turn went badly makes the
    #    outage worse — so this is what stops one rolling QUIETLY.
    last_closed = session.execute(
        select(Cycle).where(Cycle.project_id == project.id,
                            Cycle.status == CycleStatus.MISSED)
        .order_by(Cycle.ordinal.desc()).limit(1)
    ).scalars().first()
    if last_closed is not None and open_flag(
            session, project, FlagKind.CYCLE_MISSED,
            f"Cycle {last_closed.ordinal} closed with scope still open"
            + _scoreline(last_closed)
            + (f" Its goal was: {last_closed.goal[:200]}." if last_closed.goal else "")
            + " The rhythm rolled on, as it should; this is the part that "
              "should not roll on quietly.",
            arithmetic={"cycle": last_closed.ordinal,
                        "closed_at": (last_closed.closed_at.isoformat()
                                      if last_closed.closed_at else None),
                        "goal": last_closed.goal,
                        **(last_closed.scorecard or {})},
            subject_id=last_closed.id,
    ):
        opened += 1

    # 3. The arithmetic says the date does not hold. This is the one the
    #    chairman asked about directly and was reassured about.
    cycle = policy.current_cycle(session, project.id) if (
        project.mode == ProjectMode.OPERATE) else None
    if cycle is not None and cycle.due_at is not None:
        forecast = capacity_forecast(session, project, cons, cycle.due_at)
        if not forecast["feasible"]:
            if open_flag(
                session, project, FlagKind.DEADLINE_AT_RISK,
                f"Cycle {cycle.ordinal} is due {cycle.due_at:%Y-%m-%d %H:%M} UTC and "
                f"the arithmetic does not reach it: {forecast['why']}.",
                arithmetic=forecast,
                subject_id=cycle.id,
            ):
                opened += 1
        else:
            _resolve(session, project, FlagKind.DEADLINE_AT_RISK, cycle.id,
                     f"capacity recovered: {forecast['why']}")

    # 4. Work exists, is reachable, and a cap is stopping it. Not every quiet
    #    hour — only the ones where the board has claimable work behind the
    #    wall, which is the difference between pacing and starvation.
    state = activity_state(session, project, cons)
    starving = state["blocked_by"] in ("daily_runs", "governance_reserve")
    ready = state["waiting"]["claimable_tasks"]
    if starving and ready:
        if open_flag(
            session, project, FlagKind.WORK_STARVED,
            f"{ready} task(s) are ready to claim and the run allowance is spent: "
            f"{state['brief']['headline']}.",
            arithmetic={"blocked_by": state["blocked_by"],
                        "claimable_tasks": ready,
                        "daily_runs": state["daily_runs"],
                        "governance_reserve": state["governance_reserve"],
                        "inbox_runs": state["inbox_runs"]},
            subject_id=None,
        ):
            opened += 1
    else:
        _resolve(session, project, FlagKind.WORK_STARVED, None,
                 "the allowance cleared or the board emptied")

    # 5. The desk gated itself shut. WORK_STARVED above needs a WALL to have
    #    stopped the work; this is the case where nothing stopped it and there
    #    is still nothing to do, because every task on the board carries a
    #    start time the CEO chose. An idle desk is an idle desk whoever caused
    #    it, and the version nobody was watching for is the self-inflicted one.
    # `starving` above covers only the two caps WORK_STARVED reports on, but
    # every gate in CAP_GATES is a wall the project ran into rather than one it
    # built. SELF_GATED's whole claim is that NO cap is responsible, so firing
    # while the hourly brake or the wake ceiling is the real gate obliges the
    # CEO to account for a self-inflicted idleness that is not self-inflicted.
    #
    # `scheduled` is deliberately not a cap: it is the dispatcher's own name for
    # "the only work on the board carries a start time somebody set", which is
    # exactly this flag's subject.
    gated = state["waiting"].get("gated_tasks") or 0
    capped = state["blocked_by"] in CAP_GATES
    if gated and not ready and not capped:
        if open_flag(
            session, project, FlagKind.SELF_GATED,
            f"Nothing is claimable and no cap is stopping you: {gated} task(s) carry a "
            f"start time you set, so nobody may claim them and nobody is woken for "
            f"them. If the dates are right, say why the desk is idle until then. If "
            f"they are a habit, bring work forward.",
            arithmetic={"claimable_tasks": ready, "gated_tasks": gated,
                        "blocked_by": state["blocked_by"]},
            subject_id=None,
        ):
            opened += 1
    else:
        _resolve(session, project, FlagKind.SELF_GATED, None,
                 "work became claimable, or a real cap took over")

    # 5b. Past the daily run plan, and still working. The budget learned this
    #     first: a number that is fatal to cross is not a plan anybody works to.
    #     Crossing the run cap used to be silently fatal for the day — no
    #     account, no flag, no way to ask — so the only safe move was to stay
    #     well under it, and a safe number is indistinguishable from an honest
    #     one from outside.
    day = state["daily_runs"]
    wall = max(day["cap"], int(day["cap"] * cons.runs_hard_stop_multiple))
    in_overrun = day["used"] > day["cap"] and wall > day["cap"]
    if in_overrun:
        if open_flag(
            session, project, FlagKind.RUN_OVERRUN,
            f"{day['used']} runs today against a plan of {day['cap']}; the wall is "
            f"{wall}. You are working in the overrun, which is allowed and is the "
            f"point — say what the extra runs bought. If the plan is simply too low "
            f"for the work, that is a capacity_request with the arithmetic, not a "
            f"month of quietly running over.",
            arithmetic={"used": day["used"], "plan": day["cap"], "wall": wall,
                        "multiple": cons.runs_hard_stop_multiple},
            subject_id=day["resets_at"][:10],   # one flag per day, not per tick
        ):
            opened += 1
    else:
        _resolve(session, project, FlagKind.RUN_OVERRUN, day["resets_at"][:10],
                 "the day rolled or the plan was raised")

    # 6. The objective is moving away from its target. The only kind here that
    #    is about whether the project is WINNING rather than whether it is
    #    behaving; everything above can be satisfied by a project losing
    #    tidily. Subject is the requirement, so two metrics going wrong are two
    #    pieces of news rather than one that overwrites the other.
    for board in metrics.project_scoreboard(session, project.id):
        requirement_key = board["requirement_key"]
        if board["verdict"] == "losing":
            trend = " -> ".join(str(v) for v in board["trend"][-4:])
            if open_flag(
                session, project, FlagKind.LOSING_GROUND,
                f"{requirement_key} is going the wrong way: {board['value']} against a "
                f"target of {board['target']}, and further from it than when this "
                f"window opened ({trend}). Say what you are changing, and what it "
                f"costs — not what you have been doing.",
                arithmetic={"requirement": requirement_key, "value": board["value"],
                            "target": board["target"], "trend": board["trend"],
                            "direction": board["direction"]},
                subject_id=board["requirement_key"],
            ):
                opened += 1
        else:
            _resolve(session, project, FlagKind.LOSING_GROUND, requirement_key,
                     f"the measurement turned: {board['verdict']}")

    return opened


def acknowledge(session: Session, project: Project, flag: Flag, note: str) -> Flag:
    """The chairman answering news the CEO was obliged to bring him.

    Every other row on his desk has a verb. A flag had a link — it took him to
    the boardroom and left the card exactly where it was, so the one kind of
    row he could not clear was the one the platform had insisted he be shown.

    Acknowledging always stamps. Whether it also CLOSES depends on what the
    flag is about, and the difference is not cosmetic:

    A moment (`ACK_CLOSES`) has nothing left to clear, so his answer is the
    end of it. A state — a forecast still negative, a board still starved —
    is still true, and resolving it here would be a lie the next janitor tick
    corrects by opening a fresh one and re-obliging the CEO to raise it. So
    the state flags stay live and drop to the watch list under his own note:
    seen, not fixed. What clears them is the arithmetic turning, which is
    `evaluate`'s job and was always `evaluate`'s job.
    """
    flag.acknowledged_at = utcnow()
    flag.acknowledgement = note.strip() or None
    if flag.kind in ACK_CLOSES:
        flag.status = FlagStatus.RESOLVED
        flag.resolution = f"the chairman answered: {flag.acknowledgement or 'noted'}"
        flag.closed_at = flag.acknowledged_at
    log.info("flag %s acknowledged for %s (status %s)",
             flag.kind, project.slug, flag.status)
    return flag


def open_flags(session: Session, project_id: str) -> list[Flag]:
    return list(session.execute(
        select(Flag).where(Flag.project_id == project_id,
                           Flag.status.in_([FlagStatus.OPEN, FlagStatus.RAISED]))
        .order_by(Flag.opened_at)
    ).scalars())


def unraised_count(session: Session, project_id: str) -> int:
    """How many the chairman has not been told about.

    Drawn on his own screen beside whatever the CEO is telling him, so
    cheerfulness in the boardroom is self-refuting rather than something he has
    to catch by reading the database.
    """
    return sum(1 for f in open_flags(session, project_id)
               if f.status == FlagStatus.OPEN)


def flags_for_context(session: Session, project_id: str) -> list[dict]:
    """What the CEO is handed, every turn, until it says something."""
    out = []
    for flag in open_flags(session, project_id):
        out.append({
            "id": flag.id,
            "kind": flag.kind,
            "status": flag.status,
            "summary": flag.summary,
            "arithmetic": flag.arithmetic,
            "opened_at": flag.opened_at.isoformat() if flag.opened_at else None,
            "raised": flag.status == FlagStatus.RAISED,
        })
    return out
