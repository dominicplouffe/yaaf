"""The founding conversation: designing a company before one exists.

Every other admin endpoint acts on a project. These act *before* there is one.
A founding thread holds a transcript and a draft `project_setup` bundle — the
same object the importer accepts and the New Project screen posts — so the
chairman argues with a proposal instead of filling in a form.

Nothing here creates a company, spends a project's money, or binds an agent.
The one endpoint that touches a real project is ``/attach``, which runs *after*
the chairman has created one and hands the CEO the conversation it came from.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, platform
from yaaf.core.db import notify
from yaaf.core.models import (
    Channel,
    ChannelKind,
    FoundingThread,
    Message,
    Project,
    User,
    utcnow,
)
from yaaf.founder import agent as founder_agent
from yaaf.server.deps import admin_auth, db

router = APIRouter(dependencies=[Depends(admin_auth)])

WAKE_CHANNEL = "yaaf_wake"


def _chairman(session: Session) -> User:
    """The one human on this system.

    Auth here is a single admin token and the product says so in as many words
    — "you are the chairman, and you are the only human on this system". So a
    founding thread belongs to the admin user rather than asking for an email
    the chairman would have to type to talk to an agent.
    """
    user = session.execute(
        select(User).where(User.is_admin.is_(True)).order_by(User.created_at)
    ).scalars().first()
    if user is None:
        user = session.execute(select(User).order_by(User.created_at)).scalars().first()
    if user is None:
        raise HTTPException(
            status_code=422,
            detail="no user exists yet; create the chairman account before founding a project",
        )
    return user


def _thread(session: Session, thread_id: str) -> FoundingThread:
    thread = session.get(FoundingThread, thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="no such founding thread")
    return thread


def _render(thread: FoundingThread) -> dict:
    """The thread as the browser needs it.

    Only what a person said and what the Founder said back. Tool calls,
    tool results and thinking blocks stay in the stored transcript because the
    model needs them replayed unchanged; showing them here would turn a
    conversation into a log.
    """
    turns = []
    for entry in thread.transcript or []:
        content = entry.get("content")
        if entry.get("role") == "user":
            if isinstance(content, str):
                turns.append({"role": "chairman", "text": content})
            continue
        if isinstance(content, list):
            said = "\n".join(
                block.get("text", "") for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ).strip()
            if said:
                turns.append({"role": "founder", "text": said})
    return {
        "id": thread.id,
        "title": thread.title,
        "turns": turns,
        "draft_bundle": thread.draft_bundle,
        "created_project_id": thread.created_project_id,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


@router.get("/status")
def status(session: Session = Depends(db)) -> dict:
    """Whether the Founder can run at all, and on what.

    The fire drill and the New Project form work without a platform credential;
    only the conversation needs one. The UI asks this to decide whether to
    offer the Founder or explain what is missing.
    """
    return {
        "credential_configured": platform.has_credential(session),
        "billing_mode": platform.billing_mode(session),
        "model": platform.founder_model(session),
    }


class CredentialIn(BaseModel):
    value: str = Field(min_length=8)
    mode: str = Field(default="api_key", pattern="^(api_key|subscription)$")


@router.put("/credential")
def put_credential(body: CredentialIn, session: Session = Depends(db)) -> dict:
    """Install or rotate the platform credential.

    This is not a project's billing key and never becomes one: it pays for
    product-wide conversations, is decrypted server-side only, and is never
    injected into a sandbox.
    """
    platform.set_credential(session, body.value.strip(), mode=body.mode)
    return {"ok": True, "billing_mode": body.mode}


@router.delete("/credential")
def delete_credential(session: Session = Depends(db)) -> dict:
    return {"removed": platform.clear_credential(session)}


class ModelIn(BaseModel):
    model: str = Field(min_length=3, max_length=64)


@router.put("/model")
def put_model(body: ModelIn, session: Session = Depends(db)) -> dict:
    platform.set_setting(session, platform.FOUNDER_MODEL, body.model.strip())
    return {"model": body.model.strip()}


class StartIn(BaseModel):
    # Optional so a thread can be opened before the chairman has written
    # anything; when present, the Founder drafts on the spot rather than
    # greeting an empty screen.
    message: str | None = Field(default=None, max_length=40_000)


@router.post("/threads")
def start_thread(body: StartIn, session: Session = Depends(db)) -> dict:
    thread = FoundingThread(
        id=ids.new_id(ids.FOUNDING_THREAD),
        user_id=_chairman(session).id,
        transcript=[],
    )
    session.add(thread)
    session.flush()
    if body.message and body.message.strip():
        _say(session, thread, body.message.strip())
    return _render(thread)


@router.get("/threads")
def list_threads(session: Session = Depends(db)) -> dict:
    rows = session.execute(
        select(FoundingThread).order_by(FoundingThread.updated_at.desc()).limit(50)
    ).scalars().all()
    return {"threads": [{
        "id": t.id,
        "title": t.title,
        "has_draft": t.draft_bundle is not None,
        "created_project_id": t.created_project_id,
        "updated_at": t.updated_at,
    } for t in rows]}


@router.get("/threads/{thread_id}")
def get_thread(thread_id: str, session: Session = Depends(db)) -> dict:
    return _render(_thread(session, thread_id))


class SayIn(BaseModel):
    message: str = Field(min_length=1, max_length=40_000)


def _say(session: Session, thread: FoundingThread, message: str) -> None:
    try:
        founder_agent.say(session, thread, message)
    except platform.NoPlatformCredential as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/threads/{thread_id}/say")
def say(thread_id: str, body: SayIn, session: Session = Depends(db)) -> dict:
    thread = _thread(session, thread_id)
    if thread.created_project_id:
        raise HTTPException(
            status_code=409,
            detail="this thread already became a company; talk to its CEO in the boardroom",
        )
    _say(session, thread, body.message.strip())
    return _render(thread)


@router.delete("/threads/{thread_id}")
def delete_thread(thread_id: str, session: Session = Depends(db)) -> dict:
    session.delete(_thread(session, thread_id))
    return {"ok": True}


class AttachIn(BaseModel):
    project_id: str


@router.post("/threads/{thread_id}/attach")
def attach(thread_id: str, body: AttachIn, session: Session = Depends(db)) -> dict:
    """Record that a thread became a company, and hand the CEO the conversation.

    The chairman explained what they wanted here, argued about it, and settled
    it. Without this the product would ask them to say all of it a second time
    to a new CEO on day one, which is exactly the kind of thing the Founder
    exists to stop.

    The briefing lands in the boardroom as a chairman message, so it arrives
    through the one channel the CEO already reads and is subject to the same
    rules as anything else said there.
    """
    thread = _thread(session, thread_id)
    project = session.get(Project, body.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="no such project")
    if thread.created_project_id:
        raise HTTPException(status_code=409, detail="this thread is already attached")

    thread.created_project_id = project.id
    thread.updated_at = utcnow()

    briefing = founder_agent.briefing(thread)
    if not briefing:
        return {"ok": True, "briefed": False}

    channel = session.execute(
        select(Channel).where(
            Channel.project_id == project.id, Channel.kind == ChannelKind.BOARDROOM)
    ).scalar_one_or_none()
    if channel is None:
        return {"ok": True, "briefed": False}
    session.add(Message(
        id=ids.new_id(ids.MESSAGE),
        project_id=project.id,
        channel_id=channel.id,
        sender_user_id=project.chairman_user_id,
        # Trimmed to the boardroom's own ceiling rather than sent whole: a long
        # founding argument is still a boardroom message, and the limit is
        # there because a CEO has to be able to read it.
        body=briefing[:20_000],
    ))
    notify(session, WAKE_CHANNEL, project.id)
    return {"ok": True, "briefed": True}
