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
CEO in its next context pack. The project still cannot move its own goalposts;
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
from yaaf.core.models import Amendment, Constitution, Project, utcnow


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
    project actually needs — were not among them.

    ``choices`` names the values a string rule accepts. The validators below
    already refuse anything else; this only lets the editor draw a menu instead
    of a text box you have to guess into.
    """
    extra: dict[str, Any] = {"group": group}
    if choices is not None:
        extra["choices"] = choices
    return Field(default=default, title=label, description=help,
                 json_schema_extra=extra, **constraints)


#: `mail_when`'s answers, each to the notice classes it lets into the inbox
#: (`yaaf.server.notify.Attention` values — spelled as strings here because
#: notify imports this module, not the other way round). The daily brief is
#: in none of them: it has its own switch, `brief_daily_at`, and a chairman
#: who set a time for it has already said yes to that one email.
MAIL_WHEN: dict[str, frozenset[str]] = {
    "your_ceo_is_waiting": frozenset({"ask"}),
    "your_ceo_is_waiting_or_the_project_stops": frozenset({"ask", "alarm"}),
    "anything_needs_you": frozenset({"ask", "action", "alarm"}),
    "never": frozenset(),
}


# Fourteen groups is a taxonomy, not navigation: rendered flat it is a wall of
# thirty-nine boxes, and "Wake-loop brakes" alone is nine of them. Themes are
# what a chairman actually reaches for; the groups survive as the subheads
# inside one. This lives here rather than in the settings page because the last
# hand-kept list on that side went stale — see the note in `_rule`.
GROUP_THEMES = {
    "Budgets": "Money",
    # Its own theme because it is the one answer that buys more than one thing:
    # money AND the model the work is done on. Filed under Budgets it read as a
    # spending dial, and a chairman looking for "how good does this have to be"
    # had nowhere to look — which is how every project on this install ran at
    # the library's effort whatever it was worth.
    "The bar": "The bar",
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
    "Code Factory": "How work is run",
    "Code Factory upkeep": "Rhythm & reporting",
}

#: Tab order on the rules screen — money first, because that is what a stuck
#: project comes here to change.
THEME_ORDER = [
    "Money",
    "The bar",
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
        help="Top-level tasks the project may have open at once. Forces finishing over "
             "starting.")
    subtask_ceiling: int = _rule(
        10, ge=1, group="Work discipline", label="Open subtasks per task",
        help="How far a lead may decompose one parent task before it must land something.")
    # The roster is the CEO's to run, and this is the number it runs it inside.
    # Hiring buys no runs — the daily ration, the hourly ceiling and the
    # monthly wall are all project-level — so a bigger roster splits the same
    # allowance thinner rather than raising it. What it does buy is wake-ups:
    # every seat reads its channels, and a seat that does nothing still costs
    # inbox runs. So the ceiling is on seats, and the CEO argues for a higher
    # one through capacity_request like every other number it cannot set.
    max_teams: int = _rule(
        8, ge=1, group="Work discipline", label="Teams the project may run at once",
        help="Your CEO hires and stands down roles itself, from your role library, up to "
             "this many teams. It cannot exceed this without asking you.")
    staffing_review_days: int = _rule(
        14, ge=1, group="Work discipline", label="Days before an unused seat is flagged",
        help="A seat that spends runs for this long and closes no work trips a flag your "
             "CEO has to answer — stand the role down, or say on the record why it stays.")
    external_ratio_min: float = _rule(
        0.5, ge=0, le=1, group="Work discipline", label="Minimum share of outward-facing work",
        help="Fraction of open tasks that must produce evidence from outside the project. "
             "Stops a project that only talks to itself.")
    external_ratio_applies_from: int = _rule(
        3, ge=1, group="Work discipline", label="Board size before that share is enforced",
        help="Below this many open tasks the ratio is not checked — three tasks cannot be "
             "half of anything useful.")
    # The floor under the work (house rule 1's other half). Every rule above
    # stops a project doing too much; nothing noticed one doing too little
    # while scope was open. The planning wake is a clock — every six hours on
    # the default pace — and a CEO whose runs went on messages and reports
    # fed its board one task at a time while six seats sat idle all morning.
    # Measured on TEAMS, not on runs (that CEO ran 29 times) and not on an
    # empty board (one task on it hid five idle teams).
    idle_team_minutes: int = _rule(
        30, ge=0, group="Work discipline",
        label="Minutes a team may sit with nothing to do while scope is open",
        help="On a project that finishes, a team holding no task — nothing ready, "
             "claimed, checking or in review — while scope is open and no limit is "
             "stopping work. Past this many minutes a flag opens that wakes the CEO "
             "at once and stays open until every such team has work, or the CEO says "
             "in the boardroom what it is waiting for. Teams that review others' work "
             "are not counted. A budget for the whole job sets this to 5: idle time "
             "is the money being saved by accident. 0 turns it off.")
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

    deliverable_review: str = _rule(
        "none", choices=["none", "independent_read"], group="The bar",
        label="Who reads the work before you do",
        help="'independent_read': the project must staff a seat whose job is to read "
             "what the others produced — an editor, an adversary, a skeptic — and the "
             "platform refuses a roster that asks for this and hires nobody who can do "
             "it. 'none' is what every project did before this rule existed: the only "
             "reader of a deliverable before you was the agent that wrote it, and 80% "
             "of those closes were never sampled. Off by default so an install that "
             "upgrades is unchanged; every preset on the new-project form turns it on.")

    require_search_record: bool = _rule(
        False, group="The bar", label="Research must say what it did not find",
        help="On, a task that produces evidence cannot be completed without a search "
             "record: what was looked for, where, and — the half that does the work — "
             "what came back EMPTY. The alternative floor, a minimum number of cited "
             "sources, is satisfied by fetching three pages and citing them whether or "
             "not a claim derives from any, which buys a citation ritual that passes "
             "the gate and looks like rigour. A search record is unverifiable and not "
             "gameable in a way that resembles compliance, which is the more useful "
             "property: a thin one reads as thin. Off by default.")

    # Autonomy thresholds (lana §6)
    approval_threshold_usd: float = _rule(
        250.0, ge=0, group="Autonomy", label="Spending that needs your approval",
        help="USD. Spends at or above this, and anything legally ambiguous, wait for you.")
    approval_timeout_days: int = _rule(
        7, ge=1, group="Autonomy", label="Days before an approval expires",
        help="An unanswered approval decides itself at this point, so a vacationing "
             "chairman degrades the project instead of deadlocking it.")
    approval_timeout_decision: str = _rule(
        "deny", group="Autonomy", label="What an expired approval decides",
        choices=["deny", "approve"],
        help="'deny' or 'approve'. Default-deny: silence is not consent.")
    complete_after_hours: float = _rule(
        24.0, ge=0, group="Autonomy", label="Hours before a finished delivery closes itself",
        help="When everything a Deliver project must make is true, you are told, and it "
             "completes itself this long afterwards — the same receipt your Complete "
             "button writes — unless its scope moves first. You can reopen it for 14 "
             "days. 0: it waits for you to press Complete.")
    ask_window_hours_max: float = _rule(
        72.0, gt=0, group="Autonomy", label="Longest window your CEO may wait on an answer",
        help="A question to you carries the moment it stops waiting and what it will do "
             "then. This is the furthest out that moment may be — it names a shorter one "
             "when the work has a deadline. There is no setting for what an unanswered "
             "question decides: the CEO writes that down before it asks, and you see it.")
    max_open_asks: int = _rule(
        3, ge=1, group="Autonomy", label="Unanswered questions allowed on your desk",
        help="A CEO past this must wait for an answer or a window to close before asking "
             "again. Asking is cheap for it and expensive for you.")

    # The authority dial (CEO Charter, stage B). Integrity
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
        help="Whatever you store, the project can use — this decides who scopes it to "
             "roles. 'chairman': grants are yours (the CEO files needs). 'ceo': the "
             "CEO re-scopes existing credentials across its own team itself, on the "
             "record. Creating a credential is always yours — you hold the values.")

    # Budgets — native unit of the project's billing mode.
    #
    # Two numbers, deliberately, because they do two different jobs. The budget
    # is the educated guess you make before the work starts; the hard stop is
    # the wall that exists so a runaway cannot drain the account. They used to
    # be the same number, and that is why every CEO on this platform lowballed:
    # crossing the plan killed the project, so the plan became a thing to stay
    # well back from. An agent that is paused for going over does not give you
    # an honest estimate, it gives you a safe one — and a safe number and an
    # honest number are indistinguishable from outside, which is how the
    # chairman lost confidence in every figure the desk produced.
    monthly_usd_cap: float = _rule(
        1000.0, ge=0, group="Budgets", label="Monthly budget (USD)",
        help="Your educated guess at the month, in USD, under API-key billing. This is "
             "a PLAN, not a wall: crossing it does not stop the project, it obliges the "
             "CEO to report the overrun with what the money bought. The wall is the "
             "hard stop below.")
    monthly_token_cap: int = _rule(
        200_000_000, ge=0, group="Budgets", label="Monthly token budget",
        help="The same plan, in tokens, under subscription billing. Crossing it obliges "
             "a report, never a pause.")
    hard_stop_multiple: float = _rule(
        3.0, ge=1.0, group="Budgets", label="Hard stop, as a multiple of the budget",
        help="The wall. At budget × this, the project pauses itself, live runs are "
             "revoked and the queue is purged — the runaway protection the budget used "
             "to provide by being a cliff. Set it to the most you would tolerate losing "
             "on a bad month, not to what you expect to spend. 1.0 restores the old "
             "behaviour, where the plan and the wall are the same number.")
    # The job's budget, as against the month's. The two numbers above plan a
    # MONTH, which is the right question for a project that runs on a rhythm
    # and the wrong one for a project that finishes: "spend $2,000 getting this
    # built" had no rule to live in, so it was typed into a monthly plan that
    # was neither total nor a limit, beside a pace that could not reach it.
    #
    # This one is a wall, and that is deliberate. The plan-is-not-a-wall lesson
    # above was learned by CEOs ESTIMATING their own month and being killed for
    # missing; nobody estimates here — the chairman named a pot and asked for
    # it to be spent on the job. What made the old cliff poisonous was a wall
    # nobody meant to reach. This one is the planned end of the money, with a
    # reserve before it so reaching it is a hand-in rather than a crash, and a
    # floor (`idle_team_minutes`) so a fixed pot is not sat on.
    project_usd_budget: float = _rule(
        0.0, ge=0, group="Budgets", label="Budget for the whole job (USD)",
        help="USD, total since the project was created, under API-key billing. Unlike "
             "the monthly budget this is a limit: at this number the project pauses. "
             "Before it, a reserve (below) is held back so the CEO can hand in what "
             "it has. For a project that finishes once — a delivery, an "
             "investigation, a build. 0: no job budget, only the monthly one.")
    project_token_budget: int = _rule(
        0, ge=0, group="Budgets", label="Token budget for the whole job",
        help="The same limit, in tokens, under subscription billing. 0: none.")
    budget_reserve_ratio: float = _rule(
        0.05, ge=0, lt=0.5, group="Budgets", label="Share of the job budget kept for the hand-in",
        help="Past the job budget minus this share, ordinary work stops and only the "
             "CEO may still run: to write up what was done, what is left, and what "
             "more money would buy. Without it, running out means stopping mid-task "
             "with nothing written down. 0 turns the reserve off.")
    # Sprint answers "as fast as the money allows". This answers the other
    # question a job budget can carry: "use it across the window". An Explore
    # project chasing one sale in sixty days that spent its whole pot in the
    # first week has nothing left for the weeks the market takes to answer.
    spread_job_budget: bool = _rule(
        False, group="Budgets", label="Spread the job budget evenly to the deadline",
        help="On, each day may spend an equal share of what is left of the job budget "
             "(less the hand-in reserve) over the days left to the project's deadline; "
             "past today's share, ordinary work waits for midnight UTC. Needs a job "
             "budget and a deadline. Off: the job budget is spent as fast as the work "
             "allows.")
    budget_warn_ratio: float = _rule(
        0.8, gt=0, lt=1, group="Budgets", label="Warn at this share of the budget",
        help="Where the CEO is told it is approaching the plan — early enough to say "
             "something about it while there is still a choice to make.")
    stakes: str = _rule(
        "normal", choices=["low", "normal", "high"], group="The bar",
        label="How much this project matters (stakes)",
        # The word is in the label because this rule is the one a chairman goes
        # LOOKING for, and could not find. The CEO calls it "STAKES: NORMAL" in
        # its own reports and the docs and CLI call it `stakes`, but the screen
        # called it nothing of the kind and there is no search here — so the one
        # dial written for "this desk trades a real account" was invisible to
        # the person holding it. It sits under The bar rather than Budgets now
        # for the same reason: once it decides which model writes what you read,
        # filing it under money is a second way to lose it.
        help="Answered once, and it now buys two things. MONEY: how aggressively to "
             "spend when the budget turns out to be wrong, which it will. HIGH means "
             "running out is worse than overspending — the CEO is told to spend what "
             "the work needs and report, and it gets real headroom before anyone "
             "worries. LOW means the work is not worth much; going over means stop and "
             "ask. CRAFT: at HIGH, the seats that produce or check what you actually "
             "read are hired on the strongest model and the deepest reasoning, rather "
             "than on whatever the shared role library says — because raising it in the "
             "library raises it for every project on this install. Seats you gave an "
             "explicit model or effort keep yours. Nothing is ever lowered. This is the "
             "difference between a project posting to LinkedIn and one trading a real "
             "account, and until it existed both ran under identical caution because "
             "the platform had no way to be told which was which.")
    # The spend envelope (CEO Charter, stage C). Real-world
    # purchases, as distinct from model spend. There is no spend_authority
    # switch on purpose: the money dial IS these numbers, and a switch would
    # only duplicate them. Inside the envelope an autonomous CEO buys without
    # asking; outside it, the existing approval machinery answers.
    external_monthly_usd_cap: float = _rule(
        500.0, ge=0, group="Budgets", label="Monthly real-world spend (USD)",
        help="USD the project may spend in the world per month — purchases, "
             "subscriptions, services — separate from the model budget and enforced "
             "in EVERY billing mode (token-billed projects used to meter this and "
             "never cap it). A purchase that would cross it is refused with the "
             "arithmetic; raising it is an amendment. 0 turns it off.")
    max_usd_per_purchase: float = _rule(
        100.0, ge=0, group="Budgets", label="Most one purchase may be, unasked",
        help="USD. A single purchase above this needs an approval even below the "
             "approval threshold — the line between running the project and "
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
        help="The daily activity PLAN across every agent — what the project should "
             "need. Not the wall: work continues past it, owing an account, up to "
             "this number times `runs_hard_stop_multiple`.")
    runs_hard_stop_multiple: float = _rule(
        1.0, ge=1.0, group="Wake-loop brakes",
        label="Run hard stop, as a multiple of the daily cap",
        help="The wall, as distinct from the plan. The daily cap is what the project "
             "should need; at cap × this, ordinary work really does stop for the day. "
             "Between the two the project keeps working and OWES an account — the same "
             "shape as the budget, which learned this first: a number that is fatal to "
             "cross is not a plan anybody works to, it is a cliff everybody stands back "
             "from, and the damage is that a safe number and an honest one look "
             "identical from outside. "
             "Raise it on a desk whose run ration is the thing actually pacing it. "
             "It defaults to 1.0 — plan and wall the same number, exactly as before "
             "this rule existed — because a default above 1 would have raised the "
             "daily volume of every project on the install the moment it shipped, "
             "without anyone choosing that for any of them. This does NOT touch 'Runs "
             "in flight at once', which is what protects the provider rate limit the "
             "whole box shares — that one is simultaneity, not volume, and it binds "
             "absolutely however far past its plan a project runs.")
    runs_per_project_per_hour: int = _rule(
        15, ge=1, group="Wake-loop brakes", label="Runs per hour, whole project",
        help="The pacing brake. The daily ceiling refills all at once, so without this a "
             "project can spend a whole day's allowance in the first hour and then sit "
             "dead until midnight with work stranded on the board. This is a trailing "
             "hour, not a clock hour, so it clears continuously rather than in a lump.")
    # The inbox lane. Reading is real cost — every wake is a paid model call —
    # but it is not work, and billing it to the work allowance is how a
    # project's own chatter eats the runs its deliverables needed. These are
    # the walls that keep "reading is cheap" from meaning "reading is free".
    inbox_runs_per_project_per_day: int = _rule(
        40, ge=0, group="Wake-loop brakes", label="Message reads per day that don't use runs",
        help="Wakes that only read messages, across every agent, that don't count against "
             "the daily runs. This is not a ceiling: past it, agents still wake to read and "
             "each read counts as one of the day's runs — after every agent with real work, "
             "and never from the runs kept for reviews. 0 switches off waking just to read: "
             "agents then read their channels on the next wake they were having anyway.")
    inbox_runs_per_project_per_hour: int = _rule(
        8, ge=0, group="Wake-loop brakes", label="Message reads per hour that don't use runs",
        help="The same, per hour: a broadcast to leadership makes every agent unread at the "
             "same instant. Past it, reads count as runs, under the hourly run limit. 0 "
             "switches off waking just to read, like the daily number.")
    pair_breaker_messages: int = _rule(
        10, ge=2, group="Wake-loop brakes", label="Messages before a thread freezes",
        help="Two agents talking this many times in one thread with nothing landing are "
             "talking, not working; the thread freezes.")
    max_usd_per_run: float = _rule(
        20.0, ge=0, group="Wake-loop brakes", label="Most one run may spend",
        help="USD. A single run that spends this much has stopped working and started "
             "looping; it is told as it approaches and stopped at the line. 0 turns it "
             "off. One task once cost $8.05 across 53 runs, and nothing bounded any of "
             "them individually. It was $2 until that line killed 1,224 working runs: "
             "an ordinary run is cents, a big healthy one about $13.")
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
             "is refused: a project should wait for the previous step to land, not for "
             "a clock. One CEO gated its only open task two hours out and the project "
             "sat silent until the chairman paused it.")
    stall_hours: float = _rule(
        3.0, ge=0, group="Wake-loop brakes", label="Hours of silence before you are told",
        help="A running project with work on its board and no run started for this long "
             "is stalled, whatever the board says. You get one notice naming the exact "
             "cause. 0 turns the watchdog off.")
    max_concurrent_runs: int = _rule(
        3, ge=1, group="Wake-loop brakes", label="Runs in flight at once",
        help="How many agents may be working simultaneously. Protects the provider rate "
             "limit that every project on the box shares.")
    reserved_governance_runs: int = _rule(
        3, ge=0, group="Wake-loop brakes", label="Runs held back for governance",
        help="Runs kept out of the ceiling so a status review, a cycle plan or a "
             "commitment re-forecast can always happen. Ordinary work stops this many "
             "short of the WALL (the daily plan times `runs_hard_stop_multiple`); "
             "governance runs may use the full wall. Without it the "
             "day the project burns its allowance by lunch is the day it cannot report "
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

    # The chairman's reporting cadence. A wake ceiling stops the project
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

    # The briefing. Everything above governs what the *project* may say to the
    # chairman and costs a model call to produce. This one is the platform
    # writing to him from rows it already holds: no run, no token, no wake, and
    # so no reason to gate it on a budget or an empty board. It exists because
    # the only mail he ever got was an alarm, and a project that writes to you
    # exclusively with bad news is one you stop opening.
    brief_daily_at: str | None = _rule(
        None, group="Reporting to you", label="A daily briefing at this time",
        help="'08:00' in your timezone, or blank for none. What happened since the last "
             "one — scope settled, work closed, proof verified, money spent, what decided "
             "itself, and what is now waiting on you. Composed from the record at zero "
             "tokens, so it arrives on a paused project and a broke one too.")

    # How much of his attention the platform may spend. Every other rule in
    # this group governs what the *project* may say; this one governs what the
    # *platform* may, and it needed saying because the platform was the noisier
    # of the two. It only rations the notices that are waiting on him — an
    # alarm and this brief are never held, since rationing the message that
    # says the project stopped is how a project sits dead for a week.
    mail_max_per_day: int = _rule(
        5, ge=0, le=100, group="Reporting to you",
        label="At most this many emails a day",
        help="Counts only mail waiting on a decision from you, over a rolling 24 hours. "
             "Past it, the platform stops interrupting and the waits are read in the "
             "daily briefing instead, which already lists every one of them. Alarms — "
             "the project paused, a promise broken, a credential dead — are never held. "
             "0 means no limit.")

    # Which of his attention the platform may spend at all — the ration above
    # only says how much. Defaults to the one thing he asked to be interrupted
    # for: his CEO waiting on him and not answered in the app. Everything
    # else is still on his desk, in the boardroom and in the briefing; the
    # inbox is just not where it lands unless he widens this.
    mail_when: str = _rule(
        "your_ceo_is_waiting", group="Reporting to you", label="Email me when",
        choices=list(MAIL_WHEN),
        help="'your_ceo_is_waiting': a decision, question, need or heads-up from your "
             "CEO that you have not answered in the app. "
             "'your_ceo_is_waiting_or_the_project_stops': that, plus alarms — the "
             "project paused, a promise broken, a credential dead. "
             "'anything_needs_you': every wait and every alarm, including the "
             "platform's own reminders (status meetings, verdicts, ready to complete). "
             "'never': no email. The daily briefing has its own switch above and is "
             "not affected. Nothing is lost either way — every one of these is still "
             "in your Inbox, the boardroom and the briefing.")
    mail_grace_minutes: int = _rule(
        60, ge=0, le=10080, group="Reporting to you",
        label="Minutes to answer in the app before you are emailed",
        help="Something your CEO is waiting on is emailed only if it is still open this "
             "long after it was filed. Answer it, or snooze it, in the app first and no "
             "email is sent. 0 emails it at once.")

    # One timezone, read by the reporting window, the daily CEO wake and every
    # recurring rhythm. It used to be set twice — once here and once in project
    # config for the daily wake — which is one place too many to get it wrong.
    timezone: str = _rule(
        "UTC", group="Your timezone", label="Your timezone",
        help="IANA name, e.g. 'America/New_York'. Every local time in these rules — the "
             "reporting window, the daily wake, a recurring rhythm — is read in this zone.")

    # The Operate rhythm. Cycles roll in the janitor — a rhythm firing while
    # the project is busy or broke must still be recorded — and all local
    # times read the constitution's one timezone.
    cycle_frequency: str = _rule(
        "weekly", group="Operate rhythm", label="How often a cycle rolls",
        choices=["hourly", "daily", "weekly", "monthly", "quarterly"],
        help="Operate projects, and a Code Factory once it is in upkeep. Each roll "
             "closes the cycle, carries unfinished "
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
        7, ge=1, group="The review", label="Days between status meetings",
        help="The status meeting: the janitor opens it on schedule regardless of "
             "budgets or an empty board, with what was done from the record and one "
             "sentence on where the project stands; the CEO files its status report on "
             "a reserved governance run. Your six levers — cut, add, reword, move the "
             "date, raise the budget, accept the slip — are recorded with your reasons "
             "and shown at the next meeting beside what they bought. On an Operate "
             "project with a weekly or longer rhythm the meeting opens at the cycle "
             "roll instead and this interval is not used.")
    review_replies: int = _rule(
        2, ge=0, group="The review", label="CEO replies per meeting on the reserve",
        help="When you say something back on an open review, the CEO answers in the "
             "meeting's thread. This many of its answers per meeting may spend the "
             "governance reserve, so they land on a day the ordinary allowance is "
             "gone; past it, an answer waits for an ordinary wake like any other "
             "message. 0 means every answer waits.")

    # The commitment. The projection itself refreshes every janitor tick; this
    # governs the third re-validation trigger — the "errors" one.
    commitment_failure_reforecast: int = _rule(
        5, ge=0, group="The commitment", label="Dead runs before the promise is re-checked",
        help="Once this many runs have died against the plan since the commitment was "
             "last validated, the platform re-projects the date and tells the CEO the "
             "ground moved. Rule changes and scope changes always re-validate; this "
             "catches the failure-storm case where neither moved but the pace did. "
             "0 turns this trigger off.")

    # Code Factory. Gates on a change before the platform merges it; they bind
    # only a Code Factory, whose main branch only the platform moves.
    max_change_lines: int = _rule(
        400, ge=20, le=5000, group="Code Factory", label="Largest change, in lines",
        help="Code Factory only. A change adding and removing more lines than this, "
             "counted by GitHub rather than by the agent, is refused and closed: split "
             "it. Reviews of big changes miss what reviews of small ones catch, and a "
             "factory that ships large changes ships them unread.")

    protected_paths: str = _rule(
        "", group="Code Factory", label="Paths that need a person",
        help="Code Factory only. Comma-separated file globs, e.g. billing/tax/**, "
             "migrations/**. A change touching any of them merges only with your own "
             "yes on the exact commit, whatever the authority dial says.")
    hidden_scenarios: bool = _rule(
        True, group="Code Factory", label="Hidden scenarios must pass",
        help="Code Factory only. A change merges only when the hidden scenarios for "
             "its requirement pass, run by the platform on its exact commit. A "
             "challenge seat writes them; builders see how many exist and pass, never "
             "what they say. Off, the gate is skipped and builders grade themselves "
             "against tests they can read.")

    ci_changes_need_chairman: bool = _rule(
        True, group="Code Factory", label="CI changes need you",
        help="Code Factory only. The checks gate trusts CI, and CI is defined in the repo, "
             "so a change to it could make every check pass. On: a change touching CI "
             "config (.github/workflows and the like) merges only with your yes on that "
             "commit. Off: a challenge seat's signed reason is enough.")
    min_changed_line_coverage: int = _rule(
        0, ge=0, le=100, group="Code Factory", label="Changed lines tested, at least %",
        help="Code Factory only. Read from the check run named below, which CI reports on "
             "the change's own commit with the percentage in its title or summary. 0 is off.")
    coverage_check_name: str = _rule(
        "coverage", group="Code Factory", label="Check run that reports coverage",
        help="Code Factory only. The name of the CI check whose title or summary carries "
             "the changed-line coverage, e.g. '87.5% of changed lines covered'.")
    code_health_ratchet: bool = _rule(
        True, group="Code Factory", label="Changes may not make the code harder to maintain",
        help="Code Factory only. The platform measures the files a change touches, before "
             "and after: functions too complex (more than 10 branches) or too long (more "
             "than 60 lines), and Python lint aimed at what models get wrong. A change that "
             "makes either worse waits for a challenge seat's signed reason. Models fix the "
             "problem in front of them and leave the codebase worse; this is the ratchet.")
    platform_scans: bool = _rule(
        True, group="Code Factory", label="The platform scans every change",
        help="Code Factory only. The platform runs osv-scanner (known-vulnerable "
             "dependencies, high and critical) and gitleaks (secrets) on each change's "
             "commit in the sandbox image, and holds the merge on what they find. A "
             "runner whose image lacks them holds every merge until it is rebuilt.")
    release_authority: str = _rule(
        "approve_first", choices=["approve_first", "autonomous"], group="Code Factory",
        label="Releases to production",
        help="Code Factory only. 'approve_first': you approve the first production "
             "release and every major one; after that the crew ships minor and patch "
             "releases. 'autonomous': the crew ships every release, and you are told "
             "after. Either way a release points at a commit the platform merged, the "
             "canary guard watches it, and a broken guard rolls it back without asking.")
    canary_minutes: int = _rule(
        30, ge=1, le=1440, group="Code Factory", label="Canary watch, in minutes",
        help="Code Factory only. How long the platform reads production health after "
             "a deploy before it calls the release live.")
    canary_max_failures: int = _rule(
        3, ge=1, le=100, group="Code Factory", label="Failed health reads that roll back",
        help="Code Factory only. This many failed reads of the health URL during the "
             "watch, and the platform deploys the previous release again and opens an "
             "incident. Nobody is asked first.")

    # Upkeep. Each becomes a standing promise the platform writes when a
    # factory enters upkeep and judges at every roll from what the repo
    # reported — 0 leaves that promise out.
    upkeep_main_green_pct: int = _rule(
        95, ge=0, le=100, group="Code Factory upkeep", label="Main green, % of the week",
        help="Code Factory only. The share of the cycle main's CI was green, from the "
             "check runs GitHub reported on main's head. 0 leaves this promise out.")
    upkeep_issue_hours: int = _rule(
        24, ge=0, le=720, group="Code Factory upkeep", label="New issues answered within, hours",
        help="Code Factory only. The longest any new issue waited for a reply from "
             "someone with write access. 0 leaves this promise out.")
    upkeep_critical_days: int = _rule(
        2, ge=0, le=90, group="Code Factory upkeep", label="Critical advisories fixed within, days",
        help="Code Factory only. Counted from GitHub's own Dependabot alert. 0 leaves "
             "advisories out of upkeep.")
    upkeep_high_days: int = _rule(
        7, ge=0, le=180, group="Code Factory upkeep", label="High advisories fixed within, days",
        help="Code Factory only. Counted from GitHub's own Dependabot alert.")
    upkeep_flaky_max: int = _rule(
        3, ge=0, le=100, group="Code Factory upkeep", label="Most flaky checks allowed",
        help="Code Factory only. A check on main that both failed and passed on the same "
             "commit this cycle is flaky. Set it to 0 to promise none. Off with "
             "upkeep_flaky_enabled.")
    upkeep_flaky_enabled: bool = _rule(
        True, group="Code Factory upkeep", label="Promise on flaky checks",
        help="Code Factory only. Off leaves flaky checks out of upkeep.")
    upkeep_majors_behind: int = _rule(
        1, ge=0, le=20, group="Code Factory upkeep", label="Most major versions behind",
        help="Code Factory only. For each direct dependency on main, how many major "
             "versions it trails the registry's latest. Off with upkeep_packages_enabled.")
    upkeep_health_enabled: bool = _rule(
        True, group="Code Factory upkeep", label="Promise the codebase gets no harder to maintain",
        help="Code Factory only. Main is measured each day; the cycle is kept when it ends "
             "with no more over-complex functions or lint findings than it began with.")
    upkeep_packages_enabled: bool = _rule(
        True, group="Code Factory upkeep", label="Promise on package age",
        help="Code Factory only. Off leaves package lag out of upkeep.")

    # Runs
    run_timeout_s: int = _rule(
        5400, ge=60, group="Runs", label="Run timeout (seconds)",
        help="A run past this is reaped and its task claim released. The CEO's "
             "resident chat session is exempt — it is meant to stay open for "
             "hours and dies only when its heartbeat stops. It was 30 minutes until "
             "that line killed 90 runs in one week, most of them a few minutes from "
             "done; the per-run dollar cap is what stops a run that is looping.")
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

    @field_validator("mail_when")
    @classmethod
    def _mail_when_is_known(cls, raw: str) -> str:
        if raw not in MAIL_WHEN:
            raise ValueError(f"{raw!r} is not a choice here: " + ", ".join(MAIL_WHEN))
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

    @model_validator(mode="after")
    def _sprint_has_a_pot(self) -> ConstitutionDoc:
        # Sprint's run ceilings are set so high that money is the only thing
        # pacing it. Without a job budget the only money limit left is the
        # monthly plan times its multiple — three months of budget in a month,
        # spent as fast as the provider allows. The card means "spend THIS";
        # with no "this" it is refused rather than left to mean "spend".
        if self.spread_job_budget and not (self.project_usd_budget
                                           or self.project_token_budget):
            raise ValueError(
                "spread_job_budget spreads a budget for the whole job across its "
                "window, and there is no job budget to spread")
        if (tempo_name(self.model_dump()) == "sprint"
                and not (self.project_usd_budget or self.project_token_budget)):
            raise ValueError(
                "the Sprint pace needs a budget for the whole job "
                "(project_usd_budget, or project_token_budget under subscription "
                "billing): it lifts the run limits so the budget is what paces it, "
                "and with no budget nothing would")
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


def rule_label(key: str) -> str:
    """A rule as a person names it, mid-sentence: the settings page's own label
    ("open tasks at once"), not the field that stores it (`queue_ceiling`).

    The briefing used to list what he changed by key — "you changed 7 rules
    (ceo_heartbeat_always, ceo_heartbeat_hours, …)" — on Home, in the morning
    email and in `yaaf briefing`, beside a settings page that has never shown
    him a key. Lower-cased only where the label does not open on an acronym.
    A key the schema no longer knows still reads as words rather than vanishing.
    """
    field = ConstitutionDoc.model_fields.get(key)
    label = (field.title if field is not None else None) or key.replace("_", " ")
    first = label.split(" ", 1)[0]
    acronym = len(first) > 1 and first.isupper()
    return label if acronym else label[:1].lower() + label[1:]


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


# The authority dial as two named bundles. The five rules
# only ever move together at creation — that is what a preset is — and they
# were spelled out in three places free to disagree: the New-project form, the
# Founder's prompt, and whatever a caller happened to send. They live here now,
# beside the rules themselves.
AUTONOMY_RULES = ("plan_authority", "scope_authority", "commitment_authority",
                  "verdict_policy", "capability_authority")

AUTONOMY_PRESETS: dict[str, dict[str, str]] = {
    "supervised": {
        "plan_authority": "chairman",
        "scope_authority": "chairman",
        "commitment_authority": "chairman",
        "verdict_policy": "chairman",
        "capability_authority": "chairman",
    },
    "autonomous": {
        "plan_authority": "ceo",
        "scope_authority": "ceo",
        "commitment_authority": "ceo",
        "verdict_policy": "ceo_judgment",
        "capability_authority": "ceo",
    },
}

#: What a new project runs at when nobody said. The field defaults above stay
#: `chairman` on purpose — those are what an *omitted* rule means for a
#: document already stored, and flipping them would quietly re-chair every
#: constitution written before the dial existed. This answers a different
#: question: what a chairman who never mentioned autonomy actually wanted. He
#: wanted the thing to run. Supervised is one card in the form, one line in a
#: spec, and one amendment afterwards.
DEFAULT_AUTONOMY = "autonomous"


def preset_name(constitution: dict[str, Any]) -> str | None:
    """Which preset this constitution's Autonomy group *is*, or None for a dial
    somebody set rule by rule."""
    return next((name for name, rules in AUTONOMY_PRESETS.items()
                 if all(constitution.get(key) == value for key, value in rules.items())), None)


def default_autonomy(constitution: dict[str, Any]) -> dict[str, Any]:
    """A creation-time constitution with the dial filled in if it was silent.

    Silent means *none* of the five named. Naming even one is somebody choosing
    rule by rule — the form calls that a mixed dial and refuses to round it —
    so the others are left to their field defaults rather than stamped with an
    opinion the author never wrote.
    """
    if any(rule in constitution for rule in AUTONOMY_RULES):
        return dict(constitution)
    return {**AUTONOMY_PRESETS[DEFAULT_AUTONOMY], **constitution}


# The bar, and the tempo, as named bundles — the same move `AUTONOMY_PRESETS`
# above made, for the same reason. These lived in `web/src/pages/NewProject.tsx`
# and nowhere else, which meant the Founder could not pick one, the MCP surface
# could not pick one, a spec file could not name one, no stored constitution
# could say which one it was, and the chat box — the door most projects are
# actually created through — had no access to any of them.
#
# They are TWO bundles rather than one because they answer two questions that
# were being asked as one. The card was labelled "How does it run?" and set
# money and quality; it touched exactly one of the twenty-two rules that govern
# pace, so "Cheap and patient" and "Full send" woke the project equally often —
# 150 runs a day, a planning wake every six hours, forty message-only wakes.
# The chairman picking the cheap card got a smaller number to overshoot, not a
# quieter project, and there was no way at all to say "do it properly, but
# rarely".

#: How good the work has to be. It used to carry the money too — the monthly
#: budget and the approval threshold — and that is how a card called "Full
#: send" came to sit beside "Live" reading as a speed: it set $5,000 a month
#: and a stronger model, and the chairman heard "go fast". The money is the
#: chairman's own number now, typed on the form as a budget for the whole job
#: or for the month, and a quality card never moves it. `hard_stop_multiple`
#: stays: how far past a monthly plan is tolerable is a stakes question.
BAR_RULES = ("stakes", "deliverable_review",
             "hard_stop_multiple", "max_usd_per_run",
             "run_timeout_s", "require_evidence_verification", "drought_hours",
             "chairman_report_interval_hours")

#: How often the project thinks. Every one of these was previously reachable
#: only by finding it in Settings, one rule at a time, unaided.
TEMPO_RULES = ("runs_per_project_per_day", "runs_per_project_per_hour",
               "wakes_per_agent_per_hour", "inbox_runs_per_project_per_day",
               "inbox_runs_per_project_per_hour", "max_concurrent_runs",
               "reserved_governance_runs", "ceo_heartbeat_hours",
               "ceo_daily_at", "max_task_gate_hours", "pair_breaker_messages",
               "mail_max_per_day", "queue_ceiling")

BAR_PRESETS: dict[str, dict[str, Any]] = {
    "full_send": {
        "chairman_report_interval_hours": 1.0,
        "stakes": "high", "deliverable_review": "independent_read",
        "hard_stop_multiple": 3.0,
        "max_usd_per_run": 50.0,
        "run_timeout_s": 10800, "require_evidence_verification": False,
        "drought_hours": 72,
    },
    "balanced": {
        "chairman_report_interval_hours": 1.0,
        "stakes": "normal", "deliverable_review": "independent_read",
        "hard_stop_multiple": 3.0,
        "max_usd_per_run": 20.0,
        "run_timeout_s": 5400, "require_evidence_verification": False,
        "drought_hours": 24,
    },
    "frugal": {
        "chairman_report_interval_hours": 4.0,
        "stakes": "low", "deliverable_review": "independent_read",
        # The one number that moves off the field default. Every card used to
        # leave this alone, so the cheap card's real wall was 3x its plan — a
        # chairman who asked for the cheapest bar got the same wall as one who
        # asked for "nothing spared". Low stakes means the wall sits close.
        "hard_stop_multiple": 2.0,
        "max_usd_per_run": 20.0,
        "run_timeout_s": 5400, "require_evidence_verification": False,
        "drought_hours": 48,
    },
    "show_me_proof": {
        "chairman_report_interval_hours": 1.0,
        "stakes": "normal", "deliverable_review": "independent_read",
        "hard_stop_multiple": 3.0,
        "max_usd_per_run": 20.0,
        "run_timeout_s": 5400, "require_evidence_verification": True,
        "drought_hours": 24,
    },
}

TEMPO_PRESETS: dict[str, dict[str, Any]] = {
    # The card for "get it done, within this budget". Every volume ceiling is
    # set past anything a real board reaches, so the job budget — which this
    # card is refused without (`_sprint_has_a_pot`) — is the limit that binds.
    # What is NOT lifted is every brake that stops waste rather than work:
    # wakes per agent (a looping seat), the pair breaker (two seats talking),
    # and concurrency, which protects the provider rate limit every project on
    # the box shares — raised to 6, and still under the install's own
    # per-provider ceiling. Buying parallelism is what a budget can do; a
    # chain of dependent steps is as long as it is.
    "sprint": {
        "runs_per_project_per_day": 2000, "runs_per_project_per_hour": 120,
        "wakes_per_agent_per_hour": 6,
        "inbox_runs_per_project_per_day": 400,
        "inbox_runs_per_project_per_hour": 60,
        "max_concurrent_runs": 6, "reserved_governance_runs": 5,
        "ceo_heartbeat_hours": 2.0, "ceo_daily_at": None,
        "max_task_gate_hours": 6, "pair_breaker_messages": 10,
        "mail_max_per_day": 5,
        "queue_ceiling": 12,
    },
    # Today's field defaults, and deliberately so: a quieter default would have
    # lowered the daily volume of every project on the install the moment it
    # shipped, without anyone choosing that for any of them. That is the
    # reasoning `runs_hard_stop_multiple` already records, and it applies in
    # both directions.
    "live": {
        "runs_per_project_per_day": 150, "runs_per_project_per_hour": 15,
        "wakes_per_agent_per_hour": 6,
        "inbox_runs_per_project_per_day": 40,
        "inbox_runs_per_project_per_hour": 8,
        "max_concurrent_runs": 3, "reserved_governance_runs": 3,
        "ceo_heartbeat_hours": 6.0, "ceo_daily_at": None,
        "max_task_gate_hours": 6, "pair_breaker_messages": 10,
        "mail_max_per_day": 5,
        "queue_ceiling": 8,
    },
    # Not invented here. These are the reference project's
    # starting numbers, which a chairman wrote by hand, one rule at a time,
    # with a justification per row — because there was no card. Its own
    # reasons are worth keeping: 40 a day is "roughly one run every 35
    # minutes, around the clock"; 6 an hour "stops the day's allowance being
    # spent by 10am"; 3 wakes an agent is "halved from the default"; and 12/4
    # on the inbox lane because "reading is real money". `fpl-manager.md`
    # independently landed within spitting distance of all of it.
    "working_hours": {
        "runs_per_project_per_day": 40, "runs_per_project_per_hour": 6,
        "wakes_per_agent_per_hour": 3,
        "inbox_runs_per_project_per_day": 12,
        "inbox_runs_per_project_per_hour": 4,
        "max_concurrent_runs": 2, "reserved_governance_runs": 3,
        "ceo_heartbeat_hours": 12.0, "ceo_daily_at": "08:30",
        "max_task_gate_hours": 12, "pair_breaker_messages": 6,
        "mail_max_per_day": 3,
        "queue_ceiling": 5,
    },
    # The card that did not exist, and the reason the whole complaint kept
    # coming back: a project whose scripts are already written and which needs
    # to wake, look, and write a report. Two rules here are the ones nobody
    # ever found on their own. `inbox_runs_per_project_per_day: 0` is the
    # chatter switch — its own help text says agents then read their channels
    # "on the next wake they were having anyway", so nothing is lost and the
    # reading rides a run already paid for. And `max_task_gate_hours: 36`,
    # because the default of 6 refuses a CEO trying to lay work out for
    # tomorrow morning: set this tempo without it and the project is throttled
    # AND cannot sequence, which is the shape of a bug rather than a budget.
    "few_times_daily": {
        "runs_per_project_per_day": 12, "runs_per_project_per_hour": 3,
        "wakes_per_agent_per_hour": 2,
        "inbox_runs_per_project_per_day": 0,
        "inbox_runs_per_project_per_hour": 0,
        "max_concurrent_runs": 1, "reserved_governance_runs": 2,
        "ceo_heartbeat_hours": 24.0, "ceo_daily_at": "09:00",
        "max_task_gate_hours": 36, "pair_breaker_messages": 4,
        "mail_max_per_day": 2,
        "queue_ceiling": 4,
    },
}

#: `chairman_report_interval_hours` looks like a tempo rule and is deliberately
#: not one. It is opt-in by design — 0 means "you never asked for a cadence and
#: nothing is refused" — and tempo is the one bundle that gets stamped on
#: silence. Putting it here would have made a project nobody said anything about
#: start REFUSING its CEO's unprompted statuses, which is the opposite of the
#: direction that rule was written to protect: it exists because a CEO posted
#: thirteen statuses in 2h20m, not because one posted too few. It stays on the
#: bar card, where it only ever arrives by an explicit pick, exactly as the
#: run card always applied it.
#:
#: `stall_hours` is deliberately NOT a tempo rule either. A low-tempo project with work
#: on its board is throttled rather than silent, and a throttled project names
#: the moment its ceiling clears (`dispatcher.activity_state` -> `clears_at`),
#: which the watchdog already skips. Moving it per card would have been a guess
#: dressed as a bundle.

#: Which tempo a mode means when the chairman said nothing about pace. Explore
#: and Deliver both end — one against a window, one against a date — so racing
#: is the correct behaviour and there is no long tail to pay for. Operate is the
#: one that runs forever, and its own rhythm already says how fast: a project
#: sweeping an inbox on the hour is live; one that rolls a cycle a week is the
#: project this whole card exists for.
TEMPO_FOR_CYCLE = {"hourly": "live", "daily": "working_hours",
                   "weekly": "few_times_daily", "monthly": "few_times_daily",
                   "quarterly": "few_times_daily"}

DEFAULT_TEMPO = "live"


def tempo_for(mode: str, cycle_frequency: str | None = None,
              job_budget: bool = False) -> str:
    """The tempo a project of this shape runs at when nobody picked one.

    A project that ends, given a budget for the whole job, sprints: the
    chairman named the money and the finish line, and the only thing left to
    decide is how fast — which a pot exists to answer. Without one it stays on
    the old default, because sprinting on a monthly plan has no wall in sight.
    """
    if str(mode) != "operate":
        return "sprint" if job_budget else DEFAULT_TEMPO
    return TEMPO_FOR_CYCLE.get(cycle_frequency or "weekly", "few_times_daily")


def has_job_budget(constitution: dict[str, Any]) -> bool:
    return bool(constitution.get("project_usd_budget")
                or constitution.get("project_token_budget"))


def job_budget_problem(billing_mode: str, cons: ConstitutionDoc) -> str | None:
    """Why this constitution's job budget could not be enforced on a project
    that bills this way, or None.

    `ConstitutionDoc` cannot ask: it does not know how the project pays. A
    Sprint on a subscription project carrying only a dollar budget validates
    there and then has no wall at all, which is the one state the card's own
    validator exists to refuse.
    """
    usd_mode = billing_mode == "api_key"
    has_it = cons.project_usd_budget if usd_mode else cons.project_token_budget
    other = cons.project_token_budget if usd_mode else cons.project_usd_budget
    if other and not has_it:
        return ("this project bills in " + ("USD" if usd_mode else "tokens")
                + ", so its job budget has to be "
                + ("project_usd_budget" if usd_mode else "project_token_budget")
                + " — the other unit cannot be measured here and would never stop it")
    return None


SPREAD_NEEDS_A_DATE = ("spread_job_budget spreads the job budget to the project's "
                       "deadline, and this project has none: set the date first")


#: How long a team may sit idle on a project with a budget for the whole job.
JOB_IDLE_TEAM_MINUTES = 5


def default_job_month(constitution: dict[str, Any]) -> dict[str, Any]:
    """A creation-time constitution whose monthly plan matches its job budget
    when the author stated the one and not the other.

    The monthly machinery — the warning line, the overrun account — runs on
    every project. On one with a job budget it must never trip before the job
    budget does, or the CEO is told to account for "the month going over" a
    number nobody chose. Matching them makes the job budget the first line
    crossed in every month. A monthly plan somebody did write is left alone.
    """
    out = dict(constitution)
    if has_job_budget(out) and "idle_team_minutes" not in out:
        # A pot handed over to be spent: a team idle for five minutes is the
        # CEO's to answer, not half an hour. Written into the rules, where the
        # chairman can see and change it, rather than inferred from the card.
        out["idle_team_minutes"] = JOB_IDLE_TEAM_MINUTES
    if out.get("project_usd_budget") and "monthly_usd_cap" not in out:
        out["monthly_usd_cap"] = float(out["project_usd_budget"])
    if out.get("project_token_budget") and "monthly_token_cap" not in out:
        out["monthly_token_cap"] = int(out["project_token_budget"])
    return out


def bar_name(constitution: dict[str, Any]) -> str | None:
    """Which bar preset this constitution *is*, or None for a hand-tuned one."""
    return next((name for name, rules in BAR_PRESETS.items()
                 if all(constitution.get(key) == value for key, value in rules.items())), None)


def tempo_name(constitution: dict[str, Any]) -> str | None:
    """Which tempo preset this constitution *is*, or None for a hand-tuned one.

    None is the honest answer and callers must show it as such. A chairman who
    edited four rules by hand is not "on Steady"; telling him he is, is
    how he later reads a card blurb as a description of his own project and
    believes something untrue about what it costs.
    """
    return next((name for name, rules in TEMPO_PRESETS.items()
                 if all(constitution.get(key) == value for key, value in rules.items())), None)


def stated_tempo(constitution: dict[str, Any]) -> str | None:
    """The card this constitution NAMES, as against the one it happens to equal.

    `tempo_name` answers "what is this project's pace", and it is right to answer
    from the whole document. But `live` is byte-identical to the field defaults,
    so a spec that names one pace rule at its default value — `queue_ceiling: 8`,
    say — produces a project that genuinely IS live, and `tempo_name` says so.
    Nobody chose it.

    Two callers need that difference and both got it wrong by asking the first
    question: the doctrine check that refuses a Founder who did not choose, and
    the MCP preview line that reports what a spec asked for. A card is chosen
    only when every rule of it is written down.
    """
    if not set(TEMPO_RULES) <= set(constitution):
        return None
    return tempo_name(constitution)


#: When a new project's daily briefing arrives if nobody said. A schedule, not
#: a model call: the briefing is composed from rows at zero tokens.
DEFAULT_BRIEF_AT = "08:00"


def default_briefing(constitution: dict[str, Any]) -> dict[str, Any]:
    """A creation-time constitution with the daily briefing switched on if it
    was silent.

    The field default stays `None` for the reason `DEFAULT_AUTONOMY` gives:
    it is what an omitted rule means for a document already stored, and
    flipping it would start mailing every existing project's chairman one
    morning without anybody choosing it. A new project with no opinion gets the
    letter, because the alternative was that the one notice which arrives when
    nothing is wrong was off unless somebody found the rule that turns it on.
    An explicit `null` is an opinion, and is left alone.
    """
    if "brief_daily_at" in constitution:
        return dict(constitution)
    return {**constitution, "brief_daily_at": DEFAULT_BRIEF_AT}


#: A Code Factory's evidence floors when its founder was silent on them. Its
#: outside proof is a change the platform merged or a release that passed its
#: canary (`policy.attest_shipped`), so the share of open tasks that must
#: "face outward" asks a question its work has no answer to — building is the
#: work, and the merge is the evidence. The drought stays real and is sized to
#: a first merge: a spec to accept, then CI and a skeleton, inside three days.
#:
#: An Amazon PPC Factory takes the same floors for the same reason: its work is
#: reading one account through the platform's own data-source gateway, and its
#: outside proof is the report the platform checked against that data and
#: attested on publish. A third of its tasks "facing outward" asks a question
#: an audit has no answer to, and a 24-hour drought would refuse the lead's
#: next task while the analysts are still pulling sixty days of rows.
FACTORY_FLOORS: dict[str, Any] = {"external_ratio_min": 0.0, "drought_hours": 72}
FLOORED_KINDS = ("code_factory", "ppc_factory")


def default_factory_floors(mode: str, constitution: dict[str, Any]) -> dict[str, Any]:
    """A creation-time constitution with a factory's evidence floors filled in,
    rule by rule, wherever it was silent. Any other kind is returned as given."""
    if str(mode) not in FLOORED_KINDS:
        return dict(constitution)
    return {**{k: v for k, v in FACTORY_FLOORS.items() if k not in constitution},
            **constitution}


def default_tempo(mode: str, constitution: dict[str, Any]) -> dict[str, Any]:
    """A creation-time constitution with the tempo filled in if it was silent.

    Silent means *none* of the tempo rules named — the same test
    `default_autonomy` applies, for the same reason. Naming even one is somebody
    choosing rule by rule, and stamping a bundle over that would overwrite a
    decision they did write with one they did not.

    `cycle_frequency` is read from the constitution being built rather than
    passed alongside it, because it *is* one of these rules: an Operate project
    that says it rolls hourly has already told us how fast it thinks.
    """
    if any(rule in constitution for rule in TEMPO_RULES):
        return dict(constitution)
    frequency = constitution.get("cycle_frequency",
                                 ConstitutionDoc.model_fields["cycle_frequency"].default)
    return {**TEMPO_PRESETS[tempo_for(mode, frequency, has_job_budget(constitution))],
            **constitution}


# What each card SAYS. It lives beside the rules it explains rather than in the
# screen that draws it, for the reason the settings page already learned the
# hard way (see the note in `_rule`): a hand-kept list on the other side of an
# API goes stale, and a card whose blurb no longer matches its numbers is worse
# than no card, because it is believed.
#
# The pace words have been taken out of the bar blurbs. They used to say "big
# queue", "steady pace" and "a report every few hours" while setting none of
# those things — the queue was the only pace rule the card touched, and it was
# doing all the throttling with no label on it.
AUTONOMY_COPY = {
    "autonomous": {
        "label": "Autonomous",
        "blurb": "The CEO accepts its own plan, signs the platform's projected date, and "
                 "judges unverifiable closes itself. Every decision is audited and sent "
                 "to you; all of it is reversible; your buttons never close.",
        "example": "The default. The CEO runs the project; you chair it. Budgets and "
                   "brakes still bind.",
    },
    "supervised": {
        "label": "Supervised",
        "blurb": "You approve scope, sign the date, and judge unverifiable closes. The "
                 "CEO proposes — and executes any order you give it in the boardroom.",
        "example": "Right for a first project, or real money on the line. It waits for "
                   "you, so nothing moves while you are away.",
    },
}

BAR_COPY = {
    "full_send": {
        "label": "High stakes",
        "blurb": "The seats that write and check the work run on the strongest model "
                 "at the deepest reasoning, with the per-run ceiling and timeout raised "
                 "so a long piece is not cut off mid-thought.",
        "example": "Something a stranger judges you by.",
    },
    "balanced": {
        "label": "Normal",
        "blurb": "The work is read by somebody who did not write it before it reaches "
                 "you. The default for a reason.",
        "example": "Most work.",
    },
    "frugal": {
        "label": "Low stakes",
        "blurb": "A wall close behind a monthly budget — still read before it reaches "
                 "you. The read costs a run per deliverable, so expect roughly a "
                 "quarter less throughput for it.",
        "example": "A scratch project whose failure costs an afternoon.",
    },
    "show_me_proof": {
        "label": "Proof required",
        "blurb": "External validation matters: evidence counts only once the platform "
                 "verifies it, and a proof drought blocks busywork.",
        "example": "Anywhere being confidently wrong is expensive.",
    },
}

# The labels name the workload, the keys keep their old names. "Live" sat next
# to "Full send" on the form and a chairman read the pair as one speed dial;
# neither card was. The keys are what the Founder's `tempo` argument, setup
# files and the audit log say, so they stay put — a card is still recognised
# by its rules (`tempo_name`), never by what it is called.
TEMPO_COPY = {
    "sprint": {
        "label": "Sprint",
        "headline": "up to 2,000 runs a day, paced by the budget",
        "blurb": "The run limits are lifted so the job budget is what paces the "
                 "project: work starts the moment it is ready, up to six at once as "
                 "the provider allows, and the CEO plans every two hours. A reserve "
                 "is kept so it can hand in what it has if the money runs out first. "
                 "Money buys parallel work; a chain of steps that depend on each "
                 "other still takes as long as it takes.",
        "example": "A delivery, an investigation or a build with a budget and no "
                   "reason to wait.",
    },
    "live": {
        "label": "Heavy",
        "headline": "about 150 runs a day",
        "blurb": "Something happens during the day and the project should notice. Agents "
                 "wake on each other's messages, the CEO plans every six hours, and work "
                 "starts the moment it is ready.",
        "example": "A desk trading a real account. Anything that ends — racing is "
                   "correct when there is no long tail to pay for.",
    },
    "working_hours": {
        "label": "Steady",
        "headline": "about 40 runs a day",
        "blurb": "Wakes when there is work, quiet at night. Two planning wakes a day plus "
                 "a morning standup, and the inbox lane narrowed so the project's own "
                 "chatter cannot crowd out an agent holding a task.",
        "example": "The numbers a chairman running an early-stage build wrote by hand, "
                   "one rule at a time, because this card did not exist.",
    },
    "few_times_daily": {
        "label": "Light",
        "headline": "about 12 runs a day",
        "blurb": "Wakes on a schedule and otherwise sleeps. Message-only wakes are off "
                 "entirely — agents read their channels on the next wake they were having "
                 "anyway — and work may be scheduled a day and a half ahead, so the CEO "
                 "can lay out tomorrow morning instead of being refused.",
        "example": "Research that runs itself once the scripts are written: wake, look, "
                   "write the report.",
    },
}


def preset_cards() -> dict[str, list[dict[str, Any]]]:
    """Every card this platform offers, with the rules each one writes.

    Served to the screens rather than mirrored by them. The new-project form and
    the settings page draw from this, so a card cannot read one way while it is
    being picked and another way afterwards.
    """
    def cards(presets: dict[str, dict[str, Any]], copy: dict[str, dict[str, str]]):
        # Iterated over the copy, not the rules: this order is the order the
        # cards are drawn in, and which card a chairman reads first is a
        # decision about the product rather than about the dict above.
        return [{"value": name, **words, "constitution": presets[name]}
                for name, words in copy.items()]

    return {
        "autonomy": cards(AUTONOMY_PRESETS, AUTONOMY_COPY),
        "bar": cards(BAR_PRESETS, BAR_COPY),
        "tempo": cards(TEMPO_PRESETS, TEMPO_COPY),
    }


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
# reporting preferences, which are instructions *to* the project about how to
# treat him rather than limits *on* it. Everything else in the constitution is
# the chairman's alone to change.
CADENCE_FIELDS = frozenset({"chairman_report_interval_hours",
                            "chairman_report_window_local",
                            "timezone"})


def unknown_rules(changes: dict[str, Any]) -> list[str]:
    """Rule names this platform does not have, with the near miss named.

    `ConstitutionDoc` ignores extra keys, so an invented rule validates, stores,
    and is then ignored for the life of the project — the chairman reads
    `reporting_cadence: 4` in the amendment log and believes he set something. A
    real Founder run invented exactly that key, which is why this exists; it is
    checked on every write path, so the browser and the MCP surface refuse it in
    the same words.

    Near misses are named because the author can almost always fix it in the
    same breath once told what the field is really called. Prefixes, not whole
    words: the invented key was `reporting_cadence` and the real one is
    `chairman_report_interval_hours` — "reporting" does not appear in it, but
    "repor" does.
    """
    known = set(ConstitutionDoc.model_fields)
    problems = []
    for key in changes:
        if key in known:
            continue
        stem = [part[:5] for part in key.split("_") if len(part) > 3]
        near = sorted(name for name in known if any(part in name for part in stem))[:3]
        problems.append(
            f"{key}: this platform has no such rule"
            + (f" — did you mean {', '.join(near)}?" if near else ""))
    return problems[:10]


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
    invented = unknown_rules(changes)
    if invented:
        # Before the overlay, because the overlay would quietly accept these:
        # the document ignores extra keys, so an invented rule is stored in the
        # amendment row, shown back to the chairman, and never in force.
        raise ValueError("; ".join(invented))
    after = effective(session, project_id).overlay(changes)  # raises if the result is invalid
    project = session.get(Project, project_id)
    if project is not None and (problem := job_budget_problem(project.billing_mode, after)):
        raise ValueError(problem)
    if project is not None and after.spread_job_budget and project.deadline is None:
        raise ValueError(SPREAD_NEEDS_A_DATE)
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
