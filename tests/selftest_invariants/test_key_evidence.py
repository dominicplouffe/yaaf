"""Invariant selftests: green means a key has answered (yaaf.core.key_evidence).

The readiness report says what yaaf would refuse. It cannot say whether the
provider takes the key, and Settings drew three keys no provider would accept
in green under "Ready". So a provider's `status` now reads the record too:

- a key nobody has called is `untested`, never `ready`;
- a Test that came back, or a run of a project the key pays, makes it `ready`;
- a refusal of the key itself ("auth") makes it `failing`, and the doctor names it;
- a call that failed for another reason says nothing about the key;
- only what happened after the key's value last changed counts.

And the dry runs that let the page show a consequence before the press, not
after it: removing web search, changing a lineup. Both answer without writing.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from tests.conftest import ADMIN, headers_of, make_project, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Run, RunStatus, UserSetupLink, utcnow
from yaaf.runtimes import wire

CREDENTIALS = "/admin/settings/llm-credentials"
READINESS = "/admin/settings/providers/readiness"
SEARCH = "/admin/settings/web-search"


def _sse(events: list[dict]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _answers(request: httpx.Request) -> httpx.Response:
    return _sse([{"model": "deepseek-v4-flash", "choices": [{"index": 0, "delta": {
        "tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {
            "name": "report_ready", "arguments": '{"ready": true}'}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 8}}])


def _deepseek(client, name: str = "DeepSeek", value: str = "sample-key-one") -> str:
    """A DeepSeek key on an install where nothing else would hold it back:
    its lineup priced, and a search backend for the roles that browse."""
    client.post("/admin/settings/model-prices/import", headers=ADMIN,
                json={"source": "snapshot", "apply": True, "provider": "deepseek"})
    client.put(SEARCH, headers=ADMIN, json={"backend": "brave", "key": "sample-search-key",
                                            "price_usd": 0.005})
    response = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": name, "mode": "api_key", "provider": "deepseek", "value": value})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _provider(client, name: str = "deepseek") -> dict:
    response = client.get(READINESS, headers=ADMIN)
    assert response.status_code == 200, response.text
    return next(p for p in response.json()["providers"] if p["provider"] == name)


def test_a_key_nobody_has_called_is_untested_not_ready(client):
    _deepseek(client)
    found = _provider(client)
    assert found["ready"] is True, found["issues"]
    assert found["status"] == "untested"
    assert found["evidence"]["state"] == "never"
    assert found["credentials"][0]["evidence"]["state"] == "never"


def test_a_test_that_answers_is_what_turns_it_green(client, monkeypatch):
    key = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(_answers))
    assert client.post(f"{CREDENTIALS}/{key}/smoke", headers=ADMIN, json={}).json()["ok"] is True
    found = _provider(client)
    assert found["status"] == "ready"
    assert (found["evidence"]["state"], found["evidence"]["via"]) == ("answered", "test")


def test_a_refused_key_is_failing_and_the_doctor_names_it(client, monkeypatch):
    key = _deepseek(client, name="Dead key")
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "Authentication Fails"}})))
    assert client.post(f"{CREDENTIALS}/{key}/smoke", headers=ADMIN, json={}).json()["kind"] == "auth"
    found = _provider(client)
    assert found["status"] == "failing"
    # Still runnable as far as yaaf's own doors go: the refusal is the provider's.
    assert found["ready"] is True
    doctor = {c["name"]: c for c in client.get("/admin/settings/doctor",
                                               headers=ADMIN).json()["checks"]}
    assert doctor["keys answer"]["ok"] is False
    assert "Dead key" in doctor["keys answer"]["detail"]


def test_a_model_list_the_provider_refused_marks_the_key_failing(client, monkeypatch):
    # The list endpoint answers a refusal with a 502, which rolls its own
    # transaction back; the refusal must outlive it.
    key = _deepseek(client, name="Listed dead")
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "invalid api key"}})))
    assert client.get(f"{CREDENTIALS}/{key}/models", headers=ADMIN).status_code == 502
    found = _provider(client)
    assert found["status"] == "failing"
    assert found["evidence"]["via"] == "model_list"


def test_a_call_that_failed_for_another_reason_says_nothing_about_the_key(client, monkeypatch):
    key = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(503, json={"error": {"message": "overloaded"}})))
    said = client.post(f"{CREDENTIALS}/{key}/smoke", headers=ADMIN, json={}).json()
    assert said["ok"] is False and said["kind"] != "auth"
    assert _provider(client)["status"] == "untested"


def test_a_rotated_key_is_untested_until_something_calls_it_again(client, monkeypatch):
    key = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(_answers))
    client.post(f"{CREDENTIALS}/{key}/smoke", headers=ADMIN, json={})
    assert _provider(client)["status"] == "ready"
    # A rename is not a new value: the answer still stands.
    client.patch(f"{CREDENTIALS}/{key}", headers=ADMIN, json={"name": "DeepSeek, renamed"})
    assert _provider(client)["status"] == "ready"
    client.patch(f"{CREDENTIALS}/{key}", headers=ADMIN,
                 json={"name": "DeepSeek, renamed", "value": "sample-key-two"})
    assert _provider(client)["status"] == "untested"


def test_runs_of_a_project_the_key_pays_are_evidence_both_ways(client):
    key = _deepseek(client)
    project = make_project(client, start=False)
    assert client.put(f"/admin/projects/{project['id']}/credentials/deepseek", headers=ADMIN,
                      json={"credential_id": key}).status_code == 200
    now = utcnow() + timedelta(seconds=5)

    def run(status: RunStatus, error_kind: str | None, at) -> None:
        with db_session() as session:
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], trigger={}, status=status,
                            error_kind=error_kind, provider="deepseek",
                            started_at=at, finished_at=at))

    run(RunStatus.DONE, None, now)
    found = _provider(client)
    assert (found["status"], found["evidence"]["via"]) == ("ready", "run")
    assert found["evidence"]["project"] == "Acme"
    # A later refusal of the key outranks the earlier answer.
    run(RunStatus.FAILED, "auth", now + timedelta(minutes=1))
    assert _provider(client)["status"] == "failing"
    # And a later answer clears it again: the newest word is the key's state.
    run(RunStatus.DONE, None, now + timedelta(minutes=2))
    assert _provider(client)["status"] == "ready"


def test_removing_web_search_can_be_asked_about_without_removing_it(client):
    _deepseek(client)
    dry = client.delete(f"{SEARCH}?dry_run=true", headers=ADMIN)
    assert dry.status_code == 200, dry.text
    assert dry.json()["removed"] is False and dry.json()["would_remove"] is True
    assert isinstance(dry.json()["unrunnable"], list)
    assert client.get(SEARCH, headers=ADMIN).json()["configured"] is not None


def test_a_lineup_change_is_previewed_with_its_price_and_never_saved(client):
    _deepseek(client)
    before = client.get("/admin/settings/models", headers=ADMIN).json()
    preview = client.post("/admin/settings/models/lineup/preview", headers=ADMIN,
                          json={"provider": "deepseek", "class": "fast",
                                "model": "deepseek-v4-pro"})
    assert preview.status_code == 200, preview.text
    said = preview.json()
    assert said["after"]["model"] == "deepseek-v4-pro"
    assert said["before"]["model"] != said["after"]["model"]
    assert said["after"]["input"] is not None and said["before"]["input"] is not None
    assert {"seats", "projects", "unrunnable"} <= set(said)
    assert client.get("/admin/settings/models", headers=ADMIN).json()["lineups"] == before["lineups"]


def test_a_preview_meets_the_same_refusals_as_the_save(client):
    preview = client.post("/admin/settings/models/lineup/preview", headers=ADMIN,
                          json={"provider": "no-such-provider", "class": "fast", "model": "x"})
    assert preview.status_code == 422


def test_the_dry_runs_and_the_report_are_an_administrators(client):
    member = headers_of(person("member@example.com"))
    assert client.post("/admin/settings/models/lineup/preview", headers=member,
                       json={"provider": "deepseek", "class": "fast"}).status_code == 403
    assert client.delete(f"{SEARCH}?dry_run=true", headers=member).status_code == 403
    assert client.get(READINESS, headers=member).status_code == 403


def test_a_connection_test_is_remembered_and_a_changed_token_forgets_it(client, monkeypatch):
    from yaaf.server.slack import provision

    monkeypatch.setattr(provision, "workspace_check",
                        lambda token: {"ok": True, "detail": "connected to Sample as yaaf"})
    client.put("/admin/settings/slack", headers=ADMIN, json={"bot_token": "xoxb-sample-one"})
    assert client.get("/admin/settings", headers=ADMIN).json()["slack"]["last_test"] is None
    assert client.post("/admin/settings/slack/test", headers=ADMIN).json()["ok"] is True
    tested = client.get("/admin/settings", headers=ADMIN).json()["slack"]["last_test"]
    assert tested["ok"] is True and tested["at"]
    # A preference is not a token: the test still stands.
    client.put("/admin/settings/slack", headers=ADMIN, json={"channel_visibility": "public"})
    assert client.get("/admin/settings", headers=ADMIN).json()["slack"]["last_test"] is not None
    client.put("/admin/settings/slack", headers=ADMIN, json={"bot_token": "xoxb-sample-two"})
    assert client.get("/admin/settings", headers=ADMIN).json()["slack"]["last_test"] is None


def test_an_invite_says_when_its_link_runs_out(client):
    made = client.post("/admin/users", headers=ADMIN,
                       json={"email": "new@example.com", "display_name": "New"}).json()
    rows = {r["email"]: r for r in client.get("/admin/users", headers=ADMIN).json()}
    assert rows["new@example.com"]["invite"]["expired"] is False
    with db_session() as session:
        for link in session.query(UserSetupLink).filter_by(user_id=made["id"]):
            link.expires_at = utcnow() - timedelta(hours=1)
    rows = {r["email"]: r for r in client.get("/admin/users", headers=ADMIN).json()}
    assert rows["new@example.com"]["invite"]["expired"] is True
    # Someone with a password has no invitation outstanding.
    assert rows["chair@example.com"]["invite"] is None
