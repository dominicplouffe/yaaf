"""The third principal: a model client acting for the chairman.

`yaaf.server.deps` has two principals — the chairman's admin bearer, and the
short-lived per-run token an agent acts through. This is the third, and the
first rule about it is what it is *not*: it is never the admin token. The admin
bearer is unscoped and unattributable, so a model holding it would hold every
verb the platform exists to deny a model, and the audit trail would record the
chairman as the one who used them.

So an MCP client presents its own credential, which carries two narrowings the
admin token cannot express — the scopes it may reach, and the identity it acts
for, which is written into every record it touches as machine-mediated. What it
may *see* is not a third narrowing: it is chairmanship, which the chairman
already decides per project by chairing it.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.db import session_factory
from yaaf.core.models import McpClient, McpToken, Project, utcnow
from yaaf.core.tokens import hash_token

#: Smallest to largest. `read` is implied by every other scope: a client that
#: may steer a project can obviously look at it, and a grant that had to list
#: both would eventually be issued without the first.
SCOPES = ("read", "steer", "create", "configure")

mcp_bearer = HTTPBearer(
    auto_error=False,
    scheme_name="McpToken",
    description="An MCP client token (yaaf mcp-token mint)",
)


def base_url() -> str:
    """The origin this install answers on, as the outside world sees it."""
    return (settings().public_base_url or settings().api_base_url).rstrip("/")


def canonical_resource() -> str:
    """The audience every token minted here is bound to (RFC 8707).

    Lives beside the principal rather than with the OAuth endpoints, because
    the *resource server* is what has to know its own name: it is the half that
    refuses a token minted for somewhere else.
    """
    return f"{base_url()}/mcp"


def challenge() -> str:
    """The RFC 9728 pointer that starts discovery, for a 401's WWW-Authenticate.

    A client that arrives with no credential learns from this header alone
    where to go and ask for one. Without it, discovery is guesswork.
    """
    return (f'Bearer realm="yaaf", '
            f'resource_metadata="{base_url()}/.well-known/oauth-protected-resource/mcp"')


class ToolRefusal(Exception):
    """A refusal the calling model is meant to read.

    The same shape as the platform's refusals to agents: the wall says why it
    is there, because a model that learns nothing from being refused just
    rephrases and tries again.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Principal:
    client_id: str
    name: str
    user_id: str
    scopes: frozenset[str]

    def holds(self, scope: str) -> bool:
        if not self.scopes:
            return False
        return scope in self.scopes or scope == "read"


def mcp_db():
    """A session for the MCP surface.

    Its own dependency rather than `deps.db` because this router is mounted
    outside the admin one and must never inherit its auth by accident.
    """
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def mcp_auth(
    request: Request,
    session: Session = Depends(mcp_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(mcp_bearer),
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="missing bearer token",
            headers={"WWW-Authenticate": challenge()},
        )
    token = credentials.credentials.strip()
    # Named explicitly, and first, so the refusal teaches instead of reading as
    # a typo. Someone who reaches for the admin token here is not making a
    # mistake about the value; they are making one about the design.
    if token == settings().admin_token:
        raise HTTPException(
            status_code=401,
            detail=(
                "the admin token is not an MCP principal. Mint a scoped client "
                "token instead (`yaaf mcp-token mint`) — the chairman's bearer "
                "is unscoped and unattributable, and a model must hold neither."
            ),
            headers={"WWW-Authenticate": challenge()},
        )
    row = session.execute(
        select(McpToken).where(McpToken.token_hash == hash_token(token))
    ).scalar_one_or_none()
    if row is None or row.kind == "refresh":
        # A refresh token is not an access token, however valid it looks. The
        # token endpoint is the only place it means anything.
        raise HTTPException(status_code=401, detail="unknown MCP client token",
                            headers={"WWW-Authenticate": challenge()})
    if row.revoked_at is not None:
        raise HTTPException(status_code=401, detail="this MCP client token was revoked",
                            headers={"WWW-Authenticate": challenge()})
    if row.expires_at is not None and row.expires_at < utcnow():
        raise HTTPException(status_code=401, detail="this access token expired; refresh it",
                            headers={"WWW-Authenticate": challenge()})
    # RFC 8707. A token minted for another resource is refused here even when
    # it is otherwise perfectly valid — the rule that stops one stolen from
    # another service being replayed at yaaf. CLI tokens name no audience.
    if row.resource and row.resource.rstrip("/") != canonical_resource().rstrip("/"):
        raise HTTPException(
            status_code=401,
            detail=f"this token was minted for {row.resource}, not {canonical_resource()}",
            headers={"WWW-Authenticate": challenge()})
    client = session.get(McpClient, row.client_id)
    if client is None or client.revoked_at is not None:
        raise HTTPException(status_code=401, detail="this MCP client token was revoked",
                            headers={"WWW-Authenticate": challenge()})
    client.last_used_at = utcnow()
    request.state.mcp_client = client.id
    return Principal(
        client_id=client.id,
        name=client.name,
        user_id=client.user_id,
        scopes=frozenset(client.scopes or ()),
    )


def allowed_projects(session: Session, principal: Principal) -> list[Project]:
    """Every project this principal may see, in creation order.

    One gate: the chairmanship of the user it acts for. A per-client allowlist
    used to sit in front of it and was removed, because it answered a question
    at grant time that only the chairman's project list can answer — a project
    founded after consent fell outside a list frozen weeks earlier, and the
    only way back in was a second trip through the browser. Chairmanship is
    evaluated on every call, so a project founded a minute ago is reachable
    and one handed to another chairman stops being reachable, both without
    reissuing anything.
    """
    query = select(Project).where(Project.chairman_user_id == principal.user_id)
    return list(session.execute(query.order_by(Project.created_at)).scalars())


def resolve_project(session: Session, principal: Principal, ref: str) -> Project:
    """Turn an id or slug into a project this principal may act on.

    Every project-addressed tool goes through here, including ones that were
    handed an id indirectly — a run id, an approval id, a requirement key read
    out of some other project's page. A project another chairman runs is
    refused in the same words as one that does not exist, so the surface does
    not become an oracle for which projects other people run.
    """
    reachable = allowed_projects(session, principal)
    for project in reachable:
        if ref in (project.id, project.slug):
            return project
    raise ToolRefusal(
        f"no project {ref!r} is reachable by this client. It can see: "
        + (", ".join(p.slug for p in reachable) or "nothing")
    )
