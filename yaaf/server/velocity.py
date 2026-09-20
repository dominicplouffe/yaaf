"""Velocity: the measured pace of requirements becoming true.

The forecast stage 5 builds is remaining ÷ velocity, so velocity must be
measured on requirements — a metric needs its own log, and `requirement_events`
is it. Tasks closing is activity; a requirement going MET is progress. The
audited install confused the two: 126 tasks completed, 20 side effects, and no
number anywhere that could say whether the project was faster than last week.

Three numbers travel together, because each is misleading alone:

  * the pace — requirements met per week, trailing 3 weeks and trailing 12
  * the waste — the share of the window's runs that died or failed a check,
    with the single worst offender named, so a falling pace has a cause with
    a key on it rather than a mood
  * the attribution — how much of the window's spend was stamped against a
    requirement at all. 62% of runs in the audit carried nothing, which made
    cost per deliverable uncomputable by construction.

Everything here is read-only over rows other code already writes.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Project,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    Run,
    RunKind,
    RunStatus,
    utcnow,
)
from yaaf.server import policy

SHORT_WINDOW_DAYS = 21
LONG_WINDOW_DAYS = 84

#: Below this many met requirements there is no measured pace, only one
#: observation wearing a decimal point.
#:
#: `policy.py` already guards its weaker numbers this way — MIN_PRICED_RUNS
#: before it will average a cost, MIN_SETTLED_FOR_BIAS before it will trust an
#: estimate multiplier — and the number with no floor at all was the one that
#: freezes a project's scope and mails the chairman that his date is at risk.
#: A single requirement landing produced "0.33/week" and, from it, a confident
#: calendar date six weeks out.
#:
#: Two, matching MIN_SETTLED_FOR_BIAS, on the same reasoning it was written
#: with: two is not a trend, but it is the point where one lucky or unlucky
#: week stops being the entire dataset. Three was the alternative and would
#: leave a five-requirement project with no measured date until it was most of
#: the way done — a long silence bought for little, since what actually broke
#: here was extrapolating from one.
#:
#: Under the floor the projection falls through to the plan's own arithmetic,
#: which already says out loud that it is a best case rather than a measurement.
MIN_MET_FOR_PACE = 2

#: A weekly rate needs a week. Under one, `met ÷ elapsed` is a rate with an
#: enormous error bar and no way to show it, so no weekly pace is stated.
MIN_DAYS_FOR_A_WEEKLY_RATE = 7

#: Runs that went wrong. TERMINATED_REFUSALS is deliberately NOT here.
#:
#: A run that hit the refusal cap did not fail; it asked for things the rules
#: said no to. Counted as failure it made probing a wall arithmetically
#: identical to breaking, and the chain ran all the way to a flag: refusal ->
#: dead run -> measured failure_rate up -> retry reserve up -> forecast slack
#: down -> DEADLINE_AT_RISK. A CEO that tested where the edges were made its
#: own project look like it was falling apart, and the constitution's advice
#: was to stop testing.
#:
#: The wake was still spent, so `capacity` still counts it against the run
#: budget — that is arithmetic and stays true. What changes is that it stops
#: being reported as the project failing, and gets its own number instead.
DEAD_STATUSES = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED)

#: Runs that ended because the agent hit the per-run refusal cap. Its own
#: number, because "how often is this project told no" is a real and useful
#: signal — it is just not the same signal as "how often does this project
#: break", and one of the two was invisible while they shared a counter.
PROBE_STATUSES = (RunStatus.TERMINATED_REFUSALS,)


def countable_scope(session: Session, project_id: str) -> list[Requirement]:
    """The requirements whose landing means the deliverable moved.

    ONE definition, because the pace and the burn-up used to hold two. The
    chart's line counted every MET event bar R-00; the pace behind the chart's
    own forecast dropped standing and metric requirements as well. Two
    datasets, one picture, and a dashed projection drawn from numbers the solid
    line beside it did not agree with.

    Three exclusions, each already argued for somewhere in this file:

    R-00 is the plan gate lifting, not scope landing, and counting it would
    gift every project one free unit of pace in its first week.
    `policy.scope_requirements` drops it, and is cycle-scoped for Operate — so
    a numerator taken from here matches the `remaining` denominator, which has
    always come from the same place.

    Standing requirements are met again every cycle by design — the memo, the
    sweep, the routing check — so an Operate project owing three of them
    weekly would post three met-events a week forever. Fed to
    `commitment_projection`, that pace divides the remaining scope into an
    ever-nearer delivery date, and the platform would promise the chairman a
    date its own rhythm had manufactured. Whether a standing obligation is
    honoured is a real question with a real answer — "met in 23 of the last 24
    cycles" — and it lives in RequirementCycleResult, where it cannot be
    mistaken for progress.

    Metric requirements go for the reason `project_velocity` subtracts them
    from `remaining`: a season-long rank target landing would have made the
    remaining MEMOS look faster.

    Proposed and cut are out because they are not in force — the same cut
    `policy.commitment_scope_keys` makes, so a burn-up built from this can
    never climb above the total built from that.
    """
    from yaaf.server import metrics as metrics_mod

    return [
        r
        for r in policy.scope_requirements(session, project_id)
        if r.status not in (RequirementStatus.PROPOSED, RequirementStatus.CUT)
        and r.recurrence != Recurrence.STANDING
        and not metrics_mod.is_metric(r.acceptance_test)
    ]


def _met_count(session: Session, project_id: str, since) -> int:
    """MET transitions in the window that represent scope actually landing."""
    countable = [r.id for r in countable_scope(session, project_id)]
    if not countable:
        return 0
    return session.execute(
        select(func.count(RequirementEvent.id)).where(
            RequirementEvent.project_id == project_id,
            RequirementEvent.to_status == RequirementStatus.MET,
            RequirementEvent.at >= since,
            RequirementEvent.requirement_id.in_(countable),
        )
    ).scalar_one()


def standing_record(session: Session, project_id: str, cycles: int = 12) -> list[dict]:
    """How the standing obligations have actually gone, cycle by cycle.

    What an Operate project has instead of a burndown. A burndown of work that
    recurs is a flat line; this is the shape that carries the information —
    which obligation slipped, and in which turn.
    """
    from yaaf.core.models import Cycle, RequirementCycleResult

    recent = list(session.execute(
        select(Cycle).where(Cycle.project_id == project_id)
        .order_by(Cycle.ordinal.desc()).limit(cycles)
    ).scalars())
    if not recent:
        return []
    ids_in_window = {c.id for c in recent}
    rows: dict[str, dict] = {}
    for result in session.execute(
        select(RequirementCycleResult).where(
            RequirementCycleResult.project_id == project_id)
    ).scalars():
        if result.cycle_id not in ids_in_window:
            continue
        entry = rows.setdefault(result.requirement_id, {"met": 0, "judged": 0})
        entry["judged"] += 1
        if result.status == RequirementStatus.MET:
            entry["met"] += 1
    out = []
    for requirement in session.execute(
        select(Requirement).where(
            Requirement.project_id == project_id,
            Requirement.recurrence == Recurrence.STANDING)
    ).scalars():
        entry = rows.get(requirement.id, {"met": 0, "judged": 0})
        out.append({
            "key": requirement.key,
            "statement": requirement.statement[:200],
            "met": entry["met"],
            "judged": entry["judged"],
            "status_now": requirement.status,
        })
    return sorted(out, key=lambda r: r["key"])


def _runs_in_window(session: Session, project_id: str, since) -> dict:
    """What the window's runs were, and — since the split — what they were FOR.

    Three lanes, because one number hid the finding that started all of this:
    57% of every run an audited project ever spent was an agent waking to read
    a message. Its own broadcasts generated that traffic, and neither the CEO
    nor the chairman could see it, so nobody could price a message before
    sending it. `inbox` is reading; `noop` is a wake that changed nothing and
    was refunded; `work` is the rest.
    """
    total, dead, attributed, usd, attributed_usd = 0, 0, 0, 0.0, 0.0
    probed = 0
    inbox, noop, work = 0, 0, 0
    rows = session.execute(
        select(Run.status, Run.requirement_id, Run.cost_usd, Run.kind,
               Run.metered).where(
            Run.project_id == project_id,
            Run.kind != RunKind.CHAT_SESSION,
            Run.created_at >= since,
        )
    ).all()
    for status, requirement_id, cost, kind, metered in rows:
        total += 1
        if status in DEAD_STATUSES:
            dead += 1
        elif status in PROBE_STATUSES:
            probed += 1
        if requirement_id:
            attributed += 1
            attributed_usd += cost or 0.0
        usd += cost or 0.0
        if kind == RunKind.INBOX_RUN:
            inbox += 1
        elif metered is False:
            # `metered` is the ONLY honest signal. Inferring a no-op from
            # produced == 0 reads every run that predates the counter as
            # having done nothing: on the first deploy this reported 26 of 42
            # runs as waste, which was the backfill talking, not the project.
            noop += 1
        else:
            work += 1
    return {
        "total": total,
        "dead": dead,
        "failure_share": round(dead / total, 3) if total else 0.0,
        # Runs that ended by being told no, reported apart from runs that broke.
        # A high number here is worth looking at and is not the same news: it
        # may be an agent misreading the rules, or a rule that is in the way of
        # work somebody wants done.
        "probed": probed,
        "refused_share": round(probed / total, 3) if total else 0.0,
        "attributed": attributed,
        "attributed_share": round(attributed / total, 3) if total else 0.0,
        "usd": round(usd, 2),
        "attributed_usd": round(attributed_usd, 2),
        # The tax, made countable.
        "work": work,
        "inbox": inbox,
        "noop": noop,
        "inbox_share": round(inbox / total, 3) if total else 0.0,
        "noop_share": round(noop / total, 3) if total else 0.0,
    }


def _worst_failure(session: Session, project_id: str) -> dict | None:
    """The open requirement carrying the most accumulated failures. One name,
    because "R-07 has eaten 53 runs" is actionable and a histogram is not."""
    worst = session.execute(
        select(Requirement)
        .where(
            Requirement.project_id == project_id,
            Requirement.status.in_(policy.OPEN_REQUIREMENT_STATUSES),
            Requirement.failure_load > 0,
        )
        .order_by(Requirement.failure_load.desc())
        .limit(1)
    ).scalar_one_or_none()
    if worst is None:
        return None
    return {"key": worst.key, "failure_load": worst.failure_load,
            "statement": worst.statement[:200]}


def _observed_days(project: Project | None, window_days: int, now) -> float:
    """How much of the window this project was actually alive for.

    A window is a promise about how far back we looked, not about how long the
    project has existed. Dividing by the nominal 21 days scored a thirteen-day
    project as though eight days of silence had been measured and found empty:
    one requirement met became 0.33/week instead of 0.54, the projection landed
    weeks past a date the project was in fact on course for, and the chairman
    was mailed an at-risk warning manufactured by the arithmetic. Pessimism
    that lands hardest on the youngest projects is not conservatism; it is a
    bug with a direction.
    """
    if project is None or project.created_at is None:
        return float(window_days)
    age_days = (now - project.created_at).total_seconds() / 86400
    return max(0.0, min(float(window_days), age_days))


def _per_week(met: int, observed_days: float) -> float:
    """A rate, or 0.0 when the window is too short to have measured one.

    0.0 rather than None because every reader downstream already treats "no
    pace" as falsy — `commitment_projection` falls through to the longer
    window, `weeks_left_at_pace` declines to divide, and the screen hides the
    sentence. A new sentinel would need all three taught about it.
    """
    if observed_days < MIN_DAYS_FOR_A_WEEKLY_RATE:
        return 0.0
    return round(met / (observed_days / 7), 2)


def project_velocity(session: Session, project_id: str) -> dict:
    """The block the context pack ships and the Plan screen draws. One
    producer, so the CEO and the chairman are always reading the same pace."""
    now = utcnow()
    short_since = now - timedelta(days=SHORT_WINDOW_DAYS)
    long_since = now - timedelta(days=LONG_WINDOW_DAYS)
    project = session.get(Project, project_id)

    met_short = _met_count(session, project_id, short_since)
    met_long = _met_count(session, project_id, long_since)
    runs_short = _runs_in_window(session, project_id, short_since)

    from yaaf.server import metrics as metrics_mod

    open_scope = [
        r for r in policy.scope_requirements(session, project_id)
        if r.status in policy.OPEN_REQUIREMENT_STATUSES
    ]
    # A metric requirement is not a unit of work and must not be divided by a
    # pace measured in units of work. The FPL desk is the worked example: eleven
    # grep-over-workspace requirements plus a season-long rank target, burned
    # down at the rate memos were landing, produced `weeks_left_at_pace: 1.1`
    # on a 38-gameweek season. The memos really were nearly done. The season
    # was not, and the arithmetic had no way to know the difference.
    #
    # So the burn-down answers only the question it can answer — when does the
    # *work* run out — and the metric answers its own, beside it. Same argument
    # as the STANDING exclusion in `_met_count`, one object further along.
    metric_reqs = [r for r in open_scope if metrics_mod.is_metric(r.acceptance_test)]
    remaining = len(open_scope) - len(metric_reqs)
    observed_short = _observed_days(project, SHORT_WINDOW_DAYS, now)
    per_week_short = _per_week(met_short, observed_short)
    scoreboard = [metrics_mod.scoreboard(session, r) for r in metric_reqs]

    return {
        # The objective, measured. Empty for a project whose finish line is
        # still only prose, which is most of them.
        "metrics": scoreboard,
        "metrics_open": len(metric_reqs),
        "losing": [s["requirement_key"] for s in scoreboard if s["verdict"] == "losing"],
        "met_3w": met_short,
        "per_week_3w": per_week_short,
        "met_12w": met_long,
        "per_week_12w": _per_week(met_long, _observed_days(project, LONG_WINDOW_DAYS, now)),
        "remaining": remaining,
        # What the pace was actually measured over, so a reader can tell a
        # slow project from a young one. Both report a low rate; only one of
        # them is a problem.
        "measured_over_days": round(observed_short, 1),
        "met_sample": met_short,
        # Whether `per_week_3w` is a measurement at all. Without this a screen
        # has no way to tell "nothing is landing" from "nothing has had time
        # to land yet", and would print a confident 0 a week over a project
        # two days old — a slander the arithmetic cannot support.
        "pace_known": bool(per_week_short) and met_short >= MIN_MET_FOR_PACE,
        # The naive read of the pace, here so nobody computes it two ways.
        # None while there is no pace at all: "infinite weeks" helps nobody,
        # and a made-up number would be worse. The sample floor applies here
        # for the same reason it applies in `commitment_projection` — this is
        # the same division, and the screen quoting a confident "6.1 weeks"
        # beside a projection that declined to name a date would be the
        # platform disagreeing with itself in public.
        "weeks_left_at_pace": (
            round(remaining / per_week_short, 1)
            if per_week_short > 0 and met_short >= MIN_MET_FOR_PACE
            else None
        ),
        # Said out loud, because "1.1 weeks left" on a project with an open
        # season-long target is true about the work and a lie about the goal.
        "weeks_left_covers": (
            "the work only — this project's objective is measured separately, "
            "see `metrics`" if metric_reqs else "all open scope"
        ),
        "runs_3w": runs_short,
        "worst_failure": _worst_failure(session, project_id),
    }


def commitment_projection(session: Session, project: Project, cons: ConstitutionDoc) -> dict:
    """Where the remaining scope lands, as a date. The ONLY producer of the
    delivery projection: the propose route quotes it, the janitor stamps it on
    the commitment, the scope refusal shows its arithmetic, and the Plan
    screen draws it — one function, so none of them can ever disagree.

    Measured pace first: remaining ÷ met-per-week, trailing 3 weeks, falling
    back to 12 — but only once MIN_MET_FOR_PACE requirements have actually
    landed. A project that has met nothing yet, or too little to have a pace,
    falls back to the plan's own arithmetic — adjusted runs at the daily cap,
    the same sum the feasibility refusals quote. The basis is named in the
    result, because a date that cannot say where it came from is an assertion,
    not a forecast.

    The floor is not decoration. Without it one requirement landing was a
    "measured pace" of 0.33/week, and that number does not stay on the screen:
    it flips the commitment to AT_RISK and hard-refuses the CEO's next
    requirement with a 409. A promise frozen on the strength of a single
    observation is the platform manufacturing the very lateness it is warning
    about.
    """
    pace = project_velocity(session, project.id)
    remaining = pace["remaining"]
    now = utcnow()
    if remaining == 0:
        return {"known": True, "remaining": 0, "method": "settled",
                "projected_done_at": now,
                "basis": "nothing is open — the scope is settled"}
    # Each window carries its own sample, so the floor has to be checked
    # against the one whose rate is about to be divided by.
    per_week = window = sample = None
    for rate, met, name in ((pace["per_week_3w"], pace["met_3w"], "3 weeks"),
                            (pace["per_week_12w"], pace["met_12w"], "12 weeks")):
        if rate and met >= MIN_MET_FOR_PACE:
            per_week, window, sample = rate, name, met
            break
    if per_week:
        projected = now + timedelta(weeks=remaining / per_week)
        return {"known": True, "remaining": remaining, "method": "velocity",
                "per_week": per_week, "projected_done_at": projected,
                "basis": f"{remaining} open requirement(s) at the measured pace of "
                         f"{per_week}/week ({sample} met in the trailing {window})"}
    sums = policy.plan_arithmetic(session, project, cons)
    if sums["adjusted_runs"] > 0 and cons.runs_per_project_per_day:
        days = sums["adjusted_runs"] / cons.runs_per_project_per_day
        too_few = (f"only {pace['met_3w']} requirement(s) have landed, which is not yet a pace"
                   if pace["met_3w"] else "nothing has been met yet")
        return {"known": True, "remaining": remaining, "method": "plan_arithmetic",
                "projected_done_at": now + timedelta(days=days),
                "basis": f"{too_few}, so this is the plan's own arithmetic: "
                         f"{sums['adjusted_runs']} adjusted runs at "
                         f"{cons.runs_per_project_per_day}/day ≈ {round(days, 1)} days — "
                         "an at-the-cap best case, not a measured pace"}
    return {"known": False, "remaining": remaining,
            "why": "too little has been met to measure a pace and nothing is "
                   "estimated — there is no arithmetic to project a date from"}
