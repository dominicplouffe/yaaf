"""Heads-ups: what the chairman asked to be told, which he notes rather than decides.

The line between this and information is one sentence of his: a standing order
in the brief that says to tell him. "Send me tomorrow's weather" makes the
weather a heads-up; without it the weather goes in the daily delivery, with no
button at all. And the line between this and an approval is whether a click
changes anything — it does not, so there is no Approve, no Deny and no clock
that expires to no. There is Noted, which tells the CEO he saw it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import AuditLog, HeadsUp, Project, utcnow
from yaaf.server import acting


class HeadsUpError(ValueError):
    """Refused, in words the CEO can act on."""


def _norm(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def matching_order(project: Project, cited: str) -> str:
    """The standing order this heads-up answers, as the brief words it."""
    orders = list(project.standing_orders or [])
    want = _norm(cited)
    exact = [o for o in orders if _norm(o) == want]
    if exact:
        return exact[0]
    # Quoting the part of the order that says "tell me" is enough, as long as
    # it names one order and not two.
    partial = [o for o in orders if want and len(want) >= 12 and want in _norm(o)]
    if len(partial) == 1:
        return partial[0]
    if not orders:
        raise HeadsUpError(
            "The brief holds no standing orders, so the chairman has not asked to be told "
            "anything. This is information: put it in the deliverable or let the daily "
            "brief carry it. If he has asked you to keep him informed of this, that is a "
            "standing order — propose it with brief_propose first.")
    listed = "\n".join(f"- {o}" for o in orders)
    raise HeadsUpError(
        "A heads-up answers a standing order that tells you to keep the chairman "
        f"informed, quoted from the brief. {cited!r} is not one of them"
        + (" (it matches more than one)" if len(partial) > 1 else "")
        + f". The standing orders are:\n{listed}\n"
        "If none of them asks for this, it is information: the deliverable or the daily "
        "brief carries it, and his inbox does not. If something needs him to decide or "
        "to do, that is an approval or an assist.")


def file(session: Session, project: Project, agent_id: str, title: str, body: str,
         standing_order: str) -> HeadsUp:
    order = matching_order(project, standing_order)
    row = HeadsUp(id=ids.new_id(ids.HEADS_UP), project_id=project.id, agent_id=agent_id,
                  title=title.strip(), body=body.strip(), standing_order=order)
    session.add(row)
    return row


def acknowledge(session: Session, row: HeadsUp) -> None:
    row.acknowledged_at = utcnow()
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=row.project_id, actor=acting.stamp(),
        action="heads_up.ack", entity="heads_up", entity_id=row.id, detail={}))


def unacknowledged(session: Session, project_id: str) -> list[HeadsUp]:
    return list(session.execute(
        select(HeadsUp).where(HeadsUp.project_id == project_id,
                              HeadsUp.acknowledged_at.is_(None))
        .order_by(HeadsUp.created_at)
    ).scalars())


def row(h: HeadsUp, reply: dict | None = None) -> dict:
    return {"id": h.id, "project_id": h.project_id, "title": h.title, "body": h.body,
            "standing_order": h.standing_order,
            "created_at": h.created_at.isoformat(),
            "acknowledged_at": h.acknowledged_at.isoformat() if h.acknowledged_at else None,
            "note": reply}
