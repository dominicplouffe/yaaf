"""Who a credential reaches: only roles this project actually employs.

The chairman met a free-text box asking for "role slugs, comma-separated" and
reasonably typed the names of the two people on his screen. The box wanted
`engineer`. Behind it, the check asked whether the role existed in the
*library* — so a project with no researcher accepted a grant to `researcher`,
stored it, injected it nowhere, and said nothing.

`agent.py` already named the failure mode for the weaker case: "a grant to a
role that does not exist grants nothing while telling everyone it did — the
worst kind of success". These tests hold that line at the roster.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, make_project, seed_role


def _need(client, project, agent_id):
    """A pending tool_request, the thing the fulfil form answers."""
    from yaaf.core import ids
    from yaaf.core.db import db_session
    from yaaf.core.models import Approval, ApprovalStatus, utcnow

    with db_session() as session:
        approval = Approval(
            id=ids.new_id(ids.APPROVAL), project_id=project["id"],
            requested_by_agent_id=agent_id, kind="tool_request",
            title="I need a GitHub token", body="cannot push without one",
            amount_usd=None, status=ApprovalStatus.PENDING,
            expires_at=utcnow() + timedelta(days=5))
        session.add(approval)
        session.flush()
        return approval.id


@pytest.fixture()
def project(client):
    return make_project(client, start=False)


def test_a_credential_can_be_granted_to_a_role_the_project_employs(client, project):
    agent_id = project["teams"][0]["agents"][0]["id"]
    approval_id = _need(client, project, agent_id)

    response = client.post(f"/admin/approvals/{approval_id}/fulfill",
                           json={"secret_name": "github-token", "value": "ghp_x",
                                 "roles": ["worker"], "note": "here you go"},
                           headers=ADMIN)
    assert response.status_code == 200, response.text


def test_a_grant_to_a_role_nobody_here_has_is_refused_with_the_roster(client, project):
    """The silent no-op this whole change is about. `researcher` is a real
    library role — it is just not on this project."""
    seed_role(client, "researcher")
    agent_id = project["teams"][0]["agents"][0]["id"]
    approval_id = _need(client, project, agent_id)

    response = client.post(f"/admin/approvals/{approval_id}/fulfill",
                           json={"secret_name": "github-token", "value": "ghp_x",
                                 "roles": ["researcher"], "note": ""},
                           headers=ADMIN)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "researcher" in detail
    # And it says what would have worked, because an error that says no without
    # saying what instead is how the chairman ended up guessing.
    assert "This project employs" in detail
    assert "worker" in detail


def test_typing_a_persons_name_is_refused_and_names_the_roles(client, project):
    """Exactly what happened: 'rafael-okonkwo, mira-tanaka' into a box that
    wanted role slugs."""
    agent_id = project["teams"][0]["agents"][0]["id"]
    approval_id = _need(client, project, agent_id)

    response = client.post(f"/admin/approvals/{approval_id}/fulfill",
                           json={"secret_name": "github-token", "value": "ghp_x",
                                 "roles": ["rafael-okonkwo", "mira-tanaka"], "note": ""},
                           headers=ADMIN)
    assert response.status_code == 422
    assert "worker" in response.json()["detail"]


def test_the_settings_credential_form_checks_the_roster_too(client, project):
    """`POST /admin/secrets` had no role check at all, so the Settings table
    stored whatever was ticked from a list of every library role."""
    seed_role(client, "designer")
    response = client.post("/admin/secrets",
                           json={"project_id": project["id"], "name": "stripe-key",
                                 "value": "sk_live_x", "role_scopes": ["designer"]},
                           headers=ADMIN)
    assert response.status_code == 422
    assert "designer" in response.json()["detail"]

    ok = client.post("/admin/secrets",
                     json={"project_id": project["id"], "name": "stripe-key",
                           "value": "sk_live_x", "role_scopes": ["worker"]},
                     headers=ADMIN)
    assert ok.status_code == 200, ok.text


def test_the_ceos_own_role_is_grantable(client, project):
    """The CEO's role hangs off the agent rather than a team. Leaving it out of
    the roster would refuse a grant the runner honours."""
    from yaaf.core.db import db_session
    from yaaf.server import policy

    with db_session() as session:
        assert "ceo" in policy.roles_on_project(session, project["id"])

    response = client.post("/admin/secrets",
                           json={"project_id": project["id"], "name": "ceo-key",
                                 "value": "x", "role_scopes": ["ceo"]},
                           headers=ADMIN)
    assert response.status_code == 200, response.text


def test_clearing_every_grant_is_still_allowed(client, project):
    """An empty list is how a chairman revokes access — it must not trip the
    roster check on its way through."""
    client.post("/admin/secrets",
                json={"project_id": project["id"], "name": "wp-key", "value": "v",
                      "role_scopes": ["worker"]}, headers=ADMIN)
    response = client.post("/admin/secrets",
                           json={"project_id": project["id"], "name": "wp-key",
                                 "role_scopes": []}, headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["role_scopes"] == []


def test_an_existing_dead_grant_is_still_readable(client, project):
    """A grant made before this rule existed must remain visible, so the UI can
    show it as reaching nobody and offer to clear it. Refusing to *read* it
    would make it permanent and invisible."""
    from yaaf.core import ids
    from yaaf.core.crypto import encrypt
    from yaaf.core.db import db_session
    from yaaf.core.models import Secret

    with db_session() as session:
        session.add(Secret(id=ids.new_id(ids.SECRET), project_id=project["id"],
                           name="legacy", ciphertext=encrypt("v"),
                           role_scopes=["gtm"], kind="service"))

    rows = client.get(f"/admin/secrets?project_id={project['id']}", headers=ADMIN).json()
    legacy = next(r for r in rows if r["name"] == "legacy")
    assert legacy["role_scopes"] == ["gtm"]
