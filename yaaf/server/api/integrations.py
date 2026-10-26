"""Integrations over HTTP: the administrator's list, and each project's choice.

Two halves with two authorities. The list is above every project — one
address, one sign-in, one set of allowed tools for every project that uses it
— so only an instance administrator edits it. Whether a project uses one, and
for which roles, binds that project, so it is the project's steer level like
every other data source decision.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from yaaf.core import ids
from yaaf.core.models import Integration, IntegrationDecision, ProjectIntegration, utcnow
from yaaf.server import acting, authority, integrations, mcp_oauth
from yaaf.server.deps import db
from yaaf.server.identity import Person, person_auth, signed_in

router = APIRouter(dependencies=[Depends(signed_in)])


def _refused(exc: integrations.IntegrationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.message)


def _get(session: Session, name: str) -> Integration:
    row = integrations.by_name(session, name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no integration called {name!r}")
    return row


def _audit(session: Session, project_id: str | None, action: str, entity_id: str,
           detail: dict) -> None:
    from yaaf.server.api.admin import _audit as admin_audit

    admin_audit(session, project_id, action, "integration", entity_id, detail)


# ── the list (instance administrators) ──────────────────────────────────────────


class IntegrationIn(BaseModel):
    name: str = Field(pattern=integrations.NAME_PATTERN)
    url: str
    description: str = Field(default="", max_length=2000)
    covers: list[str] = Field(default_factory=list, max_length=12)
    # Every field below reads as it does on a project's data source, for the
    # same reasons (`DataSourceIn`): absent keeps, "" removes, a value rotates.
    bearer: str | None = None
    access: Literal["inline", "bulk"] = "inline"
    tools: list[str] | None = None
    auth: Literal["none", "bearer", "oauth"] | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None


class ProbeIn(BaseModel):
    url: str
    bearer: str | None = None


@router.get("/integrations", dependencies=[authority.SIGNED_IN])
def list_integrations(session: Session = Depends(db),
                      person: Person = Depends(person_auth)) -> list[dict]:
    """The list, for anyone founding or running a project: what each one is
    and what it is for. Which projects use one is the administrator's to see —
    it names projects a chairman may not sit on."""
    rows = integrations.catalog(session)
    if person.is_admin:
        return [integrations.view(r, using=integrations.projects_using(session, r.id))
                for r in rows]
    return [integrations.view(r) for r in rows]


@router.post("/integrations/probe", dependencies=[authority.INSTANCE_ADMIN])
def probe_integration(body: ProbeIn) -> dict:
    """The form's first step: reach the address and say how it signs in."""
    from yaaf.server.api.admin import probe_source

    return probe_source(body.url, body.bearer)


@router.post("/integrations", dependencies=[authority.INSTANCE_ADMIN])
def save_integration(body: IntegrationIn, session: Session = Depends(db)) -> dict:
    """Connect a server once, for every project. Saving under an existing name
    is the edit path; the sign-in survives an edit that keeps the address, so
    changing a description never sends anyone back through a consent screen."""
    from yaaf.server.api.admin import _check_data_source, _oauth_checked, _parse_source_url

    parsed = _parse_source_url(
        body.url,
        "that is not a URL — an integration needs a full address like "
        "https://host:1234/path (schemes http/https only)")
    url = body.url.strip()
    row = integrations.by_name(session, body.name)
    created = row is None
    if row is None:
        row = Integration(id=ids.new_id(ids.INTEGRATION), name=body.name, entry={},
                          vault={}, covers=[], created_at=utcnow())
        session.add(row)
    home = integrations.IntegrationHome(row)
    prior = dict(row.entry or {})
    prior_auth = prior.get("auth") or ("bearer" if prior.get("bearer_secret") else "none")
    prior_url = home.read(integrations.URL_KEY)
    if body.auth:
        auth = body.auth
    elif body.bearer:
        auth = "bearer"
    elif not created and body.bearer is None:
        auth = prior_auth
    else:
        auth = "none"

    def drop_tokens() -> None:
        oauth = prior.get("oauth") or {}
        for key in (prior.get("bearer_secret"), oauth.get("refresh_secret"),
                    oauth.get("client_secret_secret")):
            if key:
                home.delete(key)

    if not created and prior_auth == "oauth" and auth != "oauth":
        drop_tokens()
        prior = {k: v for k, v in prior.items() if k not in ("bearer_secret", "oauth")}

    home.write({}, integrations.URL_KEY, url, granted=False)
    entry = {
        "url_secret": integrations.URL_KEY,
        "url_host": parsed.hostname,
        "url_port": parsed.port or (443 if parsed.scheme == "https" else 80),
        "access": body.access,
        "auth": auth,
    }
    allowlist = body.tools if body.tools is not None else prior.get("tools")
    if allowlist is not None:
        entry["tools"] = sorted({t.strip() for t in allowlist if t.strip()})

    needs_connect = False
    if auth == "oauth":
        token = None
        if prior_auth == "oauth" and (prior_url or "").strip() == url and prior.get("oauth"):
            oauth_state = dict(prior["oauth"])
            if prior.get("bearer_secret"):
                entry["bearer_secret"] = prior["bearer_secret"]
                token = home.read(prior["bearer_secret"])
        else:
            # A new address, or a switch from a pasted token: whatever tokens
            # exist were minted for something else.
            if not created:
                drop_tokens()
            oauth_state = {"status": "needs_auth", "resource": url}
        if body.client_id:
            oauth_state["client_id"] = body.client_id
        if body.client_secret:
            oauth_state["client_secret_secret"] = home.write(
                entry, "oauth-client", body.client_secret, granted=False)
        if body.scope is not None:
            oauth_state["scope"] = body.scope or None
        entry["oauth"] = oauth_state
        needs_connect = token is None or oauth_state.get("status") != "connected"
        check = (_oauth_checked(url, token, entry) if token else
                 {"ok": False, "state": "needs_signin",
                  "detail": "OAuth connection required — press Connect"})
    else:
        token = None
        held = prior.get("bearer_secret")
        if body.bearer:
            entry["bearer_secret"] = home.write(entry, "token", body.bearer, granted=False)
            token = body.bearer
        elif body.bearer == "" and held:
            home.delete(held)
        elif body.bearer is None and held:
            entry["bearer_secret"] = held
            token = home.read(held)
        check = _check_data_source(url, token)
    entry["last_check"] = {**check, "at": utcnow().isoformat()}
    row.entry = entry
    row.description = body.description.strip()
    row.covers = sorted({c.strip().lower() for c in body.covers if c.strip()})
    row.updated_at = utcnow()
    _audit(session, None, "integration.save", row.id,
           {"name": row.name, "host": entry["url_host"], "access": body.access,
            "auth": auth, "check_ok": check["ok"], "tools": entry.get("tools"),
            "created": created})
    session.flush()
    return {"name": row.name, "check": check, "needs_connect": needs_connect,
            "integration": integrations.view(row),
            "note": "Effective on the next run of every role a project gave it to."}


@router.get("/integrations/{name}/address", dependencies=[authority.INSTANCE_ADMIN])
def integration_address(name: str, session: Session = Depends(db)) -> dict:
    """The stored address, for the edit form — you cannot fix a port you cannot
    see. Kept out of the list and audited like a secret's reveal, because some
    servers carry a key in their query string."""
    row = _get(session, name)
    _audit(session, None, "integration.reveal_address", row.id, {"name": name})
    return {"url": integrations.IntegrationHome(row).read(integrations.URL_KEY) or ""}


@router.post("/integrations/{name}/check", dependencies=[authority.INSTANCE_ADMIN])
def check_integration(name: str, session: Session = Depends(db)) -> dict:
    from yaaf.server.api.admin import _check_data_source, _oauth_checked

    row = _get(session, name)
    home = integrations.IntegrationHome(row)
    url = home.read(integrations.URL_KEY)
    if not url:
        raise HTTPException(status_code=422, detail="the integration's address is missing — save it again")
    entry = integrations.entry_of(row)
    if entry.get("auth") == "oauth":
        token = integrations.ensure_fresh_locked(session, row)
        entry = integrations.entry_of(row)
        check = (_oauth_checked(url, token, entry) if token else
                 {"ok": False, "state": "needs_signin",
                  "detail": "OAuth connection required — press Connect"})
    else:
        check = _check_data_source(url, home.read(entry.get("bearer_secret")))
    entry["last_check"] = {**check, "at": utcnow().isoformat()}
    home.persist(entry)
    return check


@router.post("/integrations/{name}/oauth/start", dependencies=[authority.INSTANCE_ADMIN])
def start_integration_oauth(name: str, request: Request, session: Session = Depends(db)) -> dict:
    from yaaf.server.api.admin import oauth_redirect_uri

    row = _get(session, name)
    entry = integrations.entry_of(row)
    if entry.get("auth") != "oauth":
        raise HTTPException(status_code=422, detail="this integration is not set to OAuth "
                                                    "sign-in — edit it and pick OAuth first")
    try:
        authorize_url = mcp_oauth.begin_sign_in(
            integrations.IntegrationHome(row), entry, oauth_redirect_uri(request),
            by_user_id=acting.user_id())
    except mcp_oauth.OAuthSetupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _audit(session, None, "integration.oauth_start", row.id,
           {"name": name, "authorization_endpoint":
            (row.entry.get("oauth") or {}).get("authorization_endpoint")})
    return {"authorize_url": authorize_url,
            "note": "Complete the sign-in in the opened tab, then test the integration."}


@router.delete("/integrations/{name}", dependencies=[authority.INSTANCE_ADMIN])
def delete_integration(name: str, session: Session = Depends(db)) -> dict:
    """Refused while any project uses it — the same rule as an LLM credential a
    live project pays with. Deleting it out from under a project would take
    tools away from runs mid-plan, with nothing on that project saying why."""
    from sqlalchemy import delete, select

    from yaaf.core.models import TaskMirrorItem

    row = _get(session, name)
    using = integrations.projects_using(session, row.id)
    if using:
        names = ", ".join(p.name for p in using[:5]) + (" and more" if len(using) > 5 else "")
        raise HTTPException(
            status_code=409,
            detail=f"{len(using)} project(s) use {name}: {names}. Each one stops using it "
                   "in its own Settings → Data sources first, so its chairman sees it go.")
    session.execute(delete(TaskMirrorItem).where(TaskMirrorItem.integration_id == row.id))
    for decision in session.execute(select(ProjectIntegration).where(
            ProjectIntegration.integration_id == row.id)).scalars():
        session.delete(decision)
    session.flush()
    session.delete(row)
    _audit(session, None, "integration.delete", row.id, {"name": name})
    return {"deleted": name}


# ── a project's choice (the project's steer level) ──────────────────────────────


class DecisionIn(BaseModel):
    decision: Literal["use", "decline"]
    roles: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=1000)
    # Mirror this project's task board into it — only for an integration the
    # administrator set up as a task manager.
    mirror_tasks: bool | None = None


@router.get("/projects/{project_id}/integrations", dependencies=[authority.READ])
def project_integrations(project_id: str, session: Session = Depends(db)) -> dict:
    """Every integration on the box, by what this project decided about it."""
    from yaaf.server.api.admin import _get_project

    project = _get_project(session, project_id)
    decided = integrations.decisions(session, project.id)
    seen = {i.id for _, i in decided}
    return {
        "used": [integrations.decision_view(d, i) for d, i in decided
                 if d.decision == IntegrationDecision.USE],
        "declined": [integrations.decision_view(d, i) for d, i in decided
                     if d.decision == IntegrationDecision.DECLINE],
        "undecided": [integrations.view(i) for i in integrations.catalog(session)
                      if i.id not in seen],
    }


@router.put("/projects/{project_id}/integrations/{name}", dependencies=[authority.STEER])
def decide_integration(project_id: str, name: str, body: DecisionIn,
                       session: Session = Depends(db)) -> dict:
    """Use it for these roles, or decline it. Effective on each role's next run."""
    from yaaf.server.api.admin import _get_project, _latest_role_version

    project = _get_project(session, project_id)
    row = _get(session, name)
    try:
        if body.decision == IntegrationDecision.USE:
            for slug in body.roles:
                _latest_role_version(session, slug)  # 422s on unknown role
            hired = integrations.roster(session, project.id)
            if strangers := sorted(set(body.roles) - hired):
                raise HTTPException(
                    status_code=422,
                    detail=f"{', '.join(strangers)} is not a role this project runs, so "
                           f"no run would receive {name} — its roles are "
                           f"{', '.join(sorted(hired))}")
            decision = integrations.attach(session, project, row, body.roles,
                                           decided_by=acting.stamp(), reason=body.reason,
                                           mirror_tasks=body.mirror_tasks)
        else:
            decision = integrations.decline(session, project, row, decided_by=acting.stamp(),
                                            reason=body.reason)
    except integrations.IntegrationError as exc:
        raise _refused(exc) from exc
    _audit(session, project.id, f"integration.{body.decision}", row.id,
           {"name": name, "roles": decision.roles, "reason": body.reason,
            "mirror_tasks": decision.mirror_tasks})
    return {**integrations.decision_view(decision, row),
            "note": "Effective on each granted role's next run."}


@router.delete("/projects/{project_id}/integrations/{name}", dependencies=[authority.STEER])
def forget_integration(project_id: str, name: str, session: Session = Depends(db)) -> dict:
    """Back to undecided: the next founding conversation, or agent, may raise it again."""
    from yaaf.server.api.admin import _get_project

    project = _get_project(session, project_id)
    row = _get(session, name)
    if not integrations.forget(session, project.id, row.id):
        raise HTTPException(status_code=404, detail=f"this project never decided about {name}")
    _audit(session, project.id, "integration.forget", row.id, {"name": name})
    return {"forgotten": name}
