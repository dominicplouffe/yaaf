"""Deciding a waiting item from anywhere that is not the dashboard's own button.

Two calls, never one. `preview` says exactly what would happen and returns a
single-use ticket; `commit` spends it. The ticket is bound to the person, the
project, the item, the option, the note and the item's content as it was
previewed, and it lives five minutes — so the second call cannot be a
different decision wearing the first one's confirmation, and a request that
changed after it was shown cannot be approved on the strength of the old one.
It is `mcp/confirm.py`'s rule for model clients, made the rule for every
surface a person decides from: a phone, a Slack card, an email.

`commit` does not decide anything itself. It calls the verb the dashboard's
own button calls — `decide_approval`, a boardroom reply, `accept_requirement`,
`accept_commitment` / `decline_commitment`, `requirement_verdict`,
`complete_project` — under the person's own bound actor, so every rule those
verbs enforce still applies and every record they write still names who did
it and through which door.

Opening a link never reaches here: both routes are POSTs behind a signed-in
person with the authority to decide (STEER). A prefetching mail scanner, a
notification tap and a forwarded email all stop at a page that shows the item.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Approval,
    Ask,
    AuditLog,
    CommitTicket,
    Project,
    Requirement,
    utcnow,
)
from yaaf.core.tokens import hash_token, mint_prefixed
from yaaf.server import acting, authority, needs_you, policy
from yaaf.server.deps import db
from yaaf.server.identity import Person, person_auth, signed_in

router = APIRouter(dependencies=[Depends(signed_in)])

#: Long enough to read what is about to happen, short enough that a ticket is
#: not lying around to be spent on something the person has forgotten.
TTL = timedelta(minutes=5)


class Choice(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    option: str = Field(min_length=1, max_length=40)
    note: str = Field(default="", max_length=10_000)


class Commit(Choice):
    ticket: str = Field(min_length=1, max_length=200)


def _project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:  # pragma: no cover - the STEER gate refuses first
        raise HTTPException(status_code=404, detail="unknown project")
    return project


def _fate(session: Session, project: Project, key: str) -> str:
    """What became of an item that is no longer waiting, in words."""
    approval = session.get(Approval, key)
    if approval is not None and approval.project_id == project.id:
        return f"it is already {approval.status}"
    ask = session.get(Ask, key)
    if ask is not None and ask.project_id == project.id:
        return f"the question is already {ask.status}"
    return "nothing is waiting under that key any more"


def _waiting(session: Session, project: Project, key: str) -> dict:
    for item in needs_you.items(session, project):
        if item["key"] == key:
            return item
    raise HTTPException(status_code=409,
                        detail=f"This is no longer waiting on you: {_fate(session, project, key)}.")


def _option(item: dict, choice: Choice) -> dict:
    if not item["commit"]:
        raise HTTPException(
            status_code=422,
            detail=f"This one is decided on its page, not from here: {item['path_label']} "
                   f"(/app/#{item['path']}).")
    option = next((o for o in item["options"] if o["id"] == choice.option), None)
    if option is None:
        raise HTTPException(
            status_code=422,
            detail="That is not one of this item's options: "
                   + ", ".join(o["id"] for o in item["options"]))
    if option["needs_note"] and not choice.note.strip():
        raise HTTPException(status_code=422,
                            detail=f"“{option['label']}” needs a note: it is what the CEO reads.")
    return option


def _fingerprint(user_id: str, project_id: str, item: dict, choice: Choice) -> str:
    return hashlib.sha256(json.dumps(
        [user_id, project_id, item["key"], item["version"], choice.option, choice.note.strip()]
    ).encode()).hexdigest()


@router.post("/projects/{project_id}/needs-you/preview", dependencies=[authority.STEER])
def preview(project_id: str, body: Choice, session: Session = Depends(db),
            person: Person = Depends(person_auth)) -> dict:
    """What this choice would do, and a ticket to do it once. Changes nothing."""
    project = _project(session, project_id)
    item = _waiting(session, project, body.key)
    option = _option(item, body)
    plaintext, token_hash = mint_prefixed("yct")
    session.add(CommitTicket(
        id=ids.new_id(ids.COMMIT_TICKET), user_id=person.id, project_id=project.id,
        item_key=item["key"], token_hash=token_hash,
        fingerprint=_fingerprint(person.id, project.id, item, body),
        expires_at=utcnow() + TTL))
    return {
        "about_to": {"key": item["key"], "project": item["project"],
                     "headline": item["headline"], "option": option["label"],
                     "consequence": option["consequence"],
                     "irreversible": option["irreversible"], "undo": item["undo"],
                     "note": body.note.strip() or None},
        "ticket": plaintext,
        "expires_in_seconds": int(TTL.total_seconds()),
    }


@router.post("/projects/{project_id}/needs-you/commit", dependencies=[authority.STEER])
def commit(project_id: str, body: Commit, session: Session = Depends(db),
           person: Person = Depends(person_auth)) -> dict:
    """Spend a ticket: the previewed choice, through the verb that owns it."""
    project = _project(session, project_id)
    row = session.execute(
        select(CommitTicket).where(CommitTicket.token_hash == hash_token(body.ticket))
    ).scalar_one_or_none()
    # One answer for every ticket that is not this person's on this project:
    # the refusal must not tell a guesser which tickets exist.
    if row is None or row.user_id != person.id or row.project_id != project.id:
        raise HTTPException(status_code=403, detail="That ticket is not yours for this project.")
    if row.used_at is not None:
        raise HTTPException(status_code=409, detail="That ticket was already spent; preview again.")
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(
        tzinfo=utcnow().tzinfo)
    if expires < utcnow():
        raise HTTPException(status_code=409, detail="That ticket expired; preview again.")
    if row.item_key != body.key:
        raise HTTPException(status_code=409,
                            detail="That ticket was issued for a different item; preview this one.")
    item = _waiting(session, project, body.key)
    option = _option(item, body)
    if row.fingerprint != _fingerprint(person.id, project.id, item, body):
        raise HTTPException(
            status_code=409,
            detail="This is not what you previewed — the choice, your note or the request "
                   "itself changed since. Preview it again.")
    row.used_at = utcnow()
    outcome = _apply(session, project, item, body)
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor=acting.stamp(),
        action="needs_you.commit", entity="needs_you", entity_id=item["key"],
        detail={"family": item["family"], "option": option["id"],
                "note": body.note.strip() or None}))
    return {"key": item["key"], "option": option["id"], "done": option["label"],
            "outcome": outcome}


def _apply(session: Session, project: Project, item: dict, choice: Commit) -> dict:
    """The dashboard's own verb for this family and option."""
    from yaaf.core.constitution import effective
    from yaaf.server.api import admin

    family, note = item["family"], choice.note.strip()
    if family == "approval":
        return admin.decide_approval(
            approval_id=item["key"], session=session,
            body=admin.ApprovalDecision(approve=choice.option == "approve", note=note))
    if family == "ask":
        said = note if choice.option == "reply" else next(
            o["label"] for o in item["options"] if o["id"] == choice.option)
        if choice.option != "reply" and note:
            said = f"{said} — {note}"
        # The same line the Inbox's one-click answer sends: the CEO reads the
        # boardroom, and a reply there is what answers a question.
        return admin.boardroom_post(
            project_id=project.id, session=session,
            body=admin.BoardroomPost(body=f"Re: “{item['headline']}” — {said}"))
    cons = effective(session, project.id)
    waiting = policy.awaiting_chairman(session, project, cons)
    if family == "scope":
        accepted = []
        for requirement in waiting["plan"]:
            admin.accept_requirement(requirement_id=requirement.id, session=session,
                                     body=admin.RequirementDecision(note=note))
            accepted.append(requirement.key)
        return {"accepted": accepted}
    if family == "commitment":
        commitment = waiting["commitment"]
        if choice.option == "accept":
            return admin.accept_commitment(commitment_id=commitment.id, session=session,
                                           body=admin.CommitmentAccept(note=note))
        return admin.decline_commitment(commitment_id=commitment.id, session=session,
                                        body=admin.CommitmentDecline(reason=note))
    if family == "verdict":
        key = item["key"].removeprefix(f"{project.id}-verdict-")
        requirement = session.execute(
            select(Requirement).where(Requirement.project_id == project.id,
                                      Requirement.key == key)).scalar_one()
        return admin.requirement_verdict(
            requirement_id=requirement.id, session=session,
            body=admin.RequirementVerdict(ok=choice.option == "met", note=note))
    if family == "signoff":
        return admin.complete_project(project_id=project.id, session=session)
    raise HTTPException(status_code=422, detail="This one is decided on its page.")  # pragma: no cover
