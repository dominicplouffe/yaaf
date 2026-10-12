"""People on the box: who manages them, and the two things that must always hold.

docs/AUTH.md rule 6: **the box always has an admin, and a live project always
has a chairman who can sign in.** Both are refusals rather than warnings,
because a box nobody can administer and a project nobody may decide on are
each a deadlock that no amount of telling anybody fixes.

And the half of "who may do what" that is about people rather than projects:
only an instance admin invites, promotes or deactivates; a project is chaired
by whoever founds it; a model client acts for whoever mints it.
"""

from __future__ import annotations

from typer.testing import CliRunner

from tests.conftest import (
    ADMIN,
    CHAIR_EMAIL,
    PASSWORD,
    bearer,
    headers_of,
    make_project,
    person,
    sign_in,
)
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from yaaf.cli.users import users as users_cli
from yaaf.core.db import db_session
from yaaf.core.models import User


def _user_id(email: str) -> str:
    with db_session() as session:
        return session.query(User).filter_by(email=email).one().id


def test_only_an_instance_admin_manages_people(client):
    member = headers_of(person("member@example.com"))
    target = _user_id("member@example.com")
    assert client.get("/admin/users", headers=member).status_code == 403
    assert client.post("/admin/users", json={"email": "x@example.com", "display_name": "X"},
                       headers=member).status_code == 403
    assert client.patch(f"/admin/users/{target}", json={"is_admin": True},
                        headers=member).status_code == 403
    assert client.post(f"/admin/users/{target}/setup-link",
                       headers=member).status_code == 403
    # Not even on themselves: promoting yourself is the whole attack.
    with db_session() as session:
        assert session.get(User, target).is_admin is False


def test_an_invite_is_an_account_with_no_password_and_a_link(client):
    invited = client.post("/admin/users", json={"email": "New@Example.com",
                                                "display_name": "New"}, headers=ADMIN)
    assert invited.status_code == 200, invited.text
    body = invited.json()
    assert body["email"] == "new@example.com"
    assert body["has_password"] is False and "/app/#/setup?token=ysl_" in body["setup_url"]
    again = client.post("/admin/users", json={"email": "new@example.com",
                                              "display_name": "New"}, headers=ADMIN).json()
    assert again["existing"] is True and "setup_url" not in again


def test_the_last_admin_cannot_be_demoted_or_deactivated(client):
    me = _user_id(CHAIR_EMAIL)
    for change in ({"is_admin": False}, {"active": False}):
        refused = client.patch(f"/admin/users/{me}", json=change, headers=ADMIN)
        assert refused.status_code == 409, change
        assert "last active admin" in refused.json()["detail"]
    # With a second admin, stepping down is allowed.
    person("deputy@example.com", admin=True)
    assert client.patch(f"/admin/users/{me}", json={"is_admin": False},
                        headers=ADMIN).status_code == 200


def test_whoever_chairs_a_live_project_is_handed_off_it_first(client):
    founder = person("founder@example.com")
    project = make_project(client, slug="theirs", chairman_email="founder@example.com")
    refused = client.patch(f"/admin/users/{founder['id']}", json={"active": False},
                           headers=ADMIN)
    assert refused.status_code == 409
    assert "theirs" in refused.json()["detail"]
    # A finished project needs nobody to decide on it — and stopping it is
    # theirs, not the instance admin's.
    assert client.post(f"/admin/projects/{project['id']}/stop",
                       json={"reason": "wound down"}, headers=ADMIN).status_code == 403
    assert client.post(f"/admin/projects/{project['id']}/stop",
                       json={"reason": "wound down"},
                       headers=headers_of(founder)).status_code == 200
    assert client.patch(f"/admin/users/{founder['id']}", json={"active": False},
                        headers=ADMIN).status_code == 200


def test_deactivating_somebody_ends_every_credential_they_hold(client):
    leaver = person("leaver@example.com", password=PASSWORD)
    model = mint(client, chairman="leaver@example.com")["token"]
    sign_in(client, "leaver@example.com")
    assert client.get("/auth/me").status_code == 200
    assert rpc(client, model, "initialize").status_code == 200

    assert client.patch(f"/admin/users/{leaver['id']}", json={"active": False},
                        headers=ADMIN).status_code == 200
    assert client.get("/auth/me").status_code == 401, "the browser session"
    assert client.get("/auth/me", headers=headers_of(leaver)).status_code == 401, "the token"
    assert rpc(client, model, "initialize").status_code == 401, "the model client"
    assert client.post("/auth/login", json={"email": "leaver@example.com",
                                            "password": PASSWORD}).status_code == 401
    # And coming back is a decision, not a side effect of time passing.
    assert client.patch(f"/admin/users/{leaver['id']}", json={"active": True},
                        headers=ADMIN).status_code == 200
    assert client.get("/auth/me", headers=headers_of(leaver)).status_code == 401, \
        "credentials revoked on the way out stay revoked"


def test_a_project_is_chaired_by_whoever_founds_it(client):
    make_project(client, slug="seed-roles", start=False)  # roles for the bodies below
    founder = person("founder@example.com")
    body = {"slug": "mine", "name": "Mine", "goal": "Prove a founder chairs what they found.",
            "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}]}
    created = client.post("/admin/projects", json=body, headers=headers_of(founder))
    assert created.status_code == 200, created.text
    with db_session() as session:
        from yaaf.core.models import Project

        assert session.get(Project, created.json()["id"]).chairman_user_id == founder["id"]

    # Naming somebody else is setting a project up for them: running the box.
    refused = client.post("/admin/projects",
                          json={**body, "slug": "for-you", "chairman_email": CHAIR_EMAIL},
                          headers=headers_of(founder))
    assert refused.status_code == 403
    assert "chaired by whoever founds it" in refused.json()["detail"]
    assert client.post("/admin/projects",
                       json={**body, "slug": "for-them",
                             "chairman_email": "founder@example.com"},
                       headers=ADMIN).status_code == 200


def test_a_clone_is_chaired_by_whoever_clones_it(client):
    from yaaf.core.models import Project, ProjectMember

    source = make_project(client, slug="source", start=False)
    clone = client.post(f"/admin/projects/{source['id']}/clone", json={"slug": "twin"},
                        headers=ADMIN)
    assert clone.status_code == 200, clone.text
    with db_session() as session:
        twin = session.get(Project, clone.json()["id"])
        assert twin.chairman_user_id == _user_id(CHAIR_EMAIL)
        members = session.query(ProjectMember).filter_by(project_id=twin.id).all()
        assert [(m.user_id, m.role) for m in members] == [(twin.chairman_user_id, "chairman")]


def test_a_model_client_acts_for_whoever_mints_it(client):
    person("colleague@example.com")
    refused = client.post("/admin/mcp_clients", json={
        "name": "theirs", "chairman_email": "colleague@example.com", "scopes": ["read"],
    }, headers=ADMIN)
    assert refused.status_code == 403, "an instance admin included"
    mine = client.post("/admin/mcp_clients", json={"name": "mine", "scopes": ["read"]},
                       headers=ADMIN)
    assert mine.status_code == 200
    assert rpc(client, mine.json()["token"], "initialize").status_code == 200


def test_people_list_only_their_own_model_clients(client):
    theirs = mint(client, name="theirs", chairman="colleague@example.com")
    colleague = headers_of(person("colleague@example.com"))
    mint(client, name="mine")
    assert {c["name"] for c in client.get("/admin/mcp_clients", headers=colleague).json()} \
        == {"theirs"}
    assert client.delete(f"/admin/mcp_clients/{_client_id(client, 'mine')}",
                         headers=colleague).status_code == 404
    # The instance admin sees and may revoke everybody's: a departed colleague's
    # laptop is the box's problem.
    assert client.delete(f"/admin/mcp_clients/{theirs['id']}", headers=ADMIN).status_code == 200
    assert rpc(client, theirs["token"], "initialize").status_code == 401


def _client_id(client, name: str) -> str:
    return next(c["id"] for c in client.get("/admin/mcp_clients", headers=ADMIN).json()
                if c["name"] == name)


def test_bootstrap_makes_an_admin_only_on_a_box_without_one(client):
    runner = CliRunner()
    refused = runner.invoke(users_cli, ["bootstrap", "--email", "late@example.com"])
    assert refused.exit_code == 1, "the suite's chair is already an admin"
    assert "already has an admin" in refused.output

    with db_session() as session:
        session.query(User).update({User.is_admin: False})
    done = runner.invoke(users_cli, ["bootstrap", "--email", "Chair@Example.com"])
    assert done.exit_code == 0, done.output
    assert "/app/#/setup?token=ysl_" in done.output
    with db_session() as session:
        chair = session.query(User).filter_by(email=CHAIR_EMAIL).one()
        assert chair.is_admin, "the existing chairman is promoted, not duplicated"
    token = runner.invoke(users_cli, ["token", "--first-admin", "--minutes", "10",
                                      "--name", "deploy"]).output.strip()
    assert client.get("/admin/users", headers=bearer(token)).status_code == 200
