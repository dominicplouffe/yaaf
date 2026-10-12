"""The model-client principal: a machine acting for a person.

People sign in through `yaaf.server.identity` and agents act through their
per-run token; this is the third kind of credential, and the first rule about
it is what it is *not*: it is never the person's own. A session or a personal
token carries everything its person may do, unattributed to any machine, so a
model holding one would hold every verb the platform exists to deny a model,
and the audit trail would record the person as the one who used them. (It was
the static admin token this paragraph used to be about; that was retired, and
the rule outlived it.)

So an MCP client presents its own credential, which carries two narrowings a
person's own credential cannot express — the scopes it may reach, and the
identity it acts for, which is written into every record it touches as
machine-mediated. What it may *see* is not a third narrowing: it is the
projects its person may see, read on every call.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.db import session_factory
from yaaf.core.models import McpClient, McpToken, Project, User, utcnow
from yaaf.core.tokens import hash_token
from yaaf.server import authority, identity

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
    # a typo. Someone who reaches for their own personal token here is not
    # making a mistake about the value; they are making one about the design.
    # It used to be the admin token this refused; the rule did not change when
    # that was retired, only the credential a person might mistakenly hand over.
    if token.startswith(f"{identity.TOKEN_PREFIX}_"):
        raise HTTPException(
            status_code=401,
            detail=(
                "a personal token is not an MCP principal. Mint a scoped client "
                "token instead (`yaaf mcp-token mint`) — your own token carries "
                "everything you may do, unattributed to any machine, and a model "
                "must hold neither."
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


#: The tool being dispatched has a scope, and its scope says how much standing
#: on a project it needs: reading is for any member, steering and configuring
#: for the chairman and co-chairs. Bound by `registry.call` for the length of
#: the call, so a new steer tool that forgets to ask still asks.
_TOOL_SCOPE: ContextVar[str | None] = ContextVar("yaaf_mcp_tool_scope", default=None)
LEVEL_FOR_SCOPE = {
    "read": authority.Level.READ,
    "steer": authority.Level.STEER,
    "configure": authority.Level.STEER,
    "create": authority.Level.READ,
}


def reachable_projects(session: Session, principal: Principal,
                       level: authority.Level = authority.Level.READ) -> list[Project]:
    """Every project this principal may see, in creation order.

    Its person's memberships, read on every call — so reach tracks membership
    in both directions with nothing to reissue. Not the instance admin's
    read-everything: that is a person running the box, and a machine acting for
    them stays on the projects they are actually on.

    One function rather than the query written at each call site, because the
    gate has to hold on payloads nobody wrote for this surface — the desk is a
    cross-project rollup, and the day it stops passing through here is the day
    it lists a project the client cannot open.
    """
    user = session.get(User, principal.user_id)
    if user is None or user.deactivated_at is not None:
        return []
    return authority.reachable(session, principal.user_id, False, level)


def resolve_project(session: Session, principal: Principal, ref: str,
                    level: authority.Level | None = None) -> Project:
    """Turn an id or slug into a project this principal may act on.

    Every project-addressed tool goes through here, including ones that were
    handed an id indirectly — a run id, an approval id, a requirement key read
    out of some other project's page. A project its person is not on is
    refused in the same words as one that does not exist, so the surface does
    not become an oracle for which projects other people run; one they only
    observe is named, with the role a steer tool would need.
    """
    need = level or LEVEL_FOR_SCOPE.get(_TOOL_SCOPE.get() or "", authority.Level.READ)
    reachable = reachable_projects(session, principal)
    for project in reachable:
        if ref in (project.id, project.slug):
            have = authority.user_level(session, principal.user_id, project) or 0
            if have < need:
                raise ToolRefusal(
                    f"on {project.slug} this client's person is "
                    f"{authority.role_of(session, principal.user_id, project) or 'not a member'}"
                    f", and only {authority.HOLDERS[need]} may do this")
            return project
    raise ToolRefusal(
        f"no project {ref!r} is reachable by this client. It can see: "
        + (", ".join(p.slug for p in reachable) or "nothing")
    )
