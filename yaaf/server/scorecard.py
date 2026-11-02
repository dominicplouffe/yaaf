"""What a cycle actually did, written once at close and said one way.

The scorecard used to be four numbers: accepted, met, carried, and the keys.
A chairman read "Cycle 8 closed with 0 of 4 met, 1 carried forward" in the
headline of his weekly meeting and could not say what the four were, whether
it was bad, or why — when his report had landed before its deadline every day
of that week. All three of the reasons were in how the numbers were made:

* three of the four were cut versions of the same report, still counted;
* the fourth was owed DAILY and judged by a trigger that never fired for it;
* the sentence named a count and no thing.

So the card now keeps each kind of scope apart — one-off requirements met or
carried, standing obligations honoured or missed (in days, for a daily one),
scope cut — and `sentence` names what it counts.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Cycle,
    Recurrence,
    Requirement,
    RequirementEvent,
    RequirementStatus,
)
from yaaf.server import standing

#: How much of a requirement's name a scorecard row keeps. A sentence names
#: things; the Roadmap holds the whole text.
NAME_LEN = 120

HONOURED, MISSED, UNCHECKED, NOT_DUE = "honoured", "missed", "unchecked", "not_due"


def _name(requirement: Requirement) -> str:
    from yaaf.server.rows import requirement_name

    return requirement_name(requirement)[:NAME_LEN]


def _title(requirement: Requirement) -> str | None:
    return ((requirement.meta or {}).get("title") or "").strip() or None


def _ruled_in(session: Session, requirement: Requirement, cycle: Cycle,
              until: datetime) -> bool:
    """Did the platform run this requirement's test at all during the cycle?
    A failed test and a test that never ran are different news."""
    return session.execute(
        select(RequirementEvent.id).where(
            RequirementEvent.requirement_id == requirement.id,
            RequirementEvent.from_status == RequirementStatus.CHECKING,
            RequirementEvent.at > cycle.opened_at,
            RequirementEvent.at <= until,
        ).limit(1)
    ).first() is not None


def build(session: Session, cycle: Cycle, scope: list[Requirement], tz: str,
          until: datetime) -> dict:
    in_force = [r for r in scope
                if r.status not in (RequirementStatus.PROPOSED, RequirementStatus.CUT)]
    cut = [r for r in scope if r.status == RequirementStatus.CUT]
    one_off = [r for r in in_force if r.recurrence != Recurrence.STANDING]
    owed = [r for r in in_force if r.recurrence == Recurrence.STANDING]

    rows = []
    for r in owed:
        row: dict = {"key": r.key, "name": _name(r), "title": _title(r)}
        if standing.is_daily(r):
            days = standing.tally(session, r, cycle, tz, until)
            row.update(days, due_daily_at=r.acceptance_test.get("due_daily_at"))
            if not days["days_due"]:
                row["verdict"] = NOT_DUE
            elif days["days_met"] == days["days_due"]:
                row["verdict"] = HONOURED
            elif days["days_met"] + days["days_missed"]:
                row["verdict"] = MISSED
            else:
                row["verdict"] = UNCHECKED
        elif r.status == RequirementStatus.MET:
            row["verdict"] = HONOURED
        else:
            row["verdict"] = MISSED if _ruled_in(session, r, cycle, until) else UNCHECKED
        rows.append(row)

    met_keys = ([r.key for r in one_off if r.status == RequirementStatus.MET]
                + [row["key"] for row in rows if row["verdict"] == HONOURED])
    carried_keys = [r.key for r in one_off if r.status != RequirementStatus.MET]
    missed_keys = [row["key"] for row in rows if row["verdict"] in (MISSED, UNCHECKED)]
    return {
        "accepted": len(in_force),
        "met": len(met_keys),
        "carried": len(carried_keys),
        "missed": len(missed_keys),
        "cut": len(cut),
        "met_keys": met_keys,
        "carried_keys": carried_keys,
        "missed_keys": missed_keys,
        "cut_keys": [r.key for r in cut],
        "standing": rows,
    }


def _days(row: dict) -> str:
    due, met = row.get("days_due", 0), row.get("days_met", 0)
    if row["verdict"] == UNCHECKED:
        return f"not checked on any of {due} {_plural(due, 'day')}"
    text = f"on time {met} of {due} {_plural(due, 'day')}"
    if row.get("missed_days"):
        text += f", missed {', '.join(row['missed_days'])}"
    if row.get("days_unchecked"):
        text += f", {row['days_unchecked']} not checked"
    return text


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def sentence(card: dict | None) -> str:
    """The card in words, naming what it counts. One function, so the
    headline, the flag, the narrative stub and the CEO's notice cannot say
    four different things about the same week."""
    card = card or {}
    if "standing" not in card:
        return _legacy(card)
    parts = []
    for row in card["standing"]:
        label = row.get("title") or row["key"]
        if row["verdict"] == NOT_DUE:
            continue
        if "days_due" in row:
            parts.append(f"{label}: {_days(row)}")
        elif row["verdict"] == HONOURED:
            parts.append(f"{label}: done")
        elif row["verdict"] == MISSED:
            parts.append(f"{label}: owed this cycle and not done")
        else:
            parts.append(f"{label}: owed this cycle and never checked")
    owed = {row["key"] for row in card["standing"]}
    one_off_met = [k for k in card.get("met_keys", []) if k not in owed]
    carried = card.get("carried_keys", [])
    total = len(one_off_met) + len(carried)
    if total:
        text = f"{len(one_off_met)} of {total} one-off {_plural(total, 'requirement')} met"
        if carried:
            text += f", {', '.join(carried)} carried forward"
        parts.append(text)
    if card.get("cut_keys"):
        parts.append(f"{', '.join(card['cut_keys'])} cut")
    if not parts:
        return "Nothing was owed."
    return "; ".join(parts) + "."


def _legacy(card: dict) -> str:
    """A card written before the scorecard named its rows."""
    if not card.get("accepted"):
        return "Nothing was accepted."
    return (f"{card.get('met', 0)} of {card['accepted']} met"
            + (f", {', '.join(card['carried_keys'])} carried forward"
               if card.get("carried_keys") else "") + ".")
