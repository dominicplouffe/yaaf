"""Whether a project's seats can run: one check for every door and the lease.

The rules are yaaf.runtimes.seats.refusal, which knows no database. This is the
half that does — which credential pays each seat (yaaf.core.project_credentials)
and whether the install can price its model (yaaf.core.prices) — so the doors
(`admin.refuse_unrunnable_seats`), the lease backstop and the health checklist
ask one function and cannot disagree.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from yaaf.core import prices, project_credentials
from yaaf.core.models import Agent, Project
from yaaf.runtimes.providers import provider as seller
from yaaf.runtimes.seats import refusal, seat_for


def seat_problem(session: Session, project: Project, agents: list[Agent]) -> str | None:
    """The first seat of `agents` that could not run on this project, named,
    with why — or None. The doors raise it; the lease and the health checklist
    read it."""
    from yaaf.server.contextpack import role_version_for

    # A project that holds no credential at all is not configured yet (its
    # health says so) or runs on the scripted backend, which reads none.
    pays = bool(project.billing_secret_id) and (
        (project.integrations or {}).get("runtime") != "mock")
    for agent in agents:
        role_version = role_version_for(session, agent)
        seat = seat_for(agent, role_version)
        credential = project_credentials.seat_credential(session, project, seat.provider)
        if credential is None and pays:
            # R5 — it fails at the door, not at 3am.
            label = seller(seat.provider).label
            return (f"{agent.name}: runs on {seat.model}, a {label} model, and this "
                    f"project holds no {label} credential — add one in the project's "
                    f"Settings, or put this seat on a model the project can pay for")
        problem = refusal(seat.harness, seat.model,
                          role_version.tool_permissions if role_version else {},
                          credential, priced=prices.covered(session, seat.model))
        if problem:
            return f"{agent.name}: {problem}"
    return None
