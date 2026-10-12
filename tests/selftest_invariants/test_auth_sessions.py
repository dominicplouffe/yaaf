"""A person in a browser: signing in, staying in, and being put out.

docs/AUTH.md rules 7 and 8, attacked over HTTP the way a browser — or
somebody holding a copied cookie, or a page on another origin — would. The
session replaced a static bearer kept in localStorage, so the attacks are the
ones that bearer was exposed to and a cookie must not be: replay after logout,
use from another origin, guessing at speed, and outliving a password change.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.conftest import (
    ADMIN,
    CHAIR_EMAIL,
    ORIGIN,
    PASSWORD,
    bearer,
    live_token,
    make_project,
    person,
    sign_in,
)
from yaaf.core.db import db_session
from yaaf.core.models import LoginFailure, UserSession, utcnow
from yaaf.server import identity

COOKIE = identity.COOKIE


def _login(client, email=CHAIR_EMAIL, password=PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def test_a_password_signs_in_and_the_cookie_cannot_be_read_by_script(client):
    person(CHAIR_EMAIL, password=PASSWORD)
    response = _login(client)
    assert response.status_code == 200, response.text
    assert response.json()["email"] == CHAIR_EMAIL
    assert response.json()["via"] == "session"
    cookie = response.headers["set-cookie"]
    # The property localStorage never had: script in the page cannot read it.
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower() and "Path=/" in cookie
    assert PASSWORD not in response.text
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["via"] == "session"


def test_emails_are_matched_without_regard_to_case(client):
    person(CHAIR_EMAIL, password=PASSWORD)
    assert _login(client, email="  Chair@Example.COM ").status_code == 200


def test_a_wrong_password_and_an_unknown_address_read_the_same(client):
    """The sign-in page is not an oracle for which addresses exist here."""
    person(CHAIR_EMAIL, password=PASSWORD)
    wrong = _login(client, password="not-the-password-at-all")
    stranger = _login(client, email="nobody@example.com")
    assert wrong.status_code == stranger.status_code == 401
    assert wrong.json() == stranger.json()


def test_guessing_is_throttled_per_address_and_strangers_alike(client):
    person(CHAIR_EMAIL, password=PASSWORD)
    for _ in range(identity.THROTTLE_MAX):
        assert _login(client, password="wrong-guess-wrong-guess").status_code == 401
    # The right password is refused too, until the window clears: a throttle
    # that let the right answer through would be a timer on the wrong ones.
    locked = _login(client)
    assert locked.status_code == 429
    # Somebody else's address is untouched by somebody's guesses at this one.
    person("colleague@example.com", password=PASSWORD)
    assert _login(client, email="colleague@example.com").status_code == 200
    # And an address with no account is throttled exactly the same, so the
    # throttle cannot answer the question the 401 is careful not to.
    for _ in range(identity.THROTTLE_MAX):
        _login(client, email="nobody@example.com")
    assert _login(client, email="nobody@example.com").status_code == 429

    # The CLI's door shares the count: it is the same password.
    assert client.post("/auth/token", json={"email": CHAIR_EMAIL, "password": PASSWORD,
                                            "name": "laptop"}).status_code == 429

    with db_session() as session:
        session.execute(update(LoginFailure).values(
            at=utcnow() - identity.THROTTLE_WINDOW - timedelta(minutes=1)))
    assert _login(client).status_code == 200


def test_a_cookie_write_has_to_come_from_this_installs_own_pages(client):
    project = make_project(client, start=False)
    sign_in(client)
    url = f"/admin/projects/{project['id']}/boardroom"
    body = {"body": "status please"}
    assert client.post(url, json=body).status_code == 403
    assert client.post(url, json=body, headers={"Origin": "https://evil.example"}
                       ).status_code == 403
    assert client.post(url, json=body,
                       headers={"Referer": "https://evil.example/page"}).status_code == 403
    assert client.post(url, json=body, headers=ORIGIN).status_code == 200
    # Older browsers send only a Referer; it names the origin just as well.
    assert client.post(url, json=body,
                       headers={"Referer": "http://testserver/app/"}).status_code == 200
    # A read needs no Origin: a link the browser follows by itself sends none.
    assert client.get(f"/observe/projects/{project['id']}").status_code == 200
    # A bearer request is not ambient — another page cannot make a browser
    # send a header — so it is not asked where it came from.
    client.cookies.clear()
    assert client.post(url, json=body, headers=ADMIN).status_code == 200


def test_signing_out_ends_the_session_on_the_server(client):
    sign_in(client)
    kept = client.cookies.get(COOKIE)
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 200
    # Whoever copied the cookie before the click is signed out as well.
    client.cookies.set(COOKIE, kept)
    assert client.get("/auth/me").status_code == 401


def test_an_idle_or_expired_session_is_refused(client):
    sign_in(client)
    assert client.get("/auth/me").status_code == 200
    with db_session() as session:
        session.execute(update(UserSession).values(
            last_seen_at=utcnow() - identity.SESSION_IDLE - timedelta(minutes=1)))
    assert client.get("/auth/me").status_code == 401

    sign_in(client)
    with db_session() as session:
        session.execute(update(UserSession).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.get("/auth/me").status_code == 401


def test_a_new_password_signs_out_every_other_browser(client):
    sign_in(client)
    other = TestClient(client.app)
    sign_in(other)
    new = "a-different-long-passphrase"
    changed = client.post("/auth/password", json={"current": PASSWORD, "new": new},
                          headers=ORIGIN)
    assert changed.status_code == 200, changed.text
    assert client.get("/auth/me").status_code == 200, "the browser that changed it stays in"
    assert other.get("/auth/me").status_code == 401
    assert _login(other, password=PASSWORD).status_code == 401
    assert _login(other, password=new).status_code == 200


def test_changing_a_password_needs_the_current_one(client):
    sign_in(client)
    refused = client.post("/auth/password",
                          json={"current": "not-the-password", "new": "another-long-passphrase"},
                          headers=ORIGIN)
    assert refused.status_code == 401
    short = client.post("/auth/password", json={"current": PASSWORD, "new": "short"},
                        headers=ORIGIN)
    assert short.status_code == 422


def test_a_setup_link_works_once_and_a_newer_one_replaces_it(client):
    invited = client.post("/admin/users", json={"email": "new@example.com",
                                                "display_name": "New"}, headers=ADMIN)
    assert invited.status_code == 200, invited.text
    first = invited.json()["setup_url"].split("token=", 1)[1]
    assert _login(client, email="new@example.com").status_code == 401, "no password yet"

    about = client.get("/auth/setup", params={"token": first})
    assert about.status_code == 200 and about.json()["email"] == "new@example.com"

    reissued = client.post(f"/admin/users/{invited.json()['id']}/setup-link", headers=ADMIN)
    second = reissued.json()["setup_url"].split("token=", 1)[1]
    assert client.get("/auth/setup", params={"token": first}).status_code == 404

    # A password that is refused does not spend the link.
    assert client.post("/auth/setup", json={"token": second, "password": "short"}
                       ).status_code == 422
    done = client.post("/auth/setup", json={"token": second, "password": PASSWORD})
    assert done.status_code == 200, done.text
    assert client.get("/auth/me").json()["email"] == "new@example.com"
    assert client.post("/auth/setup", json={"token": second, "password": PASSWORD}
                       ).status_code == 404
    assert _login(TestClient(client.app), email="new@example.com").status_code == 200


def test_an_expired_setup_link_is_refused(client):
    from yaaf.core.models import UserSetupLink

    invited = client.post("/admin/users", json={"email": "late@example.com",
                                                "display_name": "Late"}, headers=ADMIN).json()
    token = invited["setup_url"].split("token=", 1)[1]
    with db_session() as session:
        session.execute(update(UserSetupLink).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert client.post("/auth/setup", json={"token": token, "password": PASSWORD}
                       ).status_code == 404


def test_the_retired_admin_token_opens_nothing_and_says_so(client):
    make_project(client)
    for path in ("/observe/projects", "/admin/users", "/auth/me"):
        response = client.get(path, headers=bearer("test-admin-token"))
        assert response.status_code == 401, path
        assert "retired" in response.json()["detail"]


def test_a_link_the_browser_follows_by_itself_is_signed_in(client):
    """The evidence snapshot is a plain <a href>, so it cannot carry a bearer —
    it used to 401 in every browser for exactly that reason. A cookie rides on
    a followed link like on anything else."""
    from yaaf.server.verifier import verify_pending_evidence

    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    evidence = client.post("/agent/evidence", json={
        "kind": "external", "description": "a stranger reviewed us",
        "source_url": "https://independent.example.org/review"},
        headers=bearer(ceo_token)).json()
    page = b"<html>what the platform fetched</html>"
    with db_session() as session:
        verify_pending_evidence(session, fetch=lambda url: (200, page))

    sign_in(client)
    served = client.get(f"/observe/evidence/{evidence['id']}/snapshot")
    assert served.status_code == 200
    assert served.text == page.decode()


def test_another_site_cannot_sign_your_browser_in(client):
    """Login CSRF: a page elsewhere posts its own owner's password to this
    install, and your browser is now signed in as them — reading what you type
    into an account somebody else controls."""
    person(CHAIR_EMAIL, password=PASSWORD)
    body = {"email": CHAIR_EMAIL, "password": PASSWORD}
    assert client.post("/auth/login", json=body,
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/auth/login", json=body, headers=ORIGIN).status_code == 200
    # The CLI sends no Origin, and that is allowed: it is not a browser.
    assert client.post("/auth/login", json=body).status_code == 200
