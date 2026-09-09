"""Measurement windows: finding out what something costs, instead of arguing.

Every budget conversation on this platform was two guesses meeting. The CEO had
no cost model, no lever it could pull on itself, and — the part nobody had
noticed — no permission to say "I don't know": every sentence the platform owns
about money is an instruction to CUT. So it produced numbers. Three of them, in
two hours, each a mean over a window that did not fit the question.

A window replaces the argument with an experiment. It carries a question, an
allowance and an end date, and at the end the PLATFORM writes what it cost —
the daily series, the median day, the price of each kind of run. No agent verb
reaches those fields. That is the commitment doctrine applied to money: the CEO
may ask the question, and only the arithmetic answers it.

Two mechanisms do the enforcing, and neither is a prompt:

* **The findings are a state change.** `close_due` runs in the janitor, at zero
  tokens, and stamps the measured numbers whether or not anybody is watching.
* **The checkpoint is an obligation.** It opens a flag, and a flag cannot be
  closed by staying quiet — only by being raised in the boardroom with the
  numbers and a proposal. The chairman asked to "adjust along the way"; this is
  what makes the adjustment happen on a schedule rather than when it occurs to
  somebody.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    FlagKind,
    LedgerEntry,
    LedgerKind,
    NoticeKind,
    Project,
    SpendWindow,
    SpendWindowStatus,
    utcnow,
)

log = logging.getLogger("yaaf.spendwindow")


def open_window(session: Session, project_id: str) -> SpendWindow | None:
    """The one live window, or None. At most one runs at a time: two
    overlapping answers to "what does this cost" is the state this replaces."""
    return session.execute(
        select(SpendWindow).where(
            SpendWindow.project_id == project_id,
            SpendWindow.status == SpendWindowStatus.OPEN,
        )
    ).scalars().first()


def spent_in_window(session: Session, window: SpendWindow) -> float:
    """What this window has used, in its own unit, straight from the ledger."""
    usd_mode = window.unit == "usd"
    amount = (func.sum(LedgerEntry.usd) if usd_mode else
              func.sum(LedgerEntry.tokens_in + LedgerEntry.tokens_out
                       + LedgerEntry.cache_creation_tokens
                       + LedgerEntry.cache_read_tokens))
    until = window.closed_at or utcnow()
    total = session.execute(
        select(amount).where(
            LedgerEntry.project_id == window.project_id,
            LedgerEntry.at >= window.started_at,
            LedgerEntry.at <= until,
            LedgerEntry.kind != LedgerKind.REVENUE,
        )
    ).scalar_one_or_none()
    return float(total or 0.0)


def progress(session: Session, window: SpendWindow) -> dict:
    """Where the window stands right now — all of it arithmetic, none of it
    anybody's opinion."""
    from yaaf.server.janitor import burn_rate

    now = utcnow()
    spent = spent_in_window(session, window)
    elapsed = max(0.0, (now - window.started_at).total_seconds() / 86400.0)
    remaining_days = max(0.0, (window.ends_at - now).total_seconds() / 86400.0)
    rate = burn_rate(session, window.project_id, window.allowance, spent)
    median = rate["unit_per_day_median"]
    projected_total = spent + median * remaining_days if median else None
    return {
        "window_id": window.id,
        "question": window.question,
        "unit": window.unit,
        "allowance": window.allowance,
        "spent": round(spent, 2),
        "fraction_used": round(spent / window.allowance, 3) if window.allowance else None,
        "days_elapsed": round(elapsed, 1),
        "days_remaining": round(remaining_days, 1),
        "ends_at": window.ends_at.isoformat(),
        "measured": rate,
        "projected_total": round(projected_total, 2) if projected_total else None,
        # Only meaningful once the rate is meaningful. A window whose whole
        # purpose is that the pace is unknown must not report a confident
        # overrun on day two.
        "on_course_to_overspend": bool(
            rate["projectable"] and projected_total and projected_total > window.allowance
        ),
    }


def _findings(session: Session, window: SpendWindow) -> dict:
    """What it actually cost. Written by the platform, never from input."""
    from yaaf.server.janitor import burn_rate, run_cost_profile

    spent = spent_in_window(session, window)
    days = max(1.0, (window.ends_at - window.started_at).total_seconds() / 86400.0)
    rate = burn_rate(session, window.project_id, window.allowance, spent)
    return {
        "question": window.question,
        "unit": window.unit,
        "allowance": window.allowance,
        "spent": round(spent, 2),
        "fraction_used": round(spent / window.allowance, 3) if window.allowance else None,
        "days": round(days, 1),
        "measured_median_per_day": rate["unit_per_day_median"],
        "measured_mean_per_day": rate["unit_per_day_mean"],
        "busiest_day": rate["busiest_day"],
        "days_are_alike": rate["days_are_alike"],
        "series": rate["series"],
        "cost_per_run": run_cost_profile(session, window.project_id,
                                         days=int(days) + 1),
        "measured_at": utcnow().isoformat(),
        "note": (
            "Measured from the ledger by the platform. No agent wrote any number "
            "here. This is the input to the next budget conversation — refine it "
            "together rather than replacing it with a fresh estimate."
        ),
    }


def close(session: Session, window: SpendWindow, reason: str,
          status: str = SpendWindowStatus.CLOSED) -> None:
    """Stamp the findings and shut it. Idempotent by status."""
    if window.status != SpendWindowStatus.OPEN:
        return
    from yaaf.server import flags as flag_lib

    window.findings = _findings(session, window)
    window.status = status
    window.closed_at = utcnow()
    window.close_reason = reason
    # The flag is about THIS window overspending, so it dies with the window.
    # Nothing resolved it before: `evaluate` never sees this kind, so a raised
    # spend_window sat on the chairman's desk for good — including after he had
    # answered it by raising the ceiling, which is what the flag asked for.
    flag_lib.resolve_subject(session, window.project_id, FlagKind.SPEND_WINDOW,
                             window.id, f"the window closed: {reason}")
    log.info("spend window %s closed for %s: %s",
             window.id, window.project_id, reason)


def sweep(session: Session, project: Project) -> None:
    """One tick of the window: close it if it is due, and make the CEO speak if
    a checkpoint has come round or the allowance is going to run out early.

    Zero tokens, like every janitor sweep — so the findings land on a project
    that has paused, gone broke, or simply stopped being interesting.
    """
    from yaaf.server import flags as flag_lib
    from yaaf.server.janitor import _notice_ceo

    window = open_window(session, project.id)
    if window is None:
        return
    now = utcnow()

    if now >= window.ends_at:
        close(session, window, "ran to its end date")
        found = window.findings or {}
        # janitor's `_notice_ceo` takes the Project; admin's namesake takes an
        # id. Same name, different modules, and the wrong one fails loudly.
        _notice_ceo(
            session, project, NoticeKind.BUDGET_WARNING,
            f"The measurement window closed. Question: {window.question}\n\n"
            f"MEASURED, by the platform, from the ledger: "
            f"{found.get('spent'):,.0f} of {window.allowance:,.0f} "
            f"{window.unit} over {found.get('days')} days — a median day of "
            f"{found.get('measured_median_per_day'):,.0f}"
            + ("" if found.get("days_are_alike") else
               ", though the days were NOT alike, so treat the median as the "
               "ordinary day and name the exceptional ones separately")
            + ".\n\nTake this to the chairman as the answer to the question he "
            "funded, with what you would now set the budget to and why. Do not "
            "replace these numbers with a fresh estimate — he paid for the "
            "measurement precisely because the estimates were wrong.",
        )
        return

    reason = None
    detail: dict = progress(session, window)
    if detail["on_course_to_overspend"]:
        reason = (
            f"This window is on course to spend about "
            f"{detail['projected_total']:,.0f} against an allowance of "
            f"{window.allowance:,.0f} {window.unit} before it ends."
        )
    else:
        due = (window.last_checkpoint_at or window.started_at) + timedelta(
            hours=window.checkpoint_hours)
        if now >= due:
            reason = (
                f"Checkpoint: {detail['spent']:,.0f} of {window.allowance:,.0f} "
                f"{window.unit} used, {detail['days_remaining']} days left."
            )
            window.last_checkpoint_at = now
    if reason is None:
        return
    flag_lib.open_flag(
        session, project, "spend_window",
        summary=(
            f"{reason} The chairman funded this to find out: {window.question} "
            "Report the measured numbers so far — the series, not an average — "
            "and say what you would change. If you still do not know, say that "
            "and say what would settle it."
        ),
        arithmetic=detail,
        subject_id=window.id,
    )
