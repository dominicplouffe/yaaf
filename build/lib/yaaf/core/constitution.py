"""The constitution: the limits a project's agents cannot talk their way out of.

Every rule here is enforced by :mod:`yaaf.server.policy` as a refusal or an
automatic state change, server-side. That is what makes it binding: an agent
cannot be argued out of a rule it never held, however persuasive the argument.
Defaults derive from lana-project's numbers, the ones paid for in burned cycles.

The constraint runs on the *agents*, not on the chairman. He changes any rule
whenever he likes and it binds from the next call — the delay that used to sit
in front of behavioural changes protected nobody in practice, and it made
being wrong about a number cost three days. What survives is the accounting: a
written reason on every change, an audit entry, and mandatory disclosure to the
CEO in its next context pack. The company still cannot move its own goalposts;
the man who owns it never had to be stopped from moving them.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Amendment, Constitution, utcnow


def parse_report_window(raw: object) -> tuple[time, time] | None:
    """``"09:00-20:00"`` → (09:00, 20:00). Returns None for absent, malformed,
    or midnight-wrapping windows — the validator below turns None-from-a-value
    into a refusal, while callers read None as "no window"."""
    if not raw:
        return None
    try:
        start_s, end_s = str(raw).split("-", 1)
        start = time(*(int(part) for part in start_s.strip().split(":", 1)))
        end = time(*(int(part) for part in end_s.strip().split(":", 1)))
    except (TypeError, ValueError):
        return None
    return (start, end) if start < end else None


def parse_hhmm(raw: object) -> time | None:
    """``"09:00"`` → 09:00. None for absent or malformed, which the validator
    below turns into a refusal when a value was actually given."""
    if not raw:
        return None
    try:
        hour, minute = (int(part) for part in str(raw).strip().split(":", 1))
        return time(hour, minute)
    except (TypeError, ValueError):
        return None


def _rule(
    default: Any,
    *,
    group: str,
    label: str,
    help: str,
    choices: list[str] | None = None,
    **constraints: Any,
) -> Any:
    """A constitution field that can describe itself.

    Every rule carries its own group, label and help text, so the settings page
    is *generated* from this schema rather than from a hand-kept list beside it.
    That list existed and went stale exactly as you would expect: six of
    twenty-four rules were editable, and the memory caps — the ones a full
    company actually needs — were not among them.

    ``choices`` names the values a string rule accepts. The validators below
    already refuse anything else; this only lets the editor draw a menu instead
    of a text box you have to guess into.
    """
    extra: dict[str, Any] = {"group": group}
    if choices is not None:
        extra["choices"] = choices
    return Field(default=default, title=label, description=help,
                 json_schema_extra=extra, **constraints)


# Fourteen groups is a taxonomy, not navigation: rendered flat it is a wall of
# thirty-nine boxes, and "Wake-loop brakes" alone is nine of them. Themes are
# what a chairman actually reaches for; the groups survive as the subheads
# inside one. This lives here rather than in the settings page because the last
# hand-kept list on that side went stale — see the note in `_rule`.
GROUP_THEMES = {
    "Budgets": "Money",
    "Wake-loop brakes": "Pace & brakes",
    "Runs": "Pace & brakes",
    "Work discipline": "How work is run",
    "Evidence": "How work is run",
    "Sampled audit": "How work is run",
    "Autonomy": "Autonomy",
    "Memory": "Memory",
    "CEO cadence": "Rhythm & reporting",
    "Reporting to you": "Rhythm & reporting",
    "Your timezone": "Rhythm & reporting",
    "Operate rhythm": "Rhythm & reporting",
    "The review": "Rhythm & reporting",
    "The commitment": "Rhythm & reporting",
}

#: Tab order on the rules screen — money first, because that is what a stuck
#: company comes here to change.
THEME_ORDER = [
    "Money",
    "Pace & brakes",
    "How work is run",
    "Autonomy",
    "Memory",
    "Rhythm & reporting",
]


class ConstitutionDoc(BaseModel):
    """Schema for the frozen policy document (stored as JSON)."""

    # Work discipline (lana §3, §5)
    queue_ceiling: int = _rule(
        8, ge=1, group="Work discipline", label="Open tasks at once",
        help="Top-level tasks the company may have open at once. Forces finishing over "
             "starting.")
    subtask_ceiling: int = _rule(
        10, ge=1, group="Work discipline", label="Open subtasks per task",
        help="How far a lead may decompose one parent task before it must land something.")
    external_ratio_min: float = _rule(
        0.5, ge=0, le=1, group="Work discipline", label="Minimum share of outward-facing work",
        help="Fraction of open tasks that must produce evidence from outside the company. "
             "Stops a company that only talks to itself.")
    external_ratio_applies_from: int = _rule(
        3, ge=1, group="Work discipline", label="Board size before that share is enforced",
        help="Below this many open tasks the ratio is not checked — three tasks cannot be "
             "half of anything useful.")
    min_success_test_len: int = _rule(
        15, ge=1, group="Work discipline", label="Shortest allowed success test",
        help="Characters. A success test shorter than this is not falsifiable, so the "
             "platform refuses the task.")

    # The sampled audit. Gates ask the chairman at commitment, review and
    # completion; between them the platform samples observable closes at a
    # rate he sets. Sampling never blocks — an expired sample records "not
    # reviewed" and moves on — but the draw happens server-side AFTER the
    # close, so no agent can know which claim will be checked.
    audit_sample_rate: float = _rule(
        0.2, ge=0, le=1, group="Sampled audit", label="Share of observable closes sampled",
        help="When a task closes on an observable success test (one the platform cannot "
             "run itself), this is the chance it lands on your desk for a look. 0 turns "
             "sampling off; 1 samples everything. Command-checked closes are never "
             "sampled — the platform already ran those.")
    audit_response_hours: int = _rule(
        48, ge=1, group="Sampled audit", label="Hours before a sample expires",
        help="A sample you have not answered by then is recorded as 'not reviewed' and "
             "counted in the next review's coverage line. It never blocks the work — "
             "sampling is a thermometer, not a gate.")

    # Evidence discipline (lana §4) — drought counts verified/attested external only
    drought_hours: int = _rule(
        24, ge=1, group="Evidence", label="Hours without proof before a drought",
        help="With no verified external evidence for this long, only evidence-producing "
             "tasks may be created until proof lands.")
    require_evidence_verification: bool = _rule(
        False, group="Evidence", label="External evidence must be verified",
        help="On, evidence counts only once the platform has fetched and checked it — "
             "evidence nobody can check is opinion. Off by default: most projects verify "
             "with the data at their disposal. The 'Show me proof' preset turns it on.")

    # Autonomy thresholds (lana §6)
    approval_threshold_usd: float = _rule(
        250.0, ge=0, group="Autonomy", label="Spending that needs your approval",
        help="USD. Spends at or above this, and anything legally ambiguous, wait for you.")
    approval_timeout_days: int = _rule(
        7, ge=1, group="Autonomy", label="Days before an approval expires",
        help="An unanswered approval decides itself at this point, so a vacationing "
             "chairman degrades the company instead of deadlocking it.")
    approval_timeout_decision: str = _rule(
        "deny", group="Autonomy", label="What an expired approval decides",
        choices=["deny", "approve"],
        help="'deny' or 'approve'. Default-deny: silence is not consent.")

    # The authority dial (CEO Charter, stage B — docs/AUTONOMY.md). Integrity
    # rules never move; these decide only WHO holds each verb. 'chairman' is
    # today's behavior; 'ceo' hands the verb to the CEO, exercised through the
    # same machinery, with every use audited and disclosed. Autonomy is
    # granted by the constitution — the CEO still cannot edit its own rulebook,
    # and the chairman's routes, veto and pause never close.
    plan_authority: str = _rule(
        "chairman", group="Autonomy", label="Who accepts scope into the plan",
        choices=["chairman", "ceo"],
        help="'chairman': proposals wait for your click (today's behavior). 'ceo': the "
             "CEO accepts its own proposals — the plan gate still runs the same "
             "arithmetic, and every acceptance lands in your Decisions record.")
    scope_authority: str = _rule(
        "chairman", group="Autonomy", label="Who may change the plan",
        choices=["chairman", "ceo"],
        help="Cut, reopen, restage, reestimate, reword, unsequence. 'chairman': yours "
             "alone (the CEO can still execute your boardroom orders as directives). "
             "'ceo': the CEO wields them itself, on the record, reversibly.")
    commitment_authority: str = _rule(
        "chairman", group="Autonomy", label="Who signs the delivery date",
        choices=["chairman", "ceo"],
        help="'chairman': a proposed commitment waits for you. 'ceo': proposing adopts "
             "the platform's own projected date immediately — the CEO still cannot "
             "write a date, only accept the arithmetic's.")
    verdict_policy: str = _rule(
        "chairman", group="Autonomy", label="Who judges unverifiable closes",
        choices=["chairman", "ceo_judgment"],
        help="Observable acceptance tests a script cannot check. 'chairman': they park "
             "on your desk. 'ceo_judgment': the CEO judges and keeps going — and every "
             "self-judged close is disclosed to you in full, so nothing is quietly "
             "trusted.")
    capability_authority: str = _rule(
        "chairman", group="Autonomy", label="Who delegates the credentials",
        choices=["chairman", "ceo"],
        help="Whatever you store, the company can use — this decides who scopes it to "
             "roles. 'chairman': grants are yours (the CEO files needs). 'ceo': the "
             "CEO re-scopes existing credentials across its own team itself, on the "
             "record. Creating a credential is always yours — you hold the values.")

    # Budgets — native unit of the project's billing mode
    monthly_usd_cap: float = _rule(
        1000.0, ge=0, group="Budgets", label="Monthly budget (USD)",
        help="Hard stop: the project pauses itself at 100%. The unit under API-key billing.")
    monthly_token_cap: int = _rule(
        200_000_000, ge=0, group="Budgets", label="Monthly token cap",
        help="The budget unit under subscription billing. Same hard stop at 100%.")
    budget_warn_ratio: float = _rule(
        0.8, gt=0, lt=1, group="Budgets", label="Warn at this share of the budget",
        help="The CEO is told to cut burn at this point — before the platform pauses the "
             "project for it.")
    # The spend envelope (CEO Charter, stage C — docs/AUTONOMY.md). Real-world
    # purchases, as distinct from model spend. There is no spend_authority
    # switch on purpose: the money dial IS these numbers, and a switch would
    # only duplicate them. Inside the envelope an autonomous CEO buys without
    # asking; outside it, the existing approval machinery answers.
    external_monthly_usd_cap: float = _rule(
        500.0, ge=0, group="Budgets", label="Monthly real-world spend (USD)",
        help="USD the company may spend in the world per month — purchases, "
             "subscriptions, services — separate from the model budget and enforced "
             "in EVERY billing mode (token-billed projects used to meter this and "
             "never cap it). A purchase that would cross it is refused with the "
             "arithmetic; raising it is an amendment. 0 turns it off.")
    max_usd_per_purchase: float = _rule(
        100.0, ge=0, group="Budgets", label="Most one purchase may be, unasked",
        help="USD. A single purchase above this needs an approval even below the "
             "approval threshold — the line between running the company and "
             "surprising its owner. 0 turns the per-purchase line off.")

    # Memory (lana §6b) — caps are refusals; curation is explicit, never auto-evict
    memory_cap_project: int = _rule(
        60, ge=1, group="Memory", label="Project memory slots",
        help="Live entries the CEO may keep. At the cap, new entries are refused until it "
             "compacts or forgets one.")
    memory_cap_team: int = _rule(
        40, ge=1, group="Memory", label="Team memory slots",
        help="Live entries per team. Any member may write one; only the team's lead may "
             "compact or forget. Memory-heavy roles need more room than this.")
    memory_warn_ratio: float = _rule(
        0.85, gt=0, lt=1, group="Memory", label="Warn at this share of the slots",
        help="The scope's curator is told to compact at this point, so it curates on a wake "
             "it was having anyway instead of discovering the wall mid-write.")

    # Wake-loop prevention — the #1 way agent systems burn money
    wakes_per_agent_per_hour: int = _rule(
        6, ge=1, group="Wake-loop brakes", label="Wakes per agent per hour",
        help="The runaway-loop brake. Every wake is a paid model call.")
    runs_per_project_per_day: int = _rule(
        150, ge=1, group="Wake-loop brakes", label="Runs per day, whole project",
        help="The daily activity ceiling across every agent.")
    runs_per_project_per_hour: int = _rule(
        15, ge=1, group="Wake-loop brakes", label="Runs per hour, whole project",
        help="The pacing brake. The daily ceiling refills all at once, so without this a "
             "company can spend a whole day's allowance in the first hour and then sit "
             "dead until midnight with work stranded on the board. This is a trailing "
             "hour, not a clock hour, so it clears continuously rather than in a lump.")
    # The inbox lane. Reading is real cost — every wake is a paid model call —
    # but it is not work, and billing it to the work allowance is how a
    # company's own chatter eats the runs its deliverables needed. These are
    # the walls that keep "reading is cheap" from meaning "reading is free".
    inbox_runs_per_project_per_day: int = _rule(
        40, ge=0, group="Wake-loop brakes", label="Inbox runs per day, whole project",
        help="Wakes that only read messages, across every agent. Separate from the work "
             "allowance: an agent catching up on its channels no longer crowds out an "
             "agent holding a ready task. Still every-one-a-paid-model-call, so it has a "
             "ceiling of its own. 0 stops message-only wakes entirely — agents then read "
             "their channels on the next wake they were having anyway.")
    inbox_runs_per_project_per_hour: int = _rule(
        8, ge=0, group="Wake-loop brakes", label="Inbox runs per hour, whole project",
        help="The pacing brake for the inbox lane, for the same reason the work lane has "
             "one: a broadcast to leadership makes every agent unread at the same instant, "
             "and without this they all wake in the same minute.")
    pair_breaker_messages: int = _rule(
        10, ge=2, group="Wake-loop brakes", label="Messages before a thread freezes",
        help="Two agents talking this many times in one thread with nothing landing are "
             "talking, not working; the thread freezes.")
    max_usd_per_run: float = _rule(
        2.0, ge=0, group="Wake-loop brakes", label="Most one run may spend",
        help="USD. A single run that spends this much has stopped working and started "
             "looping; it is told as it approaches and stopped at the line. 0 turns it "
             "off. One task once cost $8.05 across 53 runs, and nothing bounded any of "
             "them individually.")
    refusal_cap_per_run: int = _rule(
        5, ge=1, group="Wake-loop brakes", label="Refusals a run may hit",
        help="Past this the run's token is revoked. Rephrasing a refused request is not a "
             "strategy.")
    task_check_failures_before_block: int = _rule(
        3, ge=1, group="Wake-loop brakes", label="Failed checks before a task blocks",
        help="A task whose success test fails this many times is not one the claimant can "
             "finish. It blocks and becomes a question for whoever planned it, instead of "
             "buying another wake to fail the same way. Counts across re-claims; only a "
             "passing check resets it.")
    requirement_check_failures_before_escalation: int = _rule(
        3, ge=1, group="Wake-loop brakes",
        label="Failed acceptance tests before a requirement escalates",
        help="The same idea one level up. A requirement whose acceptance test has failed "
             "this many times — each after work closed under it — is not a thing more "
             "work will fix on its own: the test and the plan disagree about what done "
             "means. Past this the CEO gets one escalation instead of another copy of the "
             "same traceback. 0 turns it off.")
    max_task_gate_hours: int = _rule(
        6, ge=1, group="Wake-loop brakes", label="Furthest ahead work may be scheduled",
        help="Hours. A task may carry a start time before which nobody may claim it, "
             "which is how the CEO lays work out in a sequence. Past this many hours it "
             "is refused: a company should wait for the previous step to land, not for "
             "a clock. One CEO gated its only open task two hours out and the company "
             "sat silent until the chairman paused it.")
    stall_hours: float = _rule(
        3.0, ge=0, group="Wake-loop brakes", label="Hours of silence before you are told",
        help="A running company with work on its board and no run started for this long "
             "is stalled, whatever the board says. You get one notice naming the exact "
             "cause. 0 turns the watchdog off.")
    max_concurrent_runs: int = _rule(
        3, ge=1, group="Wake-loop brakes", label="Runs in flight at once",
        help="How many agents may be working simultaneously. Protects the provider rate "
             "limit that every project on the box shares.")
    reserved_governance_runs: int = _rule(
        3, ge=0, group="Wake-loop brakes", label="Runs held back for governance",
        help="Runs kept out of the daily ceiling so a status review, a cycle plan or a "
             "commitment re-forecast can always happen. Ordinary work stops at the daily "
             "cap minus this number; governance runs may use the full cap. Without it the "
             "day the company burns its allowance by lunch is the day it cannot report "
             "that it did. 0 turns the reservation off.")

    # The CEO's own cadence. These were project config until the rebuild, which
    # meant they generated no settings UI, recorded no amendment, and were never
    # disclosed to the CEO that they governed. They are limits; limits live here.
    ceo_heartbeat_hours: float = _rule(
        6.0, gt=0, group="CEO cadence", label="Hours between planning wakes",
        help="How often the CEO wakes to plan when nothing else has woken it. Every wake "
             "is a full-price run.")
    ceo_heartbeat_always: bool = _rule(
        False, group="CEO cadence", label="Plan even with an empty board",
        help="Off, a planning wake is skipped when there is no open task and no live bet — "
             "it would buy a full-price run to conclude 'nothing to do'. On for a project "
             "you want thinking anyway.")
    ceo_daily_at: str | None = _rule(
        None, group="CEO cadence", label="A daily wake at this time",
        help="'09:00' in your timezone, or blank for none. Fires once per local day, at or "
             "after the time. The standup pattern.")

    # The chairman's reporting cadence. A wake ceiling stops the company
    # burning money on itself; this stops it burning money on *him* — the CEO
    # reported thirteen times in 2h20m before it existed. Enforced by
    # policy.check_chairman_report as a refusal on unprompted boardroom posts;
    # 0 hours means the chairman never asked for one and nothing is refused.
    chairman_report_interval_hours: float = _rule(
        0.0, ge=0, group="Reporting to you", label="Hours between unprompted status reports",
        help="0 means you never asked for a cadence and nothing is refused. Replying to "
             "something you actually said is always allowed and is not a status.")
    chairman_report_window_local: str | None = _rule(
        None, group="Reporting to you", label="Only report during these hours",
        help="'09:00-20:00' in your local time, or blank for any hour.")

    # The briefing. Everything above governs what the *company* may say to the
    # chairman and costs a model call to produce. This one is the platform
    # writing to him from rows it already holds: no run, no token, no wake, and
    # so no reason to gate it on a budget or an empty board. It exists because
    # the only mail he ever got was an alarm, and a company that writes to you
    # exclusively with bad news is one you stop opening.
    brief_daily_at: str | None = _rule(
        None, group="Reporting to you", label="A daily briefing at this time",
        help="'08:00' in your timezone, or blank for none. What happened since the last "
             "one — scope settled, work closed, proof verified, money spent, what decided "
             "itself, and what is now waiting on you. Composed from the record at zero "
             "tokens, so it arrives on a paused project and a broke one too.")

    # One timezone, read by the reporting window, the daily CEO wake and every
    # recurring rhythm. It used to be set twice — once here and once in project
    # config for the daily wake — which is one place too many to get it wrong.
    timezone: str = _rule(
        "UTC", group="Your timezone", label="Your timezone",
        help="IANA name, e.g. 'America/New_York'. Every local time in these rules — the "
             "reporting window, the daily wake, a recurring rhythm — is read in this zone.")

    # The Operate rhythm. Cycles roll in the janitor — a rhythm firing while
    # the company is busy or broke must still be recorded — and all local
    # times read the constitution's one timezone.
    cycle_frequency: str = _rule(
        "weekly", group="Operate rhythm", label="How often a cycle rolls",
        choices=["hourly", "daily", "weekly", "monthly", "quarterly"],
        help="Operate projects only. Each roll closes the cycle, carries unfinished "
             "scope forward, reopens the plan gate, and hands the CEO the last three "
             "cycles' narratives verbatim. 'hourly' is for rhythms shorter than a day "
             "— an inbox swept every morning, an order queue checked on the hour. A "
             "cycle that carries its own due-by rolls on that instead, so work paced "
             "by the outside world is not forced onto the calendar.")
    cycle_day: int = _rule(
        0, ge=0, le=28, group="Operate rhythm", label="Which day the cycle rolls",
        help="Weekly: 0=Monday … 6=Sunday. Monthly and quarterly: the day of the month, "
             "1–28 — the 29th to 31st do not exist in every month and are refused "
             "rather than quietly moved. Daily ignores this.")

    # The review. Opened, numbered and emailed by the janitor with zero
    # tokens; the CEO's narrative rides the governance reserve.
    review_interval_days: int = _rule(
        7, ge=1, group="The review", label="Days between status reviews",
        help="The standing meeting: the janitor opens it on schedule regardless of "
             "budgets or an empty board, emails you the numbers, and the CEO files its "
             "variance narrative on a reserved governance run. Your six levers — cut, "
             "add, reword, move the date, raise the budget, accept the slip — are "
             "recorded with your reasons and shown at the next review beside what "
             "they bought.")

    # The commitment. The projection itself refreshes every janitor tick; this
    # governs the third re-validation trigger — the "errors" one.
    commitment_failure_reforecast: int = _rule(
        5, ge=0, group="The commitment", label="Dead runs before the promise is re-checked",
        help="Once this many runs have died against the plan since the commitment was "
             "last validated, the platform re-projects the date and tells the CEO the "
             "ground moved. Rule changes and scope changes always re-validate; this "
             "catches the failure-storm case where neither moved but the pace did. "
             "0 turns this trigger off.")

    # Runs
    run_timeout_s: int = _rule(
        1800, ge=60, group="Runs", label="Run timeout (seconds)",
        help="A run past this is reaped and its task claim released. The CEO's "
             "resident chat session is exempt — it is meant to stay open for "
             "hours and dies only when its heartbeat stops.")
    run_token_budget: int = _rule(
        0, ge=0, group="Runs", label="Tokens one run may spend",
        help="Handed to the model as a task budget so it paces itself and wraps up "
             "before the line, instead of being cut off mid-thought. Complements the "
             "per-run dollar ceiling: dollars stop the bleeding, this tells the agent "
             "where the tourniquet is. 0 turns it off.")

    @field_validator("chairman_report_window_local")
    @classmethod
    def _window_is_readable(cls, raw: str | None) -> str | None:
        if raw is not None and parse_report_window(raw) is None:
            raise ValueError(
                f"{raw!r} is not a reporting window: write it 'HH:MM-HH:MM' in the "
                "chairman's local time, with the start before the end")
        return raw

    @field_validator("timezone")
    @classmethod
    def _timezone_exists(cls, raw: str) -> str:
        try:
            ZoneInfo(raw)
        except Exception as exc:
            raise ValueError(f"{raw!r} is not an IANA timezone (e.g. 'America/New_York')") from exc
        return raw

    @field_validator("cycle_frequency")
    @classmethod
    def _frequency_is_known(cls, raw: str) -> str:
        if raw not in ("hourly", "daily", "weekly", "monthly", "quarterly"):
            raise ValueError(
                f"{raw!r} is not a cycle frequency: hourly, daily, weekly, monthly "
                "or quarterly")
        return raw

    @model_validator(mode="after")
    def _cycle_day_fits_the_frequency(self) -> ConstitutionDoc:
        if self.cycle_frequency == "weekly" and not 0 <= self.cycle_day <= 6:
            raise ValueError("weekly cycles take cycle_day 0 (Monday) through 6 (Sunday)")
        if self.cycle_frequency in ("monthly", "quarterly") and not 1 <= self.cycle_day <= 28:
            raise ValueError(
                "monthly and quarterly cycles take cycle_day 1-28: the 29th to 31st do "
                "not exist in every month, and a rhythm that quietly moves is not a "
                "rhythm — pick a day every month has")
        return self

    @field_validator("ceo_daily_at")
    @classmethod
    def _daily_time_is_readable(cls, raw: str | None) -> str | None:
        if raw is not None and parse_hhmm(raw) is None:
            raise ValueError(
                f"{raw!r} is not a time of day: write it 'HH:MM' in your timezone, or "
                "leave it blank for no daily wake")
        return raw

    @field_validator("brief_daily_at")
    @classmethod
    def _brief_time_is_readable(cls, raw: str | None) -> str | None:
        if raw is not None and parse_hhmm(raw) is None:
            raise ValueError(
                f"{raw!r} is not a time of day: write it 'HH:MM' in your timezone, or "
                "leave it blank for no daily briefing")
        return raw

    def overlay(self, changes: dict[str, Any]) -> ConstitutionDoc:
        """Apply a partial amendment overlay, re-validating the result."""
        merged = self.model_dump()
        merged.update(changes)
        return ConstitutionDoc.model_validate(merged)


def effective(session: Session, project_id: str, at: datetime | None = None) -> ConstitutionDoc:
    """The constitution in force for a project right now: the highest version
    whose effective_at has passed."""
    at = at or utcnow()
    row = session.execute(
        select(Constitution)
        .where(Constitution.project_id == project_id, Constitution.effective_at <= at)
        .order_by(Constitution.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        raise LookupError(f"project {project_id} has no effective constitution")
    return ConstitutionDoc.model_validate(row.body)


def effective_version(session: Session, project_id: str, at: datetime | None = None) -> int:
    """The version number behind :func:`effective` — same filter, so the two
    can never describe different documents. The commitment freezes this at
    acceptance and the janitor diffs it to notice an amendment landed."""
    at = at or utcnow()
    version = session.execute(
        select(Constitution.version)
        .where(Constitution.project_id == project_id, Constitution.effective_at <= at)
        .order_by(Constitution.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    return int(version or 0)


def create_initial(session: Session, project_id: str, doc: ConstitutionDoc) -> Constitution:
    row = Constitution(
        id=ids.new_id(ids.CONSTITUTION),
        project_id=project_id,
        version=1,
        body=doc.model_dump(),
        effective_at=utcnow(),
    )
    session.add(row)
    return row


# The fields a CEO may set itself via `directive_file` — the chairman's
# reporting preferences, which are instructions *to* the company about how to
# treat him rather than limits *on* it. Everything else in the constitution is
# the chairman's alone to change.
CADENCE_FIELDS = frozenset({"chairman_report_interval_hours",
                            "chairman_report_window_local",
                            "timezone"})


def propose_amendment(
    session: Session,
    project_id: str,
    user_id: str,
    reason: str,
    changes: dict[str, Any],
) -> list[Amendment]:
    """Chairman-only (checked by the caller). Validates the whole overlay — so a
    bad amendment is refused rather than half-applied — records it with its
    written reason, and puts it in force in this very call. Returns the
    recorded amendment in a list, since callers still iterate for auditing and
    disclosure.

    There is no delay. A rule the chairman has decided on is in force for the
    next agent call, and the record of who changed what, when and why is what
    makes it accountable — not a waiting period he was always able to wait out
    or work around.
    """
    effective(session, project_id).overlay(changes)  # raises if the result is invalid
    if not changes:
        return []
    amendment = Amendment(
        id=ids.new_id(ids.AMENDMENT),
        project_id=project_id,
        proposed_by_user_id=user_id,
        reason=reason,
        changes=dict(changes),
        effective_at=utcnow(),
    )
    session.add(amendment)
    session.flush()
    apply_due_amendments(session, project_id)  # in force NOW
    return [amendment]


def apply_due_amendments(session: Session, project_id: str) -> list[Amendment]:
    """Turn every amendment whose time has come into a new constitution version.
    Idempotent.

    New amendments are due the instant they are proposed, so in practice this
    runs once, inline, from ``propose_amendment``. The janitor still calls it
    each tick: amendments written before the cooling-off was removed may still
    be sitting in the table with a future ``effective_at``, and they land on
    schedule rather than being stranded.
    """
    now = datetime.now(UTC)
    due = (
        session.execute(
            select(Amendment)
            .where(
                Amendment.project_id == project_id,
                Amendment.applied_constitution_id.is_(None),
                Amendment.cancelled_at.is_(None),
                Amendment.effective_at <= now,
            )
            .order_by(Amendment.effective_at)
        )
        .scalars()
        .all()
    )
    applied = []
    for amendment in due:
        current = session.execute(
            select(Constitution)
            .where(Constitution.project_id == project_id)
            .order_by(Constitution.version.desc())
            .limit(1)
        ).scalar_one()
        doc = ConstitutionDoc.model_validate(current.body).overlay(amendment.changes)
        new = Constitution(
            id=ids.new_id(ids.CONSTITUTION),
            project_id=project_id,
            version=current.version + 1,
            body=doc.model_dump(),
            effective_at=now,
        )
        session.add(new)
        session.flush()
        amendment.applied_constitution_id = new.id
        applied.append(amendment)
    return applied
