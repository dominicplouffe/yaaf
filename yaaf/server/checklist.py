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
#: A project's own email address. Listed only for a project that needs one or
#: has one — a checklist step nobody needs is a step somebody skips forever —
#: and required where the work runs through email: such a project cannot start
#: without it (yaaf.server.mail.settings.start_refusal).
EMAIL = Step("email", "Email", "mail")
#: A Code Factory's own wiring, right after the key: without a repo it has
#: nowhere to push, and without a key its seats cannot push to it. Each used to
#: fail only inside an agent's run. Where it deploys can wait until it has
#: something to release.
FACTORY = (
    Step("repo", "GitHub repository", "integrations", required=True),
    Step("push", "Agents' GitHub key", "integrations", required=True),
    Step("deploy", "Where it deploys", "integrations"),
)
BY_KEY = {s.key: s for s in (*STEPS, EMAIL, *FACTORY)}


def steps_for(project: Project) -> tuple[Step, ...]:
    """This project's steps, in order. The email step sits right after the key:
    when it is required, it is the second thing standing between a new project
    and its first run."""
    from yaaf.core import shape

    head = (STEPS[0], *(FACTORY if shape.is_factory(project) else ()))
    if not (project.needs_mail or (project.integrations or {}).get("mail")):
        return (*head, *STEPS[1:])
    email = Step(EMAIL.key, EMAIL.label, EMAIL.section, required=bool(project.needs_mail))
    return (*head, email, *STEPS[1:])


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
        sources = integrations.get("mcp_servers") or []
        if sources:
            return True, f"{len(sources)} connected."
        return False, "Where agents read from: a database, an API, a folder of files."
    if step.key == "secrets":
        asked = session.execute(
            select(func.count()).select_from(Approval).where(
                Approval.project_id == project.id, Approval.kind == ApprovalKind.TOOL_REQUEST,
                Approval.status == ApprovalStatus.PENDING, Approval.grant.is_(None))
        ).scalar_one()
        held = session.execute(
            select(func.count()).select_from(Secret).where(
                Secret.project_id == project.id, Secret.llm_mode.is_(None),
                # The mailbox's password is the platform's to use, not the agents'.
                Secret.kind != "mailbox")
        ).scalar_one()
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
    if step.key == "repo":
        from yaaf.server import github

        if project.github_repo:
            return True, f"Builds in {project.github_repo}."
        if github.platform_token(session) is None:
            return False, ("Connect the platform to GitHub first (a key for the account "
                           "that creates repos and merges), then create or pick the repo.")
        return False, "Nowhere to push yet: create a repo, or pick an empty one."
    if step.key == "push":
        from yaaf.server.api.admin import _project_secret, _push_roles

        push = _project_secret(session, project.id, "github")
        missing = sorted(set(_push_roles(session, project)) - set(push.role_scopes or [])) \
            if push else None
        if push and not missing:
            return True, "The seats that build can push branches."
        if push:
            return False, f"Not granted to {', '.join(missing)} yet: save the key again."
        return False, "The seats that build push branches with their own GitHub key."
    if step.key == "deploy":
        environments = sorted((integrations.get("environments") or {}).keys())
        if environments:
            return True, f"Deploys to {', '.join(environments)}."
        return False, "A deploy command and a health URL, before its first release."
    if step.key == "email":
        from yaaf.server.mail import settings as mail_settings

        config = mail_settings.project_config(project)
        if mail_settings.enabled(project):
            return True, f"The CEO sends from {config.get('address')} and reads its replies."
        if config is None:
            return False, ("Its work runs through email: give it an address, its servers "
                           "and who it may write to." if project.needs_mail
                           else "Give the CEO an email address.")
        gaps = mail_settings.missing(project)
        return False, (f"Still needs {', '.join(gaps)}." if gaps
                       else "Set up, and switched off.")
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
    needs = _needs(session, project)
    steps = []
    for step in steps_for(project):
        if step.key == "secrets" and needs:
            # Promised credentials are named, one step each, in place of the
            # generic step that ticked itself off for any single secret.
            steps.extend(needs)
            continue
        done, detail = _done(session, project, step)
        steps.append({
            "key": step.key, "label": step.label, "section": step.section,
            "required": step.required, "detail": detail,
            "state": "done" if done else "skipped" if step.key in skipped else "todo",
        })
    todo = [s for s in steps if s["state"] in ("todo", "failed")]
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


def _needs(session: Session, project: Project) -> list[dict]:
    """One step per credential the chairman promised (CredentialNeed). A
    broken one is "failed" — still to do, and drawn red — and one he said the
    project does without is "skipped", like any skipped step."""
    from yaaf.core.models import CredentialNeed

    state = {"needed": "todo", "failed": "failed", "ok": "done", "saved": "done",
             "waived": "skipped"}
    out = []
    for need in session.execute(
            select(CredentialNeed).where(CredentialNeed.project_id == project.id)
            .order_by(CredentialNeed.created_at)).scalars():
        detail = {"needed": f"Needed: {need.purpose or 'you said you would give this'}.",
                  "failed": (need.last_check or {}).get("summary")
                  or (need.last_use or {}).get("detail") or "It stopped working.",
                  "ok": "Works.", "saved": "Saved, not tested.",
                  "waived": "You said the project does without it."}[need.status]
        out.append({"key": f"cred:{need.id}", "label": need.label,
                    "section": f"credentials&need={need.id}", "required": False,
                    "detail": detail, "state": state.get(need.status, "todo"),
                    "credential": need.status})
    return out


class Refused(ValueError):
    """A change to the checklist that the project's state does not allow."""


def act(session: Session, project: Project, action: str, step: str | None,
        user_id: str | None) -> dict:
    """Skip or unskip one step, or close or reopen the whole list."""
    state = _state(project)
    skipped = list(state.get("skipped") or [])
    if action in ("skip", "unskip") and step and step.startswith("cred:"):
        # "I'm good without Beehiiv" is the need's own answer, kept on it.
        from yaaf.core.models import CredentialNeed

        need = session.get(CredentialNeed, step.removeprefix("cred:"))
        if need is None or need.project_id != project.id:
            raise Refused(f"there is no setup step called {step!r}")
        if action == "skip":
            need.status = "waived"
        elif need.last_check:
            need.status = "ok" if need.last_check.get("ok") else "failed"
        else:
            from yaaf.server.credential_needs import values_of

            need.status = "saved" if values_of(session, need) else "needed"
        return view(session, project)
    if action in ("skip", "unskip"):
        if step not in BY_KEY:
            raise Refused(f"there is no setup step called {step!r}")
        if action == "skip":
            this = next((s for s in steps_for(project) if s.key == step), BY_KEY[step])
            if this.required and not _done(session, project, this)[0]:
                # The one step a project cannot run without. Skipping it would
                # put the list away over a project whose every run will fail.
                raise Refused(f"{this.label} cannot be skipped: "
                              + ("this project's work runs through email"
                                 if this.key == "email" else "agents cannot run without it"))
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
