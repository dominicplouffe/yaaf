"""The brief: what the chairman wants now, confirmed by him.

A project's direction used to live in one place that every agent read and
nobody changed — the goal, written the day it was founded — and every later
"actually, I've changed my mind" went into a place nobody was made to read: a
memory, a workspace file, a handoff note. So the original spirit always came
back. Two desks showed it: a blog told to stop being an HN editorial that went
on ranking topics by HN points, and a trading desk whose deleted stop cut an
order five weeks after it was deleted, "from the original project settings".

The brief has three parts, pushed together to every agent on every run:

* the goal, in plain words, as it stands today;
* standing orders, one sentence each with its value;
* retired orders: what he has taken back, so an agent that finds the old rule
  in a file or a memory knows it is dead.

The CEO writes it back to him (``brief_propose``) whenever he changes
direction; his Confirm is what applies it. He can also edit it himself in
Settings. Either way it replaces — nothing is appended next to the old text.
"""

from __future__ import annotations

import difflib
import hashlib
import json

from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import AuditLog, Notice, NoticeKind, Project, utcnow
from yaaf.server import acting, policy

MAX_ORDERS = 25
MAX_RETIRED = 25
ORDER_CHARS = 400
GOAL_CHARS = 6000


class BriefError(ValueError):
    """A brief that cannot be applied, in words the person who wrote it can fix."""


def _clean_list(items, what: str, limit: int) -> list[str]:
    out: list[str] = []
    for raw in items or []:
        text = " ".join(str(raw).split())
        if not text:
            continue
        if len(text) > ORDER_CHARS:
            raise BriefError(
                f"Each {what} is one sentence with its value — {ORDER_CHARS} characters "
                f"at most. This one is {len(text)}: {text[:80]}…")
        if text.lower() not in {o.lower() for o in out}:
            out.append(text)
    if len(out) > limit:
        raise BriefError(
            f"{len(out)} {what}s is more than anyone keeps in their head ({limit} at "
            "most). Fold the small ones together, or let the goal carry them.")
    return out


def normalise(goal: str, standing_orders, retired) -> dict:
    """The brief as it will be stored, or a BriefError saying what to fix."""
    goal = (goal or "").strip()
    if len(goal) < 10:
        raise BriefError("The goal is the first thing every agent reads; it needs to say "
                         "what this project is for.")
    if len(goal) > GOAL_CHARS:
        raise BriefError(f"The goal is {len(goal)} characters. Keep it under {GOAL_CHARS}: "
                         "it is re-read on every run, and detail belongs in the orders.")
    orders = _clean_list(standing_orders, "standing order", MAX_ORDERS)
    gone = _clean_list(retired, "retired order", MAX_RETIRED)
    both = {o.lower() for o in orders} & {r.lower() for r in gone}
    if both:
        raise BriefError(f"“{next(iter(both))}” is listed as both standing and retired.")
    return {"goal": goal, "standing_orders": orders, "retired": gone}


def current(project: Project) -> dict:
    return {"goal": project.goal,
            "standing_orders": list(project.standing_orders or []),
            "retired": list(project.retired_orders or [])}


def is_change(project: Project, brief: dict) -> bool:
    return normalise(brief["goal"], brief.get("standing_orders"),
                     brief.get("retired")) != current(project)


def mark(project: Project) -> str:
    """A fingerprint of the brief. The resident chat session writes its system
    prompt once, at boot, so it compares this to restart itself onto a new one."""
    return hashlib.sha256(json.dumps(current(project), sort_keys=True).encode()).hexdigest()[:16]


def apply(session: Session, project: Project, brief: dict, *, how: str,
          carry_retired: bool = True) -> dict:
    """Make ``brief`` the project's brief. ``how`` is the sentence the CEO's
    notice opens with: who changed it and where.

    Retired orders carry forward. A rule taken back in October is still sitting
    in the files it was written into in November, and the reason it is listed is
    so the agent that finds it knows it is dead. An order brought back to life
    leaves the retired list by being named a standing order again. Settings,
    where he edits the whole list himself, passes ``carry_retired=False``.
    """
    clean = normalise(brief["goal"], brief.get("standing_orders"), brief.get("retired"))
    was = current(project)
    alive = {o.lower() for o in clean["standing_orders"]}
    named = alive | {x.lower() for x in clean["retired"]}
    carried = [r for r in was["retired"] if r.lower() not in named] if carry_retired else []
    clean["retired"] = (clean["retired"] + carried)[:MAX_RETIRED]
    project.goal = clean["goal"]
    project.standing_orders = clean["standing_orders"]
    project.retired_orders = clean["retired"]
    project.brief_confirmed_at = utcnow()

    ceo = policy.ceo_of(session, project.id)
    if ceo is not None:
        session.add(Notice(
            id=ids.new_id(ids.NOTICE), project_id=project.id, agent_id=ceo.id,
            kind=NoticeKind.GOAL_CHANGED, body=_notice(how, was, clean)))
    session.add(AuditLog(
        id=ids.new_id(ids.AUDIT), project_id=project.id, actor=acting.stamp(),
        action="brief.confirm", entity="project", entity_id=project.id,
        detail={"was": was, "now": clean, "how": how},
    ))
    return clean


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"


def _notice(how: str, was: dict, now: dict) -> str:
    dropped = [o for o in was["standing_orders"] if o not in now["standing_orders"]]
    return (
        f"{how} Every agent now reads this brief first, on every run, and the old "
        "one is gone from their prompts.\n\n"
        f"GOAL: {now['goal']}\n\nSTANDING ORDERS:\n{_bullets(now['standing_orders'])}\n\n"
        f"RETIRED:\n{_bullets(now['retired'])}\n"
        + (f"\nNo longer standing orders:\n{_bullets(dropped)}\n" if dropped else "")
        + (f"\nIT WAS: {was['goal']}\n" if was["goal"] != now["goal"] else "")
        + "\nThe brief changed; your workspace did not. Rules, check scripts, gates, "
        "handoff notes and memories written under the old brief are still there and "
        "still say the old thing. This run: find every one that disagrees with the brief "
        "and rewrite it to match — replace, do not append a correction next to it. "
        "Re-aim or close open tasks planned against the old direction, and tell the "
        "chairman in one line what you changed."
    )


# ── what a draft changes ─────────────────────────────────────────────────────
#
# A draft is the whole brief, rewritten. Shown whole, a chairman who asked for one
# sentence to change has to re-read ten to find it — and the one he did not ask
# for is the one he misses. So every place a draft is put in front of him leads
# with what it changes against the brief in force; the full text is a click away.
# The Decisions card computes the same thing client-side, live, as he edits.

# Below this, an old order and a new one are two different rules, not one edited.
_SAME_RULE = 0.5


def _words(text: str) -> list[str]:
    return text.split()


def _likeness(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _words(a.lower()), _words(b.lower())).ratio()


def changes(was: dict, now: dict) -> dict:
    """What ``now`` changes against ``was``, rule by rule.

    An order reworded is one edit, not one removal plus one addition: each new
    order is paired with the most alike dropped one when they share most of their
    words. Retired orders carry forward on confirm, so only the newly retired are
    a change; the rest are counted.
    """
    old, new = was["standing_orders"], now["standing_orders"]
    kept = {o.lower() for o in old} & {o.lower() for o in new}
    gone = [o for o in old if o.lower() not in kept]
    came = [o for o in new if o.lower() not in kept]
    pairs = sorted(((_likeness(g, c), g, c) for g in gone for c in came), reverse=True)
    edited: dict[str, str] = {}
    for score, g, c in pairs:
        if score >= _SAME_RULE and g not in edited.values() and c not in edited:
            edited[c] = g
    retired_before = {r.lower() for r in was["retired"]}
    return {
        "goal_changed": was["goal"] != now["goal"],
        "added": [c for c in came if c not in edited],
        "edited": [(edited[c], c) for c in came if c in edited],
        "dropped": [g for g in gone if g not in edited.values()],
        "unchanged": len(kept),
        "newly_retired": [r for r in now["retired"] if r.lower() not in retired_before],
        "still_retired": sum(1 for r in now["retired"] if r.lower() in retired_before),
    }


def _inline(was: str, now: str, context: int | None = None) -> str:
    """``was`` → ``now`` as one line of markdown: struck words out, bold words in.
    With ``context``, only the edited stretches and that many words either side."""
    a, b = _words(was), _words(now)
    out: list[str] = []
    ops = difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    for i, (tag, a1, a2, b1, b2) in enumerate(ops):
        if tag == "equal":
            run = a[a1:a2]
            if context is not None and len(run) > 2 * context:
                head = run[:context] if i > 0 else []
                tail = run[-context:] if i < len(ops) - 1 else []
                out += head + ["…"] + tail
            else:
                out += run
            continue
        if a2 > a1:
            out.append("~~" + " ".join(a[a1:a2]) + "~~")
        if b2 > b1:
            out.append("**" + " ".join(b[b1:b2]) + "**")
    return " ".join(out)


def changes_markdown(was: dict, now: dict, *, since: str) -> str:
    """The changes as boardroom markdown, headed by what they are measured against."""
    c = changes(was, now)
    rows: list[str] = []
    if c["goal_changed"]:
        rows.append(f"- **Goal, edited:** {_inline(was['goal'], now['goal'], context=6)}")
    rows += [f"- **New order:** {o}" for o in c["added"]]
    rows += [f"- **Order, edited:** {_inline(a, b)}" for a, b in c["edited"]]
    rows += [f"- **Order dropped:** ~~{o}~~" for o in c["dropped"]]
    rows += [f"- **Now retired:** {o}" for o in c["newly_retired"]]
    if not rows:
        rows.append("- No wording changes — the same orders, in a different order.")
    same = []
    if not c["goal_changed"]:
        same.append("the goal")
    if c["unchanged"]:
        same.append(f"{c['unchanged']} standing order{'s' if c['unchanged'] != 1 else ''}")
    if c["still_retired"]:
        same.append(f"{c['still_retired']} retired")
    tail = f"\n\n_Unchanged: {', '.join(same)}._" if same else ""
    return f"**What changes** against {since}:\n" + "\n".join(rows) + tail


def changes_text(was: dict, now: dict) -> list[str]:
    """The same, as plain lines for mail, where markdown would print as symbols."""
    c = changes(was, now)
    rows = ["- The goal is reworded; the new one is below."] if c["goal_changed"] else []
    rows += [f"- New order: {o}" for o in c["added"]]
    for a, b in c["edited"]:
        rows += [f"- Order edited. Was: {a}", f"  Now: {b}"]
    rows += [f"- Order dropped: {o}" for o in c["dropped"]]
    rows += [f"- Now retired: {o}" for o in c["newly_retired"]]
    return rows


def prompt_block(project: Project) -> str:
    """The brief as every agent reads it, right under the goal."""
    orders = list(project.standing_orders or [])
    retired = list(project.retired_orders or [])
    if not orders and not retired:
        return ""
    when = (f", confirmed {project.brief_confirmed_at:%Y-%m-%d}"
            if project.brief_confirmed_at else "")
    parts = []
    if orders:
        parts.append(
            f"STANDING ORDERS (the chairman's current word{when}). Each one replaces "
            "anything older that disagrees with it — in your charter, a memory, a "
            "workspace file, a check script or a handoff note:\n" + _bullets(orders))
    if retired:
        parts.append(
            "RETIRED — the chairman took these back. If you find one in a file, a "
            "memory, a script or a note, it is dead: do not follow it, and fix the "
            "place you found it:\n" + _bullets(retired))
    return "\n\n".join(parts) + "\n"
