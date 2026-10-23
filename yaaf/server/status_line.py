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
VERDICTS = ("done", "stopped", "not_started", "blocked", "paused", "late", "at_risk",
            "needs_you", "stalled", "on_track", "idle")


def _date(at: datetime | None) -> str:
    return f"{at.day} {at:%b}" if at else "an unknown date"


def _money(unit: str, spent: float, cap: float) -> str:
    if unit == "usd":
        return f"${spent:,.0f} of ${cap:,.0f} this month."
    return f"{spent:,.0f} of {cap:,.0f} tokens this month."


def status_line(*, status: str, status_reason: str | None, unpaid: bool,
                commitment, waiting: int, open_tasks: int, runs_running: int,
                last_run_at: datetime | None, stall_hours: float,
                unit: str, spent: float, cap: float) -> dict:
    """{verdict, headline, sentence} for one project, from the facts the desk
    holds. The headline is the verdict in words; the sentence adds the money,
    for a surface that has no money column of its own."""
    verdict, headline, with_money = _verdict(
        status=status, status_reason=status_reason, unpaid=unpaid, commitment=commitment,
        waiting=waiting, open_tasks=open_tasks, runs_running=runs_running,
        last_run_at=last_run_at, stall_hours=stall_hours)
    sentence = f"{headline} {_money(unit, spent, cap)}" if with_money else headline
    return {"verdict": verdict, "headline": headline, "sentence": sentence}


def _verdict(*, status, status_reason, unpaid, commitment, waiting, open_tasks,
             runs_running, last_run_at, stall_hours) -> tuple[str, str, bool]:
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

    due = commitment.deliver_by if commitment is not None else None
    if commitment is not None and commitment.status == CommitmentStatus.BREACHED:
        return "late", f"Late: promised for {_date(due)}, still open.", True
    if commitment is not None and commitment.status == CommitmentStatus.AT_RISK:
        return ("at_risk", f"At risk: projected {_date(commitment.projected_done_at)}, "
                f"promised {_date(due)}.", True)
    if waiting:
        plural = "decision" if waiting == 1 else "decisions"
        return "needs_you", f"{waiting} {plural} waiting on you.", True
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
