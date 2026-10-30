"""Whether a project's seats can run: one check for every door and the lease.

The rules are yaaf.runtimes.seats.refusal, which knows no database. This is the
half that does — which credential pays each seat (yaaf.core.project_credentials)
and whether the install can price its model (yaaf.core.prices) — so the doors
(`admin.refuse_unrunnable_seats`), the lease backstop and the health checklist
ask one function and cannot disagree.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy.orm import Session

from yaaf.core import model_policy, prices, project_credentials
from yaaf.core.models import Agent, Project
from yaaf.runtimes import catalog
from yaaf.runtimes.providers import provider as seller
from yaaf.runtimes.seats import Problem, efforts, problems, seat_for, unmodelled


def unpaid(who: str, model: str, paid: list[str]) -> str:
    """R5's sentence: a seat on a provider nothing on the project pays. Names
    what the project does pay, because the fix is one of two moves — run the
    seat on one of those providers' models, or pay this one too — and a
    chairman who picked a DeepSeek key for a project of Claude roles needs to
    see that the key paid DeepSeek and the roles ran on Claude."""
    label = seller(catalog.provider_of(model)).label
    article = "an" if label[:1].lower() in "aeiou" else "a"
    pays = ", ".join(seller(p).label for p in paid) or "nothing yet"
    return (f"{who}: runs on {model}, {article} {label} model, and this project holds no "
            f"{label} credential — it pays {pays}. Run this seat on a model of a provider "
            f"the project pays (a model override, or a role on that model), or pay {label} "
            f"too with one of its credentials")


def _bills_in_tokens(credential: tuple[str, str]) -> bool:
    """Whether a seat's (provider, mode) is a kind metered in tokens. A
    provider this install does not have is not ours to judge here."""
    from yaaf.runtimes.providers import PROVIDERS

    provider_name, mode = credential
    found = PROVIDERS.get(provider_name)
    return found is not None and found.kind(mode).meter == "tokens"


def seat_problem(session: Session, project: Project, agents: list[Agent], *,
                 chat: bool = True, context=None, fallback: bool | None = None) -> str | None:
    """The first seat of `agents` that could not run on this project, named,
    with why — or None. The doors raise it; the lease and the health checklist
    read it. `chat` asks R7 too — whether the CEO's seat can hold the
    boardroom — which the lease does not: a CEO's work run needs no
    conversation, and the chat supervisor holds R7's backstop itself."""
    # `context` names another provider for the bare classes — the fallback
    # door asking whether the seats would run there.
    context = context or model_policy.context(session, project)
    for agent in agents:
        found = seat_problems(session, project, agent, chat=chat, context=context,
                              fallback=fallback)
        if found.problems:
            return f"{agent.name}: {found.problems[0].text}"
    return None


class SeatCheck(NamedTuple):
    problems: list[Problem]
    #: The efforts this seat can take (R6) — what the seat dialog offers.
    efforts: tuple[str, ...]
    #: The one of them nearest the seat's own, never lower while a higher
    #: one exists (`catalog.rung`) — what the dialog proposes when it is not.
    fit: str | None = None


def seat_problems(session: Session, project: Project, agent: Agent, *,
                  chat: bool = True, context=None, fallback: bool | None = None) -> SeatCheck:
    """Every reason one seat could not run on this project, in the order the
    doors meet them — not only the first. The seat dialog asks before it
    saves, so a move that needs a credential, a price and a search backend is
    one motion with all three in view, rather than three refusals in a row."""
    from yaaf.core.models import AgentKind
    from yaaf.runner.chat import chat_enabled
    from yaaf.server.contextpack import role_version_for

    # A project that holds no credential at all is not configured yet (its
    # health says so) or runs on the scripted backend, which reads none.
    pays = bool(project.billing_secret_id) and (
        (project.integrations or {}).get("runtime") != "mock")
    context = context or model_policy.context(session, project)
    role_version = role_version_for(session, agent)
    seat = seat_for(agent, role_version, context=context)
    if not seat.resolved:
        # R8 — a class with no model on its provider, before R5 asks who
        # pays for a model that does not exist.
        return SeatCheck([Problem("unmodelled", unmodelled(seat.model))], catalog.EFFORTS)
    found: list[Problem] = []
    credential = project_credentials.seat_credential(session, project, seat.provider,
                                                     fallback=fallback)
    if credential is None and pays:
        # R5 — it fails at the door, not at 3am.
        found.append(Problem("payer", unpaid(
            agent.name, seat.model,
            sorted(project_credentials.held(session, project, fallback=fallback)))))
    if credential is not None and pays and project.billing_mode == "api_key" \
            and _bills_in_tokens(credential) and not prices.covered(session, seat.model):
        # R3, for a budget kept in dollars: a subscription's runs move no
        # dollars and are counted at their catalog price (budget.counted),
        # so a model with none would be a seat the dollar wall cannot see.
        found.append(Problem("price",
                             f"{seat.model} has no price on this install, and this "
                             f"project's budget is in dollars — a subscription's runs on "
                             f"it are counted at their catalog price. Import or set one in "
                             f"Settings → Model prices"))
    found += problems(seat.harness, seat.model,
                      role_version.tool_permissions if role_version else {},
                      credential, priced=prices.covered(session, seat.model),
                      effort=seat.effort,
                      converses=(chat and agent.kind == AgentKind.CEO
                                 and chat_enabled(project)),
                      pinned=seat.pinned, platform_search=context.platform_search)
    allowed = efforts(seat.harness, seat.model, credential) if seat.pinned else catalog.EFFORTS
    fit = seat.effort if not seat.effort or seat.effort in allowed \
        else catalog.rung(seat.effort, allowed)
    return SeatCheck(found, allowed, fit)
