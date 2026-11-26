"""What the agents said to each other, as conversations rather than rows.

The Messages page was a table — When, From, Message — per room. Replies, who
was talking to whom, and whether anything came of it all read the same, so a
handoff nobody picked up ("the report is yours now") sat in the list looking
exactly like "On it." This turns a room's messages into conversations (a
thread root and its replies) and says how each one ended, in words worked out
from the record, never from what an agent claims:

  frozen    the pair breaker fired on it
  handoff   a task came out of it (named in it, or made by someone in it while
            it was going on)
  answered  somebody other than the one who started it replied
  stuck     it hands something over, nobody replied, nothing was made, and it
            is older than HANDOFF_GRACE
  waiting   nobody has replied yet
  note      in the Boardroom, nothing that needs a reply (his own chat)

"Needs attention" is waiting, stuck and frozen.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Agent,
    AgentKind,
    AgentStatus,
    Artifact,
    Channel,
    ChannelKind,
    ChannelRead,
    Message,
    Project,
    Requirement,
    Run,
    Task,
    Team,
    utcnow,
)
from yaaf.server import policy

#: How long a handoff may sit with no reply and nothing made before it is
#: said to have been missed rather than merely not yet read.
HANDOFF_GRACE = timedelta(hours=2)

#: A task made by someone in the conversation this long after its last word
#: is still counted as coming out of it.
TASK_WINDOW = timedelta(hours=2)

#: Phrases that hand work to somebody else. Deliberately narrow: a false
#: "handoff missed" on ordinary chatter would teach him to ignore the tag.
_HANDOFF = re.compile(
    r"\b(over to you|yours (?:now|to take)|your (?:team'?s|turn) now|is yours\b|are yours\b|"
    r"hand(?:ing|ed)? (?:it |this |them )?(?:off|over)|ready for you|you can (?:start|pick)|"
    r"pick (?:it|this|them) up|take it from here)",
    re.IGNORECASE,
)
_TASK_ID = re.compile(r"\btask_[0-9a-z]{8,}\b")
_REQ_KEY = re.compile(r"\bR-\d{2,3}\b")
_MENTION = re.compile(r"@([A-Za-z][\w-]{1,40})")


def _aware(dt: datetime | None) -> datetime | None:
    from datetime import UTC

    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


def _title(body: str) -> str:
    """The first sentence, as the conversation's subject."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)  # [label](link) -> label
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"[#*_`>]", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    first = re.split(r"(?<=[.?!])\s", text, maxsplit=1)[0]
    return first if len(first) <= 90 else first[:87].rstrip() + "…"


def view(session: Session, project: Project, *, days: int = 14) -> dict:
    since = utcnow() - timedelta(days=days)
    agents = {a.id: a for a in session.execute(
        select(Agent).where(Agent.project_id == project.id)).scalars()}
    teams = {t.id: t.name for t in session.execute(
        select(Team).where(Team.project_id == project.id)).scalars()}
    leads = {a.id for a in agents.values() if a.kind != AgentKind.CEO and policy.is_lead(session, a)}

    def role(a: Agent) -> str:
        if a.kind == AgentKind.CEO:
            return "CEO"
        team = teams.get(a.team_id) or "Team"
        return f"{team} lead" if a.id in leads else team

    people = {aid: {"id": aid, "name": a.name, "role": role(a)} for aid, a in agents.items()}

    channels = list(session.execute(
        select(Channel).where(Channel.project_id == project.id)).scalars())
    order = {ChannelKind.TEAM: 0, ChannelKind.LEADERSHIP: 1, ChannelKind.BOARDROOM: 2}
    channels.sort(key=lambda c: (order.get(c.kind, 3), teams.get(c.team_id) or ""))
    members: dict[str, list[str]] = defaultdict(list)
    # Active seats only: one stood down will never read again, and listing it
    # under "not read yet" forever would be a wait nobody can end.
    for a in agents.values():
        if a.status == AgentStatus.ACTIVE:
            for cid in policy.agent_channel_ids(session, a):
                members[cid].append(a.id)

    cursors = {(r.agent_id, r.channel_id): _aware(r.last_read_at) for r in session.execute(
        select(ChannelRead).where(ChannelRead.channel_id.in_([c.id for c in channels] or [""]))).scalars()}
    last_run = {k: _aware(v) for k, v in session.execute(
        select(Run.agent_id, func.max(Run.created_at)).where(Run.project_id == project.id,
                                                             Run.agent_id.is_not(None))
        .group_by(Run.agent_id)).all()}

    reqs = {r.key: r for r in session.execute(
        select(Requirement).where(Requirement.project_id == project.id)).scalars()}
    files = {a.name for a in session.execute(
        select(Artifact).where(Artifact.project_id == project.id)).scalars()}
    tasks_by_creator: dict[str, list[Task]] = defaultdict(list)
    for t in session.execute(select(Task).where(Task.project_id == project.id,
                                                Task.created_at >= since)).scalars():
        tasks_by_creator[t.created_by_agent_id].append(t)

    rows = list(session.execute(
        select(Message).where(Message.project_id == project.id, Message.created_at >= since)
        .order_by(Message.created_at, Message.id)).scalars())
    # A reply whose root is older than the window still belongs with its root.
    roots_needed = {m.thread_id for m in rows if m.thread_id} - {m.id for m in rows}
    for root in session.execute(select(Message).where(Message.id.in_(roots_needed or {""}))).scalars():
        rows.insert(0, root)
    threads: dict[str, list[Message]] = defaultdict(list)
    by_channel: dict[str, list[Message]] = defaultdict(list)
    for m in rows:
        threads[m.thread_id or m.id].append(m)
        by_channel[m.channel_id].append(m)

    room_name = {c.id: ("Boardroom" if c.kind == ChannelKind.BOARDROOM
                        else "Leadership" if c.kind == ChannelKind.LEADERSHIP
                        else teams.get(c.team_id) or "Team") for c in channels}
    now = utcnow()
    convs: list[dict] = []
    for root_id, msgs in threads.items():
        msgs.sort(key=lambda m: (_aware(m.created_at), m.id))
        root = msgs[0]
        if root.id != root_id:  # the root itself was deleted or is elsewhere
            continue
        cid = root.channel_id
        boardroom = room_name.get(cid) == "Boardroom"
        # A message with no sender is a line the platform posted to him.
        speakers = [m.sender_agent_id or ("you" if m.sender_user_id else "yaaf") for m in msgs]
        starter = speakers[0]
        repliers = [s for s in speakers[1:] if s and s != starter]
        start, last = _aware(root.created_at), _aware(msgs[-1].created_at)

        # Who has not read the last word, among the room's agents who did not say it.
        unread, read = [], []
        if not boardroom:
            for aid in members.get(cid, []):
                if aid == msgs[-1].sender_agent_id:
                    continue
                cursor = cursors.get((aid, cid))
                (read if cursor is not None and cursor >= last else unread).append(aid)

        # What it names, and what came out of it.
        text = "\n".join(m.body for m in msgs)
        named_tasks = [t for t in session.execute(select(Task).where(
            Task.id.in_(set(_TASK_ID.findall(text)) or {""}))).scalars() if t.project_id == project.id]
        made = [t for s in {s for s in speakers if s not in ("you", "yaaf")}
                for t in tasks_by_creator.get(s, [])
                if start <= _aware(t.created_at) <= last + TASK_WINDOW]
        out_tasks = list({t.id: t for t in named_tasks + made}.values())
        refs = ([{"kind": "task", "id": t.id, "label": t.title} for t in out_tasks]
                + [{"kind": "goal", "id": k, "label": reqs[k].statement}
                   for k in dict.fromkeys(_REQ_KEY.findall(text)) if k in reqs]
                + [{"kind": "file", "id": f, "label": f} for f in sorted(files) if f in text][:3])

        handoff_said = bool(_HANDOFF.search(root.body))
        if boardroom:
            # His own room, which he reads as a chat: the CEO answers there in
            # the open rather than in a thread, and he sees it as it happens.
            # Nothing in it is a conversation between agents left hanging.
            ceo = project_ceo(agents)
            answered = bool(repliers) or (starter == "you" and any(
                m.sender_agent_id == ceo and _aware(m.created_at) > start
                for m in by_channel.get(cid, [])))
            outcome = "answered" if answered else "note"
        elif root.frozen_at is not None:
            outcome = "frozen"
        elif out_tasks:
            outcome = "handoff"
        elif repliers:
            outcome = "answered"
        elif handoff_said and now - last > HANDOFF_GRACE and not boardroom:
            outcome = "stuck"
        else:
            outcome = "waiting"

        def name(s: str | None) -> str:
            return "You" if s == "you" else people.get(s, {}).get("name", "Someone")

        mentions = [w for w in _MENTION.findall(root.body)
                    if any(p["name"].lower() == w.lower() for p in people.values())]
        to = (mentions[0] if mentions
              else "Everyone" if room_name.get(cid) == "Leadership" and starter == project_ceo(agents)
              else room_name.get(cid))
        convs.append({
            "id": root.id,
            "room": cid,
            "title": _title(root.body),
            "outcome": outcome,
            "started_by": starter,
            "people": list(dict.fromkeys(s for s in speakers if s)),
            "replies": len(msgs) - 1,
            "started_at": start.isoformat(),
            "last_at": last.isoformat(),
            "frozen": root.frozen_at is not None,
            "to": to,
            "unread": [name(a) for a in unread],
            "unread_ids": unread,
            "read": [name(a) for a in read],
            "asleep": [name(a) for a in unread if (last_run.get(a) is None or last_run[a] < last)],
            "refs": refs,
            "messages": [{
                "id": m.id,
                "from": speakers[i],
                "body": m.body,
                "at": _aware(m.created_at).isoformat(),
            } for i, m in enumerate(msgs)],
        })
    convs.sort(key=lambda c: c["last_at"], reverse=True)

    attention = {"waiting", "stuck", "frozen"}
    rooms = []
    for c in channels:
        mine = [v for v in convs if v["room"] == c.id]
        rooms.append({
            "id": c.id, "kind": c.kind, "name": room_name[c.id],
            "members": ["you", *members.get(c.id, [])] if c.kind == ChannelKind.BOARDROOM
            else members.get(c.id, []),
            "last_at": mine[0]["last_at"] if mine else None,
            "open": sum(1 for v in mine if v["outcome"] in attention),
        })
    people["you"] = {"id": "you", "name": "You", "role": "Chairman"}
    people["yaaf"] = {"id": "yaaf", "name": "yaaf", "role": "Platform"}
    by_agent = unread_by_agent(session, project, agents, people)
    # An agent in the middle of a run has woken up: it is reading now.
    busy = {a["name"] for a in by_agent if a["busy"]}
    for c in convs:
        c["asleep"] = [n for n in c["asleep"] if n not in busy]
    return {"days": days, "rooms": rooms, "people": people, "conversations": convs,
            "agents": by_agent}


#: Unread mail this old, on an agent nothing is holding, means the wake that
#: should have read it did not happen.
OVERDUE = timedelta(minutes=30)


def unread_by_agent(session: Session, project: Project, agents: dict[str, Agent],
                    people: dict[str, dict]) -> list[dict]:
    """Each agent's unread count, and — the question the chairman actually
    asks — why it has not read them yet, in his words. The answer is the
    dispatcher's own state (`activity_state`), the same gates that decide the
    wake, so the sentence cannot disagree with what happens."""
    from yaaf.core.constitution import effective
    from yaaf.server import dispatcher

    cons = effective(session, project.id)
    state = dispatcher.activity_state(session, project, cons)
    rows = {r["id"]: r for r in state["agents"]}
    blocked = state["blocked_by"]
    delivered = blocked == "delivery_ready"
    now = utcnow()
    out = []
    for aid, agent in agents.items():
        unread = dispatcher.unread_messages_for(session, project, agent) \
            if agent.status == AgentStatus.ACTIVE else []
        if not unread:
            continue
        oldest = min(_aware(m.created_at) for m, _ in unread)
        row = rows.get(aid, {})
        fault = False
        if project.status != "running":
            why = "The project is paused, so nobody wakes up."
        elif row.get("busy"):
            why = "It's working right now and reads them when this run ends."
        elif dispatcher.reading_off(cons):
            why = ("This project doesn't wake agents just to read. It reads them the next time "
                   "it wakes up for work.")
        elif delivered:
            why = "The project is finished, so only your messages wake agents now."
        elif blocked in ("daily_runs", "governance_reserve"):
            why = "Today's runs are used up. It reads them after the daily reset."
        elif blocked == "pacing":
            why = "The project's hourly limit is reached. It reads them within the hour."
        elif blocked == "concurrency":
            why = "Every run slot is busy. It reads them when one frees up."
        elif row.get("wake_slot_frees_at"):
            why = "It has used its runs for this hour and reads them within the hour."
        elif row.get("batching_until"):
            why = "It waits a few minutes to read several messages at once."
        elif now - oldest > OVERDUE:
            why = "Nothing is holding it, so it should have woken up by now. This looks like a fault."
            fault = True
        else:
            why = "It reads them on its next wake, within a few minutes."
        out.append({**people[aid], "unread": len(unread), "oldest_at": oldest.isoformat(),
                    "why": why, "fault": fault, "busy": bool(row.get("busy"))})
    out.sort(key=lambda a: (-a["unread"], a["name"]))
    return out


def project_ceo(agents: dict[str, Agent]) -> str | None:
    return next((a.id for a in agents.values() if a.kind == AgentKind.CEO), None)
