"""The chairman's notes: what he wrote on a decision, held until the CEO answers.

Every decision on his desk has a text box. What he typed used to ride the
CEO's wake notice once and go nowhere: read on the next run, then gone. Sixty-
four decisions carried a note, and one — on a credential request — read "yes,
please go ahead, every day". That is a standing order, and no prompt ever
carried it again.

So a note is a row. The CEO sees every unanswered one on every run, and answers
it in one line, saying whether it applied to that one item or beyond it. Beyond
it means the brief: an answer that calls the note standing is refused unless a
brief written after the note exists, pending his confirm or confirmed. That is
how "every day" stops being a sentence in a notice and becomes an order every
agent reads first.

A note is also his line in the boardroom. This table alone was a side door:
the chat session never read it, and nothing read it once a work run had
answered, so the CEO he was talking to told him it was still waiting on a reply
he had sent two hours earlier — and then that it had "missed" it. The boardroom
is the one record every CEO lane reads, from any run and after any restart, so
whatever he writes on a decision is said there too, the way an answer to an ask
already was.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    ChairmanNote,
    Channel,
    ChannelKind,
    Message,
    Project,
    utcnow,
)

#: How many unanswered notes the CEO's pack carries. More than this is a backlog
#: the oldest of which it should already have answered.
PACK_LIMIT = 10


#: How much of what the note was written on heads its boardroom line.
ABOUT_IN_CHAT = 140


def record(session: Session, project_id: str, source_kind: str, source_id: str,
           about: str, body: str | None, *, user_id: str) -> ChairmanNote | None:
    """Hold his note for an answer, and say it in the boardroom as his.

    An empty note is no note. `user_id` is whoever wrote it — required, so a
    new door cannot record a note without saying who said it in the room."""
    text = (body or "").strip()
    if not text:
        return None
    row = ChairmanNote(id=ids.new_id(ids.CHAIRMAN_NOTE), project_id=project_id,
                       source_kind=source_kind, source_id=source_id,
                       about=about[:400], body=text)
    message = _say_in_boardroom(session, project_id, user_id, about, text)
    if message is not None:
        row.message_id = message.id
    session.add(row)
    return row


def chat_line(about: str, text: str) -> str:
    """The same shape an answer to an ask takes from the Inbox: what it answers,
    then what he said."""
    head = about if len(about) <= ABOUT_IN_CHAT else about[:ABOUT_IN_CHAT - 1] + "…"
    return f"Re: “{head}” — {text}"


def _say_in_boardroom(session: Session, project_id: str, user_id: str,
                      about: str, text: str) -> Message | None:
    from yaaf.server import unread

    boardroom = session.execute(
        select(Channel).where(Channel.project_id == project_id,
                              Channel.kind == ChannelKind.BOARDROOM)
    ).scalars().first()
    if boardroom is None:
        return None
    message = Message(id=ids.new_id(ids.MESSAGE), project_id=project_id,
                      channel_id=boardroom.id, sender_user_id=user_id,
                      body=chat_line(about, text), created_at=utcnow())
    # No wake of its own: the chat session polls the room every two seconds,
    # and with no session live the dispatcher's next tick sees his line.
    session.add(message)
    project = session.get(Project, project_id)
    if project is not None:
        # Whoever speaks has read the room up to what they said.
        unread.mark_boardroom_read(session, project, message.created_at)
    return message


def unanswered(session: Session, project_id: str) -> list[ChairmanNote]:
    return list(session.execute(
        select(ChairmanNote)
        .where(ChairmanNote.project_id == project_id, ChairmanNote.answered_at.is_(None))
        .order_by(ChairmanNote.created_at).limit(PACK_LIMIT)
    ).scalars())


class NoteError(ValueError):
    """An answer that cannot be taken, in words the CEO can act on."""


def answer(session: Session, project_id: str, note_id: str, reply: str,
           standing: bool) -> ChairmanNote:
    note = session.get(ChairmanNote, note_id)
    if note is None or note.project_id != project_id:
        raise NoteError(f"{note_id!r} is not one of the chairman's notes on this project.")
    if note.answered_at is not None:
        raise NoteError(f"You already answered that note: {note.reply}")
    if standing and not _brief_since(session, project_id, note):
        raise NoteError(
            "You called this note standing — it applies beyond the one item it was "
            "written on. Then it belongs in the brief, not in your memory: call "
            "brief_propose with it folded into the standing orders (the whole brief, "
            "his words and their numbers), then answer this note again. If it was "
            "about that one item only, answer with standing=false.")
    note.reply = reply.strip()
    note.standing = standing
    note.answered_at = utcnow()
    return note


def _brief_since(session: Session, project_id: str, note: ChairmanNote) -> bool:
    return session.execute(
        select(Approval.id).where(
            Approval.project_id == project_id, Approval.kind == ApprovalKind.BRIEF,
            Approval.status.in_([ApprovalStatus.PENDING, ApprovalStatus.APPROVED]),
            Approval.created_at >= note.created_at)
    ).first() is not None


def replies_for(session: Session, source_kind: str, ids_: list[str]) -> dict[str, dict]:
    """The CEO's answer to his note, by the item it was written on."""
    if not ids_:
        return {}
    rows = session.execute(
        select(ChairmanNote).where(ChairmanNote.source_kind == source_kind,
                                   ChairmanNote.source_id.in_(ids_))
    ).scalars()
    return {n.source_id: row(n) for n in rows}


def row(n: ChairmanNote) -> dict:
    return {"id": n.id, "about": n.about, "note": n.body,
            "created_at": n.created_at.isoformat(),
            "reply": n.reply, "standing": n.standing,
            "answered_at": n.answered_at.isoformat() if n.answered_at else None}


def for_context(session: Session, project_id: str) -> dict | None:
    notes = unanswered(session, project_id)
    if not notes:
        return None
    return {
        "note": (
            "The chairman wrote these on decisions he made. Each one stays here until "
            "you answer it with note_answer — one line he reads under his note. Decide "
            "first whether it applies to that one item (standing=false) or from now on "
            "(standing=true). From now on means the brief: fold it in with "
            "brief_propose, then answer. Do not file it as a memory. Each is also his "
            "line in the boardroom (boardroom_message_id), so whoever answers him "
            "there has it in front of them: a reply in the boardroom is what he "
            "reads now, and note_answer is still what closes it here."),
        "notes": [{"id": n.id, "on": n.about, "he_wrote": n.body,
                   "at": n.created_at.isoformat(),
                   **({"boardroom_message_id": n.message_id} if n.message_id else {})}
                  for n in notes],
    }
