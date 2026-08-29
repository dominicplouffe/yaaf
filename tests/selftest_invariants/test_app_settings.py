"""Invariant selftests: app-wide settings.

Two settings live above every project, and they are here for opposite reasons.

The **model credential** was storable and not manageable — no way to see, test,
rotate or remove it. The rule it carries is the load-bearing part and is
asserted here directly: *the platform key never reaches a project's agents*, no
inheritance and no fallback, however a project is configured.

The **Slack connection** is shared on purpose — one workspace, every project,
different channels — so the tests below are mostly about the seam that makes
that safe: inheritance is per field, a project may override one token and keep
the rest, and a project may switch Slack off entirely without unwiring the
install. Provisioning is asserted to be idempotent, because a chairman will
press that button twice.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

from sqlalchemy import select

from tests.conftest import ADMIN, make_project
from yaaf.core import platform
from yaaf.core.db import db_session
from yaaf.core.models import Message, Project, Secret
from yaaf.server.slack import bridge
from yaaf.server.slack import provision as slack_provision
from yaaf.server.slack import settings as slack_settings

GLOBAL_BOT = "xoxb-global-workspace"
GLOBAL_SIGNING = "global-signing-secret"
GLOBAL_APP = "xapp-global-socket"
PROJECT_BOT = "xoxb-this-project-only"
API_KEY = "sk-ant-api-not-a-real-key-0000"
OAUTH_TOKEN = "sk-ant-oat-not-a-real-token-00"


# ── fakes for the two wires ─────────────────────────────────────────────────────


class _Recorder:
    """bridge._post_message, recorded rather than sent."""

    def __init__(self) -> None:
        self.posts: list[dict] = []

    def __call__(self, token: str, channel: str, text: str) -> str:
        self.posts.append({"token": token, "channel": channel, "text": text})
        return f"{1700000000 + len(self.posts)}.000100"


class _FakeSlack:
    """The Slack Web API, as much of it as provisioning touches.

    Holds a workspace: names already taken keep their ids, so a second
    provisioning run reproduces the `name_taken` path a real workspace gives.
    """

    def __init__(self, taken: dict[str, str] | None = None, *, visible: bool = True) -> None:
        self.channels: dict[str, str] = dict(taken or {})  # name -> id
        self.visible = visible  # whether conversations.list can see a taken name
        self.calls: list[tuple[str, dict]] = []
        self.invites: list[tuple[str, str]] = []
        self.fail: set[str] = set()  # channel names conversations.create refuses

    def call(self, token: str, method: str, payload: dict) -> dict:
        self.calls.append((method, payload))
        if method == "auth.test":
            return {"ok": True, "team": "Acme HQ", "user": "yaaf"}
        if method == "conversations.create":
            name = payload["name"]
            if name in self.fail:
                return {"ok": False, "error": "restricted_action"}
            if name in self.channels:
                return {"ok": False, "error": "name_taken"}
            channel_id = f"C-{len(self.channels) + 1:03d}"
            self.channels[name] = channel_id
            return {"ok": True, "channel": {"id": channel_id, "name": name}}
        if method == "conversations.invite":
            self.invites.append((payload["channel"], payload["users"]))
            return {"ok": True}
        if method == "conversations.setPurpose":
            return {"ok": True}
        return {"ok": False, "error": "unknown_method"}

    def get(self, token: str, method: str, params: dict) -> dict:
        self.calls.append((method, params))
        if method == "conversations.list":
            if not self.visible:
                return {"ok": True, "channels": []}
            return {"ok": True, "channels": [{"id": cid, "name": name}
                                             for name, cid in self.channels.items()]}
        return {"ok": False, "error": "unknown_method"}

    def install(self, monkeypatch) -> _FakeSlack:
        monkeypatch.setattr(slack_provision, "_call", self.call)
        monkeypatch.setattr(slack_provision, "_get", self.get)
        return self


# ── helpers ─────────────────────────────────────────────────────────────────────


def set_global_slack(client, **fields) -> dict:
    body = {"bot_token": GLOBAL_BOT, "signing_secret": GLOBAL_SIGNING,
            "app_token": GLOBAL_APP, "chairman_slack_user": "U-CHAIR", **fields}
    response = client.put("/admin/settings/slack", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def map_channels(client, project_id: str, channel_map: dict, **slack_fields) -> None:
    response = client.patch(
        f"/admin/projects/{project_id}",
        json={"integrations": {"slack": {"channel_map": channel_map, **slack_fields}}},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


def sign(raw: bytes, timestamp: str, secret: str) -> str:
    base = b"v0:" + timestamp.encode() + b":" + raw
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def post_event(client, project_id: str, secret: str, *,
               user: str = "U-CHAIR", channel: str = "C-BOARD", ts: str = "1723710000.000200"):
    raw = json.dumps({
        "type": "event_callback",
        "event": {"type": "message", "channel": channel, "user": user,
                  "text": "Focus on the launch.", "ts": ts},
    }).encode()
    timestamp = str(int(time.time()))
    return client.post(
        f"/webhooks/slack/{project_id}",
        content=raw,
        headers={"X-Slack-Request-Timestamp": timestamp,
                 "X-Slack-Signature": sign(raw, timestamp, secret),
                 "Content-Type": "application/json"},
    )


# ── the model credential ────────────────────────────────────────────────────────


def test_the_platform_key_can_be_seen_rotated_tested_and_removed(client):
    """The gap this work closes: it was storable, and nothing else."""
    before = client.get("/admin/settings", headers=ADMIN).json()
    assert before["anthropic"]["configured"] is False

    installed = client.put("/admin/settings/anthropic",
                           json={"value": API_KEY, "mode": "api_key"}, headers=ADMIN)
    assert installed.status_code == 200, installed.text

    after = client.get("/admin/settings", headers=ADMIN).json()["anthropic"]
    assert after["configured"] is True
    assert after["billing_mode"] == "api_key"
    assert after["model"] == platform.DEFAULT_FOUNDER_MODEL
    # Whether one is installed, never what it is.
    assert API_KEY not in json.dumps(after)

    model = client.put("/admin/settings/anthropic/model",
                       json={"model": "claude-sonnet-5"}, headers=ADMIN)
    assert model.status_code == 200, model.text
    assert client.get("/admin/settings",
                      headers=ADMIN).json()["anthropic"]["model"] == "claude-sonnet-5"

    removed = client.delete("/admin/settings/anthropic", headers=ADMIN)
    assert removed.status_code == 200 and removed.json()["removed"] is True
    assert client.get("/admin/settings", headers=ADMIN).json()["anthropic"]["configured"] is False


def test_a_credential_of_the_wrong_shape_is_refused_at_the_door(client):
    """The same sentence the per-project billing form gives, from the same
    function — which form you pasted into must not change the answer."""
    refused = client.put("/admin/settings/anthropic",
                         json={"value": OAUTH_TOKEN, "mode": "api_key"}, headers=ADMIN)
    assert refused.status_code == 422
    assert "subscription OAuth token" in refused.json()["detail"]
    assert client.get("/admin/settings", headers=ADMIN).json()["anthropic"]["configured"] is False


def test_both_doors_to_the_platform_key_are_the_same_key(client):
    """The Founding screen's gate and the settings page write one credential."""
    client.put("/admin/founder/credential",
               json={"value": API_KEY, "mode": "api_key"}, headers=ADMIN)
    assert client.get("/admin/settings", headers=ADMIN).json()["anthropic"]["configured"] is True

    client.put("/admin/settings/anthropic",
               json={"value": OAUTH_TOKEN, "mode": "subscription"}, headers=ADMIN)
    status = client.get("/admin/founder/status", headers=ADMIN).json()
    assert status["credential_configured"] is True
    assert status["billing_mode"] == "subscription"


def test_the_platform_key_never_becomes_a_projects_billing_credential(client):
    """The rule, asserted where it would break: a project with no key of its
    own runs on nothing, however well-configured the platform is."""
    from yaaf.runner.daemon import _billing_env

    client.put("/admin/settings/anthropic",
               json={"value": API_KEY, "mode": "api_key"}, headers=ADMIN)
    project = make_project(client)

    with db_session() as session:
        row = session.get(Project, project["id"])
        assert row.billing_secret_id is None
        assert _billing_env(session, row) == {}

        # It is not reachable as a project secret either, and no role can be
        # granted it — the credential is billing-kind with no scopes.
        stored = session.execute(
            select(Secret).where(Secret.name == platform.PLATFORM_BILLING_SECRET)
        ).scalar_one()
        assert stored.project_id is None
        assert stored.role_scopes == []
        assert stored.kind == "billing"

    listed = client.get(f"/admin/secrets?project_id={project['id']}", headers=ADMIN)
    assert platform.PLATFORM_BILLING_SECRET not in [s["name"] for s in listed.json()]


# ── the shared Slack connection ─────────────────────────────────────────────────


def test_a_project_with_only_a_channel_map_inherits_the_whole_workspace(client, monkeypatch):
    """The case the feature exists for: same workspace, different channels."""
    set_global_slack(client)
    project = make_project(client)
    map_channels(client, project["id"], {"boardroom": "C-BOARD"})

    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "Ship the widget."}, headers=ADMIN)
    with db_session() as session:
        assert bridge.mirror_once(session) == 1
    assert recorder.posts[0]["token"] == GLOBAL_BOT
    assert recorder.posts[0]["channel"] == "C-BOARD"

    # Inbound too: the app-wide signing secret and the app-wide chairman.
    assert post_event(client, project["id"], GLOBAL_SIGNING).status_code == 200

    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["sources"]["bot_token"] == "global"
    assert view["sources"]["chairman_slack_user"] == "global"
    assert view["can_mirror"] and view["can_receive"]


def test_overriding_one_token_leaves_the_rest_inherited(client, monkeypatch):
    """Per-field, not all-or-nothing: a project in another workspace needs its
    own token and nothing else special."""
    set_global_slack(client)
    project = make_project(client)
    stored = client.post("/admin/secrets",
                         json={"project_id": project["id"], "name": "slack-bot",
                               "value": PROJECT_BOT, "role_scopes": []}, headers=ADMIN)
    assert stored.status_code == 200, stored.text
    map_channels(client, project["id"], {"boardroom": "C-BOARD"},
                 bot_token_secret="slack-bot")

    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "Ship the widget."}, headers=ADMIN)
    with db_session() as session:
        assert bridge.mirror_once(session) == 1
    assert recorder.posts[0]["token"] == PROJECT_BOT  # the override won

    # …and the signing secret it did NOT override is still the global one.
    assert post_event(client, project["id"], GLOBAL_SIGNING).status_code == 200

    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["sources"]["bot_token"] == "project"
    assert view["sources"]["signing_secret"] == "global"


def test_a_project_can_switch_slack_off_however_the_platform_is_wired(client, monkeypatch):
    """Without this a chairman could not silence one project without unwiring
    the whole install."""
    set_global_slack(client)
    project = make_project(client)
    map_channels(client, project["id"], {"boardroom": "C-BOARD"})
    client.patch(f"/admin/projects/{project['id']}",
                 json={"integrations": {"slack": {"enabled": False}}}, headers=ADMIN)

    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "Ship the widget."}, headers=ADMIN)
    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    assert recorder.posts == []

    refused = post_event(client, project["id"], GLOBAL_SIGNING)
    assert refused.status_code == 403
    assert "switched off" in refused.json()["detail"]


def test_a_project_inherits_nothing_before_a_workspace_exists(client, monkeypatch):
    """Inheritance is not a default that turns itself on: with no app-wide
    workspace, a project mirrors nowhere and says so."""
    project = make_project(client)
    map_channels(client, project["id"], {"boardroom": "C-BOARD"})
    recorder = _Recorder()
    monkeypatch.setattr(bridge, "_post_message", recorder)
    client.post(f"/admin/projects/{project['id']}/boardroom",
                json={"body": "Ship the widget."}, headers=ADMIN)
    with db_session() as session:
        assert bridge.mirror_once(session) == 0
    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["global_configured"] is False
    assert view["can_mirror"] is False


def test_no_slack_token_value_is_ever_returned(client):
    set_global_slack(client)
    project = make_project(client)
    for path in ("/admin/settings", f"/admin/projects/{project['id']}/slack"):
        body = client.get(path, headers=ADMIN).text
        for token in (GLOBAL_BOT, GLOBAL_SIGNING, GLOBAL_APP):
            assert token not in body, path


def test_a_bot_token_in_the_app_token_field_is_refused_now(client):
    refused = client.put("/admin/settings/slack",
                         json={"app_token": GLOBAL_BOT}, headers=ADMIN)
    assert refused.status_code == 422
    assert "xapp-" in refused.json()["detail"]


def test_blank_fields_keep_what_is_stored(client):
    """A form that cannot be saved without re-pasting three tokens is a form
    nobody uses to change the one field they came for."""
    set_global_slack(client)
    updated = client.put("/admin/settings/slack",
                         json={"chairman_slack_user": "U-OTHER"}, headers=ADMIN)
    assert updated.status_code == 200, updated.text
    assert updated.json()["bot_token_configured"] is True
    assert updated.json()["chairman_slack_user"] == "U-OTHER"
    with db_session() as session:
        assert platform.secret_value(session, platform.SLACK_BOT_SECRET) == GLOBAL_BOT


def test_dropping_the_workspace_leaves_projects_with_their_own_tokens_alone(client):
    set_global_slack(client)
    project = make_project(client)
    client.post("/admin/secrets", json={"project_id": project["id"], "name": "slack-bot",
                                        "value": PROJECT_BOT, "role_scopes": []}, headers=ADMIN)
    map_channels(client, project["id"], {"boardroom": "C-BOARD"},
                 bot_token_secret="slack-bot", chairman_slack_user="U-OWN")

    removed = client.delete("/admin/settings/slack", headers=ADMIN)
    assert removed.status_code == 200, removed.text
    assert client.get("/admin/settings", headers=ADMIN).json()["slack"][
        "bot_token_configured"] is False

    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["sources"]["bot_token"] == "project"
    assert view["can_mirror"] is True


# ── provisioning the rooms ──────────────────────────────────────────────────────


def test_provisioning_creates_the_rooms_and_reports_their_names(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client)

    result = client.post(f"/admin/projects/{project['id']}/slack/channels", headers=ADMIN)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["ok"] is True
    names = {row["key"]: row["name"] for row in body["channels"]}
    assert names["boardroom"] == "acme-boardroom"
    assert names["leadership"] == "acme-leadership"
    assert any(name == "acme-alpha" for name in names.values())  # the team room
    assert all(row["created"] for row in body["channels"])
    assert "acme-boardroom" in body["detail"]

    # Private by default, and the chairman is invited so he can actually read them.
    creates = [payload for method, payload in slack.calls if method == "conversations.create"]
    assert all(payload["is_private"] is True for payload in creates)
    assert {user for _, user in slack.invites} == {"U-CHAIR"}

    # The ids landed in the project's own channel map, and mirroring now works.
    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["can_mirror"] is True
    assert {room["key"]: room["channel_id"] for room in view["rooms"]}["boardroom"] \
        == slack.channels["acme-boardroom"]


def test_provisioning_twice_creates_nothing_twice(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client)

    client.post(f"/admin/projects/{project['id']}/slack/channels", headers=ADMIN)
    created_first = len(slack.channels)
    again = client.post(f"/admin/projects/{project['id']}/slack/channels", headers=ADMIN)
    assert again.status_code == 200, again.text
    assert len(slack.channels) == created_first
    assert all(row["already_mapped"] for row in again.json()["channels"])
    assert again.json()["ok"] is True


def test_a_name_slack_already_holds_is_adopted_not_duplicated(client, monkeypatch):
    """`name_taken` is the ordinary answer on a re-run, and on an install that
    made its channels by hand. Treating it as an error would make the button a
    trap."""
    slack = _FakeSlack({"acme-boardroom": "C-EXISTING"}).install(monkeypatch)
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client)

    body = client.post(f"/admin/projects/{project['id']}/slack/channels",
                       headers=ADMIN).json()
    boardroom = next(row for row in body["channels"] if row["key"] == "boardroom")
    assert boardroom["channel_id"] == "C-EXISTING"
    assert boardroom["created"] is False
    assert "adopted existing #acme-boardroom" in body["detail"]
    assert len([n for n in slack.channels if n == "acme-boardroom"]) == 1


def test_a_taken_name_the_bot_cannot_see_is_reported_not_guessed(client, monkeypatch):
    _FakeSlack({"acme-boardroom": "C-HIDDEN"}, visible=False).install(monkeypatch)
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client)

    body = client.post(f"/admin/projects/{project['id']}/slack/channels",
                       headers=ADMIN).json()
    boardroom = next(row for row in body["channels"] if row["key"] == "boardroom")
    assert boardroom["channel_id"] is None
    assert "cannot see it" in boardroom["error"]
    assert body["ok"] is False
    # One bad room does not cost the others.
    assert any(row["created"] for row in body["channels"])


def test_one_failed_room_does_not_undo_the_others(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    slack.fail.add("acme-leadership")
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client)

    body = client.post(f"/admin/projects/{project['id']}/slack/channels",
                       headers=ADMIN).json()
    rows = {row["key"]: row for row in body["channels"]}
    assert rows["boardroom"]["channel_id"]
    assert rows["leadership"]["channel_id"] is None
    assert "restricted_action" in rows["leadership"]["error"]
    assert body["ok"] is False

    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert {room["key"]: room["channel_id"] for room in view["rooms"]}["boardroom"]


def test_creating_a_project_provisions_its_rooms_and_says_what_they_are(client, monkeypatch):
    """The ask, end to end: make a project, be told its channels."""
    _FakeSlack().install(monkeypatch)
    set_global_slack(client)
    project = make_project(client, slug="widget")

    assert project["slack"]["ok"] is True
    names = sorted(row["name"] for row in project["slack"]["channels"])
    assert names == ["widget-alpha", "widget-boardroom", "widget-leadership"]
    view = client.get(f"/admin/projects/{project['id']}/slack", headers=ADMIN).json()
    assert view["can_mirror"] is True


def test_auto_provisioning_is_skipped_when_it_is_turned_off(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client, auto_create_channels=False)
    project = make_project(client, slug="widget")
    assert project["slack"] is None
    assert slack.channels == {}


def test_a_project_is_created_even_when_slack_is_having_a_bad_afternoon(client, monkeypatch):
    """A chairman whose Slack is down still wants his project."""
    def explode(*args, **kwargs):
        raise RuntimeError("slack is on fire")

    monkeypatch.setattr(slack_provision, "provision", explode)
    set_global_slack(client)
    project = make_project(client, slug="widget")
    assert project["id"]
    assert project["slack"]["ok"] is False
    assert "Retry from Settings" in project["slack"]["detail"]


def test_channel_names_survive_a_team_called_r_and_d(client, monkeypatch):
    """Slack refuses `invalid_name_specials`, which is a confusing way to be
    told your team has an ampersand in it."""
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client)
    make_project(client, slug="widget",
                 teams=[{"role_slug": "worker", "name": "R&D Squad",
                         "employees": ["Lead"], "lead": "Lead"}])
    assert "widget-r-d-squad" in slack.channels


def test_the_prefix_and_visibility_are_app_wide_and_overridable(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client, channel_prefix="proj", channel_visibility="public",
                     auto_create_channels=False)
    project = make_project(client)
    body = client.post(f"/admin/projects/{project['id']}/slack/channels",
                       headers=ADMIN).json()
    assert {row["name"] for row in body["channels"]} >= {"proj-boardroom", "proj-leadership"}
    creates = [payload for method, payload in slack.calls if method == "conversations.create"]
    assert all(payload["is_private"] is False for payload in creates)


def test_a_project_may_name_its_own_prefix(client, monkeypatch):
    slack = _FakeSlack().install(monkeypatch)
    set_global_slack(client, channel_prefix="proj", auto_create_channels=False)
    project = make_project(client)
    map_channels(client, project["id"], {}, channel_prefix="special")
    client.post(f"/admin/projects/{project['id']}/slack/channels", headers=ADMIN)
    assert "special-boardroom" in slack.channels
    assert "proj-boardroom" not in slack.channels


def test_provisioning_refuses_cleanly_with_no_workspace(client):
    project = make_project(client)
    refused = client.post(f"/admin/projects/{project['id']}/slack/channels", headers=ADMIN)
    assert refused.status_code == 422
    assert "no Slack bot token" in refused.json()["detail"]


def test_the_workspace_test_button_says_which_workspace(client, monkeypatch):
    _FakeSlack().install(monkeypatch)
    set_global_slack(client)
    checked = client.post("/admin/settings/slack/test", headers=ADMIN)
    assert checked.status_code == 200, checked.text
    assert checked.json()["ok"] is True
    assert "Acme HQ" in checked.json()["detail"]


def test_channel_names_stay_inside_slacks_eighty_characters(client):
    name = slack_settings.channel_name("x" * 70, "team:t", {"t": "y" * 40})
    assert len(name) <= slack_settings.MAX_CHANNEL_NAME
    assert name.startswith("x" * 70)


def test_mirroring_survives_one_project_with_a_broken_override(client, monkeypatch):
    """One misconfigured project never stops another — the promise the bridge
    already made, now that a shared workspace makes it easier to break."""
    set_global_slack(client)
    good = make_project(client, slug="good")
    map_channels(client, good["id"], {"boardroom": "C-GOOD"})
    bad = make_project(client, slug="bad")
    map_channels(client, bad["id"], {"boardroom": "C-BAD"})

    def selective(token: str, channel: str, text: str) -> str:
        if channel == "C-BAD":
            raise RuntimeError("channel_not_found")
        return "1700000000.000100"

    monkeypatch.setattr(bridge, "_post_message", selective)
    for project in (good, bad):
        client.post(f"/admin/projects/{project['id']}/boardroom",
                    json={"body": "Ship the widget."}, headers=ADMIN)
    with db_session() as session:
        assert bridge.mirror_once(session) == 1
        unmirrored = session.execute(
            select(Message).where(Message.project_id == bad["id"],
                                  Message.slack_ts.is_(None))
        ).scalars().all()
    assert len(unmirrored) == 1  # kept for the next tick, never lost
