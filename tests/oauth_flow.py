"""Walking the OAuth flow the way a chairman does, for the suites that attack it.

Not a test module: the consent flow is six requests, and two suites need all
six — one to prove the ladder is complete, one to try to break it.
"""

from __future__ import annotations

import base64
import hashlib
import re

import httpx

from tests.conftest import CHAIR_EMAIL, make_project, person

REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PASSWORD = "a-long-enough-passphrase"
VERIFIER = "verifier-that-is-long-enough-to-be-a-real-pkce-verifier"


class Forwarding(httpx.BaseTransport):
    """A sync transport that hands requests to the TestClient.

    `mcp_oauth` is real client code with a `transport=` seam for exactly this;
    the seam wants a plain sync transport, and the TestClient's own is not one.
    """

    def __init__(self, test_client) -> None:
        self.client = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method, str(request.url), headers=dict(request.headers),
            content=request.read(), follow_redirects=False)
        return httpx.Response(response.status_code, headers=response.headers,
                              content=response.content, request=request)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def chairman_with_password(client):
    """The chairman, with a password the consent screen can check. Set in the
    database, as a setup link would leave it: the consent flow is what these
    suites attack, not how the password got there."""
    make_project(client, slug="acme")
    person(CHAIR_EMAIL, password=PASSWORD)


def register(client, name="claude.ai", uris=(REDIRECT,)) -> dict:
    response = client.post("/oauth/register",
                           json={"client_name": name, "redirect_uris": list(uris)})
    assert response.status_code == 201, response.text
    return response.json()


def consent(client, registered: dict, scope="read steer", verifier=VERIFIER,
            challenge_method="S256", state="xyz") -> str:
    """Sign in and approve, the way a chairman does. Returns the code."""
    params = {
        "response_type": "code", "client_id": registered["client_id"],
        "redirect_uri": REDIRECT, "scope": scope, "state": state,
        "code_challenge": challenge_for(verifier), "code_challenge_method": challenge_method,
    }
    page = client.get("/oauth/authorize", params=params)
    assert page.status_code == 200, page.text
    assert "Sign in" in page.text

    signed_in = client.post("/oauth/authorize", data={
        **params, "action": "login", "email": "chair@example.com", "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.text
    assert "Approve" in signed_in.text
    # The consent screen is the grant: it names the client, the redirect host
    # and one checkbox per scope. Projects are not on it — a grant reaches
    # every project its chairman chairs, then and later.
    assert "claude.ai" in signed_in.text

    approved = client.post("/oauth/authorize", data={
        **params, "action": "approve", "scope_grant": ["read", "steer"]},
        follow_redirects=False)
    assert approved.status_code == 302, approved.text
    location = approved.headers["location"]
    assert location.startswith(REDIRECT)
    assert "state=xyz" in location
    return re.search(r"code=([^&]+)", location).group(1)


def exchange(client, registered: dict, code: str, verifier=VERIFIER, **overrides):
    return client.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "client_id": registered["client_id"], "code_verifier": verifier, **overrides})


