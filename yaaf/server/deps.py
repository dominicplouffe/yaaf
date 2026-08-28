"""Auth dependencies.

Two principals exist in M1:

- the *admin* (chairman) — static bearer token from settings; full user auth
  arrives with the UI milestone
- an *agent run* — the short-lived per-run token minted by the runner. It maps
  to exactly one run row, and through it one agent and one project. Forging one
  requires the SHA-256 preimage of a value that never leaves the sandbox env.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from yaaf.core.config import settings
from yaaf.core.db import session_factory
from yaaf.core.models import Agent, Project, Run, RunStatus
from yaaf.core.tokens import hash_token

# Declared security schemes give Swagger UI its global Authorize button.
admin_bearer = HTTPBearer(auto_error=False, scheme_name="AdminToken",
                          description="The YAAF_ADMIN_TOKEN value")
run_bearer = HTTPBearer(auto_error=False, scheme_name="RunToken",
                        description="A per-run agent token (minted by the runner)")


def db() -> Iterator[Session]:
    session = session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _bearer(credentials: HTTPAuthorizationCredentials | None) -> str:
    if credentials is None or not credentials.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    return credentials.credentials.strip()


def admin_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
) -> str:
    token = _bearer(credentials)
    if token != settings().admin_token:
        raise HTTPException(status_code=401, detail="bad admin token")
    return "admin"


@dataclass
class RunContext:
    run: Run
    agent: Agent
    project: Project


def run_auth(
    request: Request,
    session: Session = Depends(db),
    credentials: HTTPAuthorizationCredentials | None = Depends(run_bearer),
) -> RunContext:
    token = _bearer(credentials)
    run = (
        session.query(Run)
        .filter(Run.token_hash == hash_token(token), Run.status == RunStatus.RUNNING)
        .one_or_none()
    )
    if run is None:
        raise HTTPException(status_code=401, detail="unknown or finished run token")
    if run.token_revoked:
        raise HTTPException(
            status_code=401,
            detail=(
                "This run's token was revoked (refusal cap or kill switch). "
                "Stop and end the run; the record of why is in your run trace."
            ),
        )
    agent = session.get(Agent, run.agent_id) if run.agent_id else None
    if agent is None:
        raise HTTPException(status_code=401, detail="run has no agent principal")
    project = session.get(Project, run.project_id)
    # Exception handler uses this to count refusals against the right run.
    request.state.run_id = run.id
    _count_production(request, run)
    return RunContext(run=run, agent=agent, project=project)


#: Agent routes whose mutating calls change nothing about the project.
#: `typing` is a presence flag the boardroom draws; `chat_usage` books tokens
#: the session already spent. A run that did only these did nothing, and
#: counting them as production would make the no-op refund unclaimable by the
#: very lane — reading the mail — that it exists for.
UNPRODUCTIVE_AGENT_PATHS = frozenset({"/agent/typing", "/agent/chat_usage"})


def _count_production(request: Request, run: Run) -> None:
    """Record that this run is changing something.

    A run's cost is known the moment it starts; whether it was worth anything
    is not. So the platform counts mutating calls as they are authorised, and
    :func:`yaaf.runner.daemon.finalize` refunds the run's work allowance if the
    count is still zero when it ends — the wake happened, the model was paid,
    and the project was left exactly as it was found.

    Counted on the REQUEST'S OWN session rather than in middleware with a
    session of its own: a second connection per write serialises against the
    first under SQLite and turns every agent write into lock contention.

    Counted at authorisation rather than on the response, which means a call
    that is admitted and then refused by policy still counts. That is the safe
    direction — it can only ever leave a run metered that might have been
    refunded, never the reverse, and a refusal is already its own signal.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    path = request.url.path
    if not path.startswith("/agent/") or path in UNPRODUCTIVE_AGENT_PATHS:
        return
    run.produced = (run.produced or 0) + 1
