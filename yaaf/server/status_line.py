"""One sentence per project: is it OK, and if not, why — the same everywhere.

A project's health was spread across four Overview tiles, a delivery banner,
the setup checklist, the Watching list and the flags, and every surface that
summarised a project composed its own summary: Home said "Running" over a
project that could not run, the portfolio counted one thing and its header
another, and Claude's `desk` had no headline at all. So the verdict is
decided once, here, from facts the desk rollup already holds, and every
surface reads it: Home, `yaaf projects`, and the MCP `desk`.

The order is the order of what a reader must hear first: a project that is
finished, stopped or cannot run says that before anything about its date;
a broken promise before a decision waiting; a decision before "on track".
"""

from __future__ import annotations

from datetime import datetime, timedelta

from yaaf.core.models import CommitmentStatus, ProjectStatus, utcnow

#: The verdicts, in the words a filter or a colour can key on.
VERDICTS = ("done", "stopped", "not_started", "blocked", "paused", "failing", "late",
            "at_risk", "needs_you", "spinning", "stalled", "on_track", "idle")

#: How many runs in a row must fail before the project's line says so. One
#: failure is retried and usually gone; three is a project that is spending
#: and producing nothing — the same count the credential pause uses.
FAILING_AFTER = 3

#: "Busy, nothing to show": this many paid runs in a week with nothing
#: finished, met, published or proven in it. High on purpose — a project that
#: plans for a day, or reads before it writes, must never read as wasting
#: money; one that has spent twenty runs and produced nothing at all is.
SPINNING_RUNS = 20
SPINNING_DAYS = 7


def _date(at: datetime | None) -> str:
    return f"{at.day} {at:%b}" if at else "an unknown date"


_FAILURE = {
    "auth": " — the provider refused the key",
    "transient": " — the provider was unavailable",
    "fatal": " — the run crashed",
}


def _money(unit: str, spent: float, cap: float) -> str:
    if unit == "usd":
        return f"${spent:,.0f} of ${cap:,.0f} this month."
    return f"{spent:,.0f} of {cap:,.0f} tokens this month."


def status_line(*, status: str, status_reason: str | None, unpaid: bool,
                commitment, waiting: int, open_tasks: int, runs_running: int,
                last_run_at: datetime | None, stall_hours: float,
                unit: str, spent: float, cap: float,
                failed_in_a_row: int = 0, last_error_kind: str | None = None,
                spinning_runs: int = 0) -> dict:
    """{verdict, headline, sentence} for one project, from the facts the desk
    holds. The headline is the verdict in words; the sentence adds the money,
    for a surface that has no money column of its own."""
    verdict, headline, with_money = _verdict(
        status=status, status_reason=status_reason, unpaid=unpaid, commitment=commitment,
        waiting=waiting, open_tasks=open_tasks, runs_running=runs_running,
        last_run_at=last_run_at, stall_hours=stall_hours,
        failed_in_a_row=failed_in_a_row, last_error_kind=last_error_kind,
        spinning_runs=spinning_runs)
    sentence = f"{headline} {_money(unit, spent, cap)}" if with_money else headline
    return {"verdict": verdict, "headline": headline, "sentence": sentence}


def _verdict(*, status, status_reason, unpaid, commitment, waiting, open_tasks,
             runs_running, last_run_at, stall_hours, failed_in_a_row=0,
             last_error_kind=None, spinning_runs=0) -> tuple[str, str, bool]:
    if status == ProjectStatus.COMPLETED:
        return "done", "Finished.", True
    if status == ProjectStatus.STOPPED:
        why = f": {status_reason}" if status_reason else ""
        return "stopped", f"Stopped{why}.", False
    if status == ProjectStatus.DRAFT:
        return ("not_started", "Not started — nothing is set to pay for it yet." if unpaid
                else "Not started.", False)
    if unpaid:
        return ("blocked", "Cannot run: nothing is set to pay for it. Add a key in its "
                "Settings → Money.", False)
    if status == ProjectStatus.PAUSED:
        why = f": {status_reason}" if status_reason else "."
        return "paused", f"Paused{why}", True
    if failed_in_a_row >= FAILING_AFTER:
        # Before the date and the decisions: a project whose runs all die
        # will miss any date, and "On track" or "Working." over it is the
        # line that sent the chairman away from a crashed first run.
        # The harness's classification, never the run's own summary: this
        # line reaches Claude through `desk`, and a summary is agent text.
        why = _FAILURE.get(last_error_kind or "", "")
        return ("failing", f"Failing: its last {failed_in_a_row} runs failed{why}. "
                "Activity → Runs says why.", True)

    due = commitment.deliver_by if commitment is not None else None
    if commitment is not None and commitment.status == CommitmentStatus.BREACHED:
        return "late", f"Late: promised for {_date(due)}, still open.", True
    if commitment is not None and commitment.status == CommitmentStatus.AT_RISK:
        return ("at_risk", f"At risk: projected {_date(commitment.projected_done_at)}, "
                f"promised {_date(due)}.", True)
    if waiting:
        plural = "decision" if waiting == 1 else "decisions"
        return "needs_you", f"{waiting} {plural} waiting on you.", True
    if spinning_runs >= SPINNING_RUNS:
        # Activity is not achievement: the one question a busy project's
        # "Working." could not answer was whether any of it bought anything.
        return ("spinning", f"Busy, nothing to show: {spinning_runs} paid runs in "
                f"{SPINNING_DAYS} days, and nothing finished, met, published or proven.",
                True)
    quiet_for = (utcnow() - last_run_at) if last_run_at else None
    if (open_tasks and not runs_running and stall_hours > 0 and quiet_for is not None
            and quiet_for > timedelta(hours=stall_hours)):
        hours = int(quiet_for.total_seconds() // 3600)
        return "stalled", f"Quiet for {hours}h with {open_tasks} task(s) open.", True
    if commitment is not None and due is not None:
        return "on_track", f"On track for {_date(due)}.", True
    if open_tasks or runs_running:
        return "on_track", "Working.", True
    return "idle", "Nothing open right now.", True


#: Verdicts that are news. The rest are a count: "4 others are fine" is the
#: whole of what a reader needs to hear about a project that is fine.
_QUIET = ("on_track", "idle", "done", "not_started")


def portfolio_summary(rows: list[dict], also: list[dict] | None = None) -> str:
    """Every project in one paragraph, from desk rows: how many, each one whose
    verdict is news with its headline, how many others are fine, and what else
    waits (`also`, as counts). Platform-written facts only — the project's own
    name and its headline, never an approval title or a CEO's words — because
    Claude reads it unwrapped through `desk`; `yaaf status` prints the same."""
    if not rows:
        return "No projects."
    order = {v: i for i, v in enumerate(VERDICTS)}
    news = sorted((r for r in rows if (r.get("status_line") or {}).get("verdict")
                   not in _QUIET),
                  key=lambda r: order.get(r["status_line"]["verdict"], len(order)))
    parts = [f"{len(rows)} project{'s' if len(rows) != 1 else ''}."]
    if news:
        parts.append(" ".join(f"{r['name']}: {r['status_line']['headline']}" for r in news))
    rest = len(rows) - len(news)
    if rest and news:
        parts.append(f"{rest} other{'s are' if rest != 1 else ' is'} fine.")
    elif rest:
        parts.append("All fine.")
    if also:
        counts: dict[str, int] = {}
        for item in also:
            counts[item["kind"]] = counts.get(item["kind"], 0) + 1
        words = {"flag": "flag", "heads_up": "heads-up", "review": "open review"}
        listed = ", ".join(f"{n} {words.get(k, k)}{'s' if n != 1 else ''}"
                           for k, n in counts.items())
        parts.append(f"Also yours: {listed}.")
    return " ".join(parts)
