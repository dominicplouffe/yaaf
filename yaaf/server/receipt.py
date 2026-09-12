"""The receipt: what a project cost, and what the money bought.

Every screen in the product answers "is it working right now". None of them
answers the question a stranger asks first, which is "what happens if I run
this for a month" — and the honest answer to that is not a price. A cheaper
run buys a document nobody checked. So the receipt puts the two halves side by
side and lets the reader do the division themselves.

The split that matters is not cost-vs-outcome, it is **who wrote the number**:

- `cost` is metered by the platform from run rows, including the runs that died.
- `verified` is the half no agent can move. A requirement reaches MET only when
  the platform ran its acceptance test; external evidence counts only once the
  platform fetched, hashed and snapshotted it; an audit sample is a close the
  janitor drew at random and a human judged.
- `chairman` is what the human actually spent, in decisions rather than dollars.

`self_reported` is therefore false for the middle block by construction, and
saying so in the payload is the point: a receipt whose good numbers are the
agent's own is an invoice, not evidence.

Read-only, and derived entirely from the same producers the Review tab freezes
(`janitor.review_numbers`) — over the project's whole life rather than since
the last meeting. It introduces no state and no verb.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Agent,
    AuditSample,
    AuditSampleStatus,
    Commitment,
    Decision,
    Evidence,
    EvidenceKind,
    EvidenceProvenance,
    LedgerEntry,
    LedgerKind,
    Project,
    Requirement,
    RequirementStatus,
    Run,
    RunStatus,
    Team,
    VerificationStatus,
    utcnow,
)
from yaaf.server import policy

#: A run that stopped without finishing. Money left the building and nothing
#: came back, which is why it belongs in the cost column rather than a footnote.
#: Mirrors WASTE_STATUSES in yaaf/cli/spend.py — the same four, counted for the
#: same reason; kept separate so the server never imports the CLI.
ENDED_EARLY = (RunStatus.FAILED, RunStatus.REAPED, RunStatus.KILLED,
               RunStatus.TERMINATED_REFUSALS)

#: Dollars are trustworthy; input tokens are known-short for runs recorded
#: before the metering fix, because cache tokens were dropped. Same caveat the
#: spend report carries, surfaced here so a pasted receipt carries it too.
TOKENS_CAVEAT = ("input tokens exclude cache tokens for runs recorded before the "
                 "metering fix; dollar figures are unaffected")


def _cost(session: Session, project_id: str) -> dict:
    """Dollars from the ledger, attribution from the runs.

    The ledger is where spend is counted — the budget meter reads it, the
    monthly wall reads it, and `/observe/.../spend` was moved onto it for
    exactly this reason: summing the run table gives a different number, and
    two dollar figures on one page that cannot both be true is worse than
    having none. Revenue is excluded here for the same reason it is excluded
    there: it is money coming in.

    Runs still supply "how many, and how many died", because that is what a
    run row knows and a ledger row does not.
    """
    entries = session.execute(
        select(LedgerEntry.usd, LedgerEntry.tokens_in, LedgerEntry.tokens_out,
               LedgerEntry.cache_creation_tokens, LedgerEntry.cache_read_tokens,
               LedgerEntry.run_id, LedgerEntry.kind)
        .where(LedgerEntry.project_id == project_id,
               LedgerEntry.kind != LedgerKind.REVENUE)
    ).all()
    runs = session.execute(
        select(Run.id, Run.status).where(Run.project_id == project_id)
    ).all()

    early_ids = {r.id for r in runs if r.status in ENDED_EARLY}
    # A run that no longer exists still spent its money, so unattributed rows
    # stay in the total and simply cannot be blamed on a dead run.
    usd_early = sum(e.usd or 0.0 for e in entries if e.run_id in early_ids)

    return {
        "usd": round(sum(e.usd or 0.0 for e in entries), 2),
        "usd_on_runs_that_died": round(usd_early, 2),
        "external_usd": round(sum(e.usd or 0.0 for e in entries
                                  if e.kind == LedgerKind.EXTERNAL_SPEND), 2),
        "runs": len(runs),
        "runs_ended_early": len(early_ids),
        "tokens_in": sum((e.tokens_in or 0) + (e.cache_creation_tokens or 0)
                         + (e.cache_read_tokens or 0) for e in entries),
        "tokens_out": sum(e.tokens_out or 0 for e in entries),
    }


def _verified(session: Session, project: Project) -> dict:
    """The half an agent cannot move."""
    scope = policy.scope_requirements(session, project.id)
    evidence = session.execute(
        select(Evidence.kind, Evidence.provenance, Evidence.verification)
        .where(Evidence.project_id == project.id)
    ).all()
    samples = session.execute(
        select(AuditSample.status).where(AuditSample.project_id == project.id)
    ).all()

    external = [e for e in evidence if e.kind == EvidenceKind.EXTERNAL]
    commitment = policy.live_commitment(session, project.id)
    promised = session.execute(
        select(func.count(Commitment.id)).where(Commitment.project_id == project.id)
    ).scalar_one()

    return {
        "requirements": {
            "met": len([r for r in scope if r.status == RequirementStatus.MET]),
            "cut": len([r for r in scope if r.status == RequirementStatus.CUT]),
            "open": len([r for r in scope
                         if r.status in policy.OPEN_REQUIREMENT_STATUSES]),
            # Every MET here was written by the platform after it ran the
            # acceptance test. There is no agent verb that writes it.
            "written_by": "platform",
        },
        "evidence": {
            "external": len(external),
            "verified": len([e for e in external
                             if e.verification == VerificationStatus.VERIFIED]),
            "failed": len([e for e in external
                           if e.verification == VerificationStatus.FAILED]),
            # Arrived through a connector, so no agent could have forged it.
            "platform_attested": len([e for e in evidence
                                      if e.provenance == EvidenceProvenance.PLATFORM_ATTESTED]),
        },
        "audit": {
            "sampled": len([s for s in samples
                            if s.status != AuditSampleStatus.SKIPPED]),
            "failed": len([s for s in samples
                           if s.status == AuditSampleStatus.FAILED]),
            "expired_unreviewed": len([s for s in samples
                                       if s.status == AuditSampleStatus.EXPIRED]),
        },
        "commitment": None if commitment is None else {
            "status": str(commitment.status),
            "deliver_by": commitment.deliver_by.isoformat(),
        },
        # Each reset closes one and opens the next, so the count is how many
        # dates this project has stood behind — one is the happy path.
        "dates_promised": int(promised),
    }


def _chairman(session: Session, project_id: str) -> dict:
    decisions = session.execute(
        select(func.count(Decision.id))
        .where(Decision.project_id == project_id, Decision.by_user_id.is_not(None))
    ).scalar_one()
    return {
        "decisions": int(decisions),
        "note": ("What the project cost you in attention rather than dollars. "
                 "Every one is on the record with the reason you gave."),
    }


def receipt(session: Session, project: Project) -> dict:
    """Everything a stranger needs to judge one finished project."""
    teams = session.execute(
        select(func.count(Team.id)).where(Team.project_id == project.id)
    ).scalar_one()
    # Seats are agent rows, which is what a team's membership actually is —
    # Team carries the charter and the lead, never a roster.
    seats = session.execute(
        select(func.count(Agent.id)).where(Agent.project_id == project.id)
    ).scalar_one()
    requirements = session.execute(
        select(func.count(Requirement.id)).where(Requirement.project_id == project.id)
    ).scalar_one()

    days = max((utcnow() - project.created_at).days, 0)
    caveats = [TOKENS_CAVEAT]
    if project.billing_mode != "api_key":
        caveats.append("billed against a subscription, so no dollar figure is meaningful")
    if not requirements:
        caveats.append("no requirements were ever accepted, so nothing here was verified")

    return {
        "project": {
            "slug": project.slug,
            "name": project.name,
            "mode": str(project.mode),
            # render() needs it, and a receipt that printed dollars for a
            # subscription project would be inventing a number.
            "billing_mode": project.billing_mode,
            "topology": str(project.topology),
            "status": str(project.status),
            "started": project.created_at.isoformat(),
            "days": days,
            "teams": int(teams),
            "seats": int(seats),
        },
        "cost": _cost(session, project.id),
        "verified": _verified(session, project),
        "chairman": _chairman(session, project.id),
        "self_reported": False,
        "caveats": caveats,
    }


# ── rendering ───────────────────────────────────────────────────────────────────
# The text lives here rather than in the CLI so that every surface prints the
# same artifact. A receipt pasted from the terminal and one pulled through MCP
# that disagreed about anything would be worth less than either alone.

_GUTTER = 36


def _pair(left: list[str], right: list[str]) -> list[str]:
    out = []
    for i in range(max(len(left), len(right))):
        a = left[i] if i < len(left) else ""
        b = right[i] if i < len(right) else ""
        out.append(f"  {a.ljust(_GUTTER)}{b}".rstrip())
    return out


def _money(usd: float, billing: str) -> str:
    return f"${usd:,.2f}" if billing == "api_key" else "(subscription)"


def render(data: dict) -> str:
    """The receipt as pasteable text."""
    p, cost, ver = data["project"], data["cost"], data["verified"]
    billing_mode = p.get("billing_mode", "api_key")
    req, ev, audit = ver["requirements"], ver["evidence"], ver["audit"]
    accepted = req["met"] + req["open"] + req["cut"]

    head = (f'{p["name"]} — {p["mode"]} · {p["days"]} days · '
            f'{p["teams"]} teams, {p["seats"]} seats')

    left = [
        f'{_money(cost["usd"], billing_mode)} total',
        f'{cost["runs"]} runs, {cost["runs_ended_early"]} ended early',
    ]
    if cost["usd_on_runs_that_died"]:
        left.append(f'{_money(cost["usd_on_runs_that_died"], billing_mode)} of that bought nothing')
    if cost["external_usd"]:
        left.append(f'{_money(cost["external_usd"], billing_mode)} spent in the real world')
    left.append(f'{cost["tokens_in"]:,} in / {cost["tokens_out"]:,} out')

    right = [f'{req["met"]} of {accepted} requirements met']
    if req["cut"]:
        right.append(f'{req["cut"]} cut, on the record')
    right.append(f'{ev["verified"]} of {ev["external"]} external claims verified')
    if ev["failed"]:
        right.append(f'{ev["failed"]} failed verification — caught, not shipped')
    if ev["platform_attested"]:
        right.append(f'{ev["platform_attested"]} arrived by signed webhook')
    right.append(f'{audit["sampled"]} closes spot-checked, {audit["failed"]} failed')
    if ver["commitment"]:
        right.append(f'promised {ver["commitment"]["deliver_by"][:10]} · '
                     f'{ver["commitment"]["status"]}')
    if ver["dates_promised"] > 1:
        right.append(f'{ver["dates_promised"]} dates stood behind in total')

    lines = [
        head,
        "",
        f'  {"WHAT IT COST".ljust(_GUTTER)}WHAT THE PLATFORM VERIFIED',
        *_pair(left, right),
        "",
        "  WHAT IT COST YOU",
        f'  {data["chairman"]["decisions"]} decisions, each on the record with your reason',
        "",
        "  Nothing in the right column was written by an agent. A requirement reaches",
        '  "met" only after the platform ran its acceptance test, and an external claim',
        "  counts only once the platform fetched, hashed and snapshotted it.",
    ]
    for caveat in data["caveats"]:
        lines.append(f"  caveat: {caveat}")
    return "\n".join(lines)
