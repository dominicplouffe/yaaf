"""Invariant selftests: the project health checklist — every silent
misconfiguration class gets a named, plain-language finding."""

from __future__ import annotations

import re

from tests.conftest import ADMIN, make_project, seed_role


def _health(client, project_id) -> dict:
    response = client.get(f"/observe/projects/{project_id}/health", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def _by_key(health: dict, key: str) -> dict | None:
    return next((c for c in health["checks"] if c["key"] == key), None)


def _attach_billing(client, project_id) -> None:
    secret = client.post('/admin/settings/llm-credentials', json={'name': 'anthropic-api-key', 'value': 'sk-ant-api03-fake', 'mode': 'api_key'}, headers=ADMIN).json()
    assert client.patch(f"/admin/projects/{project_id}",
                        json={"billing_mode": "api_key",
                              "billing_secret_id": secret["id"]},
                        headers=ADMIN).status_code == 200


def test_billing_check_goes_green_when_attached(client):
    project = make_project(client, start=False)
    health = _health(client, project["id"])
    billing = _by_key(health, "billing")
    assert billing["status"] == "fail" and "no billing credential" in billing["detail"]
    assert health["ok"] is False

    _attach_billing(client, project["id"])
    health = _health(client, project["id"])
    assert _by_key(health, "billing")["status"] == "ok"
    assert health["all_ok"] is True  # nothing else configured => nothing else checked


def test_roles_cannot_declare_their_own_data_sources(client):
    """Data sources are project-owned. The ghost this kills: a server line
    frozen into a role template follows every project that pins the role —
    the live failure where a fresh project inherited a source named 'ppc' it
    had never heard of. The API refuses the shape, with directions to the
    right place."""
    from tests.conftest import ROLE_MIN

    refused = client.post("/admin/roles", json={
        "slug": "worker", "name": "Worker", **ROLE_MIN,
        "tool_permissions": {
            "mcp_servers": [{"name": "ppc", "url_env": "MCP_SERVER"}]},
    }, headers=ADMIN)
    assert refused.status_code == 422
    assert "Data Source" in refused.json()["detail"]
    assert "Settings" in refused.json()["detail"]

    # No orphan role or version slipped in alongside the refusal.
    assert client.get("/admin/roles/worker", headers=ADMIN).status_code == 404


def test_role_drift_warns_with_one_click_action(client):
    project = make_project(client, start=False)
    _attach_billing(client, project["id"])
    team = project["teams"][0]
    seed_role(client, "worker", tool_permissions={"bash": True})  # v2, unadopted

    health = _health(client, project["id"])
    drift = _by_key(health, f"role-drift:{team['name']}")
    assert drift["status"] == "warn"
    assert drift["action"] == {"kind": "repin", "team_id": team["id"]}
    assert health["ok"] is True  # a warn degrades all_ok, not ok
    assert health["all_ok"] is False

    client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN)
    assert _by_key(_health(client, project["id"]), f"role-drift:{team['name']}") is None


# ── what a suggestion may say ───────────────────────────────────────────────────

#: Words for the platform's insides. A chairman who reads "`recurrence:
#: standing`" or "yaaf/runtimes/catalog.py" on his project's health card has
#: been handed someone else's job, and learns to press past the card unread.
_INSIDE = re.compile(r"`|\.py\b|\b[a-z]+_[a-z_]+\b")
#: About three lines on the card. A row past this is a paragraph, and a
#: paragraph on this card is not read.
_LONGEST = 360


def test_every_suggestion_is_said_in_the_readers_words(client):
    """Each row names what is happening and what to do, briefly, in words a
    chairman uses. Rewriting the rows once does not keep them that way; this
    does, for every warning a project can light here at once."""
    from datetime import timedelta

    from yaaf.core import ids
    from yaaf.core.constitution import TEMPO_PRESETS
    from yaaf.core.db import db_session
    from yaaf.core.models import Project, Run, RunStatus, utcnow

    seen: dict[str, str] = {}

    def collect(project: dict) -> None:
        for check in _health(client, project["id"])["checks"]:
            if check["status"] == "warn":
                seen[check["key"].split(":")[0]] = check["detail"]

    operate = make_project(client, slug="desk", mode="operate",
                           controlled_domains=["github:acme"],
                           constitution={"cycle_frequency": "weekly",
                                         **TEMPO_PRESETS["live"], "monthly_usd_cap": 75.0})
    with db_session() as session:
        for _ in range(6):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=operate["id"],
                            agent_id=operate["ceo_agent_id"], kind="agent_run", trigger={},
                            status=RunStatus.DONE, cost_usd=0.15,
                            started_at=utcnow() - timedelta(days=9),
                            finished_at=utcnow() - timedelta(days=9)))
        # Provisioned at birth now; a project from before that has none.
        row = session.get(Project, operate["id"])
        row.integrations = {k: v for k, v in (row.integrations or {}).items()
                            if k != "github_webhook_secret"}
    # A version that changes what the seat runs: one that changes only its
    # note is nothing to adopt, and is not offered (yaaf.core.role_versions).
    seed_role(client, "worker", description="Checks its sources before it writes.",
              system_prompt="You are a test agent. Check your sources before you write.")
    collect(operate)
    explore = make_project(client, slug="study", mode="explore",
                           constitution={"monthly_usd_cap": 75.0})
    collect(explore)

    assert {"tempo-vs-rhythm", "tempo-vs-budget", "no-adversary", "no-standing-scope",
            "connectors", "role-drift"} <= set(seen), sorted(seen)
    for key, detail in seen.items():
        assert not _INSIDE.search(detail), f"{key} speaks the platform's words: {detail}"
        assert len(detail) <= _LONGEST, f"{key} is {len(detail)} characters: {detail}"


def test_the_missing_reviewer_is_one_click_away(client):
    """"Nobody disagrees with the plan" has one fix, a hire, so the row offers
    it, and hiring it is what clears the row."""
    project = make_project(client, slug="noadv", mode="deliver")
    seed_role(client, "plan-adversary", category="challenge")
    row = _by_key(_health(client, project["id"]), "no-adversary")
    action = row["action"]
    assert action["kind"] == "hire" and action["role_slug"] == "plan-adversary"

    hired = client.post(f"/admin/projects/{project['id']}/teams", json={
        "role_slug": action["role_slug"], "name": action["team_name"],
        "employees": [action["employee"]]}, headers=ADMIN)
    assert hired.status_code == 200, hired.text
    assert _by_key(_health(client, project["id"]), "no-adversary") is None
