"""The signed-in person's own state: today, their snoozes, their pins.

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
from yaaf.core.models import Approval, ApprovalStatus, Ask, Snooze, User, utcnow
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


# ── pins ─────────────────────────────────────────────────────────────────────────
#
# The places a person keeps going back to, one click away under the project in
# the sidebar. The sidebar holds a project's five pages and no more, because
# ten made every one of them look equally important; which of the pages under
# them matters most differs by person, so each person pins their own, and
# nobody else's sidebar grows.

#: The most pins a person may keep. Past five the pins are the old ten-row
#: sidebar again, chosen one at a time.
MAX_PINS = 5
#: What a person who never touched their pins has. Deliverables is where the
#: team's work is, drafts and all, and it is the page people asked for most
#: once it moved under Details.
DEFAULT_PINS = ({"to": "details/deliverables", "label": "Deliverables", "project_id": None},)
#: A place inside a project: a relative path, optionally with a query. The
#: browser puts it under /projects/<id>/, so it can never name another site —
#: no scheme, no leading slash, nothing but path characters before the query.
PIN_TARGET = r"^[a-z][a-z0-9/_-]{0,120}(\?[A-Za-z0-9=&_%.~+!*'()-]{0,160})?$"


class PinIn(BaseModel):
    to: str = Field(min_length=1, max_length=300, pattern=PIN_TARGET)
    label: str = Field(min_length=1, max_length=80)
    #: Set for a pin that only makes sense in one project, like one file.
    project_id: str | None = Field(default=None, max_length=40)


class PinsIn(BaseModel):
    pins: list[PinIn] = Field(max_length=MAX_PINS)


def _pins_view(session: Session, person: Person, user: User) -> dict:
    reachable = {p.id for p in authority.reachable(session, person.id, person.is_admin)}
    stored = user.pins
    pins = list(DEFAULT_PINS) if stored is None else [
        p for p in stored if not p.get("project_id") or p["project_id"] in reachable]
    return {"pins": pins, "default": stored is None, "max": MAX_PINS}


@router.get("/pins", dependencies=[authority.SIGNED_IN])
def pins(person: Person = Depends(signed_in), session: Session = Depends(db)) -> dict:
    return _pins_view(session, person, session.get(User, person.id))


@router.put("/pins", dependencies=[authority.SIGNED_IN])
def set_pins(body: PinsIn, person: Person = Depends(signed_in),
             session: Session = Depends(db)) -> dict:
    """Replace this person's pins, in the order given."""
    reachable = {p.id for p in authority.reachable(session, person.id, person.is_admin)}
    seen: set[tuple[str, str | None]] = set()
    kept: list[dict] = []
    for pin in body.pins:
        if pin.project_id is not None and pin.project_id not in reachable:
            raise HTTPException(status_code=404, detail="no such project")
        if (pin.to, pin.project_id) in seen:
            continue
        seen.add((pin.to, pin.project_id))
        kept.append({"to": pin.to, "label": pin.label.strip(), "project_id": pin.project_id})
    user = session.get(User, person.id)
    user.pins = kept
    return _pins_view(session, person, user)
