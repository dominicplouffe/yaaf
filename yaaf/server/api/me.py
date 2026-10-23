"""The signed-in person's own state: today, their snoozes.

A snooze is "not now" on one row of one person's Inbox. It hides the row from
that person's Needs-you list and badge until `until`, and from nobody else.

The one rule: a snooze never outlives the clock of the thing it hides. An
approval expires to its default and a question to the CEO stops waiting and
does what it said it would; a snooze running past either would turn "remind me
later" into a decision nobody made. So for those the route refuses an `until`
later than SNOOZE_MARGIN before the deadline — and refuses the snooze outright
once there is less than that left. Every other row may sleep for up to
SNOOZE_MAX_DAYS.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Approval, ApprovalStatus, Ask, Snooze, utcnow
from yaaf.server import authority
from yaaf.server.deps import db
from yaaf.server.identity import Person, signed_in

router = APIRouter()

#: How long before a clocked row decides itself a snooze must have ended.
SNOOZE_MARGIN = timedelta(hours=2)
#: The longest any snooze may run.
SNOOZE_MAX_DAYS = 7


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _deadline(session: Session, project_id: str, item_key: str) -> datetime | None:
    """When the row decides itself, for the rows that do; None for the rest."""
    if item_key.startswith(f"{ids.APPROVAL}_"):
        row = session.get(Approval, item_key)
        if row is None or row.project_id != project_id:
            raise HTTPException(status_code=404, detail="no such decision on that project")
        if row.status != ApprovalStatus.PENDING:
            raise HTTPException(status_code=409, detail=f"that decision is already {row.status}")
        return _aware(row.expires_at)
    if item_key.startswith(f"{ids.ASK}_"):
        row = session.get(Ask, item_key)
        if row is None or row.project_id != project_id:
            raise HTTPException(status_code=404, detail="no such question on that project")
        return _aware(row.answer_by)
    return None


def active(session: Session, person_id: str, project_ids: set[str]) -> list[dict]:
    """This person's snoozes still running, on projects they can open."""
    now = utcnow()
    rows = session.execute(
        select(Snooze).where(Snooze.user_id == person_id, Snooze.until > now)
    ).scalars()
    return [{"item_key": s.item_key, "project_id": s.project_id,
             "until": _aware(s.until).isoformat()}
            for s in rows if s.project_id in project_ids]


class SnoozeIn(BaseModel):
    project_id: str = Field(min_length=1, max_length=40)
    item_key: str = Field(min_length=1, max_length=120)
    until: datetime


@router.post("/snoozes", dependencies=[authority.SIGNED_IN])
def snooze(body: SnoozeIn, person: Person = Depends(signed_in),
           session: Session = Depends(db)) -> dict:
    reachable = {p.id for p in authority.reachable(session, person.id, person.is_admin)}
    if body.project_id not in reachable:
        raise HTTPException(status_code=404, detail="no such project")
    now = utcnow()
    until = _aware(body.until)
    if until <= now:
        raise HTTPException(status_code=422, detail="a snooze ends in the future")
    if until > now + timedelta(days=SNOOZE_MAX_DAYS):
        raise HTTPException(status_code=422,
                            detail=f"a snooze lasts at most {SNOOZE_MAX_DAYS} days")
    deadline = _deadline(session, body.project_id, body.item_key)
    if deadline is not None:
        latest = deadline - SNOOZE_MARGIN
        if latest <= now:
            raise HTTPException(
                status_code=409,
                detail="this decides itself within two hours — too soon to snooze; "
                       "decide it, or let its default happen")
        if until > latest:
            raise HTTPException(
                status_code=422,
                detail=f"this decides itself at {deadline.isoformat()}; a snooze must end "
                       f"by {latest.isoformat()} so you see it again first")
    row = session.execute(
        select(Snooze).where(Snooze.user_id == person.id, Snooze.item_key == body.item_key)
    ).scalar_one_or_none()
    if row is None:
        row = Snooze(id=ids.new_id(ids.SNOOZE), user_id=person.id,
                     project_id=body.project_id, item_key=body.item_key, until=until)
        session.add(row)
    else:
        row.project_id, row.until = body.project_id, until
    return {"item_key": row.item_key, "until": until.isoformat(),
            "deadline": deadline.isoformat() if deadline else None}


@router.delete("/snoozes/{item_key}", dependencies=[authority.SIGNED_IN])
def wake(item_key: str, person: Person = Depends(signed_in),
         session: Session = Depends(db)) -> dict:
    row = session.execute(
        select(Snooze).where(Snooze.user_id == person.id, Snooze.item_key == item_key)
    ).scalar_one_or_none()
    if row is not None:
        session.delete(row)
    return {"item_key": item_key, "snoozed": False}
