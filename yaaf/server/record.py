"""The record: everything that happened on a project, as one list a person can filter.

Activity was seven views — runs, decisions, proof, inbox, messages, memory, a
briefing — each answering "what happened?" about one kind of thing. Asking it
about one *person* ("what has Ada done this week?") or across kinds ("what
happened on Tuesday?") meant opening four of them and merging in your head.

The record merges them, newest first, one line per thing, and each line says
two things the separate views never did together:

* **who** did it — a person on the team, you, or the platform;
* **on whose authority** — your decision, a rule you set, a verb your autonomy
  dial handed the CEO, or what woke a run. An autonomous team is only
  trustworthy if every action it takes can be traced to a permission someone
  gave it, and that permission is now on the line rather than in the rulebook.

Read-only and built from rows that already exist; it adds no state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.constitution import effective
from yaaf.core.models import (
    Agent,
    AgentKind,
    Approval,
    ApprovalStatus,
    Ask,
    AskStatus,
    AuditLog,
    Channel,
    Evidence,
    Message,
    Project,
    Run,
    Task,
    Team,
    User,
    VerificationStatus,
    utcnow,
)

#: The filters the page offers, in its order. `all` is every kind.
KINDS = ("message", "run", "yours", "ceo", "rule", "proof")
#: The most lines one read returns; a CSV export may ask for this many.
MAX_LINES = 2000
#: How much of a line's free text is kept. The full text is one click away.
TEXT_CHARS = 220

#: CEO actions on the record, with the permission each one ran on. The keys are
#: audit actions; the value is a function of the effective rules so a line can
#: quote the number the chairman actually set.
_CEO_ACTIONS = {
    "team.hire": lambda c: f"the CEO staffs the project, up to {c.max_teams} teams (your rule)",
    "team.stand_down": lambda c: "the CEO staffs the project itself; you can recall on the Team page",
    "team.recall": lambda c: "the CEO staffs the project itself",
    "team.stand_down_when": lambda c: "the CEO staffs the project itself",
    "scope.self": lambda c: "your autonomy setting: the plan is the CEO's to change",
    "commitment.self_accept": lambda c: "your autonomy setting: the CEO signs the date",
    "commitment.self_move": lambda c: "your autonomy setting: the CEO signs the date",
    "credential.scope": lambda c: "your autonomy setting: the CEO scopes credentials",
    "scope.directive": lambda c: "your instruction in Chat",
    "cycle.goal_set": lambda c: "the CEO sets each cycle's goal",
}
#: The platform acting on a rule the chairman set (or a standing one).
_RULE_ACTIONS = {
    "project.auto_complete": lambda c: (
        f"your rule: a finished project completes itself after {c.complete_after_hours:g}h"),
    "review.auto_close": lambda c: "a review whose numbers are all green closes itself",
    "spend_window.stopped": lambda c: "your spend window ran out",
    "overrun.decided": lambda c: "your overrun rule",
}


@dataclass
class Line:
    at: datetime
    kind: str
    who_id: str | None
    who: str
    text: str
    authority: str | None
    run_id: str | None = None

    def as_dict(self) -> dict:
        return {"at": _aware(self.at).isoformat(), "kind": self.kind, "who_id": self.who_id,
                "who": self.who, "text": self.text, "authority": self.authority,
                "run_id": self.run_id}


def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _clip(text: str | None) -> str:
    # One line of plain words: the markdown emphasis an agent writes in Chat
    # reads as stray asterisks once it is cut down to a line.
    text = " ".join((text or "").replace("**", "").replace("__", "").replace("`", "").split())
    return text if len(text) <= TEXT_CHARS else text[:TEXT_CHARS - 1] + "…"


def _woken(trigger: dict | None) -> str:
    """What woke a run, in words, from the dispatcher's own trigger record."""
    trigger = trigger or {}
    reasons = trigger.get("reasons") or []
    kinds = [str(r.get("kind")) for r in reasons if isinstance(r, dict) and r.get("kind")]
    if not kinds and trigger.get("type"):
        kinds = [str(trigger["type"])]
    words = ", ".join(k.replace("_", " ") for k in kinds) or "scheduled work"
    return f"woken for {words}"


def project_record(session: Session, project: Project, *, kind: str = "all",
                   agent_id: str | None = None, days: int = 7,
                   limit: int = 500) -> dict:
    """The record for one project, newest first, filtered by kind and person."""
    cons = effective(session, project.id)
    since = utcnow() - timedelta(days=max(1, min(days, 90)))
    limit = max(1, min(limit, MAX_LINES))
    wanted = set(KINDS) if kind == "all" else {kind}

    agents = {a.id: a for a in session.execute(
        select(Agent).where(Agent.project_id == project.id)).scalars()}
    ceo = next((a for a in agents.values() if a.kind == AgentKind.CEO), None)
    users = {u.id: (u.display_name or u.email) for u in session.execute(select(User)).scalars()}

    def name(aid: str | None) -> str:
        return agents[aid].name if aid in agents else "an agent no longer on it"

    def mine(aid: str | None) -> bool:
        return agent_id is None or aid == agent_id

    lines: list[Line] = []

    if "run" in wanted:
        for run in session.execute(
            select(Run).where(Run.project_id == project.id, Run.created_at >= since,
                              Run.agent_id.is_not(None))
        ).scalars():
            if not mine(run.agent_id):
                continue
            cost = f" · ${run.cost_usd:.2f}" if run.cost_usd else ""
            what = _clip(run.summary) if run.summary else "ran"
            lines.append(Line(run.started_at or run.created_at, "run", run.agent_id,
                              name(run.agent_id), f"{what} · {run.status}{cost}",
                              _woken(run.trigger), run.id))

    if "message" in wanted:
        rooms = {c.id: (c.kind, c.team_id) for c in session.execute(
            select(Channel).where(Channel.project_id == project.id)).scalars()}
        teams = {t.id: t.name for t in session.execute(
            select(Team).where(Team.project_id == project.id)).scalars()}
        for m in session.execute(
            select(Message).where(Message.project_id == project.id, Message.created_at >= since)
        ).scalars():
            if agent_id is not None and m.sender_agent_id != agent_id:
                continue
            room_kind, team_id = rooms.get(m.channel_id, ("", None))
            where = "Chat" if room_kind == "boardroom" else (
                f"the {teams[team_id]} room" if team_id in teams else "a team room")
            who = name(m.sender_agent_id) if m.sender_agent_id else users.get(
                m.sender_user_id or "", "You" if m.sender_user_id else "the platform")
            lines.append(Line(m.created_at, "message", m.sender_agent_id, who,
                              f"in {where}: “{_clip(m.body)}”", None))

    approvals = list(session.execute(
        select(Approval).where(Approval.project_id == project.id,
                               Approval.decided_at.is_not(None), Approval.decided_at >= since)
    ).scalars()) if wanted & {"yours", "rule"} else []
    for a in approvals:
        if not mine(a.requested_by_agent_id):
            continue
        if a.decided_by_user_id and "yours" in wanted:
            verb = "approved" if a.status == ApprovalStatus.APPROVED else "declined"
            note = f" — “{_clip(a.decision_note)}”" if a.decision_note else ""
            lines.append(Line(a.decided_at, "yours", None, users.get(a.decided_by_user_id, "You"),
                              f"{verb} “{_clip(a.title)}” for {name(a.requested_by_agent_id)}{note}",
                              "your decision"))
        elif not a.decided_by_user_id and "rule" in wanted:
            verb = "approved" if a.status == ApprovalStatus.APPROVED else "declined"
            lines.append(Line(a.decided_at, "rule", None, "the platform",
                              f"“{_clip(a.title)}” {verb} itself — nobody answered",
                              f"your rule: silence on a decision means "
                              f"{'approve' if cons.approval_timeout_decision == 'approve' else 'decline'}"
                              f" after {cons.approval_timeout_days} days"))

    if wanted & {"yours", "rule"}:
        for q in session.execute(
            select(Ask).where(Ask.project_id == project.id,
                              (Ask.answered_at >= since) | (Ask.lapsed_at >= since))
        ).scalars():
            if not mine(q.asked_by_agent_id):
                continue
            if q.status == AskStatus.ANSWERED and q.answered_at and _aware(q.answered_at) >= since \
                    and "yours" in wanted:
                lines.append(Line(q.answered_at, "yours", None, "You",
                                  f"answered {name(q.asked_by_agent_id)}'s question “{_clip(q.title)}”",
                                  "your decision"))
            elif q.lapsed_at and _aware(q.lapsed_at) >= since and "rule" in wanted:
                lines.append(Line(q.lapsed_at, "rule", q.asked_by_agent_id, name(q.asked_by_agent_id),
                                  f"stopped waiting on “{_clip(q.title)}” and went ahead: "
                                  f"{_clip(q.default_action)}",
                                  "your silence — it said what it would do if you did not answer"))

    if wanted & {"ceo", "rule"}:
        actions = ([*(_CEO_ACTIONS if "ceo" in wanted else {}),
                    *(_RULE_ACTIONS if "rule" in wanted else {})])
        for row in session.execute(
            select(AuditLog).where(AuditLog.project_id == project.id, AuditLog.at >= since,
                                   AuditLog.action.in_(actions))
        ).scalars():
            actor = row.actor.split(":", 1)
            aid = actor[1] if actor[0] == "agent" and len(actor) > 1 else None
            if agent_id is not None and aid != agent_id:
                continue
            detail = row.detail or {}
            said = detail.get("detail") or detail.get("reason") or ""
            team = detail.get("team")
            what = row.action.split(".", 1)[1].replace("_", " ")
            text = f"{what}{f' {team}' if team else ''}{f' — “{_clip(said)}”' if said else ''}"
            if row.action in _CEO_ACTIONS:
                lines.append(Line(row.at, "ceo", aid, name(aid) if aid else (ceo.name if ceo else "the CEO"),
                                  text, _CEO_ACTIONS[row.action](cons)))
            else:
                lines.append(Line(row.at, "rule", None, "the platform", text,
                                  _RULE_ACTIONS[row.action](cons)))

    if "proof" in wanted:
        titles = {}
        for ev in session.execute(
            select(Evidence).where(Evidence.project_id == project.id, Evidence.created_at >= since)
        ).scalars():
            if not mine(ev.created_by_agent_id):
                continue
            if ev.task_id and ev.task_id not in titles:
                task = session.get(Task, ev.task_id)
                titles[ev.task_id] = task.title if task else ""
            about = f" for “{_clip(titles.get(ev.task_id))}”" if ev.task_id else ""
            checked = {VerificationStatus.VERIFIED: "checked by the platform",
                       VerificationStatus.FAILED: "the platform's check failed",
                       VerificationStatus.PENDING: "waiting on the platform's check"}.get(
                ev.verification, "the agent's own word — nothing outside can check it")
            who = name(ev.created_by_agent_id) if ev.created_by_agent_id else "a connector"
            lines.append(Line(ev.created_at, "proof", ev.created_by_agent_id, who,
                              f"filed proof{about}: {_clip(ev.description)}", checked))

    lines.sort(key=lambda line: _aware(line.at), reverse=True)
    people = [{"id": a.id, "name": a.name} for a in sorted(
        agents.values(), key=lambda a: (a.kind != AgentKind.CEO, a.name))]
    return {"since": since.isoformat(), "kinds": list(KINDS), "people": people,
            "lines": [line.as_dict() for line in lines[:limit]],
            "more": max(0, len(lines) - limit)}
