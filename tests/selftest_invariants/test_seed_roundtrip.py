"""Invariant selftests: the seed is what survives a wipe.

The rebuild drops the database. Roles are global and survive on their own, but
a goal is a page of writing that took longer than the code around it, and a
constitution is a set of decisions somebody argued about. `save-seed` writes
the design out and `seed` puts it back — and what must NOT come back with it is
this install's plumbing: no secrets, no Slack wiring, no webhook signing keys.

These run the export and import halves against the real API, so a seed folder
that cannot be restored fails here rather than on the morning of the reset.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, amend, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Project


def _export(client, project_id: str) -> dict:
    response = client.get(f"/admin/projects/{project_id}/export", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_seeded_project_carries_the_design_and_none_of_the_plumbing(client, tmp_path):
    project = make_project(client, slug="origin", start=False)
    amend(client, project["id"], reason="the mandate needs a tighter board",
          queue_ceiling=4, timezone="America/Toronto")
    client.patch(f"/admin/projects/{project['id']}", json={
        "integrations": {"runtime": "mock", "slack": {"chairman_slack_user": "U123"}},
    }, headers=ADMIN)
    client.post(f"/admin/projects/{project['id']}/webhook_secrets", headers=ADMIN)

    bundle = _export(client, project["id"])
    spec = bundle["project"]

    # Design travels.
    assert spec["goal"] == "Prove the framework works end to end."
    assert spec["constitution"]["queue_ceiling"] == 4
    assert spec["constitution"]["timezone"] == "America/Toronto"
    assert spec["integrations"]["runtime"] == "mock"
    assert spec["teams"] and spec["ceo_role_slug"] == "ceo"

    # Plumbing does not — a seed folder is not a place to leak signing keys.
    for key in ("slack", "github_webhook_secret", "webhook_secret"):
        assert key not in spec["integrations"], f"{key} must not travel in a seed"

    # And it round-trips: the spec the export produced is a body create accepts.
    spec["slug"] = "restored"
    spec["chairman_email"] = "chair@example.com"
    created = client.post("/admin/projects", json=spec, headers=ADMIN)
    assert created.status_code == 200, created.text

    restored = client.get(f"/observe/projects/{created.json()['id']}", headers=ADMIN).json()
    assert restored["goal"] == "Prove the framework works end to end."
    assert restored["constitution"]["queue_ceiling"] == 4
    assert restored["constitution"]["timezone"] == "America/Toronto"
    assert restored["status"] == "draft", "a restored project must not start spending by itself"


def test_a_rule_survives_the_wipe_but_a_limit_left_in_wiring_would_not(client):
    """Why the split earns its keep on exactly this operation: the constitution
    half of a bundle is restored as rules. Anything that had been left in
    integrations would come back as an inert key nobody reads."""
    project = make_project(client, slug="ruleful", start=False)
    amend(client, project["id"], reason="this box shares a rate limit",
          max_concurrent_runs=1, ceo_heartbeat_hours=12.0)

    spec = _export(client, project["id"])["project"]
    assert spec["constitution"]["max_concurrent_runs"] == 1
    assert spec["constitution"]["ceo_heartbeat_hours"] == 12.0
    assert "max_concurrent_runs" not in spec["integrations"]

    spec["slug"] = "ruleful-2"
    spec["chairman_email"] = "chair@example.com"
    twin = client.post("/admin/projects", json=spec, headers=ADMIN)
    assert twin.status_code == 200, twin.text

    from yaaf.core.constitution import effective

    with db_session() as session:
        cons = effective(session, twin.json()["id"])
    assert cons.max_concurrent_runs == 1
    assert cons.ceo_heartbeat_hours == 12.0


def test_save_seed_writes_a_folder_seed_can_read(client, tmp_path):
    """The two halves are only useful as a pair, so they are tested as one."""
    from yaaf.core import seed as seedlib

    make_project(client, slug="origin", start=False)
    # The seed talks over HTTP like everything else; the test client stands in
    # for the network, and the admin header rides on it.
    client.headers.update(ADMIN)
    seedlib.save_seed(client, tmp_path)

    roles = sorted(p.name for p in (tmp_path / "roles").glob("*.json"))
    assert roles == ["ceo.json", "worker.json"]
    written = json.loads((tmp_path / "roles" / "ceo.json").read_text())
    assert written["system_prompt"], "a role without its prompt is not a role"
    assert "version" not in written, "a seeded role starts a fresh version chain"

    spec = json.loads((tmp_path / "projects" / "origin.json").read_text())["project"]
    assert spec["goal"] == "Prove the framework works end to end."

    # Now wipe the projects and put it back through the real command.
    with db_session() as session:
        session.query(Project).filter_by(slug="origin").update({"slug": "origin-old"})

    seedlib.restore_roles(client, tmp_path)
    restored = seedlib.restore_projects(client, tmp_path, "chair@example.com")
    assert all(status == 200 for _, status in restored), restored

    listed = {p["slug"] for p in client.get("/observe/projects", headers=ADMIN).json()}
    assert "origin" in listed, "the seeded project did not come back"
