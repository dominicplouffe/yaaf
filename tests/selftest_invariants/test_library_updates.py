"""Updates and removals from a library source, attacked.

The dangerous update is the one that arrives after many clean ones and adds a
power: a tool, a key. Adopting it is refused until the chairman says yes to
exactly that. A listing its source removed cannot be hired again, and every
team still running it says so.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, make_project, seed_role
from tests.selftest_invariants.test_library_install import WRITER, Registry, install
from yaaf.server import library


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = Registry(tmp_path / "reg")
    reg.root.mkdir()
    reg.write()
    monkeypatch.setattr(library, "configured_sources", lambda: [reg.url])
    yield reg
    library.clear_cache()


def _project_on_writer(client):
    return make_project(client, teams=[{"role_slug": "writer", "name": "Words",
                                        "employees": ["Ann"]}], start=False)


def _team(client, project_id):
    org = client.get(f"/observe/projects/{project_id}/org", headers=ADMIN).json()
    return next(t for t in org["teams"] if t["role_slug"] == "writer")


def test_an_update_that_adds_a_power_needs_a_yes(client, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    install(client, registry, "@rae/writer")
    project = _project_on_writer(client)
    registry.publish("@rae/writer", "role", {"role": {
        **WRITER, "tool_permissions": {**WRITER["tool_permissions"], "bash": True},
        "secret_scopes": ["mailer"]}})
    assert install(client, registry, "@rae/writer", 2).json()["action"] == "updated"

    team = _team(client, project["id"])
    refused = client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN, json={})
    assert refused.status_code == 409
    assert refused.json()["detail"]["grows"] == ["bash", "the mailer key"]
    assert _team(client, project["id"])["role_version"] == 1

    adopted = client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN, json={"consent": True})
    assert adopted.status_code == 200 and adopted.json()["version"] == 2


def test_an_update_that_only_rewords_adopts_in_one_click(client, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    install(client, registry, "@rae/writer")
    project = _project_on_writer(client)
    registry.publish("@rae/writer", "role", {"role": {**WRITER, "system_prompt": WRITER["system_prompt"] + " Be brief."}})
    install(client, registry, "@rae/writer", 2)
    team = _team(client, project["id"])
    r = client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN)
    assert r.status_code == 200 and r.json()["changed"] is True


def test_a_removed_role_is_never_hired_again_and_says_so_where_it_runs(client, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    install(client, registry, "@rae/writer")
    project = _project_on_writer(client)
    registry.publish("@rae/writer", "role", {"role": WRITER}, status="removed",
                     reason="sends drafts to a third-party service")

    team = _team(client, project["id"])
    assert team["role_source"] == "@rae/writer"
    assert team["role_source_status"]["status"] == "removed"
    assert "third-party" in team["role_source_status"]["reason"]

    more = client.post(f"/admin/projects/{project['id']}/teams", headers=ADMIN,
                       json={"role_slug": "writer", "name": "More words", "employees": ["Bo"]})
    assert more.status_code == 409 and "removed from @rae/writer" in more.text

    seed_role(client, "ceo", is_ceo=True)
    new = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "second", "name": "Second", "goal": "Write one useful note for a real reader.",
        "ceo_role_slug": "ceo", "teams": [{"role_slug": "writer", "name": "W", "employees": ["C"]}]})
    assert new.status_code == 409


def test_an_unreachable_source_never_stops_a_hire(client, registry, monkeypatch):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    install(client, registry, "@rae/writer")
    (registry.root / "index.json").unlink()
    library.clear_cache()
    project = _project_on_writer(client)
    assert project["id"]


def test_updates_lists_what_each_source_says(client, registry):
    registry.publish("@rae/writer", "role", {"role": WRITER})
    install(client, registry, "@rae/writer")
    registry.publish("@rae/writer", "role", {"role": {**WRITER, "description": "v2"}})
    rows = client.get("/admin/library/updates", headers=ADMIN).json()["roles"]
    assert rows == [{**rows[0], "slug": "writer", "installed_version": 1, "latest_version": 2,
                     "update": True, "status": "active"}]


def test_a_role_edited_here_stays_one_click(client):
    """The consent rule is about a stranger's next version. A role the admin
    edited on this install adopts in one click, as it always has."""
    project = make_project(client, start=False)
    seed_role(client, "worker", tool_permissions={"bash": True})
    team = project["teams"][0]
    assert client.post(f"/admin/teams/{team['id']}/repin", headers=ADMIN).status_code == 200
