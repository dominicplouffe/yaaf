"""The founding conversation: designing a project before one exists.

Every other admin endpoint acts on a project. These act *before* there is one.
A founding thread holds a transcript and a draft `project_setup` bundle — the
same object the importer accepts and the New Project screen posts — so the
chairman argues with a proposal instead of filling in a form.

Nothing here creates a project, spends a project's money, or binds an agent.
The one endpoint that touches a real project is ``/attach``, which runs *after*
the chairman has created one and hands the CEO the conversation it came from.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids, platform
from yaaf.core.constitution import preset_cards
from yaaf.core.db import notify
from yaaf.core.models import (
    Channel,
    ChannelKind,
    FoundingThread,
    Message,
    User,
    utcnow,
)
from yaaf.founder import agent as founder_agent
from yaaf.founder import progress
from yaaf.founder import runner as founder_runner
from yaaf.server import acting, authority, policy
from yaaf.server.api import settings as app_settings
from yaaf.server.deps import db
from yaaf.server.identity import Person, person_auth, signed_in

router = APIRouter(dependencies=[Depends(signed_in)])

WAKE_CHANNEL = "yaaf_wake"


def _founder(session: Session) -> User:
    """Whoever is signed in. A founding conversation is theirs: it used to
    belong to "the admin user, else the oldest", because the only credential
    named nobody in particular."""
    user = session.get(User, acting.user_id() or "")
    if user is None:
        raise HTTPException(status_code=401, detail="sign in first")
    return user


def _thread(session: Session, thread_id: str, person: Person) -> FoundingThread:
    """A thread this person owns — somebody else's is refused in the words of
    one that does not exist, because a founding conversation is a person's
    thinking out loud, not a record of the box."""
    thread = session.get(FoundingThread, thread_id)
    if thread is None or not authority.owns(person, thread.user_id):
        raise HTTPException(status_code=404, detail="no such founding thread")
    return thread


def _render(thread: FoundingThread, running: bool | None = None) -> dict:
    """The thread as the browser needs it.

    The stored transcript is already just what each side said — the Founder
    runs through the CLI, which owns the tool calls and the thinking, so none
    of that reaches us to be filtered out here.

    `running` is sampled by the caller when the row was loaded from the
    database, and it has to be sampled BEFORE the row. The worker commits the
    reply, the reason and the cleared stamp and only then marks the turn
    finished — "the order is the point", as `runner._work` says, because *not
    running* is the signal a poller waits for. A reader that loads the row
    first and asks the flag second inverts that: a turn finishing in between
    hands back a pre-completion row with a post-completion flag, which is a
    finished turn carrying no reply and no reason — the blank failure this
    whole feature exists to stop. Sampled first, a flag that says finished
    guarantees the row read after it holds everything the worker wrote.
    """
    if running is None:
        running = progress.is_running(thread.id)
    return {
        "id": thread.id,
        "title": thread.title,
        # A turn is drawn if it said something or asked something: a round
        # can arrive with no prose around it, and it is still the turn.
        "turns": [
            {"role": turn.get("role", "founder"), "text": turn.get("text", ""),
             **{key: turn[key] for key in ("round", "answers", "note", "changed")
                if turn.get(key) is not None}}
            for turn in (thread.transcript or []) if turn.get("text") or turn.get("round")
        ],
        "draft_bundle": thread.draft_bundle,
        "created_project_id": thread.created_project_id,
        "running": running,
        # Stamped but nothing running means the process died mid-turn. Saying so
        # is the difference between "your turn was lost, send it again" and a
        # thread that looks idle for no reason.
        "interrupted": bool(thread.turn_started_at) and not running,
        "last_error": thread.last_error,
        "created_at": thread.created_at,
        "updated_at": thread.updated_at,
    }


@router.get("/status", dependencies=[authority.SIGNED_IN])
def status(session: Session = Depends(db)) -> dict:
    """Whether the Founder can run at all, and on what.

    The fire drill and the New Project form work without a platform credential;
    only the conversation needs one. The UI asks this to decide whether to
    offer the Founder or explain what is missing.
    """
    return {
        "credential_configured": platform.has_credential(session),
        "billing_mode": platform.billing_mode(session),
        "provider": platform.credential_provider(session),
        "model": platform.founder_model(session),
        # What a run has actually cost here. The new-project form inverts the
        # question rather than predicting a total — "your cap holds if a run
        # averages under $X, and yours have averaged $Y" — because this box
        # knows the second number and cannot know the first. Null on a fresh
        # install, where the honest answer is that it has never priced a run.
        "avg_usd_per_run": policy.avg_usd_per_run_box(session),
        # The cards, with the rules each one writes. Served rather than
        # mirrored: the run presets used to live in the new-project screen and
        # nowhere else, so nothing but that screen could offer one and no
        # stored constitution could say which one it was. The settings page
        # reads the same list, which is what lets a chairman re-pick a card on
        # a project that is already running.
        "presets": preset_cards(),
    }


class CredentialIn(BaseModel):
    value: str = Field(min_length=8)
    mode: str = Field(default="api_key", pattern="^(api_key|subscription)$")
    provider: str = "anthropic"
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, min_length=3, max_length=200)


@router.put("/credential", dependencies=[authority.INSTANCE_ADMIN])
def put_credential(body: CredentialIn, session: Session = Depends(db)) -> dict:
    """Install or rotate the platform credential, and refuse a bad one now.

    This is not a project's billing key and never becomes one: it pays for
    product-wide conversations, is decrypted server-side only, and is never
    injected into a sandbox.

    It is checked before it is stored, on the same promise the project billing
    key already makes — *if the key is bad, you find out now, not at 9am
    tomorrow*. The shape check is the same function and the same sentence the
    per-project form uses, because a chairman pasting a subscription token into
    an API-key field should not get a different answer depending on which form
    he was looking at.

    The same credential is managed app-wide at ``PUT /admin/settings/anthropic``;
    both doors share one implementation so they cannot drift.
    """
    return app_settings.install_credential(session, body.value, body.mode, body.provider,
                                           body.base_url, body.model)


@router.post("/credential/test", dependencies=[authority.INSTANCE_ADMIN])
def test_credential(session: Session = Depends(db)) -> dict:
    """Prove the stored credential can actually reach a model.

    A real turn, deliberately: the credential's whole job is to run the Founder,
    and the only check that cannot be wrong about it is running one. It is a
    two-word answer, so it is the cheapest turn the product can take.
    """
    return app_settings.check_credential(session)


@router.delete("/credential", dependencies=[authority.INSTANCE_ADMIN])
def delete_credential(session: Session = Depends(db)) -> dict:
    return {"removed": platform.clear_credential(session)}


class ModelIn(BaseModel):
    model: str = Field(min_length=3, max_length=200)


@router.put("/model", dependencies=[authority.INSTANCE_ADMIN])
def put_model(body: ModelIn, session: Session = Depends(db)) -> dict:
    return app_settings.set_founder_model(session, body.model)


class StartIn(BaseModel):
    # Optional so a thread can be opened before the chairman has written
    # anything; when present, the Founder drafts on the spot rather than
    # greeting an empty screen.
    message: str | None = Field(default=None, max_length=40_000)


@router.post("/threads", dependencies=[authority.SIGNED_IN])
def start_thread(body: StartIn, session: Session = Depends(db)) -> dict:
    thread = FoundingThread(
        id=ids.new_id(ids.FOUNDING_THREAD),
        user_id=_founder(session).id,
        transcript=[],
    )
    session.add(thread)
    session.flush()
    started = bool(body.message and body.message.strip())
    if started:
        _say(session, thread, body.message.strip())
    return _accepted(_render(thread)) if started else _render(thread)


@router.get("/threads", dependencies=[authority.SIGNED_IN])
def list_threads(session: Session = Depends(db),
                 person: Person = Depends(person_auth)) -> dict:
    rows = session.execute(
        select(FoundingThread).where(FoundingThread.user_id == person.id)
        .order_by(FoundingThread.updated_at.desc()).limit(50)
    ).scalars().all()
    return {"threads": [{
        "id": t.id,
        "title": t.title,
        "has_draft": t.draft_bundle is not None,
        "created_project_id": t.created_project_id,
        "updated_at": t.updated_at,
    } for t in rows]}


@router.get("/threads/{thread_id}", dependencies=[authority.IN_HANDLER])
def get_thread(thread_id: str, session: Session = Depends(db),
               person: Person = Depends(person_auth)) -> dict:
    # Liveness first, then the row — see `_render`. This is the endpoint the
    # page polls in a loop, so it is the one that meets a turn ending mid-read.
    running = progress.is_running(thread_id)
    return _render(_thread(session, thread_id, person), running)


class AnswerIn(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    #: Index into the question's options. Absent with no text: left open.
    choice: int | None = Field(default=None, ge=0, le=3)
    #: His own words instead of any option.
    text: str | None = Field(default=None, max_length=4_000)


class SayIn(BaseModel):
    message: str = Field(default="", max_length=40_000)
    #: Answers to the round the Founder last asked. With these, `message` is an
    #: optional note beside them rather than the turn itself.
    answers: list[AnswerIn] | None = Field(default=None, max_length=12)


def _accepted(body: dict) -> JSONResponse:
    """202: recorded and under way, not finished.

    The reply and the draft land on a later GET. A failed turn no longer has an
    HTTP status to be — the request that started it was answered minutes
    earlier — so its message travels on the thread (`last_error`) and in the
    progress store, with a `kind` the page can style by.
    """
    return JSONResponse(status_code=202, content=jsonable_encoder(body))


def _say(session: Session, thread: FoundingThread, message: str,
         answers: list[dict] | None = None) -> None:
    """Record the message and hand the turn to a background thread.

    Nothing here waits on a model. The turn takes minutes, and a request that
    holds it loses the work to a refresh, to any reverse proxy, and to a
    threadpool slot held for the duration.

    The credential is checked *now* rather than in the thread, because a
    missing key is the chairman's to fix and he should be told on the request
    that asked, not through a failed turn.
    """
    if progress.is_running(thread.id):
        raise HTTPException(
            status_code=409,
            detail="this thread is already working on a turn — wait for it to finish",
        )
    if not platform.has_credential(session):
        raise HTTPException(status_code=422, detail=str(platform.NoPlatformCredential(
            "no platform credential is configured. Set one in Settings so "
            "product-wide conversations (the Founder) can run; a project's own "
            "billing key is never used for them.")))
    if answers is not None:
        asked = founder_agent.open_round(thread)
        if asked is None:
            raise HTTPException(status_code=409, detail=(
                "there is no open round to answer — the conversation moved on; "
                "say it in words instead"))
        known = {q["id"] for q in asked["questions"]}
        stray = sorted({a["id"] for a in answers} - known)
        if stray:
            raise HTTPException(status_code=422, detail=(
                f"no question {', '.join(stray)} in the open round"))
    founder_agent.start_turn(session, thread, message, answers)
    # After the commit, so the thread the worker loads already has the message
    # on it. `db()` commits when the request returns.
    _after_commit(session, lambda: founder_runner.submit(thread.id))


def _after_commit(session: Session, work) -> None:
    """Run `work` once this request's transaction lands.

    The worker opens its own session and would otherwise race the commit,
    reading a thread whose new message is not there yet — and then answering
    the message before it.
    """
    from sqlalchemy import event

    @event.listens_for(session, "after_commit", once=True)
    def _fire(_session):  # pragma: no cover - exercised through every say
        work()


@router.post("/threads/{thread_id}/say", dependencies=[authority.IN_HANDLER])
def say(thread_id: str, body: SayIn, session: Session = Depends(db),
        person: Person = Depends(person_auth)) -> dict:
    thread = _thread(session, thread_id, person)
    if thread.created_project_id:
        raise HTTPException(
            status_code=409,
            detail="this thread already became a project; talk to its CEO in the boardroom",
        )
    answers = [a.model_dump() for a in body.answers] if body.answers is not None else None
    if not body.message.strip() and answers is None:
        raise HTTPException(status_code=422, detail="say something, or answer the questions")
    _say(session, thread, body.message.strip(), answers)
    return _accepted(_render(thread))


@router.get("/threads/{thread_id}/progress", dependencies=[authority.IN_HANDLER])
def thread_progress(thread_id: str, session: Session = Depends(db),
                    person: Person = Depends(person_auth)) -> dict:
    """What the Founder is doing right now.

    Reads progress from no database: a turn holds its transaction open for the
    length of the turn, so anything it wrote would be invisible here until it
    committed — which is exactly when progress stops being interesting. The
    store is in-process and ephemeral, like typing state. Who owns the thread
    is a different matter, committed when it was opened, and is checked.
    """
    _thread(session, thread_id, person)
    return progress.read(thread_id)


@router.delete("/threads/{thread_id}", dependencies=[authority.IN_HANDLER])
def delete_thread(thread_id: str, session: Session = Depends(db),
                  person: Person = Depends(person_auth)) -> dict:
    session.delete(_thread(session, thread_id, person))
    return {"ok": True}


class AttachIn(BaseModel):
    project_id: str


@router.post("/threads/{thread_id}/attach", dependencies=[authority.IN_HANDLER])
def attach(thread_id: str, body: AttachIn, session: Session = Depends(db),
           person: Person = Depends(person_auth)) -> dict:
    """Record that a thread became a project, and hand the CEO the conversation.

    The chairman explained what they wanted here, argued about it, and settled
    it. Without this the product would ask them to say all of it a second time
    to a new CEO on day one, which is exactly the kind of thing the Founder
    exists to stop.

    The briefing lands in the boardroom as a chairman message, so it arrives
    through the one channel the CEO already reads and is subject to the same
    rules as anything else said there.
    """
    thread = _thread(session, thread_id, person)
    # The briefing lands as a chair's word the CEO may act on, so only somebody
    # whose word that is may put it there.
    project = authority.require(session, person, body.project_id, authority.Level.STEER)
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
        sender_user_id=person.id,
        # Trimmed to the boardroom's own ceiling rather than sent whole: a long
        # founding argument is still a boardroom message, and the limit is
        # there because a CEO has to be able to read it.
        body=briefing[:20_000],
    ))
    notify(session, WAKE_CHANNEL, project.id)
    return {"ok": True, "briefed": True}
