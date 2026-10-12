"""Invariant selftests: the live-update WebSocket.

The socket is a latency hint over polling (like LISTEN/NOTIFY over the
dispatcher's polling floor), so the invariants are small: it must not admit
anybody without a session, nor a page from another origin; it must send an
initial change stamp; and the stamp must actually change when project data
changes — otherwise the hint never fires and the UI silently degrades to pure
polling forever.

It authenticates with the browser's session cookie, never a token in the URL:
the admin token used to ride as `?token=`, which put the master credential in
every proxy log the socket passed through.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ADMIN, CHAIR_TOKEN, ORIGIN, make_project, sign_in
from yaaf.server.api import ws as ws_module


@pytest.fixture(autouse=True)
def fast_stamp_loop(monkeypatch):
    # The endpoint reads the module constant each iteration, so shrinking it
    # makes the tests fast without touching any timing logic.
    monkeypatch.setattr(ws_module, "WS_POLL_S", 0.02)


def _refused(client, url: str, headers: dict | None = None) -> int:
    with (
        client.websocket_connect(url, headers=headers or ORIGIN) as socket,
        pytest.raises(WebSocketDisconnect) as excinfo,
    ):
        socket.receive_json()
    return excinfo.value.code


def test_no_session_is_refused(client):
    project = make_project(client, start=False)
    assert _refused(client, f"/ws/projects/{project['id']}") == 1008


def test_a_token_in_the_url_opens_nothing(client):
    """The old transport, with a credential that is perfectly good elsewhere."""
    project = make_project(client, start=False)
    assert _refused(client, f"/ws/projects/{project['id']}?token={CHAIR_TOKEN}") == 1008


def test_another_origin_cannot_borrow_the_session(client):
    """Cross-site WebSocket hijacking: CORS never covers a socket, so a page
    elsewhere could open one with the chairman's cookie attached. The Origin
    check is the only thing between that page and the change feed."""
    project = make_project(client, start=False)
    sign_in(client)
    url = f"/ws/projects/{project['id']}"
    assert _refused(client, url, {"Origin": "https://evil.example"}) == 1008
    assert _refused(client, url, {"Origin": ""}) == 1008


def test_a_signed_out_session_stops_opening_sockets(client):
    project = make_project(client, start=False)
    sign_in(client)
    kept = client.cookies.get("yaaf_session")
    assert client.post("/auth/logout", headers=ORIGIN).status_code == 200
    # The browser forgot its cookie; somebody who copied it did not. The row is
    # what refuses it, not the browser's manners.
    client.cookies.set("yaaf_session", kept)
    assert _refused(client, f"/ws/projects/{project['id']}") == 1008


def test_a_session_gets_stamps_and_changes_move_them(client):
    project = make_project(client, start=False)
    sign_in(client)
    with client.websocket_connect(f"/ws/projects/{project['id']}", headers=ORIGIN) as socket:
        first = socket.receive_json()
        assert first["type"] == "stamp"
        assert first["project"] == project["id"]
        for table in ("messages", "tasks", "runs", "approvals", "evidence", "notices"):
            assert table in first

        # A boardroom post is a new message row: the very next stamp (stamps
        # are only sent on change, so receive_json() blocking until the loop
        # notices is exactly the deterministic wait we want) must differ.
        posted = client.post(
            f"/admin/projects/{project['id']}/boardroom",
            json={"body": "hello from the chairman"},
            headers=ADMIN,
        )
        assert posted.status_code == 200, posted.text

        second = socket.receive_json()
        assert second != first
        assert second["messages"][0] == first["messages"][0] + 1


def test_a_change_of_seating_moves_the_stamp(client):
    """Membership has no timestamp to aggregate, so it would never move the stamp
    — and an observer's notice would sit there after they were made a co-chair."""
    from tests.conftest import person

    project = make_project(client, start=False)
    person("colleague@example.com")
    sign_in(client)
    with client.websocket_connect(f"/ws/projects/{project['id']}", headers=ORIGIN) as socket:
        first = socket.receive_json()
        added = client.post(f"/admin/projects/{project['id']}/members",
                            json={"email": "colleague@example.com", "role": "observer"},
                            headers=ADMIN)
        assert added.status_code == 200, added.text
        second = socket.receive_json()
        assert second["members"] != first["members"]
