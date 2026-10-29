"""The budget as a plan, and the conversation when the plan is wrong.

A budget and a wall are different objects, and this platform used one number
for both. Because crossing the monthly cap paused the project — runs revoked,
queue purged — the cap was not a plan anybody worked to, it was a cliff
everybody stood back from. Three separate CEOs, on three separate projects,
refused money their chairman offered them. That looks like timidity and is
actually arithmetic: when going over is fatal, the winning move is always to
promise less and spend less than you might need.

The damage was not the underspend. It was that a safe number and an honest
number are indistinguishable from outside, so no figure the desk produced
could be trusted, and the chairman said so: "the confidence in the entire
harness of yaaf is lost."

So there are two numbers now. The **budget** is the educated guess, sized by
how much the project matters; crossing it obliges a report and stops nothing.
The **hard stop** — budget × `hard_stop_multiple` — is the runaway protection,
and it is the only thing that ever pauses a project.

And there is a third move that did not exist before: *spend it and account for
it*. The platform writes the facts from the ledger, the CEO writes why, the
chairman decides whether the number was wrong or the work was. Nobody
negotiates the numbers, because nobody wrote them.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    BudgetOverrun,
    LedgerEntry,
    LedgerKind,
    OverrunOutcome,
    Project,
    Requirement,
    Run,
    utcnow,
)

log = logging.getLogger("yaaf.budget")

#: How much room over the plan each stakes level treats as ordinary before the
#: warning changes tone. A high-stakes project that goes 20% over has not done
#: anything alarming; a low-stakes one has.
STAKES_HEADROOM = {"low": 0.0, "normal": 0.25, "high": 0.75}


def month_start(now=None):
    now = now or utcnow()
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def budget_of(project: Project, cons: ConstitutionDoc) -> tuple[float, str]:
    """The plan, in the unit this project actually bills in."""
    if project.billing_mode == "api_key":
        return float(cons.monthly_usd_cap), "USD"
    return float(cons.monthly_token_cap), "tokens"


def hard_stop_of(project: Project, cons: ConstitutionDoc) -> float:
    """The wall. Never below the budget — a multiple under 1 would put the wall
    inside the plan, which is the state this module exists to end."""
    budget, _ = budget_of(project, cons)
    return budget * max(1.0, cons.hard_stop_multiple)


class Counted(NamedTuple):
    """One model call as its project's meter counts it."""
    #: What the credential billed in dollars — None when no dollars moved.
    bill: float | None
    #: The dollars its ledger row carries, which the dollar meter sums.
    ledger_usd: float | None
    #: Said on the ledger row when the count is not the bill.
    note: str
    paid_in_usd: bool


def counted(session: Session, project: Project, *, provider: str | None, model: str | None,
            billed_usd: float | None, tokens_in: int = 0, tokens_out: int = 0,
            cache_creation: int = 0, cache_read: int = 0) -> Counted:
    """One model call as this project's meter counts it.

    The credential that paid the call decides whether dollars moved — not the
    project's billing mode, because a project may pay two providers two ways
    (a Claude subscription for the team, a DeepSeek key for the CEO). An API
    key's call books its real bill. A subscription's call moves no dollars: on
    a project whose budget is tokens, its tokens are the count; on one whose
    budget is dollars, it is counted at its catalog price — the run's own
    price, never an exchange rate — or the dollar meter would not see it at all.
    Every run already carries its tokens, so the token meter needs nothing."""
    from yaaf.core import prices, project_credentials
    from yaaf.runtimes import providers as runtime_providers

    payer = project_credentials.seat_credential(session, project, provider) \
        if provider else None
    if payer is not None:
        paid_in_usd = runtime_providers.provider(payer[0]).kind(payer[1]).meter == "usd"
    else:
        # No credential named (an older row, a mock): the billing mode, as before.
        paid_in_usd = project.billing_mode == "api_key"
    if paid_in_usd:
        return Counted(billed_usd, billed_usd, "", True)
    if project.billing_mode != "api_key":
        return Counted(None, None, " (subscription: tokens only)", False)
    price = prices.price(session, model) if model else None
    if price is not None:
        at_catalog = (tokens_in * price.input + tokens_out * price.output
                      + cache_read * price.cache_read
                      + cache_creation * price.cache_write_5m) / 1_000_000
        return Counted(None, at_catalog, " (subscription, counted at its catalog price)", False)
    # No price on the install: the harness's own list-price figure, which the
    # CLI harnesses report even on a subscription.
    return Counted(None, billed_usd,
                   " (subscription, counted at the harness's reported price)", False)


def spend_breakdown(session: Session, project_id: str, since) -> dict:
    """Where the money went, from the ledger and the run table.

    Written by the platform and never from input: an overrun where the CEO
    supplies its own accounting of where the money went is an overrun where
    the interesting number is a claim.
    """
    total_tokens = (Run.tokens_in + Run.tokens_out
                    + Run.cache_creation_tokens + Run.cache_read_tokens)
    by_kind = [
        {"kind": kind, "runs": int(n), "tokens": int(tok or 0),
         "usd": round(float(usd or 0), 2)}
        for kind, n, tok, usd in session.execute(
            select(Run.kind, func.count(Run.id), func.sum(total_tokens),
                   func.sum(Run.cost_usd))
            .where(Run.project_id == project_id, Run.created_at >= since)
            .group_by(Run.kind).order_by(func.sum(total_tokens).desc())
        ).all()
    ]
    by_requirement = [
        {"requirement": key or "(no requirement)", "runs": int(n),
         "tokens": int(tok or 0)}
        for key, n, tok in session.execute(
            select(Requirement.key, func.count(Run.id), func.sum(total_tokens))
            .select_from(Run)
            .join(Requirement, Requirement.id == Run.requirement_id, isouter=True)
            .where(Run.project_id == project_id, Run.created_at >= since)
            .group_by(Requirement.key).order_by(func.sum(total_tokens).desc())
        ).all()
    ]
    return {
        "since": since.isoformat(),
        "by_run_kind": by_kind,
        "by_requirement": by_requirement[:12],
        "note": ("Computed from the ledger and the run table by the platform. "
                 "These are the facts of the month; your account explains them, "
                 "it does not restate them."),
    }


def open_overrun(session: Session, project_id: str) -> BudgetOverrun | None:
    """This month's overrun, if the month has one."""
    return session.execute(
        select(BudgetOverrun).where(
            BudgetOverrun.project_id == project_id,
            BudgetOverrun.period == month_start(),
        )
    ).scalars().first()


def unaccounted(session: Session, project_id: str) -> BudgetOverrun | None:
    """The oldest overrun the CEO still owes an account for."""
    return session.execute(
        select(BudgetOverrun).where(
            BudgetOverrun.project_id == project_id,
            BudgetOverrun.ceo_account.is_(None),
        ).order_by(BudgetOverrun.period)
    ).scalars().first()


def record_overrun(session: Session, project: Project, cons: ConstitutionDoc,
                   spent: float) -> BudgetOverrun | None:
    """Open this month's overrun once, with the facts attached.

    Idempotent on (project, month): a budget crossed on the 3rd and still
    crossed on the 4th is one overrun, not thirty. The running total keeps
    updating, because the interesting number is where the month ended up.
    """
    budget, unit = budget_of(project, cons)
    existing = open_overrun(session, project.id)
    if existing is not None:
        existing.final_spent = spent
        existing.breakdown = spend_breakdown(session, project.id, existing.period)
        return None
    overrun = BudgetOverrun(
        id=ids.new_id(ids.OVERRUN),
        project_id=project.id,
        period=month_start(),
        unit=unit,
        budget=budget,
        spent_at_crossing=spent,
        final_spent=spent,
        breakdown=spend_breakdown(session, project.id, month_start()),
        outcome=OverrunOutcome.PENDING,
    )
    session.add(overrun)
    log.info("budget overrun opened for %s: %.0f of %.0f %s",
             project.slug, spent, budget, unit)
    return overrun


def month_to_date(session: Session, project_id: str) -> tuple[float, int]:
    """Spend this month, both units. Revenue is money coming IN — summing it
    here once auto-paused a project for earning."""
    usd, tokens = session.execute(
        select(
            func.coalesce(func.sum(LedgerEntry.usd), 0.0),
            func.coalesce(
                func.sum(LedgerEntry.tokens_in + LedgerEntry.tokens_out
                         + LedgerEntry.cache_creation_tokens
                         + LedgerEntry.cache_read_tokens),
                0,
            ),
        ).where(LedgerEntry.project_id == project_id,
                LedgerEntry.at >= month_start(),
                LedgerEntry.kind != LedgerKind.REVENUE)
    ).one()
    return float(usd), int(tokens)


def proposed_budget(session: Session, project: Project,
                    cons: ConstitutionDoc) -> dict | None:
    """What the last three months say the budget should be.

    "Over time we learn what the actual cost is and we adjust the budgets
    accordingly" — the learning half, and the reason none of this needs a
    special measurement mode. A guess that corrects itself from history is just
    budgeting.

    Sized by stakes, because the point of a budget is not to predict the mean:
    a high-stakes project wants room to be wrong in the expensive direction,
    and a low-stakes one does not.
    """
    # Bucketed in Python rather than in SQL: the two dialects spell month
    # truncation differently, and a per-project ledger is small enough that
    # portability is worth more here than a GROUP BY.
    rows = session.execute(
        select(LedgerEntry.at, LedgerEntry.usd,
               LedgerEntry.tokens_in + LedgerEntry.tokens_out
               + LedgerEntry.cache_creation_tokens + LedgerEntry.cache_read_tokens)
        .where(LedgerEntry.project_id == project.id,
               LedgerEntry.kind != LedgerKind.REVENUE)
    ).all()
    if not rows:
        return None
    usd_mode = project.billing_mode == "api_key"
    buckets: dict[str, float] = {}
    for at, usd, tokens in rows:
        key = at.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0.0) + (float(usd or 0) if usd_mode
                                                else float(tokens or 0))
    # The running month is not evidence about a full one, and including it
    # would drag every suggestion down for twenty-nine days out of thirty.
    this_month = utcnow().strftime("%Y-%m")
    settled = sorted((m, v) for m, v in buckets.items() if m != this_month)
    if not settled:
        return None
    recent = settled[-3:]
    # The worst settled month, not the average of them: a budget exists to
    # cover the month you would not want to be caught short in, and averaging
    # is how you get caught short half the time.
    typical = max(v for _, v in recent)
    headroom = 1.0 + STAKES_HEADROOM.get(cons.stakes, 0.25)
    budget, unit = budget_of(project, cons)
    suggestion = round(typical * headroom, 2)
    return {
        "months_measured": len(recent),
        "worst_settled_month": round(typical, 2),
        "stakes": cons.stakes,
        "headroom_applied": headroom,
        "current_budget": budget,
        "suggested_budget": suggestion,
        "unit": unit,
        "note": (
            f"The worst of the last {len(recent)} settled month(s) came to "
            f"{typical:,.0f} {unit}. At {cons.stakes} stakes that suggests a budget "
            f"around {suggestion:,.0f}, against the {budget:,.0f} set now. A budget "
            "is sized to the month you would not want to be caught short in, not to "
            "the average one."
        ),
    }
