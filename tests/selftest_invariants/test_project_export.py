"""Invariant selftests: the project export, in both strengths.

The default export is the portable *design*: it must recreate the project on
any install and carries credential names, never values — this is what the
seed (wipe-survival) machinery stores. `include_values=true` is the full
backup the chairman downloads on purpose: every value travels, decrypted, so
a restore rebuilds the whole project with nothing to re-enter."""

from __future__ import annotations

import json

from tests.conftest import ADMIN, agent_by_name, amend, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentKind, Project


def _export(client, project_id: str, include_values: bool = False) -> dict:
    response = client.get(
        f"/admin/projects/{project_id}/export"
        + ("?include_values=true" if include_values else ""),
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_bundle_carries_design_not_plumbing(client):
    project = make_project(client, constitution={"queue_ceiling": 5}, start=False)

    # A service credential (its value must not travel), a billing credential
    # (must not appear at all), webhook signing keys and Slack wiring in
    # config (install plumbing — must be stripped).
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "wp-secret-value", "role_scopes": ["worker"], "kind": "service",
    }, headers=ADMIN)
    client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-api-not-real', 'mode': 'api_key'}, headers=ADMIN)
    client.post(f"/admin/projects/{project['id']}/webhook_secrets", headers=ADMIN)
    client.patch(f"/admin/projects/{project['id']}", json={
        "integrations": {"slack": {"chairman_slack_user": "U123"},
                         "runtime": "mock"},
    }, headers=ADMIN)
    # The chairman's timezone is a constitution rule, not wiring — it travels
    # in the constitution half of the bundle.
    amend(client, project["id"], timezone="America/Toronto")

    bundle = _export(client, project["id"])
    spec = bundle["project"]

    assert bundle["yaaf_bundle"] == 1
    assert bundle["values_included"] is False
    assert spec["teams"] == [{"role_slug": "worker", "name": "Alpha",
                              "employees": ["Lead", "Emp2"], "lead": "Lead",
                              "schedules": {}, "model_overrides": {},
                              "effort_overrides": {}}]
    assert spec["constitution"]["queue_ceiling"] == 5
    assert spec["ceo_role_slug"] == "ceo"
    assert spec["solo"] is False
    # Portable wiring survives; this install's plumbing does not.
    assert spec["integrations"]["runtime"] == "mock"
    for key in ("slack", "github_webhook_secret", "webhook_secret"):
        assert key not in spec["integrations"]
    # And a rule travels as a rule, so an imported twin runs under the same
    # limits rather than silently reverting to the defaults.
    assert spec["constitution"]["timezone"] == "America/Toronto"
    # Role definitions travel with their prompts; credential names travel bare.
    # `file` is the one exception and it is not a name: without it the import
    # form offers a box to type in for a credential that can only be a file.
    assert {r["slug"] for r in bundle["roles"]} == {"ceo", "worker"}
    assert all(r["system_prompt"] for r in bundle["roles"])
    assert bundle["secrets"] == [{"name": "wordpress", "role_scopes": ["worker"],
                                  "kind": "service", "file": False}]
    # No secret value anywhere in the file, under any key.
    dumped = json.dumps(bundle)
    assert "wp-secret-value" not in dumped
    assert "sk-ant-api-not-real" not in dumped


def test_data_sources_travel_without_check_results(client):
    project = make_project(client, start=False)
    added = client.post(f"/admin/projects/{project['id']}/mcp_servers", json={
        "name": "kb", "url": "http://127.0.0.1:9/mcp", "roles": ["worker"],
        "bearer": "kb-token-value",
    }, headers=ADMIN)
    assert added.status_code == 200, added.text

    bundle = _export(client, project["id"])
    servers = bundle["project"]["integrations"]["mcp_servers"]
    assert [s["name"] for s in servers] == ["kb"]
    assert "last_check" not in servers[0]
    # The data source's secrets ride along by name so an import can ask for
    # their values again.
    assert {"mcp-kb-url", "mcp-kb-token"} <= {s["name"] for s in bundle["secrets"]}
    assert "kb-token-value" not in json.dumps(bundle)


def test_bundle_recreates_the_project(client):
    original = make_project(client, constitution={"drought_hours": 12}, start=False)
    bundle = _export(client, original["id"])

    # The `project` section is a ready create body: add a slug and a chairman
    # and it must go straight through POST /admin/projects.
    created = client.post("/admin/projects", json={
        **bundle["project"], "slug": "acme-twin", "chairman_email": "chair@example.com",
    }, headers=ADMIN)
    assert created.status_code == 200, created.text

    twin = _export(client, created.json()["id"])
    # Same design, new identity: everything except the slug matches.
    assert {**bundle["project"], "slug": None} == {**twin["project"], "slug": None}


def test_solo_bundle_round_trips(client):
    original = make_project(
        client, slug="solo-src", solo=True,
        teams=[{"role_slug": "worker", "name": "Solo", "employees": []}],
        start=False,
    )
    bundle = _export(client, original["id"])
    assert bundle["project"]["solo"] is True
    assert bundle["project"]["teams"] == [{"role_slug": "worker", "name": "Solo",
                                           "employees": [], "lead": None,
                                           "schedules": {}, "model_overrides": {},
                                           "effort_overrides": {}}]

    created = client.post("/admin/projects", json={
        **bundle["project"], "slug": "solo-twin", "chairman_email": "chair@example.com",
    }, headers=ADMIN)
    assert created.status_code == 200, created.text


def test_full_backup_carries_every_value(client):
    """The chairman's explicit download: nothing of the project's held back.
    Service credentials with their decrypted values, Slack wiring, webhook
    signing keys, data-source secrets — all of it in the file, so a restore
    is a restore rather than an evening of re-pasting. The one value that is
    not the project's — the LLM credential every project shares — travels by
    name, and the form re-selects it on import."""
    project = make_project(client, start=False)
    client.post("/admin/secrets", json={
        "project_id": project["id"], "name": "wordpress",
        "value": "wp-secret-value", "role_scopes": ["worker"], "kind": "service",
    }, headers=ADMIN)
    stored = client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-api-not-real', 'mode': 'api_key'}, headers=ADMIN).json()
    client.patch(f"/admin/projects/{project['id']}", json={
        "billing_secret_id": stored["id"],
    }, headers=ADMIN)
    provisioned = client.post(f"/admin/projects/{project['id']}/webhook_secrets",
                              headers=ADMIN).json()
    client.patch(f"/admin/projects/{project['id']}", json={
        "integrations": {"slack": {"chairman_slack_user": "U123",
                                   "channel_map": {"boardroom": "C0BQ"}},
                         "runtime": "mock"},
    }, headers=ADMIN)
    client.post(f"/admin/projects/{project['id']}/mcp_servers", json={
        "name": "kb", "url": "http://127.0.0.1:9/mcp", "roles": ["worker"],
        "bearer": "kb-token-value",
    }, headers=ADMIN)

    bundle = _export(client, project["id"], include_values=True)
    spec = bundle["project"]

    assert bundle["values_included"] is True
    values = {s["name"]: s for s in bundle["secrets"]}
    assert values["wordpress"]["value"] == "wp-secret-value"
    assert values["wordpress"]["role_scopes"] == ["worker"]
    # Shared billing values stay in administrator settings, outside project backups.
    assert "anthropic-api-key" not in values
    assert values["mcp-kb-url"]["value"] == "http://127.0.0.1:9/mcp"
    assert values["mcp-kb-token"]["value"] == "kb-token-value"
    # The name is a hint; imports must select an administrator-managed credential.
    assert spec["billing_secret_name"] == "anthropic-api-key"
    # Slack wiring and this project's signing keys travel whole: a restored
    # project keeps attesting the same senders without reconfiguration.
    assert spec["integrations"]["slack"]["channel_map"] == {"boardroom": "C0BQ"}
    assert spec["integrations"]["github_webhook_secret"] == \
        provisioned["github_webhook_secret"]
    assert spec["integrations"]["webhook_secret"] == provisioned["webhook_secret"]
    # A check verdict is about this install's network at some past moment;
    # even a backup leaves it behind and the import re-proves reachability.
    assert all("last_check" not in s for s in spec["integrations"]["mcp_servers"])


def test_full_backup_round_trips_the_people_and_the_wiring(client):
    """The half of the design the old export dropped: the repo evidence
    attests against, the chairman's identity, and the people's settings —
    daily wakes, model overrides and effort overrides — must survive
    export → create."""
    project = make_project(client, start=False)
    ceo_id = project["ceo_agent_id"]
    emp_id = agent_by_name(project, "Emp2")
    for agent_id, at in ((ceo_id, "08:30"), (emp_id, "17:45")):
        set_wake = client.patch(f"/admin/agents/{agent_id}/schedule",
                                json={"daily_at": at}, headers=ADMIN)
        assert set_wake.status_code == 200, set_wake.text
    with db_session() as session:
        session.get(Project, project["id"]).github_repo = "acme/site"
        session.get(Agent, emp_id).model_override = "claude-haiku-4-5-20251001"
        session.get(Agent, emp_id).effort_override = "xhigh"
        session.get(Agent, ceo_id).effort_override = "max"

    bundle = _export(client, project["id"], include_values=True)
    spec = bundle["project"]
    assert spec["github_repo"] == "acme/site"
    assert spec["chairman_email"] == "chair@example.com"
    assert spec["ceo_daily_at"] == "08:30"
    assert spec["teams"][0]["schedules"] == {"Emp2": "17:45"}
    assert spec["teams"][0]["model_overrides"] == {"Emp2": "claude-haiku-4-5-20251001"}
    assert spec["teams"][0]["effort_overrides"] == {"Emp2": "xhigh"}
    assert spec["ceo_effort_override"] == "max"

    created = client.post("/admin/projects", json={**spec, "slug": "restored"},
                          headers=ADMIN)
    assert created.status_code == 200, created.text
    restored_id = created.json()["id"]
    with db_session() as session:
        assert session.get(Project, restored_id).github_repo == "acme/site"
        ceo = session.query(Agent).filter_by(project_id=restored_id,
                                             kind=AgentKind.CEO).one()
        assert ceo.daily_at == "08:30"
        assert ceo.effort_override == "max"
        emp = session.query(Agent).filter_by(project_id=restored_id, name="Emp2").one()
        assert emp.daily_at == "17:45"
        assert emp.model_override == "claude-haiku-4-5-20251001"
        assert emp.effort_override == "xhigh"


def test_people_settings_for_unknown_names_are_refused(client):
    """A schedule that silently fails to attach is a standup missed at 09:00
    every day with no error anywhere — so a name that matches nobody on the
    team is a refusal that names the typo, not a dropped key."""
    make_project(client, start=False)  # seeds chairman + roles
    body = {
        "slug": "typo", "name": "Typo", "goal": "Prove refusals name the typo.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Ada"],
                   "schedules": {"Adah": "09:00"}}],
    }
    refused = client.post("/admin/projects", json=body, headers=ADMIN)
    assert refused.status_code == 422
    assert "Adah" in refused.json()["detail"]

    body["teams"][0]["schedules"] = {"Ada": "9 am"}
    malformed = client.post("/admin/projects", json=body, headers=ADMIN)
    assert malformed.status_code == 422
    assert "HH:MM" in malformed.json()["detail"]
