"""What needs a person, as one object every surface renders — and the rule that
silence may only decide for somebody who was told.

**One producer.** The Inbox built its rows in the browser (`web/src/desk.tsx`),
notices composed their own sentences, and the CLI and MCP counted from
`policy.needs_you`. Three renderings of one fact, held together by a test that
compared spellings. A surface that is not the dashboard — a push, a Slack card,
an email with buttons — had nothing to render at all. `items()` is that thing:
a `yaaf.needs_you/1` item per decision waiting on a person, built from rows
alone, in the same eight families and with the same keys the Inbox has always
used, and counted by the same rule as `policy.needs_you` (a test holds the two
equal). Named *needs you* rather than *decision* because the `decisions` table
is already the record of bets, and "Needs you" is what the product calls this.

**Silence counts only if you were told.** An approval decides itself, a
question takes the CEO's default, a finished delivery closes itself — fair to a
person who heard about it, and a trap for one who did not. A desktop install
with no mail configured only logged its notices; a laptop that slept through a
clock expired it on waking. So a clock may decide on a person's behalf only
once the item *reached* a decider (`reached`): a notice was dispatched to a
configured channel, or a decider loaded a list it was in. An unreached item is
given one extension and an alarm; if it is still unreached then, it resolves
in the safe direction — an approval is declined whatever the project's timeout
rule says, because "approved by a silence nobody heard" is the one outcome this
platform must never produce. One extension, never more: a chairman who is away
still degrades the project rather than deadlocking it; what changed is that
degrading now requires having been told.

Receipts are never decisions. Nothing here changes an item's state except the
janitor's clock rules, which call in from `janitor.py`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc
from yaaf.core.models import (
    Approval,
    ApprovalStatus,
    AuditLog,
    Channel,
    Hypothesis,
    Message,
    PlatformSetting,
    Project,
    ProjectStatus,
    ReachReceipt,
    utcnow,
)
from yaaf.server import acting
from yaaf.server.notify import (
    commitment_item,
    frozen_item,
    scope_item,
    signoff_item,
    verdict_item,
)
from yaaf.server.rows import approval_undo, chairman_time, requirement_name

SCHEMA = "yaaf.needs_you/1"

FAMILIES = ("approval", "ask", "scope", "commitment", "verdict", "signoff", "bet", "frozen")

#: Families whose options can be committed through the preview/commit pair.
#: A bet's extension needs a new date and a reason; a frozen thread is a
#: conversation to read before unfreezing. Both keep their page.
COMMITTABLE = frozenset({"approval", "ask", "scope", "commitment", "verdict", "signoff"})

#: How long before its deadline an item must have reached somebody for silence
#: to count: a quarter of the window, never more than six hours. A notice that
#: lands an hour before a seven-day clock runs out is not a fair warning; one
#: that lands six hours before a six-hour question's deadline cannot exist.
LEAD_CAP = timedelta(hours=6)
LEAD_FRACTION = 0.25
#: Inside an extension the bar is lower — the extension is the last call, and
#: any reach in it counts if it leaves an hour to act.
EXTENDED_LEAD_CAP = timedelta(hours=1)
#: How long an unreached item is given, once.
EXTENSION = timedelta(hours=24)

DELIVERED, SEEN, EXTENDED = "delivered", "seen", "extended"
REACHING = (DELIVERED, SEEN)


# ── reach ────────────────────────────────────────────────────────────────────


def _aware(at: datetime | None) -> datetime | None:
    if at is None:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def deciders(session: Session, project: Project) -> list[str]:
    """Who can decide what this project files: the chairman and co-chairs."""
    from yaaf.server.authority import chairs

    return chairs(session, project)


def door() -> str:
    """The channel a read came through, named from the bound actor's door."""
    actor = acting.current()
    via = actor.via if actor is not None else None
    if not via:
        return "dashboard"
    if via.startswith("mcp:"):
        return "mcp"
    if via.startswith("token:"):
        return "cli"
    return via.split(":", 1)[0][:24]


def record_delivered(session: Session, project_id: str, item_key: str,
                     user_id: str | None, channel: str) -> None:
    """A notice about `item_key` left for a configured channel."""
    session.add(ReachReceipt(id=ids.new_id(ids.REACH_RECEIPT), project_id=project_id,
                             item_key=item_key, user_id=user_id, kind=DELIVERED,
                             channel=channel[:24]))


def record_seen(session: Session, user_id: str, project: Project, keys: list[str],
                channel: str | None = None) -> None:
    """A decider loaded a list holding these items. Once per person, item and
    door; an observer's read, or an admin's read of a project they do not
    chair, is not anybody being told about a decision they could make."""
    if not keys or user_id not in deciders(session, project):
        return
    channel = (channel or door())[:24]
    have = set(session.execute(
        select(ReachReceipt.item_key).where(
            ReachReceipt.project_id == project.id, ReachReceipt.user_id == user_id,
            ReachReceipt.kind == SEEN, ReachReceipt.channel == channel,
            ReachReceipt.item_key.in_(keys))
    ).scalars())
    for key in dict.fromkeys(keys):
        if key not in have:
            session.add(ReachReceipt(id=ids.new_id(ids.REACH_RECEIPT), project_id=project.id,
                                     item_key=key, user_id=user_id, kind=SEEN,
                                     channel=channel))
    session.flush()


def _first_reach(session: Session, project: Project, keys: list[str]) -> dict[str, datetime]:
    if not keys:
        return {}
    who = deciders(session, project)
    rows = session.execute(
        select(ReachReceipt.item_key, func.min(ReachReceipt.at)).where(
            ReachReceipt.project_id == project.id, ReachReceipt.item_key.in_(keys),
            ReachReceipt.kind.in_(REACHING), ReachReceipt.user_id.in_(who))
        .group_by(ReachReceipt.item_key)
    ).all()
    return {key: _aware(at) for key, at in rows}


def _extended(session: Session, project_id: str, keys: list[str]) -> set[str]:
    if not keys:
        return set()
    return set(session.execute(
        select(ReachReceipt.item_key).where(
            ReachReceipt.project_id == project_id, ReachReceipt.item_key.in_(keys),
            ReachReceipt.kind == EXTENDED)
    ).scalars())


def lead(filed_at: datetime, deadline: datetime, *, extended: bool = False) -> timedelta:
    """How early before `deadline` a reach must land to count."""
    window = _aware(deadline) - _aware(filed_at)
    if window <= timedelta(0):
        return timedelta(0)
    return min(EXTENDED_LEAD_CAP if extended else LEAD_CAP, window * LEAD_FRACTION)


def reach_state(session: Session, project: Project, key: str, filed_at: datetime | None,
                deadline: datetime | None) -> dict:
    """`{ok, first_at, extended}` for one item."""
    first = _first_reach(session, project, [key]).get(key)
    extended = key in _extended(session, project.id, [key])
    return _state(first, extended, filed_at, deadline)


def _state(first: datetime | None, extended: bool, filed_at: datetime | None,
           deadline: datetime | None) -> dict:
    ok = first is not None
    if ok and deadline is not None and filed_at is not None:
        ok = first <= _aware(deadline) - lead(filed_at, deadline, extended=extended)
    return {"ok": ok, "first_at": first.isoformat() if first else None, "extended": extended}


def extend(session: Session, project: Project, key: str, title: str,
           new_deadline: datetime, cons: ConstitutionDoc) -> None:
    """Give an unreached item its one extension, and say so everywhere the
    platform can speak: an alarm on any channel, and the boardroom."""
    from yaaf.server.notify import Attention, boardroom_line, chairman_notify, inbox_line

    session.add(ReachReceipt(id=ids.new_id(ids.REACH_RECEIPT), project_id=project.id,
                             item_key=key, user_id=None, kind=EXTENDED, channel="platform"))
    session.add(AuditLog(id=ids.new_id(ids.AUDIT), project_id=project.id,
                         actor="system:needs_you", action="needs_you.extended",
                         entity="needs_you", entity_id=key,
                         detail={"until": new_deadline.isoformat(), "title": title}))
    when = chairman_time(new_deadline, cons.timezone)
    line = (f"yaaf could not reach you about “{title}” before it was due, so it has not "
            f"decided itself. It waits until {when}. If nobody has seen it by then it is "
            "settled the safe way: an approval is declined, a question takes the CEO's "
            "stated default.")
    boardroom_line(session, project.id, line)
    chairman_notify(session, project, f"could not reach you: {title}",
                    f"{line}\n{inbox_line(key)}", kind="needs_you_extended",
                    data={"item_key": key, "until": new_deadline.isoformat()},
                    attention=Attention.ALARM, item_key=key)


# ── the downtime pause ───────────────────────────────────────────────────────

LAST_SWEEP = "janitor.last_sweep_at"
#: A gap shorter than this is a slow sweep, not a box that was down.
DOWNTIME_FLOOR = timedelta(minutes=10)


def downtime_threshold() -> timedelta:
    from yaaf.core.config import settings

    return max(DOWNTIME_FLOOR, timedelta(seconds=10 * settings().janitor_poll_s))


def pause_for_downtime(session: Session, now: datetime | None = None) -> timedelta | None:
    """Move every open clock later by the time the box could not tell anyone
    anything, then stamp this sweep. Returns the gap when it paused.

    A laptop asleep from Friday night to Monday morning used to wake and expire
    every approval and question whose deadline fell in between, before its
    chairman had a chance to see any of them: the clocks had measured time in
    which nothing could have reached him.
    """
    now = now or utcnow()
    row = session.execute(
        select(PlatformSetting).where(PlatformSetting.key == LAST_SWEEP)
    ).scalar_one_or_none()
    gap = None
    if row is not None:
        try:
            last = _aware(datetime.fromisoformat(row.value))
        except ValueError:
            last = None
        if last is not None and now - last > downtime_threshold():
            gap = now - last
            _shift_clocks(session, gap, last)
        row.value = now.isoformat()
    else:
        session.add(PlatformSetting(id=ids.new_id(ids.PLATFORM_SETTING), key=LAST_SWEEP,
                                    value=now.isoformat()))
    session.flush()
    return gap


def _shift_clocks(session: Session, gap: timedelta, since: datetime) -> None:
    from yaaf.core.models import Ask, AskStatus

    for project in session.execute(
        select(Project).where(Project.status == ProjectStatus.RUNNING)
    ).scalars():
        moved = 0
        for approval in session.execute(
            select(Approval).where(Approval.project_id == project.id,
                                   Approval.status == ApprovalStatus.PENDING)
        ).scalars():
            approval.expires_at = _aware(approval.expires_at) + gap
            moved += 1
        for ask in session.execute(
            select(Ask).where(Ask.project_id == project.id, Ask.status == AskStatus.OPEN)
        ).scalars():
            ask.answer_by = _aware(ask.answer_by) + gap
            moved += 1
        if moved:
            session.add(AuditLog(
                id=ids.new_id(ids.AUDIT), project_id=project.id, actor="system:needs_you",
                action="clock.paused", entity="project", entity_id=project.id,
                detail={"gap_seconds": int(gap.total_seconds()), "since": since.isoformat(),
                        "clocks_moved": moved}))


# ── items ────────────────────────────────────────────────────────────────────


def _project_ref(project: Project) -> dict:
    return {"id": project.id, "slug": project.slug, "name": project.name}


def _fingerprint(material: dict) -> str:
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()


def _item(project: Project, *, key: str, family: str, kind: str, label: str, tier: int,
          headline: str, summary: str, silence: str, path: str, path_label: str,
          case: str | None = None, recommendation: str | None = None,
          amount_usd: float | None = None, filed_at: datetime | None = None,
          deadline: datetime | None = None, undo: str | None = None,
          options: list[dict] | None = None, content: dict | None = None) -> dict:
    options = options or []
    return {
        "schema": SCHEMA, "key": key, "project": _project_ref(project),
        "family": family, "kind": kind, "label": label, "tier": tier,
        "headline": headline, "summary": summary, "case": case,
        "recommendation": recommendation, "amount_usd": amount_usd,
        "filed_at": _aware(filed_at).isoformat() if filed_at else None,
        "deadline": _aware(deadline).isoformat() if deadline else None,
        "silence": silence, "undo": undo, "options": options,
        "commit": family in COMMITTABLE and bool(options),
        "path": path, "path_label": path_label,
        "open": f"/inbox?item={key}",
        # What a preview binds to: the item as the person saw it, without the
        # clock, which the platform may move (an extension, a pause) without
        # changing what is being decided.
        "version": _fingerprint({"key": key, "headline": headline, "case": case,
                                 "amount_usd": amount_usd,
                                 "options": [(o["id"], o["label"]) for o in options],
                                 **(content or {})}),
    }


def _option(id_: str, label: str, consequence: str, *, style: str = "neutral",
            irreversible: bool = False, needs_note: bool = False) -> dict:
    return {"id": id_, "label": label, "consequence": consequence, "style": style,
            "irreversible": irreversible, "needs_note": needs_note}


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _approval_options(a: Approval, if_silent: str) -> list[dict]:
    undo = approval_undo(a.kind)
    # Unknown is treated as irreversible: the platform does not vouch for an
    # undo it cannot see (rows._UNDO), so the confirmation must not either.
    irreversible = undo is None or undo.startswith("No")
    if a.kind == "assist":
        return [
            _option("approve", "Done", "The agent that asked is woken with your note.",
                    style="approve"),
            _option("decline", "I can't do this",
                    "The agent plans without it.", style="decline"),
        ]
    amount = f" ${a.amount_usd:,.0f}" if a.amount_usd else ""
    return [
        _option("approve", f"Approve{amount}",
                "The CEO goes ahead." + (f" {undo}" if undo else ""),
                style="approve", irreversible=irreversible),
        _option("decline", "Decline", "The CEO plans without it.", style="decline"),
    ]


def _recommended(option: str, recommendation: str | None) -> bool:
    rec = (recommendation or "").lower()
    o = option.lower()
    return bool(rec) and (o in rec or rec in o)


_UNSET = object()


def items(session: Session, project: Project, cons: ConstitutionDoc | None = None, *,
          awaiting: dict | None = None, signoff=_UNSET) -> list[dict]:
    """Every decision waiting on a person for this project, as items, ranked by
    tier then deadline. Same members as `policy.needs_you`, one item each —
    except proposed scope and frozen threads, which are one item per project
    exactly as they are one count."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Requirement
    from yaaf.server import asks as ask_lib
    from yaaf.server import policy

    cons = cons or effective(session, project.id)
    if awaiting is None:
        awaiting = policy.awaiting_chairman(session, project, cons)
    if signoff is _UNSET:
        signoff = policy.signoff_waiting(session, project, cons)
    pid = project.id
    tz = cons.timezone
    out: list[dict] = []

    for h in session.execute(
        select(Hypothesis).where(Hypothesis.project_id == pid, Hypothesis.status == "expired",
                                 Hypothesis.extended_once.is_not(True))
    ).scalars():
        out.append(_item(
            project, key=h.id, family="bet", kind="bet", label="Bet expired", tier=1,
            headline=f"A bet hit its kill date: “{_clip(h.statement, 80)}”",
            summary=f"“{_clip(h.statement, 160)}” hit its kill date "
                    f"{chairman_time(_aware(h.kill_date), tz)}.",
            silence="The CEO pivots. You hold the one extension.",
            path=f"/projects/{pid}/details/tasks", path_label="Decide on the Tasks page"))

    frozen = int(session.execute(
        select(func.count(Message.id)).join(Channel, Channel.id == Message.channel_id)
        .where(Channel.project_id == pid, Message.frozen_at.is_not(None))
    ).scalar_one())
    if frozen:
        out.append(_item(
            project, key=frozen_item(pid), family="frozen", kind="frozen",
            label="Thread frozen", tier=1,
            headline=(f"{frozen} conversation{' was' if frozen == 1 else 's were'} frozen — "
                      "two agents were looping"),
            summary=f"The pair breaker froze {frozen} thread(s): two agents were talking in a loop.",
            silence="Those agents stay stuck until you unfreeze the thread.",
            path=f"/projects/{pid}/details/activity/messages", path_label="Review the thread"))

    for r in awaiting["verdicts"]:
        name = requirement_name(r)
        out.append(_item(
            project, key=verdict_item(pid, r.key), family="verdict", kind="verdict",
            label="Your verdict", tier=1,
            headline=f"Is this done? “{_clip(name, 80)}”",
            summary=(f"{_clip(name, 80)} has no work left open, and whether it is done is "
                     "something no script can check."
                     + (f" What has to be true: “{_clip(r.statement, 200)}”"
                        if name != r.statement else "")),
            case=r.statement,
            silence="It stays open. Nobody on the project can mark it met.",
            path=f"/projects/{pid}/details/roadmap", path_label="Judge it on the Roadmap",
            options=[
                _option("met", "Yes, it's met", "It is marked met, on your word.",
                        style="approve"),
                _option("not_met", "Not met", "It goes back to work, with your note as the "
                        "brief.", style="decline", needs_note=True),
            ],
            content={"requirement": r.id, "statement": r.statement}))

    if awaiting["plan"]:
        proposed: list[Requirement] = list(awaiting["plan"])
        n = len(proposed)
        s = "" if n == 1 else "s"
        out.append(_item(
            project, key=scope_item(pid), family="scope", kind="scope",
            label="Proposed scope", tier=1,
            headline=f"The CEO proposed {n} requirement{s} for you to accept",
            summary=f"The CEO proposed {n} requirement{s} — what “done” should mean — and is "
                    "waiting on you.",
            case="\n".join(f"{r.key}: {requirement_name(r)}" for r in proposed),
            silence="Proposed scope is not scope: no work can be built against it.",
            path=f"/projects/{pid}/details/roadmap", path_label="Reword or cut on the Roadmap",
            options=[_option("accept", f"Accept {'all ' if n > 1 else ''}{n} outcome{s}",
                             "They become scope and work can be built against them. You can "
                             "cut one later.", style="approve")],
            content={"requirements": sorted(r.id for r in proposed)}))

    commitment = awaiting["commitment"]
    if commitment is not None:
        n = len(commitment.scope_snapshot or [])
        s = "" if n == 1 else "s"
        when = chairman_time(_aware(commitment.deliver_by), tz)
        out.append(_item(
            project, key=commitment_item(pid), family="commitment", kind="commitment",
            label="Sign the date", tier=2,
            headline=f"Sign a delivery date: {n} requirement{s} by {when}",
            summary=f"The CEO proposes delivering {n} requirement{s} by {when}.",
            silence="The project keeps working, but it is promising nothing until you sign.",
            path=f"/projects/{pid}/details/roadmap", path_label="Sign or move it on the Roadmap",
            options=[
                _option("accept", f"Sign {when}", "The date becomes a promise the platform "
                        "holds the project to.", style="approve"),
                _option("decline", "Decline the date", "The proposal is withdrawn, with your "
                        "reason in front of the CEO.", style="decline", needs_note=True),
            ],
            content={"commitment": commitment.id,
                     "deliver_by": _aware(commitment.deliver_by).isoformat()}))

    if signoff:
        closes = datetime.fromisoformat(signoff["closes_at"]) if signoff.get("closes_at") else None
        met = signoff["met"]
        s = "" if met == 1 else "s"
        out.append(_item(
            project, key=signoff_item(pid), family="signoff", kind="signoff",
            label="Sign off", tier=2,
            headline=f"Finished: {met} requirement{s} true — sign it off",
            summary=f"Everything in scope is true ({met}). Completing writes the receipt and "
                    "closes the project.",
            deadline=closes,
            silence=(f"It completes itself {chairman_time(closes, tz)} unless its scope moves. "
                     "You can reopen it for 14 days." if closes else
                     "It waits, spending nothing, until you complete or reopen it."),
            path=f"/projects/{pid}/results", path_label="See what was delivered",
            options=[_option("complete", "Complete & write receipt",
                             "Closes the project and writes the receipt. You can reopen it "
                             "for 14 days.", style="approve")]))

    if_silent = "approved" if cons.approval_timeout_decision == "approve" else "declined"
    for a in session.execute(
        select(Approval).where(Approval.project_id == pid,
                               Approval.status == ApprovalStatus.PENDING)
        .order_by(Approval.expires_at)
    ).scalars():
        out.append(_item(
            project, key=a.id, family="approval", kind=a.kind,
            label="For you to do" if a.kind == "assist"
            else f"Decision · {a.kind.replace('_', ' ')}",
            tier=2, headline=a.title, summary=a.title, case=a.body,
            recommendation=a.recommendation, amount_usd=a.amount_usd,
            filed_at=a.created_at, deadline=a.expires_at,
            silence=(f"It is {if_silent} {chairman_time(_aware(a.expires_at), tz)}. "
                     "Silence is a decision here."),
            undo=approval_undo(a.kind),
            path=f"/projects/{pid}/details/activity/decisions",
            path_label="Read the full request",
            options=_approval_options(a, if_silent),
            content={"kind": a.kind, "brief": a.brief, "capacity": a.capacity}))

    for ask in ask_lib.open_asks(session, pid):
        choices = list(ask.options or [])
        opts = [_option(f"option:{i}", o, "Sent to the CEO in the chat as your answer.",
                        style="approve" if _recommended(o, ask.recommendation) else "neutral")
                for i, o in enumerate(choices)]
        opts.append(_option("reply", "Answer in your own words",
                            "Sent to the CEO in the chat as your answer.", needs_note=True))
        out.append(_item(
            project, key=ask.id, family="ask", kind="ask", label="Question", tier=2,
            headline=ask.title, summary=ask.title, case=ask.body,
            recommendation=ask.recommendation, filed_at=ask.created_at,
            deadline=ask.answer_by,
            silence=(f"It stops waiting {chairman_time(_aware(ask.answer_by), tz)}, then: "
                     f"{_clip(ask.default_action, 160)}"),
            path=(f"/inbox?item={ask.id}" if choices else f"/projects/{pid}/chat"),
            path_label="Answer" if choices else "Answer in the chat",
            options=opts, content={"default_action": ask.default_action}))

    keys = [i["key"] for i in out]
    first = _first_reach(session, project, keys)
    extended = _extended(session, pid, keys)
    for i in out:
        filed = datetime.fromisoformat(i["filed_at"]) if i["filed_at"] else None
        due = datetime.fromisoformat(i["deadline"]) if i["deadline"] else None
        i["reached"] = _state(first.get(i["key"]), i["key"] in extended, filed, due)

    never = "9999-12-31T00:00:00+00:00"
    order = {"signoff": 1, "commitment": 2}
    out.sort(key=lambda i: (i["tier"], i["deadline"] or never, order.get(i["family"], 0)))
    return out


# ── the person's own numbers ─────────────────────────────────────────────────


def stats(session: Session, user_id: str, is_admin: bool, days: int = 30) -> dict:
    """How deciding is going for one person, from rows alone, over `days`.

    Local by construction: read on their own Account page, sent nowhere. It is
    the instrument this design could not find anywhere when it was written —
    how many decisions a project actually asks for, how long they wait, and how
    many were taken by silence — and it is what the attention work in later
    phases is tuned against.
    """
    from statistics import median

    from yaaf.core.models import Ask, AskStatus
    from yaaf.server import authority

    since = utcnow() - timedelta(days=days)
    projects = [p for p in authority.reachable(session, user_id, is_admin)
                if user_id in deciders(session, p)]
    pids = [p.id for p in projects]
    out = {"days": days, "projects": len(pids),
           "filed": {"approvals": 0, "asks": 0, "per_week": 0.0},
           "decided_by_you": 0, "median_minutes_to_decide": None,
           "by_silence": {"approvals": 0, "asks": 0},
           "extended_unreached": 0, "resolved_unreached": 0}
    if not pids:
        return out

    approvals = list(session.execute(
        select(Approval).where(Approval.project_id.in_(pids), Approval.created_at >= since)
    ).scalars())
    asks = list(session.execute(
        select(Ask).where(Ask.project_id.in_(pids), Ask.created_at >= since)).scalars())
    out["filed"] = {"approvals": len(approvals), "asks": len(asks),
                    "per_week": round((len(approvals) + len(asks)) / (days / 7), 1)}

    waits: list[float] = []
    for a in approvals:
        if a.decided_at is None:
            continue
        if a.decided_by_user_id == user_id:
            waits.append((_aware(a.decided_at) - _aware(a.created_at)).total_seconds() / 60)
        elif a.decided_by_user_id is None and (a.decision_note or "").startswith("expired"):
            out["by_silence"]["approvals"] += 1
    replied = {m.id: m.sender_user_id for m in session.execute(
        select(Message).where(Message.id.in_(
            [a.answered_in_message_id for a in asks if a.answered_in_message_id]))
    ).scalars()}
    for ask in asks:
        if ask.answered_at is not None and replied.get(ask.answered_in_message_id) == user_id:
            waits.append((_aware(ask.answered_at) - _aware(ask.created_at)).total_seconds() / 60)
        elif ask.status == AskStatus.LAPSED and ask.answered_at is None:
            out["by_silence"]["asks"] += 1
    out["decided_by_you"] = len(waits)
    out["median_minutes_to_decide"] = round(median(waits)) if waits else None
    out["extended_unreached"] = int(session.execute(
        select(func.count(ReachReceipt.id)).where(
            ReachReceipt.project_id.in_(pids), ReachReceipt.kind == EXTENDED,
            ReachReceipt.at >= since)).scalar_one())
    out["resolved_unreached"] = int(session.execute(
        select(func.count(AuditLog.id)).where(
            AuditLog.project_id.in_(pids), AuditLog.action == "needs_you.unreached",
            AuditLog.at >= since)).scalar_one())
    return out
