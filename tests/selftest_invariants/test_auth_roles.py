"""Who may do what on a project, attacked by everyone who is not supposed to.

docs/AUTH.md rules 2, 3 and 5. One project, five people:

    the chairman   (the suite's own person, who is also the instance admin)
    a co-chair     decides as a peer, and is recorded as themselves
    an observer    reads everything, decides nothing
    a stranger     cannot tell the project exists
    the box admin  a second instance admin, on nobody's project

Every refusal is over HTTP, with the person's own credential, through the route
a real client would use — the way the platform's agents are attacked, applied to
the people who now share it.
"""

from __future__ import annotations

import pytest

from tests.conftest import (
    ADMIN,
    CHAIR_EMAIL,
    ORIGIN,
    agent_by_name,
    bearer,
    channel_of,
    headers_of,
    live_token,
    make_project,
    person,
    sign_in,
)
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from tests.selftest_invariants.test_mcp_confirm import call
from tests.selftest_invariants.test_scope_verbs import add_requirement, deliver_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Approval,
    AuditLog,
    Channel,
    ChannelKind,
    Message,
    Project,
    RequirementEvent,
    User,
)


class Cast:
    def __init__(self, client, project: dict):
        self.client = client
        self.project = project
        self.co = seat(client, project, "co@example.com", "co_chair")
        self.obs = seat(client, project, "obs@example.com", "observer")
        self.stranger = person("stranger@example.com")
        self.box = person("box@example.com", admin=True)


def seat(client, project: dict, email: str, role: str) -> dict:
    """An account, put on the project by its chairman, as the dashboard does."""
    someone = person(email)
    added = client.post(f"/admin/projects/{project['id']}/members",
                        json={"email": email, "role": role}, headers=ADMIN)
    assert added.status_code == 200, added.text
    return someone


@pytest.fixture()
def cast(client) -> Cast:
    return Cast(client, make_project(client))


def _filed(client, project: dict) -> str:
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    response = client.post("/agent/approvals", headers=bearer(token),
                           json={"kind": "spend", "title": "Buy a data pull",
                                 "body": "Two months of category data.", "amount_usd": 40})
    assert response.status_code == 200, response.text
    return response.json()["id"]


# ── strangers ───────────────────────────────────────────────────────────────────


def test_a_stranger_cannot_tell_a_project_exists(cast):
    client, pid, stranger = cast.client, cast.project["id"], headers_of(cast.stranger)
    missing = client.get("/observe/projects/proj_doesnotexist", headers=stranger)
    theirs = client.get(f"/observe/projects/{pid}", headers=stranger)
    assert missing.status_code == theirs.status_code == 404
    assert missing.json() == theirs.json(), "the same words for both, or it is an oracle"

    approval = _filed(client, cast.project)
    refused = client.post(f"/admin/approvals/{approval}/decide", json={"approve": True},
                          headers=stranger)
    nothing = client.post("/admin/approvals/appr_doesnotexist/decide",
                          json={"approve": True}, headers=stranger)
    assert refused.status_code == nothing.status_code == 404
    assert refused.json() == nothing.json()

    assert client.get("/observe/projects", headers=stranger).json() == []
    assert client.get("/observe/desk", headers=stranger).json()["projects"] == []
    assert client.get("/observe/briefing", headers=stranger).json()["projects"] == []


def test_a_project_of_your_own_opens_nobody_elses(cast):
    """Chairing something is standing on that thing and nothing more."""
    client, stranger = cast.client, headers_of(cast.stranger)
    body = {"slug": "theirs", "name": "Theirs", "goal": "A project of the stranger's own.",
            "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}]}
    assert client.post("/admin/projects", json=body, headers=stranger).status_code == 200
    channel = channel_of(client, cast.project["id"], "boardroom")
    assert client.get(f"/observe/channels/{channel}/messages",
                      headers=stranger).status_code == 404
    assert client.get(f"/observe/agents/{cast.project['ceo_agent_id']}",
                      headers=stranger).status_code == 404
    assert {p["slug"] for p in client.get("/observe/projects", headers=stranger).json()} \
        == {"theirs"}


# ── observers ───────────────────────────────────────────────────────────────────


def test_an_observer_reads_everything_and_decides_nothing(cast):
    client, pid, obs = cast.client, cast.project["id"], headers_of(cast.obs)
    page = client.get(f"/observe/projects/{pid}", headers=obs)
    assert page.status_code == 200
    assert page.json()["my_role"] == "observer" and page.json()["can_steer"] is False
    for path in ("plan", "spend", "org", "runs", "approvals", "requirements"):
        prefix = "/admin" if path == "requirements" else "/observe"
        assert client.get(f"{prefix}/projects/{pid}/{path}", headers=obs).status_code == 200, path

    approval = _filed(client, cast.project)
    writes = [
        ("post", f"/admin/projects/{pid}/boardroom", {"body": "do it my way"}),
        ("post", f"/admin/approvals/{approval}/decide", {"approve": True}),
        ("post", f"/admin/projects/{pid}/pause", {"reason": "because I can"}),
        ("post", f"/admin/projects/{pid}/amendments",
         {"reason": "loosen it", "changes": {"refusal_cap_per_run": 50}}),
        ("patch", f"/admin/projects/{pid}", {"name": "Renamed"}),
        ("post", f"/admin/projects/{pid}/members", {"email": "x@example.com",
                                                     "role": "co_chair"}),
    ]
    for method, url, body in writes:
        refused = getattr(client, method)(url, json=body, headers=obs)
        assert refused.status_code == 403, (url, refused.text)
        assert "you are an observer" in refused.json()["detail"], url
    with db_session() as session:
        assert session.get(Approval, approval).status == "pending"


def test_an_observer_cannot_read_a_credential_back(cast):
    client, pid = cast.client, cast.project["id"]
    stored = client.post("/admin/secrets", json={"project_id": pid, "name": "wp-pass",
                                                 "value": "test-value-not-real",
                                                 "role_scopes": []}, headers=ADMIN)
    assert stored.status_code == 200, stored.text
    secret = stored.json()["id"]
    names = client.get("/admin/secrets", params={"project_id": pid},
                       headers=headers_of(cast.obs))
    assert names.status_code == 200 and "test-value" not in names.text
    assert client.get(f"/admin/secrets/{secret}/value",
                      headers=headers_of(cast.obs)).status_code == 403
    assert client.get(f"/admin/secrets/{secret}/value",
                      headers=headers_of(cast.co)).status_code == 200
    # The box's own credentials are the instance admin's alone.
    assert client.get("/admin/secrets", headers=headers_of(cast.co)).status_code == 403


def test_an_observer_can_export_the_design_but_not_the_backup(cast):
    """The export is a read until it carries values: `include_values=true` is
    every project credential decrypted, so it needs the standing the eye on a
    single secret needs — and an observer reaching around the eye through the
    backup button would make the eye decorative."""
    client, pid = cast.client, cast.project["id"]
    client.post("/admin/secrets", json={"project_id": pid, "name": "wp-pass",
                                        "value": "test-value-not-real", "role_scopes": []},
                headers=ADMIN)
    design = client.get(f"/admin/projects/{pid}/export", headers=headers_of(cast.obs))
    assert design.status_code == 200 and "test-value-not-real" not in design.text
    backup = client.get(f"/admin/projects/{pid}/export", params={"include_values": True},
                        headers=headers_of(cast.obs))
    assert backup.status_code == 403
    assert client.get(f"/admin/projects/{pid}/export", params={"include_values": True},
                      headers=headers_of(cast.co)).status_code == 200


# ── co-chairs ───────────────────────────────────────────────────────────────────


def test_a_co_chair_decides_as_a_peer_and_is_recorded_as_themselves(cast):
    client, pid, co = cast.client, cast.project["id"], headers_of(cast.co)
    posted = client.post(f"/admin/projects/{pid}/boardroom", json={"body": "ship it"},
                         headers=co)
    assert posted.status_code == 200, posted.text
    approval = _filed(client, cast.project)
    assert client.post(f"/admin/approvals/{approval}/decide",
                       json={"approve": True, "note": "fine"}, headers=co).status_code == 200
    assert client.post(f"/admin/projects/{pid}/pause", json={"reason": "a breather"},
                       headers=co).status_code == 200

    with db_session() as session:
        message = session.get(Message, posted.json()["id"])
        assert message.sender_user_id == cast.co["id"]
        assert session.get(Approval, approval).decided_by_user_id == cast.co["id"]
        pause = session.query(AuditLog).filter_by(action="project.pause").one()
        assert pause.actor == f"user:{cast.co['id']} via token:test"

    # And the screens say whose words those were, rather than "chairman".
    channel = channel_of(client, pid, "boardroom")
    shown = client.get(f"/observe/channels/{channel}/messages", headers=ADMIN).json()
    assert any(m["from_name"] == "Co (co-chair)" for m in shown)


def test_only_the_chairman_decides_who_decides(cast):
    client, pid = cast.client, cast.project["id"]
    person("new@example.com")
    for who in (cast.co, cast.obs):
        refused = client.post(f"/admin/projects/{pid}/members",
                              json={"email": "new@example.com", "role": "observer"},
                              headers=headers_of(who))
        assert refused.status_code == 403
        assert "only this project's chairman" in refused.json()["detail"]
        assert client.delete(f"/admin/projects/{pid}/members/{cast.obs['id']}",
                             headers=headers_of(who)).status_code == 403
        assert client.post(f"/admin/projects/{pid}/chairman", json={"email": CHAIR_EMAIL},
                           headers=headers_of(who)).status_code == 403
    # A co-chair cannot make themselves the chairman, nor an observer a co-chair.
    assert client.patch(f"/admin/projects/{pid}/members/{cast.obs['id']}",
                        json={"role": "co_chair"},
                        headers=headers_of(cast.obs)).status_code == 403


def test_removing_a_co_chair_takes_effect_on_their_next_request(cast):
    client, pid, co = cast.client, cast.project["id"], headers_of(cast.co)
    assert client.get(f"/observe/projects/{pid}", headers=co).status_code == 200
    assert client.delete(f"/admin/projects/{pid}/members/{cast.co['id']}",
                         headers=ADMIN).status_code == 200
    assert client.get(f"/observe/projects/{pid}", headers=co).status_code == 404
    assert client.post(f"/admin/projects/{pid}/boardroom", json={"body": "still here?"},
                       headers=co).status_code == 404
    # The chairman is not removable — the chair is handed on, never vacated.
    refused = client.delete(f"/admin/projects/{pid}/members/{_chair_id()}", headers=ADMIN)
    assert refused.status_code == 409


def test_a_member_is_added_from_an_account_that_exists(cast):
    client, pid = cast.client, cast.project["id"]
    missing = client.post(f"/admin/projects/{pid}/members",
                          json={"email": "nobody@example.com", "role": "observer"},
                          headers=ADMIN)
    assert missing.status_code == 404
    assert "ask an instance admin to invite them" in missing.json()["detail"]
    again = client.post(f"/admin/projects/{pid}/members",
                        json={"email": "co@example.com", "role": "observer"}, headers=ADMIN)
    assert again.status_code == 409
    members = client.get(f"/admin/projects/{pid}/members", headers=headers_of(cast.obs)).json()
    assert [m["role"] for m in members] == ["chairman", "co_chair", "observer"]


# ── the chair ───────────────────────────────────────────────────────────────────


def test_handing_on_the_chair_keeps_the_outgoing_one_as_a_co_chair(cast):
    client, pid = cast.client, cast.project["id"]
    handed = client.post(f"/admin/projects/{pid}/chairman",
                         json={"email": "co@example.com"}, headers=ADMIN)
    assert handed.status_code == 200, handed.text
    assert handed.json()["chairman"]["id"] == cast.co["id"]
    roles = {m["email"]: m["role"] for m in handed.json()["members"]}
    assert roles == {"co@example.com": "chairman", CHAIR_EMAIL: "co_chair",
                     "obs@example.com": "observer"}
    with db_session() as session:
        assert session.get(Project, pid).chairman_user_id == cast.co["id"]
        audit = session.query(AuditLog).filter_by(action="project.chair_handed_on").one()
        assert audit.detail["break_glass"] is False

    # The old chairman now decides, but no longer decides who decides.
    assert client.post(f"/admin/projects/{pid}/pause", json={"reason": "still a co-chair"},
                       headers=ADMIN).status_code == 200
    assert client.post(f"/admin/projects/{pid}/members",
                       json={"email": "stranger@example.com", "role": "observer"},
                       headers=ADMIN).status_code == 403


def test_the_instance_admin_reads_every_project_and_decides_on_none(cast):
    client, pid, box = cast.client, cast.project["id"], headers_of(cast.box)
    page = client.get(f"/observe/projects/{pid}", headers=box)
    assert page.status_code == 200
    assert page.json()["my_role"] is None and page.json()["can_steer"] is False
    assert pid in {p["id"] for p in client.get("/observe/projects", headers=box).json()}
    refused = client.post(f"/admin/projects/{pid}/boardroom", json={"body": "hello"},
                          headers=box)
    assert refused.status_code == 403
    assert "decides on none it does not chair" in refused.json()["detail"]

    # The one exception: handing the chair on, for a chairman who has left.
    handed = client.post(f"/admin/projects/{pid}/chairman",
                         json={"email": "obs@example.com"}, headers=box)
    assert handed.status_code == 200, handed.text
    with db_session() as session:
        audit = session.query(AuditLog).filter_by(action="project.chair_handed_on").one()
        assert audit.detail["break_glass"] is True
        assert audit.actor == f"user:{cast.box['id']} via token:test"


def test_a_briefing_read_by_one_member_is_still_unread_for_the_rest(cast):
    client, pid = cast.client, cast.project["id"]
    assert client.post(f"/admin/projects/{pid}/briefing/seen",
                       headers=headers_of(cast.obs)).status_code == 200
    theirs = client.get(f"/observe/projects/{pid}/briefing",
                        headers=headers_of(cast.obs)).json()
    mine = client.get(f"/observe/projects/{pid}/briefing", headers=ADMIN).json()
    assert theirs["window_source"] == "since you last read this"
    assert mine["window_source"] == "since this project started", \
        "the observer's having read it is not the chairman's having read it"


# ── the CEO's orders ────────────────────────────────────────────────────────────


def _says(project: dict, user_id: str, body: str) -> str:
    message_id = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        channel = session.query(Channel).filter(
            Channel.project_id == project["id"], Channel.kind == ChannelKind.BOARDROOM).one()
        session.add(Message(id=message_id, project_id=project["id"], channel_id=channel.id,
                            sender_user_id=user_id, body=body))
    return message_id


def _order(client, project, message_id, requirement_key):
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/directives", headers=bearer(token), json={
        "from_message_id": message_id, "reason": "Ordered in the boardroom.",
        "action": {"kind": "cut", "requirement_key": requirement_key}})


def test_a_co_chairs_order_binds_and_names_them_and_a_removed_ones_does_not(client):
    project = deliver_project(client)
    co = seat(client, project, "co@example.com", "co_chair")
    first = add_requirement(client, project, statement="The first thing is true at the end")
    second = add_requirement(client, project, statement="The second thing is true at the end")

    said = _says(project, co["id"], f"cut {first['key']}")
    done = _order(client, project, said, first["key"])
    assert done.status_code == 200, done.text
    with db_session() as session:
        event = (session.query(RequirementEvent).filter_by(requirement_id=first["id"])
                 .order_by(RequirementEvent.at.desc()).first())
        assert event.actor == f"user:{co['id']}", "the order is the co-chair's, not the chair's"

    said_again = _says(project, co["id"], f"and cut {second['key']} too")
    assert client.delete(f"/admin/projects/{project['id']}/members/{co['id']}",
                         headers=ADMIN).status_code == 200
    refused = _order(client, project, said_again, second["key"])
    assert refused.status_code == 422
    assert "nor a co-chair's" in refused.text


def test_an_observers_words_are_never_an_order(client):
    project = deliver_project(client)
    obs = seat(client, project, "obs@example.com", "observer")
    req = add_requirement(client, project)
    refused = _order(client, project, _says(project, obs["id"], f"cut {req['key']}"),
                     req["key"])
    assert refused.status_code == 422


# ── model clients ───────────────────────────────────────────────────────────────


def test_a_model_client_holds_its_persons_standing_and_no_more(cast):
    client, slug = cast.client, "acme"
    watcher = mint(client, name="obs-laptop", scopes=("read", "steer"),
                   chairman="obs@example.com")["token"]
    failed, read = call(client, watcher, "project", {"project": slug})
    assert failed is False, read
    failed, refused = call(client, watcher, "boardroom_say", {"project": slug, "body": "hi"})
    assert failed is True and "only this project's chairman or a co-chair" in str(refused)

    steerer = mint(client, name="co-laptop", scopes=("read", "steer"),
                   chairman="co@example.com")["token"]
    failed, said = call(client, steerer, "boardroom_say", {"project": slug, "body": "hi"})
    assert failed is False, said

    # The instance admin's read-everything is a person running the box; a model
    # acting for them stays on the projects they are actually on.
    boxed = mint(client, name="box-laptop", scopes=("read",),
                 chairman="box@example.com")["token"]
    failed, refused = call(client, boxed, "project", {"project": slug})
    assert failed is True and "no project" in str(refused)
    assert rpc(client, boxed, "initialize").status_code == 200


def test_a_live_socket_is_a_projects_readers_only(cast):
    from starlette.websockets import WebSocketDisconnect

    client, pid = cast.client, cast.project["id"]
    sign_in(client, "stranger@example.com")
    with (
        client.websocket_connect(f"/ws/projects/{pid}", headers=ORIGIN) as socket,
        pytest.raises(WebSocketDisconnect) as refused,
    ):
        socket.receive_json()
    assert refused.value.code == 1008
    sign_in(client, "obs@example.com")
    with client.websocket_connect(f"/ws/projects/{pid}", headers=ORIGIN) as socket:
        assert socket.receive_json()["type"] == "stamp"


def _chair_id() -> str:
    with db_session() as session:
        return session.query(User).filter_by(email=CHAIR_EMAIL).one().id
