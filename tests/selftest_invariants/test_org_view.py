"""Invariant selftest: the org chart names the role each team runs.

The overview card carried the people and an "adopt latest role version"
button, and nothing that said which role, or which version of it, those
people were working from. The one question the card exists to answer — what
charter is this team on — could only be answered by opening the role library
and guessing from names. The slug is not enough on its own: it is not what
the library shows the reader, and it carries no version.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project, seed_role


def _org(client, project_id: str) -> dict:
    response = client.get(f"/observe/projects/{project_id}/org", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_org_names_the_role_and_version_each_seat_runs(client):
    project = make_project(client, start=False)
    org = _org(client, project["id"])

    team = org["teams"][0]
    assert team["role_slug"] == "worker"
    assert team["role_name"] == "Worker"
    assert team["role_version"] == 1
    assert team["role_latest_version"] == 1

    # The CEO's role hangs off the agent, not a team, and it is the same
    # question on the same screen. No version: only teams can be repinned.
    assert org["ceo"]["role_name"] == "Ceo"
    assert org["ceo"]["role_slug"] == "ceo"


def test_org_shows_the_team_still_running_the_old_version(client):
    """A team keeps the version it was hired under until the chairman adopts
    the new one. Both numbers travel, so the card can say 'behind' without
    the reader cross-referencing the health checklist."""
    project = make_project(client, start=False)
    team_id = _org(client, project["id"])["teams"][0]["id"]

    seed_role(client, "worker", tool_permissions={"bash": True})  # v2, unadopted
    team = _org(client, project["id"])["teams"][0]
    assert (team["role_version"], team["role_latest_version"]) == (1, 2)

    assert client.post(f"/admin/teams/{team_id}/repin",
                       headers=ADMIN).status_code == 200
    team = _org(client, project["id"])["teams"][0]
    assert (team["role_version"], team["role_latest_version"]) == (2, 2)
