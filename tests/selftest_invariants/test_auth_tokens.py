"""A person at a terminal: personal tokens, and the doors they do not open.

docs/AUTH.md rule 4: **a credential cannot widen itself.** A personal token is
its person and no more, always expires, and cannot mint a longer-lived one or
change the password that would let somebody mint more. And each kind of
credential opens its own door only — a model client's token is not a person's,
and a person's is not a model client's.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import update

from tests.conftest import (
    ADMIN,
    CHAIR_EMAIL,
    ORIGIN,
    PASSWORD,
    bearer,
    headers_of,
    live_token,
    make_project,
    person,
    sign_in,
)
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, UserToken, utcnow


def _exchange(client, password=PASSWORD, **extra):
    return client.post("/auth/token", json={"email": CHAIR_EMAIL, "password": password,
                                            "name": "laptop", **extra})


def test_a_password_buys_a_token_that_is_its_person_and_names_itself(client):
    project = make_project(client)
    person(CHAIR_EMAIL, password=PASSWORD)
    issued = _exchange(client)
    assert issued.status_code == 200, issued.text
    token = issued.json()["token"]
    assert token.startswith("ypt_")

    me = client.get("/auth/me", headers=bearer(token)).json()
    assert me["email"] == CHAIR_EMAIL and me["via"] == "token"

    assert client.post(f"/admin/projects/{project['id']}/pause",
                       json={"reason": "from a script"},
                       headers=bearer(token)).status_code == 200
    with db_session() as session:
        row = session.query(AuditLog).filter_by(action="project.pause").one()
    assert row.actor.endswith(" via token:laptop"), row.actor

    # Only its hash is kept: the listing never shows it again.
    listed = client.get("/auth/tokens", headers=bearer(token)).json()
    assert {t["name"] for t in listed} >= {"laptop"}
    assert token not in str(listed)


def test_a_token_cannot_mint_a_token_or_change_the_password(client):
    refused = client.post("/auth/tokens", json={"name": "longer"}, headers=ADMIN)
    assert refused.status_code == 403
    assert "personal token cannot" in refused.json()["detail"]
    assert client.post("/auth/password", json={"current": PASSWORD, "new": "x" * 20},
                       headers=ADMIN).status_code == 403

    # A browser session can do both: it needed the password to exist.
    sign_in(client)
    client.headers.pop("Authorization", None)
    minted = client.post("/auth/tokens", json={"name": "from the dashboard", "days": 7},
                         headers=ORIGIN)
    assert minted.status_code == 200, minted.text


def test_every_token_expires(client):
    person(CHAIR_EMAIL, password=PASSWORD)
    for days in (0, 366, 100_000):
        assert _exchange(client, days=days).status_code == 422, days
    sign_in(client)
    assert client.post("/auth/tokens", json={"name": "forever", "days": 0},
                       headers=ORIGIN).status_code == 422


def test_an_expired_or_revoked_token_is_refused(client):
    token = _token_for(client)
    assert client.get("/auth/me", headers=bearer(token)).status_code == 200
    with db_session() as session:
        session.execute(update(UserToken).where(UserToken.name == "laptop")
                        .values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401

    token = _token_for(client, name="second")
    row = next(t for t in client.get("/auth/tokens", headers=ADMIN).json()
               if t["name"] == "second" and not t["revoked_at"])
    assert client.delete(f"/auth/tokens/{row['id']}", headers=ADMIN).status_code == 200
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401


def test_yaaf_logout_revokes_the_token_it_was_holding(client):
    token = _token_for(client)
    assert client.post("/auth/logout", headers=bearer(token)).status_code == 200
    assert client.get("/auth/me", headers=bearer(token)).status_code == 401
    # The suite's own token is a different credential and is untouched.
    assert client.get("/auth/me", headers=ADMIN).status_code == 200


def test_somebody_elses_tokens_are_not_yours_to_see_or_revoke(client):
    other = person("other@example.com")
    theirs = client.get("/auth/tokens", headers=headers_of(other)).json()[0]
    assert theirs["id"] not in {t["id"] for t in client.get("/auth/tokens",
                                                             headers=ADMIN).json()}
    assert client.delete(f"/auth/tokens/{theirs['id']}", headers=ADMIN).status_code == 404
    assert client.get("/auth/me", headers=headers_of(other)).status_code == 200


def test_each_credential_opens_its_own_door_and_says_which_one(client):
    project = make_project(client)
    _, run_token = live_token(project["id"], project["ceo_agent_id"])
    model = mint(client)["token"]
    for token, words in ((run_token, "run token"), (model, "MCP client token")):
        refused = client.get("/observe/projects", headers=bearer(token))
        assert refused.status_code == 401
        assert words in refused.json()["detail"]
    # And the other way: a person's own token is no model's (test_mcp_auth.py
    # has the message), nor is a browser session — /mcp reads no cookie.
    sign_in(client)
    assert rpc(client, "", "initialize").status_code == 401


def _token_for(client, name="laptop") -> str:
    person(CHAIR_EMAIL, password=PASSWORD)
    response = client.post("/auth/token", json={"email": CHAIR_EMAIL, "password": PASSWORD,
                                                "name": name})
    assert response.status_code == 200, response.text
    return response.json()["token"]
