"""Whether a role's newer version would change what a team runs.

A team is pinned to a role version, and a newer one is offered — the health
checklist's drift line, the Team page's "Adopt", the Roles page's count of
teams behind. Each of those is a request for a decision, and a request with
nothing to decide is noise that trains a chairman to ignore the next one.

Moving the shipped library onto model classes (docs/PROVIDERS.md, migration)
stamps a version on every role whose only change is `claude-sonnet-5-5` →
`@balanced` — which, on a project paying Anthropic, resolves to the very model
it named. Nothing a team runs would change, so nothing is offered. Elsewhere
the same version does matter (on OpenAI, `@balanced` is an OpenAI model), and
there it is offered as any version is.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core import model_policy
from yaaf.core.models import Agent, AgentStatus, Project, RoleVersion
from yaaf.runtimes import catalog

#: What a version decides about a seat, besides its model. The description is
#: the author's note about the version, not something the seat runs.
_SEAT_FIELDS = ("system_prompt", "effort", "harness", "tool_permissions", "secret_scopes")


def same_seat(session: Session, project: Project | None, pinned: RoleVersion,
              newer: RoleVersion, *, models_overridden: bool = False) -> bool:
    if any(getattr(pinned, f) != getattr(newer, f) for f in _SEAT_FIELDS):
        return False
    # Every seat on the team names its own model: the role's is read by none.
    if pinned.model == newer.model or models_overridden:
        return True
    return (project is not None and catalog.is_class(newer.model)
            and not catalog.is_class(pinned.model)
            and model_policy.resolve(session, newer.model, project) == pinned.model)


def newest(session: Session, role_id: str) -> RoleVersion | None:
    return session.scalars(select(RoleVersion).where(RoleVersion.role_id == role_id)
                           .order_by(RoleVersion.version.desc()).limit(1)).first()


def offered(session: Session, project: Project | None, pinned: RoleVersion,
            team_id: str | None = None) -> RoleVersion:
    """The version a team pinned to `pinned` is offered: the newest, unless it
    would run exactly as the team does now — then `pinned` itself."""
    latest = newest(session, pinned.role_id)
    if latest is None or latest.version <= pinned.version \
            or same_seat(session, project, pinned, latest,
                         models_overridden=_all_overridden(session, team_id)):
        return pinned
    return latest


def _all_overridden(session: Session, team_id: str | None) -> bool:
    """Whether every working seat on the team names a model of its own — as a
    project on another provider's key had to before roles named classes."""
    if not team_id:
        return False
    members = session.scalars(select(Agent).where(
        Agent.team_id == team_id, Agent.status != AgentStatus.STOOD_DOWN)).all()
    return bool(members) and all(a.model_override for a in members)
