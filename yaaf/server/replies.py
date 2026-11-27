"""Which message a new line answers, so a reply is filed as one.

27 November 2026: every conversation on the Messages page read "no replies".
The agents did answer each other, but as new lines in the room ("Sasha: all
five of your ADR points are in…"), and the page groups a conversation by its
thread. 7 of 526 lines that week carried one. The chairman's answers in the
boardroom were the same: "Re: “Where should the live Snip app run?” — Fly.io"
sat as a line of its own under nothing.

So the platform files the reply where the record says it belongs:

* an agent's line in a team or leadership room goes under the latest line
  addressed to it there since it last spoke, unless it is plainly to someone
  else or to everyone;
* the chairman's answer to a question goes under the question;
* his note on a decision goes under the line that announced the decision;
* the CEO's answer to his lines goes under the line it answers.

A thread named by the poster is checked rather than trusted: it must be in the
same room, and a reply's id stands for its root.
"""

from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Agent,
    Approval,
    Ask,
    Channel,
    Flag,
    HeadsUp,
    Message,
    utcnow,
)

#: A line older than this is not what a new one answers.
ANSWER_WINDOW = timedelta(hours=12)

#: A line to the whole room is a new conversation, never a reply to one seat.
#: "CEO to leads." went under a reply Quinn had made to the CEO's last one.
_TO_EVERYONE = re.compile(
    r"\b(?:all (?:leads|seats|of you|teams?)|everyone|everybody|whole team"
    r"|to (?:the )?(?:leads|team|teams|room|all))\b",
    re.IGNORECASE)

#: How the chairman's answer to a question begins (Inbox, Boardroom, CLI desk).
_RE_LINE = re.compile(r"^\s*Re: “(?P<head>.+?)” — ", re.DOTALL)


class ThreadError(ValueError):
    """A thread the poster named that this line cannot go under."""


def root_of(session: Session, project_id: str, channel_id: str,
            thread_id: str | None) -> str | None:
    """The root a named thread stands for, checked: same project, same room."""
    if not thread_id:
        return None
    named = session.get(Message, thread_id)
    if named is None or named.project_id != project_id:
        raise ThreadError(f"{thread_id!r} is not a message on this project.")
    if named.channel_id != channel_id:
        raise ThreadError(
            f"{thread_id!r} is in another room. Reply in the room it was said in, "
            "or leave thread_id out to start a new conversation here.")
    return named.thread_id or named.id


def answering(session: Session, agent: Agent, channel: Channel, body: str) -> Message | None:
    """The line an agent's new, unthreaded line answers, if it answers one.

    The latest line in the room addressed to this agent (it names it, or it is
    in a thread the agent is in) since the agent last spoke there. Not when the
    new line is to the whole room, nor when it names other seats and not the
    one who asked."""
    from yaaf.server.dispatcher import _addressed_to

    if _TO_EVERYONE.search(body or ""):
        return None
    floor = utcnow() - ANSWER_WINDOW
    spoke = session.execute(
        select(Message.created_at).where(Message.channel_id == channel.id,
                                         Message.sender_agent_id == agent.id)
        .order_by(Message.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if spoke is not None and spoke > floor:
        floor = spoke
    recent = session.execute(
        select(Message).where(Message.channel_id == channel.id,
                              Message.created_at > floor,
                              Message.sender_agent_id.is_not(None),
                              Message.sender_agent_id != agent.id)
        .order_by(Message.created_at.desc()).limit(20)
    ).scalars()
    asked = next((m for m in recent if _addressed_to(session, agent, m)), None)
    if asked is None:
        return None
    root = session.get(Message, asked.thread_id or asked.id)
    if root is None or root.frozen_at is not None:
        return None
    names = {a.id: a.name for a in session.execute(
        select(Agent).where(Agent.project_id == agent.project_id)).scalars()}
    named = {aid for aid, n in names.items()
             if aid != agent.id and re.search(rf"(?<![\w]){re.escape(n)}\b", body, re.IGNORECASE)}
    if named and asked.sender_agent_id not in named:
        return None
    return asked


def question_for_note(session: Session, source_kind: str, source_id: str) -> str | None:
    """The boardroom line that put the decision a note is written on to him."""
    found = None
    if source_kind == "approval":
        row = session.get(Approval, source_id)
        found = row.announced_in_message_id if row is not None else None
    elif source_kind == "flag":
        row = session.get(Flag, source_id)
        found = row.raised_in_message_id if row is not None else None
    elif source_kind == "heads_up":
        row = session.get(HeadsUp, source_id)
        found = row.message_id if row is not None else None
    if found is None:
        return None
    message = session.get(Message, found)
    return (message.thread_id or message.id) if message is not None else None


def question_answered(session: Session, project_id: str, body: str) -> str | None:
    """The question a "Re: “<its title>” — …" line answers: the CEO's ask by
    that title, newest first. Matched on the title, which the Inbox, the
    Boardroom and the CLI all quote whole."""
    said = _RE_LINE.match(body or "")
    if said is None:
        return None
    head = said.group("head").strip().rstrip("…").strip()
    if not head:
        return None
    for ask in session.execute(
        select(Ask).where(Ask.project_id == project_id).order_by(Ask.created_at.desc()).limit(50)
    ).scalars():
        title = (ask.title or "").strip()
        if title == head or (said.group("head").endswith("…") and title.startswith(head)):
            message = session.get(Message, ask.asked_in_message_id)
            if message is not None and message.project_id == project_id:
                return message.thread_id or message.id
    return None


def answered_line(session: Session, project_id: str, channel_id: str,
                  answers: list[str]) -> str | None:
    """The root of the newest of the chairman's lines a CEO reply was written to."""
    lines = [m for m in (session.get(Message, i) for i in answers)
             if m is not None and m.project_id == project_id and m.channel_id == channel_id]
    if not lines:
        return None
    newest = max(lines, key=lambda m: m.created_at)
    return newest.thread_id or newest.id
