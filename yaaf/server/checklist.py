"""The setup checklist: what a new project still needs before it is set up.

Setting a project up is a burst — a key, a source or two, the credentials the
agents asked for, the room to mirror into — and then months of not touching
any of it. Those pages sat three clicks deep, under Details and then Settings,
which is the right depth for the months and the wrong one for the burst. The
checklist is the shortcut that exists only while the burst does: one line on
the project's Inbox per step, each opening the exact section, and one row in
the sidebar that opens the next step. When every step is done or skipped, or
somebody closes it, both go away for good.

Whether a step is done is read from the project every time, never stored: a
checklist that remembered "key connected" would keep saying so after the key
was removed. What is stored is only what a person decided — the steps they
skipped, and that they closed the list (`Project.setup`).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from yaaf.core.models import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Project,
    ProjectMember,
    Secret,
    utcnow,
)


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    #: The project Settings section that does it (`?s=` on the Settings page).
    section: str
    #: A required step cannot be skipped: the project cannot run without it.
    required: bool = False


STEPS = (
    Step("key", "Model key", "money", required=True),
    Step("brief", "Brief", "brief"),
    Step("sources", "Data sources", "sources"),
    Step("secrets", "Secrets & variables", "secrets"),
    Step("integrations", "Integrations", "integrations"),
    Step("people", "People", "people"),
)
BY_KEY = {s.key: s for s in STEPS}


def _state(project: Project) -> dict:
    return dict(project.setup or {})


def _done(session: Session, project: Project, step: Step) -> tuple[bool, str]:
    """Whether the project already has what the step asks for, and one line
    saying what it has — or what it is missing."""
    integrations = project.integrations or {}
    if step.key == "key":
        if integrations.get("runtime") == "mock":
            return True, "This project runs test agents, which need no key."
        if project.billing_secret_id:
            billing = session.get(Secret, project.billing_secret_id)
            name = billing.name if billing is not None else "a credential"
            return True, f"Model calls pay with {name}."
        return False, "Agents cannot run until a model key pays for their calls."
    if step.key == "brief":
        orders = len(project.standing_orders or [])
        if project.brief_confirmed_at or orders:
            return True, (f"{orders} standing order{'s' if orders != 1 else ''} under the goal."
                          if orders else "The goal is confirmed.")
        return False, "Add the standing orders every agent reads under the goal."
    if step.key == "sources":
        from yaaf.core import shared_store

        sources = list(integrations.get("mcp_servers") or [])
        sources += [h for h in shared_store.received(session, project.id)
                    if h.kind == shared_store.DATA_SOURCE]
        if sources:
            return True, f"{len(sources)} connected."
        return False, "Where agents read from: a database, an API, a folder of files."
    if step.key == "secrets":
        asked = session.execute(
            select(func.count()).select_from(Approval).where(
                Approval.project_id == project.id, Approval.kind == ApprovalKind.TOOL_REQUEST,
                Approval.status == ApprovalStatus.PENDING, Approval.grant.is_(None))
        ).scalar_one()
        from yaaf.core import shared_store

        held = session.execute(
            select(func.count()).select_from(Secret).where(
                Secret.project_id == project.id, Secret.llm_mode.is_(None))
        ).scalar_one() + sum(1 for h in shared_store.received(session, project.id)
                             if h.kind == shared_store.VARIABLE)
        if asked:
            return False, (f"Your agents asked for {asked} credential{'s' if asked != 1 else ''} "
                           "they do not have.")
        if held:
            return True, f"{held} held for your agents."
        return False, "API keys and values your agents use, such as a data vendor's token."
    if step.key == "integrations":
        slack = integrations.get("slack") or {}
        if slack or project.github_repo:
            return True, "Slack" if slack else "GitHub"
        return False, "Mirror the project into Slack, or connect a GitHub repository."
    if step.key == "people":
        others = session.execute(
            select(func.count()).select_from(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id != project.chairman_user_id)
        ).scalar_one()
        if others:
            return True, f"{others} other {'person' if others == 1 else 'people'} can open it."
        return False, "Invite a co-chair who can decide, or someone who only reads."
    raise KeyError(step.key)


def view(session: Session, project: Project) -> dict:
    """The checklist as the project's Inbox and the sidebar show it."""
    state = _state(project)
    skipped = set(state.get("skipped") or [])
    steps = []
    for step in STEPS:
        done, detail = _done(session, project, step)
        steps.append({
            "key": step.key, "label": step.label, "section": step.section,
            "required": step.required, "detail": detail,
            "state": "done" if done else "skipped" if step.key in skipped else "todo",
        })
    todo = [s for s in steps if s["state"] == "todo"]
    closed_at = state.get("closed_at")
    return {
        "open": bool(todo) and not closed_at,
        "closed_at": closed_at,
        "steps": steps,
        "settled": len(steps) - len(todo),
        "total": len(steps),
        "next": ({"key": todo[0]["key"], "label": todo[0]["label"], "section": todo[0]["section"]}
                 if todo else None),
    }


class Refused(ValueError):
    """A change to the checklist that the project's state does not allow."""


def act(session: Session, project: Project, action: str, step: str | None,
        user_id: str | None) -> dict:
    """Skip or unskip one step, or close or reopen the whole list."""
    state = _state(project)
    skipped = list(state.get("skipped") or [])
    if action in ("skip", "unskip"):
        if step not in BY_KEY:
            raise Refused(f"there is no setup step called {step!r}")
        if action == "skip":
            if BY_KEY[step].required and not _done(session, project, BY_KEY[step])[0]:
                # The one step a project cannot run without. Skipping it would
                # put the list away over a project whose every run will fail.
                raise Refused(f"{BY_KEY[step].label} cannot be skipped: agents cannot run "
                              "without it")
            if step not in skipped:
                skipped.append(step)
        elif step in skipped:
            skipped.remove(step)
        state["skipped"] = skipped
    elif action == "close":
        state["closed_at"] = utcnow().isoformat()
        state["closed_by"] = user_id
    elif action == "reopen":
        state.pop("closed_at", None)
        state.pop("closed_by", None)
    else:
        raise Refused(f"unknown action {action!r}")
    # A new dict, so the JSON column sees the change.
    project.setup = state
    return view(session, project)
