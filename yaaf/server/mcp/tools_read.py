"""Read tools — the scope that is worth shipping on its own.

Two of them land with the transport, because a principal that no tool exercises
is a principal nothing can attack: `projects` is the first project-addressed
call, and so the first place the allowlist either holds or does not.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from yaaf.core.models import User
from yaaf.server.mcp.auth import Principal, allowed_projects, resolve_project
from yaaf.server.mcp.registry import register

#: Goals are written to brief a CEO, not to fit in a list. Full text is one
#: `project` call away (M1); this is the line that tells them apart.
GOAL_PREVIEW = 240


@register(
    "whoami",
    scope="read",
    description=(
        "What this MCP client is allowed to do: the chairman it acts for, its scopes, "
        "and the projects it can reach. Ask first if a call was refused and you are "
        "unsure whether the token or the argument was wrong."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def whoami(principal: Principal, session: Session, arguments: dict) -> dict:
    user = session.get(User, principal.user_id)
    return {
        "client": principal.name,
        "acting_for": None if user is None else user.email,
        "scopes": sorted(principal.scopes),
        "implied": ["read"],
        "projects": [p.slug for p in allowed_projects(session, principal)],
        "note": (
            "Writes made through this client are recorded as machine-mediated "
            "and cannot be attributed to the chairman alone."
        ),
    }


@register(
    "projects",
    scope="read",
    description=(
        "The companies this client can see: slug, name, status, shape, and the first "
        "lines of the goal. Pass `project` (slug or id) to narrow to one."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Slug or id, to narrow to one"},
        },
        "additionalProperties": False,
    },
)
def projects(principal: Principal, session: Session, arguments: dict) -> dict:
    ref = arguments.get("project")
    if isinstance(ref, str) and ref.strip():
        rows = [resolve_project(session, principal, ref.strip())]
    else:
        rows = allowed_projects(session, principal)
    return {
        "projects": [
            {
                "slug": project.slug,
                "id": project.id,
                "name": project.name,
                "status": project.status,
                "mode": project.mode,
                "goal": _preview(project.goal),
                "created_at": project.created_at.isoformat(),
            }
            for project in rows
        ],
        "count": len(rows),
    }


def _preview(text: str | None) -> str:
    text = (text or "").strip()
    return text if len(text) <= GOAL_PREVIEW else text[:GOAL_PREVIEW].rstrip() + "…"
