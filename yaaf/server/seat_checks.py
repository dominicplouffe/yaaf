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
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import provider as seller
from yaaf.runtimes.seats import refusal, seat_for


def unpaid(who: str, model: str, paid: list[str], fix: str | None = None) -> str:
    """R5's sentence: a seat on a provider nothing on the project pays. Names
    what the project does pay, because the fix is one of two moves — run the
    seat on the project's own models, or pay this one too. With roles naming
    no provider, only a choice somebody made per seat or per tier can get
    here, so the sentence says which one to undo."""
    label = seller(catalog.provider_of(model)).label
    article = "an" if label[:1].lower() in "aeiou" else "a"
    pays = ", ".join(seller(p).label for p in paid) or "nothing yet"
    fix = fix or ("drop this seat's model override so it runs on the project's own "
                  "models, or name one of a provider the project pays")
    return (f"{who}: runs on {model}, {article} {label} model, and this project holds no "
            f"{label} credential — it pays {pays}. {fix[:1].upper()}{fix[1:]}, or pay "
            f"{label} too with one of its credentials")


def no_model(who: str, tier: str, provider_name: str) -> str:
    """A seat whose tier the project's lineup cannot answer: its provider
    ships no lineup (OpenRouter, a custom server, the long tail) and the
    project named none."""
    return (f"{who}: its role asks for the {tier} tier, and {seller(provider_name).label} "
            f"ships no model lineup — name the model this project's seats run on "
            f"(Settings → Models, or `seat_models` in a setup file; one model for "
            f"\"standard\" serves every tier)")


def seat_problem(session: Session, project: Project, agents: list[Agent], *,
                 chat: bool = True, typed: set[str] = frozenset()) -> str | None:
    """The first seat of `agents` that could not run on this project, named,
    with why — or None. The doors raise it; the lease and the health checklist
    read it. `chat` asks R7 too — whether the CEO's seat can hold the
    boardroom — which the lease does not: a CEO's work run needs no
    conversation, and the chat supervisor holds R7's backstop itself. `typed`
    is the seats whose effort override the calling request named, which R6
    holds to what the model takes; every other effort is fitted."""
    from yaaf.core.models import AgentKind
    from yaaf.runner.chat import chat_enabled
    from yaaf.server.contextpack import role_version_for

    # A project that holds no credential at all is not configured yet (its
    # health says so) or runs on the scripted backend, which reads none.
    pays = bool(project.billing_secret_id) and (
        (project.integrations or {}).get("runtime") != "mock")
    paid = sorted(project_credentials.held(session, project))
    if pays:
        for tier, model in sorted((project.seat_models or {}).items()):
            if catalog.provider_of(model) not in paid:
                return unpaid(f"the {tier} tier", model, paid,
                              fix="name one of a provider the project pays for that tier "
                                  "(Settings → Models)")
    lineup = project_credentials.project_lineup(session, project)
    for agent in agents:
        role_version = role_version_for(session, agent)
        seat = seat_for(agent, role_version, lineup)
        if seat.model is None:
            return no_model(agent.name, seat.tier,
                            project_credentials.provider_of(session, project))
        credential = project_credentials.seat_credential(session, project, seat.provider)
        if credential is None and pays:
            # R5 — it fails at the door, not at 3am.
            return unpaid(agent.name, seat.model, paid)
        problem = refusal(seat.harness, seat.model,
                          role_version.tool_permissions if role_version else {},
                          credential, priced=prices.covered(session, seat.model),
                          effort=agent.effort_override if agent.id in typed else None,
                          converses=(chat and agent.kind == AgentKind.CEO
                                     and chat_enabled(project)))
        if problem:
            return f"{agent.name}: {problem}"
    return None


def runs_on(session: Session, project: Project, agent: Agent) -> dict:
    """What a seat runs on, for the screens: the model and where it came from,
    the effort it runs at beside the one its role asked for, and whether a
    `web` grant searches or only fetches. Every fitting the resolution does is
    shown here rather than done quietly."""
    from yaaf.server.contextpack import role_version_for

    role_version = role_version_for(session, agent)
    seat = seat_for(agent, role_version, project_credentials.project_lineup(session, project))
    web = bool((role_version.tool_permissions or {}).get("web")) if role_version else False
    return {"model": seat.model, "provider": seat.provider if seat.model else None,
            "tier": seat.tier,
            # A tier the project left blank on a provider with no lineup runs
            # on the project's standard model: the project's, not the vendor's.
            "from": ("override" if agent.model_override
                     else "project" if seat.model in (project.seat_models or {}).values()
                     else "provider"),
            "effort": seat.effort if seat.effort_dial else None,
            "effort_asked": seat.effort_asked, "harness": seat.harness,
            "web": "search" if web and seat.searches else "fetch" if web else None}
