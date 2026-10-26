"""Invariant selftests: integrations — MCP servers connected once, for every project.

The administrator connects HubSpot once; each project decides to use it. The
rules these tests attack:

* **Opt in is enforced, not described.** A project that did not choose an
  integration gets neither its tools nor its credentials, even when it has a
  role of the same name as one that did.
* **Only an administrator edits the list.** It is above every project.
* **No value comes back out.** Not from the list, not to a non-admin, not in
  the project's view.
* **One shared sign-in is renewed once.** Every project shares one refresh
  token and providers rotate it on use; two projects leasing together must not
  both spend it.
* **A name means one server.** A project's own data source and an integration
  never mount under one name.
* **The allowlist binds.** A write tool outside the administrator's allowlist
  is a per-call approval, exactly as for a project's own source.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import ADMIN, agent_by_name, bearer, headers_of, live_token, make_project, person
from tests.selftest_invariants.test_mcp_oauth import MCP_URL, OAuthWorld
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Integration, ProjectIntegration, RunKind, RunQueue, Secret, utcnow
from yaaf.runner import daemon
from yaaf.server import mcp_oauth
from yaaf.server.api import admin as admin_module

URL = "https://crm.example.com/mcp"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Reachability checks never leave the test box."""
    checks: list[str | None] = []

    def check(url, bearer, transport=None):
        checks.append(bearer)
        return {"ok": True, "state": "ok", "detail": "reachable", "tools": 3}

    monkeypatch.setattr(admin_module, "_check_data_source", check)
    return checks


def _save(client, headers=ADMIN, **overrides):
    body = {"name": "hubspot", "url": URL, "bearer": "hs-secret-token",
            "description": "The company CRM and email marketing.",
            "covers": ["CRM", "email marketing"], **overrides}
    return client.post("/admin/integrations", json=body, headers=headers)


def _use(client, project_id, roles=("worker",), **extra):
    return client.put(f"/admin/projects/{project_id}/integrations/hubspot",
                      json={"decision": "use", "roles": list(roles), **extra}, headers=ADMIN)


def _lease(*project_ids_and_agents):
    with db_session() as session:
        for project_id, agent_id in project_ids_and_agents:
            session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project_id,
                                 agent_id=agent_id, kind=RunKind.AGENT_RUN,
                                 trigger={"reasons": []}, attempt=1))
    return {job.project_id: job for job in daemon.lease_and_prepare(limit=10)}


def _employee(project):
    return project["teams"][0]["agents"][0]["id"]


# ── the list ────────────────────────────────────────────────────────────────────


def test_only_an_administrator_edits_the_list(client):
    make_project(client, start=False)
    someone = person("someone@example.com")
    refused = _save(client, headers=headers_of(someone))
    assert refused.status_code == 403
    with db_session() as session:
        assert session.query(Integration).count() == 0
    assert client.delete("/admin/integrations/hubspot",
                         headers=headers_of(someone)).status_code == 403


def test_saving_stores_values_encrypted_and_never_returns_them(client):
    saved = _save(client)
    assert saved.status_code == 200, saved.text
    assert saved.json()["check"]["ok"] is True
    assert "hs-secret-token" not in saved.text and URL not in saved.text
    listed = client.get("/admin/integrations", headers=ADMIN)
    assert listed.status_code == 200
    assert "hs-secret-token" not in listed.text and "vault" not in listed.text
    row = listed.json()[0]
    assert row["name"] == "hubspot" and row["url_host"] == "crm.example.com"
    assert row["covers"] == ["crm", "email marketing"]
    with db_session() as session:
        integration = session.query(Integration).one()
        assert "hs-secret-token" not in str(integration.vault)
        assert set(integration.vault) == {"url", "token"}
        # Never a Secret row: a platform-scope secret is one role grant away
        # from every project with that role.
        assert session.query(Secret).count() == 0


def test_a_chairman_sees_the_list_but_not_who_else_uses_it(client):
    project = make_project(client, start=False)
    _save(client)
    assert _use(client, project["id"]).status_code == 200
    admin_view = client.get("/admin/integrations", headers=ADMIN).json()[0]
    assert admin_view["used_by"][0]["slug"] == "acme"
    someone = person("someone@example.com")
    chair_view = client.get("/admin/integrations", headers=headers_of(someone)).json()[0]
    assert "used_by" not in chair_view and chair_view["name"] == "hubspot"


def test_an_edit_that_omits_the_token_keeps_it(client, offline):
    _save(client)
    edited = _save(client, bearer=None, description="Now with pipelines.")
    assert edited.status_code == 200
    assert offline[-1] == "hs-secret-token"
    with db_session() as session:
        assert session.query(Integration).one().description == "Now with pipelines."


# ── opt in ──────────────────────────────────────────────────────────────────────


def test_a_project_that_did_not_choose_it_gets_nothing(client):
    """The attack: same role slug, same box, no decision. Nothing mounts."""
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    _save(client)
    assert _use(client, acme["id"]).status_code == 200
    jobs = _lease((acme["id"], _employee(acme)), (beta["id"], _employee(beta)))

    used = jobs[acme["id"]]
    assert {"name": "hubspot", "url_env": "MCP_HUBSPOT_URL", "access": "inline",
            "bearer_env": "MCP_HUBSPOT_TOKEN"} in used.tool_permissions["mcp_servers"]
    assert used.billing_env["MCP_HUBSPOT_URL"] == URL
    assert used.billing_env["MCP_HUBSPOT_TOKEN"] == "hs-secret-token"
    assert "mcp__hubspot__*" in used.system_prompt

    other = jobs[beta["id"]]
    assert "hubspot" not in str(other.tool_permissions.get("mcp_servers"))
    assert not any("HUBSPOT" in key for key in other.billing_env)
    assert "hs-secret-token" not in str(other.billing_env)


def test_only_the_chosen_roles_get_it(client):
    project = make_project(client)
    _save(client)
    assert _use(client, project["id"], roles=["ceo"]).status_code == 200
    jobs = _lease((project["id"], _employee(project)))
    assert not any("HUBSPOT" in key for key in jobs[project["id"]].billing_env)


def test_a_decline_is_recorded_and_mounts_nothing(client):
    project = make_project(client)
    _save(client)
    assert _use(client, project["id"]).status_code == 200
    declined = client.put(f"/admin/projects/{project['id']}/integrations/hubspot",
                          json={"decision": "decline", "reason": "we use Mailchimp here"},
                          headers=ADMIN)
    assert declined.status_code == 200, declined.text
    view = client.get(f"/admin/projects/{project['id']}/integrations", headers=ADMIN).json()
    assert view["used"] == [] and view["declined"][0]["reason"] == "we use Mailchimp here"
    jobs = _lease((project["id"], _employee(project)))
    assert not any("HUBSPOT" in key for key in jobs[project["id"]].billing_env)


def test_using_one_for_nobody_is_refused(client):
    project = make_project(client, start=False)
    _save(client)
    refused = _use(client, project["id"], roles=[])
    assert refused.status_code == 422 and "decline" in refused.json()["detail"]
    assert _use(client, project["id"], roles=["nobody"]).status_code == 422


def test_a_name_means_one_server(client):
    project = make_project(client, start=False)
    own = client.post(f"/admin/projects/{project['id']}/mcp_servers",
                      json={"name": "hubspot", "url": "https://other.example.com/mcp",
                            "roles": ["worker"]}, headers=ADMIN)
    assert own.status_code == 200
    _save(client)
    refused = _use(client, project["id"])
    assert refused.status_code == 409 and "promote" in refused.json()["detail"]


def test_an_integration_in_use_cannot_be_deleted(client):
    project = make_project(client, start=False)
    _save(client)
    _use(client, project["id"])
    refused = client.delete("/admin/integrations/hubspot", headers=ADMIN)
    assert refused.status_code == 409 and "Acme" in refused.json()["detail"]
    client.put(f"/admin/projects/{project['id']}/integrations/hubspot",
               json={"decision": "decline"}, headers=ADMIN)
    assert client.delete("/admin/integrations/hubspot", headers=ADMIN).status_code == 200
    with db_session() as session:
        assert session.query(ProjectIntegration).count() == 0


def test_a_project_observer_cannot_choose_for_it(client):
    project = make_project(client, start=False)
    _save(client)
    watcher = person("watcher@example.com")
    client.post(f"/admin/projects/{project['id']}/members",
                json={"email": "watcher@example.com", "role": "observer"}, headers=ADMIN)
    refused = client.put(f"/admin/projects/{project['id']}/integrations/hubspot",
                         json={"decision": "use", "roles": ["worker"]},
                         headers=headers_of(watcher))
    assert refused.status_code in (403, 404)
    with db_session() as session:
        assert session.query(ProjectIntegration).count() == 0


# ── the allowlist ───────────────────────────────────────────────────────────────


def test_the_administrators_allowlist_binds_every_project(client):
    project = make_project(client)
    _save(client, tools=["search_contacts"], access="bulk")
    _use(client, project["id"])
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    allowed = client.post("/agent/data_writes", headers=bearer(token), json={
        "source": "hubspot", "tool": "search_contacts", "arguments": {}})
    assert allowed.status_code == 422 and "already yours" in allowed.json()["error"]
    filed = client.post("/agent/data_writes", headers=bearer(token), json={
        "source": "hubspot", "tool": "send_campaign", "arguments": {"list": "all"},
        "why": "launch the spring campaign"})
    assert filed.status_code == 200, filed.text


def test_an_integration_the_project_does_not_use_is_not_its_source(client):
    project = make_project(client)
    _save(client, tools=["search_contacts"])
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    refused = client.post("/agent/data_writes", headers=bearer(token), json={
        "source": "hubspot", "tool": "send_campaign", "arguments": {}})
    assert refused.status_code == 404


# ── one shared sign-in ──────────────────────────────────────────────────────────


@pytest.fixture()
def world(monkeypatch, offline):
    fake = OAuthWorld()
    import httpx

    monkeypatch.setattr(
        mcp_oauth, "_client",
        lambda transport=None: httpx.Client(timeout=10, follow_redirects=True,
                                            transport=fake.transport))
    return fake


def _connect(client, world) -> None:
    saved = _save(client, url=MCP_URL, bearer=None, auth="oauth")
    assert saved.status_code == 200 and saved.json()["needs_connect"] is True
    started = client.post("/admin/integrations/hubspot/oauth/start", json={}, headers=ADMIN)
    assert started.status_code == 200, started.text
    state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
    world.codes.add("code-1")
    landed = client.get("/oauth/mcp/callback", params={"state": state, "code": "code-1"})
    assert landed.status_code == 200 and "The integration " in landed.text


def _age(seconds_left: int) -> None:
    with db_session() as session:
        row = session.query(Integration).one()
        entry = dict(row.entry)
        entry["oauth"] = {**entry["oauth"],
                          "expires_at": (utcnow() + timedelta(seconds=seconds_left)).isoformat()}
        row.entry = entry


def test_an_oauth_integration_signs_in_once_into_its_own_vault(client, world):
    _connect(client, world)
    with db_session() as session:
        row = session.query(Integration).one()
        assert row.entry["oauth"]["status"] == "connected"
        assert "pending" not in row.entry["oauth"]
        assert {"url", "token", "oauth-refresh"} <= set(row.vault)
        assert world.access_token not in str(row.vault)
        assert session.query(Secret).count() == 0
    view = client.get("/admin/integrations", headers=ADMIN).text
    assert world.access_token not in view and world.refresh_token not in view


def test_two_projects_leasing_together_renew_the_shared_token_once(client, world):
    """Providers rotate the refresh token on use. Two leases each spending it
    would leave the second holding a token the provider already revoked — and
    every project on the box failing auth until somebody signs in again."""
    _connect(client, world)
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    _use(client, acme["id"])
    _use(client, beta["id"])
    _age(60)
    refreshes_before = sum(1 for r in world.token_requests if r["grant_type"] == "refresh_token")
    jobs = _lease((acme["id"], _employee(acme)), (beta["id"], _employee(beta)))
    refreshes = sum(1 for r in world.token_requests
                    if r["grant_type"] == "refresh_token") - refreshes_before
    assert refreshes == 1
    assert jobs[acme["id"]].billing_env["MCP_HUBSPOT_TOKEN"] == world.access_token
    assert jobs[beta["id"]].billing_env["MCP_HUBSPOT_TOKEN"] == world.access_token
    # The refresh token never travels.
    assert world.refresh_token not in str(jobs[acme["id"]].billing_env)


def test_a_dead_shared_refresh_says_needs_reauth_once_for_everyone(client, world):
    _connect(client, world)
    project = make_project(client)
    _use(client, project["id"])
    _age(60)
    world.refresh_token = "revoked-elsewhere"
    _lease((project["id"], _employee(project)))
    row = client.get("/admin/integrations", headers=ADMIN).json()[0]
    assert row["oauth"]["status"] == "needs_reauth"
    assert "refresh token is not current" in row["oauth"]["error"]


# ── founding, export, clone ─────────────────────────────────────────────────────


def test_a_project_is_founded_with_its_decisions(client):
    _save(client)
    project = make_project(client, start=False, integration_decisions=[
        {"name": "hubspot", "use": True, "roles": ["worker", "ceo"]}])
    view = client.get(f"/admin/projects/{project['id']}/integrations", headers=ADMIN).json()
    assert view["used"][0]["roles"] == ["ceo", "worker"]


def test_founding_refuses_an_integration_that_does_not_exist(client):
    _save(client)
    with pytest.raises(AssertionError, match="no integration called 'marketo'"):
        make_project(client, start=False, integration_decisions=[
            {"name": "marketo", "use": True, "roles": ["worker"]}])


def test_founding_refuses_a_role_the_project_does_not_hire(client):
    """A grant to a role nobody holds mounts in no run at all — silently."""
    _save(client)
    with pytest.raises(AssertionError, match="does not hire"):
        make_project(client, start=False, integration_decisions=[
            {"name": "hubspot", "use": True, "roles": ["designer"]}])


def test_an_export_names_the_decisions_and_never_the_connection(client):
    _save(client)
    project = make_project(client, start=False, integration_decisions=[
        {"name": "hubspot", "use": False, "reason": "not for this one"}])
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN)
    assert exported.json()["project"]["integration_decisions"] == [
        {"name": "hubspot", "use": False, "roles": [], "reason": "not for this one",
         "mirror_tasks": False}]
    assert "hs-secret-token" not in exported.text and URL not in exported.text


def test_a_clone_uses_what_its_source_used_with_no_new_sign_in(client):
    _save(client)
    project = make_project(client, start=False, integration_decisions=[
        {"name": "hubspot", "use": True, "roles": ["worker"]}])
    twin = client.post(f"/admin/projects/{project['id']}/clone",
                       json={"slug": "acme-twin"}, headers=ADMIN)
    assert twin.status_code == 200, twin.text
    view = client.get(f"/admin/projects/{twin.json()['id']}/integrations",
                      headers=ADMIN).json()
    assert [u["name"] for u in view["used"]] == ["hubspot"]


# ── the Founder decides every one ───────────────────────────────────────────────


def _draft(**overrides) -> dict:
    return {"slug": "mailer", "name": "Mailer", "goal": "Run the spring email campaign.",
            "mode": "explore", "ceo_role_slug": "ceo",
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Ann"]}],
            **overrides}


def test_the_founder_reads_names_and_purposes_never_a_connection(client):
    from yaaf.founder import tools

    _save(client, tools=["search_contacts"])
    with db_session() as session:
        listed = tools.list_integrations(session)
    assert listed["integrations"] == [{
        "name": "hubspot", "description": "The company CRM and email marketing.",
        "covers": ["crm", "email marketing"], "task_manager": False,
        "free_tools": ["search_contacts"]}]
    assert "hs-secret-token" not in str(listed) and URL not in str(listed)
    assert "ASK the chairman" in listed["note"]


def test_a_draft_that_says_nothing_about_an_integration_is_refused(client):
    """Silence is the failure: a project told nothing about HubSpot builds its
    own email campaigns next to it."""
    from yaaf.founder import tools

    make_project(client, start=False)  # seeds the roles
    _save(client)
    with db_session() as session:
        silent = tools.integration_problems(_draft(), session)
        assert any("decide hubspot" in p for p in silent)
        used = tools.integration_problems(_draft(integration_decisions=[
            {"name": "hubspot", "use": True, "roles": ["worker"]}]), session)
        declined = tools.integration_problems(_draft(integration_decisions=[
            {"name": "hubspot", "use": False, "reason": "no marketing in this one"}]), session)
        stranger = tools.integration_problems(_draft(integration_decisions=[
            {"name": "hubspot", "use": True, "roles": ["designer"]}]), session)
        invented = tools.integration_problems(_draft(integration_decisions=[
            {"name": "hubspot", "use": False}, {"name": "marketo", "use": True,
                                                "roles": ["worker"]}]), session)
    assert used == [] and declined == []
    assert any("does not hire" in p for p in stranger)
    assert any("marketo is not on this install" in p for p in invented)


def test_with_no_integrations_the_founder_is_asked_for_nothing(client):
    from yaaf.founder import tools

    with db_session() as session:
        assert tools.integration_problems(_draft(), session) == []
