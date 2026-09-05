"""Asks: a question to the chairman that cannot go quiet.

The platform had two ways for a CEO to put something to its chairman, and
neither one fit a decision he had already delegated.

An approval fits when a human must act — money over the line, a credential, a
signature. It blocks, and it carries a default-deny clock. Filing one for a
decision the constitution put in the CEO's hands is refused, by name, because
it hands his own delegation back to him with a deadline attached.

What that refusal points at is the boardroom: ask him, and keep working. That
half was right. What was missing is that asking left no trace with a clock on
it — a message, and then nothing. So the failure had room to be perfectly
quiet at both ends. The CEO deferred an ask to a cadence slot, and closing the
work that produced the question emptied the board that was the only thing still
waking it; his desk, meanwhile, had no row for "your CEO is waiting on you", so
it truthfully reported that nothing was waiting.

An ask keeps the good half and fixes the rest. It never blocks. It carries the
moment the CEO stops waiting and the action it takes at that moment, both
written down before the answer is known — which is the property that makes the
whole thing safe to leave alone, because a question whose asker cannot say what
it will do unanswered is a deadlock wearing a question mark. The window closing
is a state change made by the janitor at zero tokens, and the notice it sends
is urgent: on a project that went quiet waiting, that notice is the only thing
left that can wake anybody.

Like a flag, it does not close by going quiet — it closes by the CEO acting and
saying so, with the boardroom message as proof.
"""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import Ask, AskStatus, Message, utcnow

log = logging.getLogger("yaaf.asks")

#: Statuses in which the ask is still the CEO's to finish — he has answered, or
#: the window closed and its default now stands, and either way the CEO owes a
#: boardroom message saying what it did.
OWED_STATUSES = (AskStatus.ANSWERED, AskStatus.LAPSED)


def open_asks(session: Session, project_id: str) -> list[Ask]:
    """Asked, and still waiting on him. This is what his desk draws."""
    return list(session.execute(
        select(Ask).where(Ask.project_id == project_id,
                          Ask.status == AskStatus.OPEN)
        .order_by(Ask.answer_by)
    ).scalars())


def open_count(session: Session, project_id: str) -> int:
    return int(session.execute(
        select(func.count(Ask.id)).where(Ask.project_id == project_id,
                                         Ask.status == AskStatus.OPEN)
    ).scalar_one())


def owed(session: Session, project_id: str, agent_id: str) -> list[Ask]:
    """Answered or lapsed, and not yet closed out in the boardroom.

    The CEO holding one of these has something to say that is not a status:
    either the chairman spoke and it must act on that, or the window passed and
    it must do what it said it would and tell him. The reporting cadence does
    not hold either sentence — which is enforced in `policy.check_chairman_report`
    off this list, not merely encouraged in a prompt.
    """
    return list(session.execute(
        select(Ask).where(Ask.project_id == project_id,
                          Ask.asked_by_agent_id == agent_id,
                          Ask.status.in_(OWED_STATUSES))
        .order_by(Ask.answer_by)
    ).scalars())


def mark_answered(session: Session, project_id: str, boardroom_channel_id: str) -> int:
    """Any OPEN ask the chairman has spoken after is answered.

    Deliberately mechanical: "did he reply" is a question about whether a
    message exists, not a judgement about what it said. A CEO deciding for
    itself whether it has been answered is a CEO that can decide it has not.

    One reply answers every open ask that predates it, and that is the
    intended reading rather than a shortcut: they were all above it in the
    boardroom when he wrote, so he chose what to address. The cost is that a
    question he skipped no longer reaches its window and takes its default
    automatically — but it does not go quiet either, because ANSWERED is still
    owed, and the CEO cannot post a status again until it has said what it did
    about each one. "You did not address the second, so I decided it as X" is
    the sentence that state exists to force, and it is better than a default
    taken silently against an answer he had already given.
    """
    moved = 0
    for ask in open_asks(session, project_id):
        reply = session.execute(
            select(Message).where(
                Message.channel_id == boardroom_channel_id,
                Message.sender_user_id.is_not(None),
                Message.created_at > ask.created_at,
            ).order_by(Message.created_at).limit(1)
        ).scalars().first()
        if reply is None:
            continue
        ask.status = AskStatus.ANSWERED
        ask.answered_at = reply.created_at or utcnow()
        ask.answered_in_message_id = reply.id
        moved += 1
    return moved


def asks_for_context(session: Session, project_id: str, agent_id: str) -> list[dict]:
    """What the CEO is handed every turn while an ask of its own is unfinished."""
    live = open_asks(session, project_id) + owed(session, project_id, agent_id)
    return [
        {
            "id": ask.id,
            "title": ask.title,
            "status": ask.status,
            "answer_by": ask.answer_by.isoformat() if ask.answer_by else None,
            "default_action": ask.default_action,
            "asked_in_message_id": ask.asked_in_message_id,
            "answered_in_message_id": ask.answered_in_message_id,
        }
        for ask in sorted(live, key=lambda a: a.answer_by)
    ]
