"""Invariant selftests: the shared store (docs/SHARED_STORE.md).

One value, stored once, reaching the teams of several projects — and attacked
from every side that must not get it: a co-chair of a project the admin never
listed, an observer, a model client, a project that already holds the name,
and the old secret doors that could have reached around the new ones.

Two keys gate every item. The admin's availability (all projects, or listed
ones) and the project chairs' grant (which roles). Each test turns one key and
proves the other still holds.
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import ADMIN, headers_of, make_project, person
from tests.selftest_invariants.test_auth_roles import seat
from tests.selftest_invariants.test_mcp_oauth import MCP_URL, OAuthWorld
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, RunKind, RunQueue, Secret, SharedAccess
from yaaf.runner import daemon
from yaaf.runner.daemon import _role_secret_env
from yaaf.server.api import admin as admin_module
from yaaf.server.contextpack import role_version_for

PASSWORD = "mail-pass-not-a-real-one-4821"


@pytest.fixture(autouse=True)
def offline_check(monkeypatch):
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer: {"ok": True, "state": "ok", "detail": "fine"})


def _share(client, name, value, headers=ADMIN, **extra):
    return client.post("/admin/shared/variables", headers=headers,
                       json={"name": name, "value": value, **extra})


def _shared(client, name, value, **extra) -> dict:
    response = _share(client, name, value, **extra)
    assert response.status_code == 200, response.text
    return response.json()


def _grant(client, project_id, item_id, roles, headers=ADMIN, kind="variables"):
    return client.put(f"/admin/projects/{project_id}/shared/{kind}/{item_id}",
                      headers=headers, json={"roles": roles})


def _available_to(client, item_id, project_id, kind="variables"):
    response = client.post(f"/admin/shared/{kind}/{item_id}/projects", headers=ADMIN,
                           json={"project_id": project_id})
    assert response.status_code == 200, response.text
    return response.json()


def _env(project, name="Lead") -> dict:
    agent_id = next(a["id"] for t in project["teams"] for a in t["agents"] if a["name"] == name)
    with db_session() as session:
        agent = session.get(Agent, agent_id)
        return _role_secret_env(session, session.get(Project, project["id"]),
                                role_version_for(session, agent))


def test_one_value_reaches_the_teams_of_two_projects(client):
    """The feature in one test: EMAIL_USERNAME and EMAIL_PASSWORD stored once,
    and each project's chairs give them to the team that sends mail."""
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    user = _shared(client, "EMAIL_USERNAME", "ops@example.com", visibility="plain",
                   shared_with="all")
    password = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")

    for project in (acme, beta):
        for item in (user, password):
            granted = _grant(client, project["id"], item["id"], ["worker"])
            assert granted.status_code == 200, granted.text

    for project in (acme, beta):
        env = _env(project)
        assert env["EMAIL_USERNAME"] == "ops@example.com"
        assert env["EMAIL_PASSWORD"] == PASSWORD

    # Rotated once, in one place, it is the new value in both.
    _shared(client, "EMAIL_PASSWORD", PASSWORD + "-rotated")
    assert _env(acme)["EMAIL_PASSWORD"] == _env(beta)["EMAIL_PASSWORD"] == PASSWORD + "-rotated"

    # And the store's own row never carries a grant of its own.
    with db_session() as session:
        assert session.get(Secret, password["id"]).role_scopes == []


def test_a_listed_item_is_invisible_until_the_admin_lists_the_project(client):
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD)
    assert item["shared_with"] == "listed"  # the narrower default

    # Not listed: the same 404 an id that does not exist gets, in the same words.
    refused = _grant(client, acme["id"], item["id"], ["worker"])
    missing = _grant(client, acme["id"], ids.new_id(ids.SECRET), ["worker"])
    assert refused.status_code == missing.status_code == 404
    assert refused.json() == missing.json()
    listing = client.get(f"/admin/projects/{acme['id']}/shared", headers=ADMIN).json()
    assert listing["variables"] == []

    _available_to(client, item["id"], acme["id"])
    # Available is not received: the admin's key grants no team anything.
    assert "EMAIL_PASSWORD" not in _env(acme)
    assert _grant(client, acme["id"], item["id"], ["worker"]).status_code == 200
    assert _env(acme)["EMAIL_PASSWORD"] == PASSWORD

    # The project next door is still outside.
    assert _grant(client, beta["id"], item["id"], ["worker"]).status_code == 404
    assert "EMAIL_PASSWORD" not in _env(beta)


def test_the_store_is_the_admins_alone(client):
    """A co-chair can steer their project and nothing above it: not the store,
    not a reveal, not another project's use of a value."""
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    co = headers_of(seat(client, acme, "co@example.com", "co_chair"))

    assert client.get("/admin/shared", headers=co).status_code == 403
    assert _share(client, "EMAIL_PASSWORD", "mine-now-1234567", headers=co).status_code == 403
    assert client.get(f"/admin/shared/variables/{item['id']}/value",
                      headers=co).status_code == 403
    assert client.delete(f"/admin/shared/variables/{item['id']}", headers=co).status_code == 403
    assert client.post(f"/admin/shared/variables/{item['id']}/projects", headers=co,
                       json={"project_id": acme["id"]}).status_code == 403
    # The old doors do not reach around: the platform-scope row is the admin's.
    assert client.get(f"/admin/secrets/{item['id']}/value", headers=co).status_code == 403

    # What a co-chair may do is grant it on their own project.
    assert _grant(client, acme["id"], item["id"], ["worker"], headers=co).status_code == 200


def test_an_observer_reads_names_and_grants_nothing(client):
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    _shared(client, "REPORT_TZ", "America/Toronto", visibility="plain", shared_with="all")
    observer = headers_of(seat(client, acme, "obs@example.com", "observer"))

    assert _grant(client, acme["id"], item["id"], ["worker"], headers=observer).status_code == 403
    view = client.get(f"/admin/projects/{acme['id']}/shared", headers=observer)
    assert view.status_code == 200
    names = {row["env"] for row in view.json()["variables"]}
    assert names == {"EMAIL_PASSWORD", "REPORT_TZ"}


def test_no_project_view_carries_a_value(client):
    """Not even to the chairman, and not a plain one: the value is the
    admin's, and a project's screen reaches everyone that project lets in."""
    acme = make_project(client, slug="acme")
    _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    _shared(client, "REPORT_TZ", "America/Toronto", visibility="plain", shared_with="all")
    view = client.get(f"/admin/projects/{acme['id']}/shared", headers=ADMIN).json()
    text = json.dumps(view)
    assert PASSWORD not in text and PASSWORD[-4:] not in text
    assert "America/Toronto" not in text
    for row in view["variables"]:
        assert "value" not in row and "hint" not in row and "projects" not in row

    # The admin's own store does show them, as the project list does its own.
    store = client.get("/admin/shared", headers=ADMIN).json()
    rows = {r["env"]: r for r in store["variables"]}
    assert rows["REPORT_TZ"]["value"] == "America/Toronto"
    assert rows["EMAIL_PASSWORD"]["hint"] == PASSWORD[-4:]


def test_a_grant_is_held_to_the_projects_own_roster(client):
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    refused = _grant(client, acme["id"], item["id"], ["researcher"])
    assert refused.status_code == 422 and "This project employs" in refused.json()["detail"]


def test_one_name_has_one_value_and_the_newer_one_wins(client):
    """Two values under one name would leave an agent guessing which it holds.
    So the newer replaces the older, in either order, and the answer says what
    it replaced."""
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")

    # The project held its own; granting the shared one replaces it.
    own = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": acme["id"], "name": "email-password", "value": "the-projects-own-9876",
        "role_scopes": ["worker"]})
    assert own.status_code == 200, own.text
    granted = _grant(client, acme["id"], item["id"], ["worker"])
    assert granted.status_code == 200, granted.text
    assert granted.json()["replaced"] == ["$EMAIL_PASSWORD"]
    with db_session() as session:
        assert session.get(Secret, own.json()["id"]) is None
    assert _env(acme)["EMAIL_PASSWORD"] == PASSWORD

    # Then the project stores its own again: that one wins, and the shared one
    # is taken back from this project's teams (access kept, nobody receiving).
    again = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": acme["id"], "name": "EMAIL_PASSWORD", "value": "the-projects-own-9876",
        "role_scopes": ["worker"]})
    assert again.status_code == 200, again.text
    assert again.json()["replaced"] == ["shared variable EMAIL_PASSWORD"]
    assert _env(acme)["EMAIL_PASSWORD"] == "the-projects-own-9876"
    with db_session() as session:
        assert session.query(SharedAccess).one().role_scopes == []

    # Never both at once on the screen either.
    view = client.get(f"/admin/projects/{acme['id']}/shared", headers=ADMIN).json()
    assert view["variables"][0]["roles"] == []


def test_a_value_something_else_runs_on_is_never_overwritten(client):
    """A Slack bridge token is not a duplicate of a shared variable; replacing
    it would break the bridge. That one clash is still refused."""
    acme = make_project(client, slug="acme")
    client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": acme["id"], "name": "slack-bot", "value": "xoxb-not-real-123456"})
    with db_session() as session:
        project = session.get(Project, acme["id"])
        project.integrations = {**(project.integrations or {}),
                                "slack": {"bot_token_secret": "slack-bot"}}
    item = _shared(client, "SLACK_BOT", "another-token-123456", shared_with="all")
    refused = _grant(client, acme["id"], item["id"], ["worker"])
    assert refused.status_code == 422 and "Slack" in refused.json()["detail"]


def _cipher(value: str) -> str:
    from yaaf.core.crypto import encrypt

    return encrypt(value)


def test_names_that_belong_to_something_else_are_refused(client):
    for name in ("yaaf-api-token", "MCP_CRM_URL", "github", "platform-thing"):
        refused = _share(client, name, "a-value-of-some-length")
        assert refused.status_code == 422, (name, refused.text)


def test_a_second_spelling_of_a_shared_name_is_the_same_item(client):
    first = _shared(client, "EMAIL_PASSWORD", PASSWORD)
    again = _shared(client, "email-password", "another-value-1234")
    assert again["id"] == first["id"] and again["rotated"] is True
    store = client.get("/admin/shared", headers=ADMIN).json()
    assert [r["env"] for r in store["variables"]] == ["EMAIL_PASSWORD"]


def test_a_shared_rows_own_role_scopes_reach_nobody(client):
    """The hole this closes: a platform-scope row with role_scopes used to be
    injected into every project that hired the role. Hand-edit one in, with
    no access row anywhere, and nothing leaks."""
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    with db_session() as session:
        session.get(Secret, item["id"]).role_scopes = ["worker"]
        session.add(Secret(id=ids.new_id(ids.SECRET), project_id=None, name="box-wide",
                           ciphertext=_cipher("box-wide-value"), role_scopes=["worker"],
                           kind="service"))
    env = _env(acme)
    assert "EMAIL_PASSWORD" not in env and "BOX_WIDE" not in env


def test_the_old_secret_doors_refuse_a_shared_row(client):
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD)
    deleted = client.delete(f"/admin/secrets/{item['id']}", headers=ADMIN)
    assert deleted.status_code == 422 and "shared store" in deleted.json()["detail"]
    rewritten = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": None, "name": "EMAIL_PASSWORD", "value": "overwritten-value-12"})
    assert rewritten.status_code == 422 and "shared store" in rewritten.json()["detail"]


def test_a_used_item_cannot_be_deleted_until_each_project_lets_go(client):
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD)
    _available_to(client, item["id"], acme["id"])
    assert _grant(client, acme["id"], item["id"], ["worker"]).status_code == 200

    refused = client.delete(f"/admin/shared/variables/{item['id']}", headers=ADMIN)
    assert refused.status_code == 409 and "Acme" in refused.json()["detail"]

    revoked = client.post(f"/admin/shared/variables/{item['id']}/revoke", headers=ADMIN,
                          json={"project_id": acme["id"]})
    assert revoked.status_code == 200 and revoked.json()["was"] == ["worker"]
    assert "EMAIL_PASSWORD" not in _env(acme)
    # On the project's own record, where its chairman reads what happened to it.
    from yaaf.core.models import AuditLog

    with db_session() as session:
        actions = {a.action for a in session.query(AuditLog).filter_by(project_id=acme["id"])}
    assert {"shared.available", "shared.grant", "shared.revoke"} <= actions

    assert client.delete(f"/admin/shared/variables/{item['id']}",
                         headers=ADMIN).status_code == 200


def test_narrowing_to_listed_keeps_projects_already_using_it(client):
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    assert _grant(client, acme["id"], item["id"], ["worker"]).status_code == 200

    narrowed = client.post("/admin/shared/variables", headers=ADMIN,
                           json={"name": "EMAIL_PASSWORD", "shared_with": "listed"})
    assert narrowed.status_code == 200, narrowed.text
    assert _env(acme)["EMAIL_PASSWORD"] == PASSWORD
    assert _grant(client, beta["id"], item["id"], ["worker"]).status_code == 404


def test_a_shared_file_reaches_the_sandbox_as_a_path(client):
    import base64

    acme = make_project(client, slug="acme")
    body = b'{"type": "service_account", "project_id": "x"}'
    item = _shared(client, "gcp-key", base64.b64encode(body).decode(),
                   filename="gcp.json", shared_with="all")
    plain = _share(client, "gcp-key-2", base64.b64encode(body).decode(), filename="g.json",
                   visibility="plain")
    assert plain.status_code == 422
    assert _grant(client, acme["id"], item["id"], ["worker"]).status_code == 200
    carried = json.loads(_env(acme)["YAAF_FILE_CRED__GCP_KEY"])
    assert carried["filename"] == "gcp.json"
    assert base64.b64decode(carried["b64"]) == body


# ── data sources ────────────────────────────────────────────────────────────────


def _source(client, **extra):
    body = {"name": "crm", "url": "http://crm.example.com:3845/mcp", "bearer": "tok-shared",
            "shared_with": "all", **extra}
    return client.post("/admin/shared/data-sources", headers=ADMIN, json=body)


def test_a_shared_source_mounts_for_the_granted_role_only(client):
    acme = make_project(client, slug="acme")
    saved = _source(client)
    assert saved.status_code == 200, saved.text
    source = saved.json()
    assert source["check"]["ok"] is True
    assert _grant(client, acme["id"], source["id"], ["worker"],
                  kind="data-sources").status_code == 200

    employee = acme["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=acme["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    job = daemon.lease_and_prepare(limit=1)[0]
    assert {"name": "crm", "url_env": "MCP_CRM_URL", "access": "inline",
            "bearer_env": "MCP_CRM_TOKEN"} in job.tool_permissions["mcp_servers"]
    assert job.billing_env["MCP_CRM_URL"] == "http://crm.example.com:3845/mcp"
    assert job.billing_env["MCP_CRM_TOKEN"] == "tok-shared"
    assert "crm" in job.system_prompt

    # The CEO's role was never granted it.
    with db_session() as session:
        ceo = session.get(Agent, acme["ceo_agent_id"])
        ceo_env = _role_secret_env(session, session.get(Project, acme["id"]),
                                   role_version_for(session, ceo))
    assert "MCP_CRM_URL" not in ceo_env


def test_a_project_source_and_a_shared_one_replace_each_other(client):
    acme = make_project(client, slug="acme")
    source = _source(client).json()
    own = client.post(f"/admin/projects/{acme['id']}/mcp_servers", headers=ADMIN, json={
        "name": "crm", "url": "http://own.example.com/mcp", "roles": ["worker"],
        "bearer": "own-token"})
    assert own.status_code == 200

    granted = _grant(client, acme["id"], source["id"], ["worker"], kind="data-sources")
    assert granted.status_code == 200, granted.text
    assert granted.json()["replaced"] == ["data source 'crm'"]
    with db_session() as session:
        assert "mcp_servers" not in (session.get(Project, acme["id"]).integrations or {}) \
            or not session.get(Project, acme["id"]).integrations["mcp_servers"]
        # Its address and token went with it: nothing half-wired is left.
        assert session.query(Secret).filter(Secret.project_id == acme["id"],
                                            Secret.name.like("mcp-crm-%")).count() == 0
    assert _env(acme)["MCP_CRM_TOKEN"] == "tok-shared"

    again = client.post(f"/admin/projects/{acme['id']}/mcp_servers", headers=ADMIN, json={
        "name": "crm", "url": "http://own.example.com/mcp", "roles": ["worker"],
        "bearer": "own-token"})
    assert again.status_code == 200, again.text
    assert again.json()["replaced"] == ["shared data source 'crm'"]
    assert _env(acme)["MCP_CRM_TOKEN"] == "own-token"


def test_an_approved_write_finds_a_shared_sources_allowlist(client):
    """A shared source with an allowlist must be approvable like a project's,
    or its unlisted tools would be unreachable by any door."""
    from tests.conftest import agent_by_name, bearer, live_token

    acme = make_project(client, slug="acme")
    source = _source(client, tools=["read_contacts"]).json()
    _grant(client, acme["id"], source["id"], ["worker"], kind="data-sources")
    _, token = live_token(acme["id"], agent_by_name(acme, "Lead"))
    filed = client.post("/agent/data_writes", headers=bearer(token), json={
        "source": "crm", "tool": "delete_contact", "arguments": {"id": 7},
        "why": "a duplicate"})
    assert filed.status_code == 200, filed.text


# ── the CEO ─────────────────────────────────────────────────────────────────────


def test_the_ceo_sees_shared_names_and_delegates_through_the_chairs_door(client):
    from tests.conftest import bearer, live_token

    acme = make_project(client, slug="acme", constitution={"capability_authority": "ceo"})
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    _, token = live_token(acme["id"], acme["ceo_agent_id"])

    roster = client.get("/agent/credentials", headers=bearer(token)).json()
    row = next(r for r in roster if r["name"] == "EMAIL_PASSWORD")
    assert row == {"name": "EMAIL_PASSWORD", "role_scopes": [], "file": False, "shared": True}
    assert PASSWORD not in json.dumps(roster)

    scoped = client.post("/agent/credentials/EMAIL_PASSWORD/scope", headers=bearer(token),
                         json={"add_roles": ["worker"], "reason": "the mail team sends it"})
    assert scoped.status_code == 200, scoped.text
    assert _env(acme)["EMAIL_PASSWORD"] == PASSWORD

    stranger = client.post("/agent/credentials/EMAIL_PASSWORD/scope", headers=bearer(token),
                           json={"add_roles": ["researcher"], "reason": "nobody hired them"})
    assert stranger.status_code == 422
    with db_session() as session:
        held = session.query(SharedAccess).filter_by(item_id=item["id"]).one()
        assert held.role_scopes == ["worker"]


def test_a_person_with_no_standing_learns_nothing(client):
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    outsider = headers_of(person("out@example.com"))
    assert client.get(f"/admin/projects/{acme['id']}/shared",
                      headers=outsider).status_code == 404
    assert _grant(client, acme["id"], item["id"], ["worker"],
                  headers=outsider).status_code == 404


# ── the rest of the footprint ───────────────────────────────────────────────────


def test_an_export_names_grants_and_carries_no_shared_value(client):
    """Not even the backup export, which decrypts the project's own values:
    a shared value is the admin's, and one project's backup is not its."""
    acme = make_project(client, slug="acme")
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    _grant(client, acme["id"], item["id"], ["worker"])
    for values in ("false", "true"):
        bundle = client.get(f"/admin/projects/{acme['id']}/export?include_values={values}",
                            headers=ADMIN).json()
        assert {"kind": "variable", "name": "EMAIL_PASSWORD",
                "roles": ["worker"]} in bundle["shared_grants"]
        assert PASSWORD not in json.dumps(bundle)


def test_a_clone_carries_open_grants_and_leaves_listed_ones_behind(client):
    """The admin listed the source project, not its twin: copying that access
    would let whoever clones a project widen what the admin approved."""
    acme = make_project(client, slug="acme")
    open_item = _shared(client, "EMAIL_USERNAME", "ops@example.com", visibility="plain",
                        shared_with="all")
    listed = _shared(client, "EMAIL_PASSWORD", PASSWORD)
    _available_to(client, listed["id"], acme["id"])
    for item in (open_item, listed):
        assert _grant(client, acme["id"], item["id"], ["worker"]).status_code == 200

    twin = client.post(f"/admin/projects/{acme['id']}/clone", headers=ADMIN,
                       json={"slug": "acme-twin"})
    assert twin.status_code == 200, twin.text
    assert twin.json()["copied"]["shared_left_behind"] == ["EMAIL_PASSWORD"]
    with db_session() as session:
        twin_project = session.get(Project, twin.json()["id"])
        team = twin.json()["teams"][0]
        agent = session.get(Agent, team["agents"][0]["id"])
        env = _role_secret_env(session, twin_project, role_version_for(session, agent))
    assert env["EMAIL_USERNAME"] == "ops@example.com"
    assert "EMAIL_PASSWORD" not in env


def test_health_and_setup_count_what_the_store_gives(client, monkeypatch):
    acme = make_project(client, slug="acme", start=False)
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer: {"ok": False, "state": "unreachable",
                                             "detail": "nothing is listening"})
    source = _source(client).json()
    item = _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    steps = {s["key"]: s["state"] for s in client.get(
        f"/observe/projects/{acme['id']}/setup", headers=ADMIN).json()["steps"]}
    assert steps["sources"] == "todo" and steps["secrets"] == "todo"

    _grant(client, acme["id"], source["id"], ["worker"], kind="data-sources")
    _grant(client, acme["id"], item["id"], ["worker"])
    steps = {s["key"]: s["state"] for s in client.get(
        f"/observe/projects/{acme['id']}/setup", headers=ADMIN).json()["steps"]}
    assert steps["sources"] == "done" and steps["secrets"] == "done"

    checks = client.get(f"/observe/projects/{acme['id']}/health", headers=ADMIN).json()["checks"]
    row = next(c for c in checks if c["key"] == "data-source:crm")
    assert row["status"] == "fail" and "instance admin" in row["detail"]


def test_a_model_client_lists_and_grants_but_never_reads_a_value(client):
    from tests.selftest_invariants.test_mcp_confirm import call
    from tests.selftest_invariants.test_mcp_secrets import configurer

    make_project(client, slug="acme")
    _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    _shared(client, "REPORT_TZ", "America/Toronto", visibility="plain", shared_with="all")
    token = configurer(client)["token"]

    failed, listing = call(client, token, "shared", {"project": "acme", "verb": "list"})
    assert failed is False
    assert {r["env"] for r in listing["variables"]} == {"EMAIL_PASSWORD", "REPORT_TZ"}
    assert PASSWORD not in json.dumps(listing) and "America/Toronto" not in json.dumps(listing)

    args = {"project": "acme", "verb": "grant", "kind": "variable",
            "name": "EMAIL_PASSWORD", "roles": ["worker"]}
    _, preview = call(client, token, "shared", args)
    assert preview["confirm_required"] is True
    failed, _ = call(client, token, "shared", {**args, "confirm": preview["confirm"]})
    assert failed is False
    with db_session() as session:
        assert session.query(SharedAccess).one().role_scopes == ["worker"]


def test_the_ceo_scopes_a_shared_name_but_never_removes_the_chairmans(client):
    from tests.conftest import bearer, live_token

    acme = make_project(client, slug="acme", constitution={"capability_authority": "ceo"})
    _shared(client, "EMAIL_PASSWORD", PASSWORD, shared_with="all")
    own = client.post("/admin/secrets", headers=ADMIN, json={
        "project_id": acme["id"], "name": "email-password", "value": "the-chairmans-own-1"})
    _, token = live_token(acme["id"], acme["ceo_agent_id"])
    refused = client.post("/agent/credentials/EMAIL_PASSWORD/scope", headers=bearer(token),
                          json={"add_roles": ["worker"], "reason": "the mail team sends it"})
    assert refused.status_code == 422 and "chairman's call" in refused.text
    with db_session() as session:
        assert session.get(Secret, own.json()["id"]) is not None


# ── one OAuth connection, every project ─────────────────────────────────────────

@pytest.fixture()
def oauth_world(monkeypatch) -> OAuthWorld:
    """The fake provider the project-source OAuth suite runs against: it
    rotates refresh tokens on use, as real providers do."""
    import httpx

    from yaaf.server import mcp_oauth

    fake = OAuthWorld()
    monkeypatch.setattr(mcp_oauth, "_client",
                        lambda transport=None: httpx.Client(timeout=10, follow_redirects=True,
                                                            transport=fake.transport))
    monkeypatch.setattr(admin_module, "_check_data_source",
                        lambda url, bearer, transport=None: {"ok": True, "detail": "reachable"})
    return fake


def _oauth_source(client, world) -> dict:
    """Save, start, and finish the one sign-in a shared OAuth source needs."""
    from urllib.parse import parse_qs, urlparse

    saved = client.post("/admin/shared/data-sources", headers=ADMIN, json={
        "name": "vault", "url": MCP_URL, "auth": "oauth", "shared_with": "all"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["needs_connect"] is True
    source = saved.json()
    started = client.post(f"/admin/shared/data-sources/{source['id']}/oauth/start",
                          headers=ADMIN, json={})
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["authorize_url"]).query)
    assert query["resource"] == [MCP_URL] and "code_challenge" in query
    world.codes.add("code-1")
    landed = client.get("/oauth/mcp/callback",
                        params={"state": query["state"][0], "code": "code-1"})
    assert landed.status_code == 200 and "every project" in landed.text
    replay = client.get("/oauth/mcp/callback",
                        params={"state": query["state"][0], "code": "code-1"})
    assert replay.status_code == 400
    return source


def _lease(project) -> object:
    employee = project["teams"][0]["agents"][0]["id"]
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=employee, kind=RunKind.AGENT_RUN,
                             trigger={"reasons": []}, attempt=1))
    return daemon.lease_and_prepare(limit=1)[0]


def _age(source_id: str, seconds: int) -> None:
    from datetime import timedelta

    from yaaf.core.models import SharedDataSource, utcnow
    from yaaf.server.shared_oauth import persist

    with db_session() as session:
        row = session.get(SharedDataSource, source_id)
        entry = dict(row.entry)
        entry["oauth"] = {**entry["oauth"],
                          "expires_at": (utcnow() + timedelta(seconds=seconds)).isoformat()}
        persist(row, entry)


def test_one_oauth_sign_in_serves_every_project(oauth_world, client):
    """The point: one connection, read by every project granted it — and
    renewed once, on the store's row, never once per project. Two copies
    would spend each other's rotating refresh token."""
    acme = make_project(client, slug="acme")
    beta = make_project(client, slug="beta")
    source = _oauth_source(client, oauth_world)
    connected = oauth_world.access_token
    for project in (acme, beta):
        assert _grant(client, project["id"], source["id"], ["worker"],
                      kind="data-sources").status_code == 200

    assert _lease(acme).billing_env["MCP_VAULT_TOKEN"] == connected
    assert _lease(beta).billing_env["MCP_VAULT_TOKEN"] == connected

    # Near expiry: the first lease renews it; the second finds the renewed
    # token instead of spending the refresh token the first one rotated.
    _age(source["id"], 60)
    renewals = len([r for r in oauth_world.token_requests if r["grant_type"] == "refresh_token"])
    first = _lease(acme).billing_env["MCP_VAULT_TOKEN"]
    second = _lease(beta).billing_env["MCP_VAULT_TOKEN"]
    assert first == second == oauth_world.access_token != connected
    assert len([r for r in oauth_world.token_requests
                if r["grant_type"] == "refresh_token"]) == renewals + 1

    # No project holds a copy of any of it.
    with db_session() as session:
        assert session.query(Secret).filter(Secret.name.like("mcp-vault-%")).count() == 0
    view = client.get(f"/admin/projects/{acme['id']}/shared", headers=ADMIN).json()
    assert view["data_sources"][0]["oauth_status"] == "connected"
    assert oauth_world.refresh_token not in json.dumps(view)
    store = client.get("/admin/shared", headers=ADMIN).json()
    assert oauth_world.refresh_token not in json.dumps(store)
    assert oauth_world.access_token not in json.dumps(store)


def test_a_dead_shared_connection_says_so_on_every_project(oauth_world, client):
    acme = make_project(client, slug="acme")
    source = _oauth_source(client, oauth_world)
    _grant(client, acme["id"], source["id"], ["worker"], kind="data-sources")
    _age(source["id"], 60)
    oauth_world.refresh_token = "revoked-elsewhere"
    checked = client.post(f"/admin/shared/data-sources/{source['id']}/check",
                          headers=ADMIN, json={})
    assert checked.status_code == 200
    checks = client.get(f"/observe/projects/{acme['id']}/health", headers=ADMIN).json()["checks"]
    row = next(c for c in checks if c["key"] == "data-source:vault")
    assert row["status"] == "fail" and "expired" in row["detail"]


def test_an_edit_that_keeps_the_address_keeps_the_sign_in(oauth_world, client):
    source = _oauth_source(client, oauth_world)
    edited = client.post("/admin/shared/data-sources", headers=ADMIN, json={
        "name": "vault", "url": MCP_URL, "access": "bulk"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["needs_connect"] is False
    assert edited.json()["oauth_status"] == "connected"
    moved = client.post("/admin/shared/data-sources", headers=ADMIN, json={
        "name": "vault", "url": MCP_URL + "/v2", "auth": "oauth"})
    assert moved.json()["needs_connect"] is True
    assert source["id"] == moved.json()["id"]
