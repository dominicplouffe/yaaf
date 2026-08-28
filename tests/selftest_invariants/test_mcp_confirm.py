"""An irreversible write cannot happen in one call.

Rule 4 of docs/MCP_SERVER.md. The attack it defends against is not a determined
adversary holding the chairman's token — it is the ordinary failure this whole
surface invites: a confident model, a channel message telling it what to do,
and a verb within reach. The preview is the turn where that stops, and the
argument binding is what stops a model spending an approval for one write on a
different one.

Server-side, deliberately. The host client's permission prompt is a prompt, and
this platform's argument is that a prompt is a suggestion.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_mcp_auth import mint, rpc

SLUG = "acme"


def steerer(client, name="desk", projects=()):
    return mint(client, name=name, scopes=("steer",), projects=projects)


def call(client, token, name, arguments):
    response = rpc(client, token, "tools/call", {"name": name, "arguments": arguments})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return (result["isError"], text if result["isError"] else json.loads(text))


def status_of(client, project) -> str:
    return client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()["status"]


def test_the_first_call_previews_and_changes_nothing(client):
    project = make_project(client, slug=SLUG)
    token = steerer(client)["token"]

    failed, preview = call(client, token, "lifecycle",
                           {"project": SLUG, "verb": "pause", "reason": "over budget"})
    assert failed is False
    assert preview["confirm_required"] is True
    assert preview["about_to"]["verb"] == "pause"
    assert preview["about_to"]["status_now"] == "running"
    assert preview["confirm"].startswith("ycf_")
    # The whole point: nothing happened yet.
    assert status_of(client, project) == "running"


def test_the_second_call_performs_it(client):
    project = make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    arguments = {"project": SLUG, "verb": "pause", "reason": "over budget"}

    _, preview = call(client, token, "lifecycle", arguments)
    failed, done = call(client, token, "lifecycle", {**arguments, "confirm": preview["confirm"]})
    assert failed is False
    assert done["status"] == "paused"
    assert status_of(client, project) == "paused"


def test_a_confirmation_is_spent_once(client):
    make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    arguments = {"project": SLUG, "verb": "pause", "reason": "over budget"}

    _, preview = call(client, token, "lifecycle", arguments)
    call(client, token, "lifecycle", {**arguments, "confirm": preview["confirm"]})
    failed, message = call(client, token, "lifecycle",
                           {**arguments, "confirm": preview["confirm"]})
    assert failed is True
    assert "already spent" in message


def test_an_approval_cannot_be_spent_on_a_different_write(client):
    """The reason the token carries a fingerprint rather than just an expiry.

    Preview a pause, then try to spend that approval on a stop. Same tool, same
    project, one word different — and the word is the whole decision.
    """
    project = make_project(client, slug=SLUG)
    token = steerer(client)["token"]

    _, preview = call(client, token, "lifecycle",
                      {"project": SLUG, "verb": "pause", "reason": "over budget"})
    failed, message = call(client, token, "lifecycle",
                           {"project": SLUG, "verb": "stop", "reason": "over budget",
                            "confirm": preview["confirm"]})
    assert failed is True
    assert "not the arguments that confirmation was issued for" in message
    assert status_of(client, project) == "running"

    # Even the reason is bound: what the chairman was shown is what gets done.
    failed, message = call(client, token, "lifecycle",
                           {"project": SLUG, "verb": "pause", "reason": "a different reason",
                            "confirm": preview["confirm"]})
    assert failed is True
    assert "not the arguments" in message


def test_a_confirmation_expires(client):
    from datetime import timedelta

    from yaaf.core.db import db_session
    from yaaf.core.models import McpConfirmation, utcnow

    make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    arguments = {"project": SLUG, "verb": "pause", "reason": "over budget"}

    _, preview = call(client, token, "lifecycle", arguments)
    with db_session() as session:
        session.query(McpConfirmation).one().expires_at = utcnow() - timedelta(seconds=1)

    failed, message = call(client, token, "lifecycle",
                           {**arguments, "confirm": preview["confirm"]})
    assert failed is True
    assert "expired" in message


def test_one_clients_confirmation_is_not_anothers(client):
    """Two tokens, one chairman: an approval belongs to the client that saw it."""
    make_project(client, slug=SLUG)
    first = steerer(client, name="laptop")["token"]
    second = steerer(client, name="phone")["token"]
    arguments = {"project": SLUG, "verb": "pause", "reason": "over budget"}

    _, preview = call(client, first, "lifecycle", arguments)
    failed, message = call(client, second, "lifecycle",
                           {**arguments, "confirm": preview["confirm"]})
    assert failed is True
    assert "not one of this client's" in message


def test_an_invented_confirmation_is_refused(client):
    make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    failed, message = call(client, token, "lifecycle",
                           {"project": SLUG, "verb": "stop", "reason": "over budget",
                            "confirm": "ycf_made-up-token"})
    assert failed is True
    assert "not one of this client's" in message


def test_speech_is_not_gated_and_the_gate_is_not_everywhere(client):
    """Boardroom speech is reversible and constant; a preview on every sentence
    would cost two calls for the one tool a chairman uses most.

    The documented exception, tested so it stays a decision rather than drift.
    """
    project = make_project(client, slug=SLUG)
    token = steerer(client)["token"]

    failed, posted = call(client, token, "boardroom_say",
                          {"project": SLUG, "body": "what is blocking the report?"})
    assert failed is False
    assert "confirm_required" not in posted
    assert posted["posted"].startswith("msg_")

    channels = client.get(f"/observe/projects/{project['id']}/channels", headers=ADMIN).json()
    boardroom = next(c["id"] for c in channels if c["kind"] == "boardroom")
    bodies = [m["body"] for m in client.get(f"/observe/channels/{boardroom}/messages",
                                            headers=ADMIN).json()]
    assert "what is blocking the report?" in bodies


def test_a_read_token_cannot_reach_any_of_this(client):
    make_project(client, slug=SLUG)
    reader = mint(client, scopes=("read",))["token"]
    for tool in ("lifecycle", "boardroom_say", "requirement_decide", "commitment_decide",
                 "review_decide", "audit_decide", "approval_decide"):
        answer = rpc(client, reader, "tools/call",
                     {"name": tool, "arguments": {"project": SLUG}}).json()
        assert answer["error"]["code"] == -32601, tool


def test_a_steer_token_still_stops_at_the_project_allowlist(client):
    """Scope says which verbs; the allowlist says which companies. Both, always."""
    make_project(client, slug=SLUG)
    beta = make_project(client, slug="beta")
    token = steerer(client, projects=[SLUG])["token"]

    failed, message = call(client, token, "lifecycle",
                           {"project": "beta", "verb": "stop", "reason": "not mine"})
    assert failed is True
    assert "is reachable by this client" in message
    assert status_of(client, beta) == "running"
