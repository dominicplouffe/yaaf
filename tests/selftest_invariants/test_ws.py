"""Invariant selftests: the live-update WebSocket.

The socket is a latency hint over polling (like LISTEN/NOTIFY over the
dispatcher's polling floor), so the invariants are small: it must not admit a
bad token, it must send an initial change stamp, and the stamp must actually
change when project data changes — otherwise the hint never fires and the UI
silently degrades to pure polling forever.
"""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from tests.conftest import ADMIN, make_project
from yaaf.server.api import ws as ws_module


@pytest.fixture(autouse=True)
def fast_stamp_loop(monkeypatch):
    # The endpoint reads the module constant each iteration, so shrinking it
    # makes the tests fast without touching any timing logic.
    monkeypatch.setattr(ws_module, "WS_POLL_S", 0.02)


def test_bad_token_is_refused(client):
    project = make_project(client, start=False)
    with (
        client.websocket_connect(
            f"/ws/projects/{project['id']}?token=wrong-token"
        ) as socket,
        pytest.raises(WebSocketDisconnect) as excinfo,
    ):
        socket.receive_json()
    assert excinfo.value.code == 1008


def test_missing_token_is_refused(client):
    project = make_project(client, start=False)
    with (
        client.websocket_connect(f"/ws/projects/{project['id']}") as socket,
        pytest.raises(WebSocketDisconnect) as excinfo,
    ):
        socket.receive_json()
    assert excinfo.value.code == 1008


def test_good_token_gets_stamp_and_changes_move_it(client):
    project = make_project(client, start=False)
    with client.websocket_connect(
        f"/ws/projects/{project['id']}?token=test-admin-token"
    ) as socket:
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
