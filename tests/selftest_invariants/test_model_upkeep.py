"""Invariant selftests: an install's models and prices kept current
(docs/PROVIDERS.md §5, §8, yaaf.core.model_upkeep).

A lineup and a price list rot. The promises:

- a seat whose model the provider no longer lists for its key is named on its
  project's health checklist — and a provider with no fresh list says
  nothing, because no list is not evidence of absence;
- the daily pass is off until an administrator switches it on; on, it re-reads
  every key's model list and refreshes prices — moving only prices that came
  from a feed, adding ones a seat needs, never an administrator's own, and
  auditing every change with what it was before;
- a provider or a feed that cannot be reached is recorded, not raised.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from tests.conftest import ADMIN, ROLE_MIN, headers_of, make_project, person
from yaaf.core import model_upkeep, price_feed
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, PlatformSetting
from yaaf.runtimes import wire

CREDENTIALS = "/admin/settings/llm-credentials"
PRICES = "/admin/settings/model-prices"
MODELS = "/admin/settings/models"


def _deepseek(client) -> str:
    return client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "DeepSeek", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-test"}).json()["id"]


def _serves(monkeypatch, *names: str) -> list:
    asked = []

    def handler(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"data": [{"id": n} for n in names]})

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(handler))
    return asked


def _project_on_deepseek(client, credential: str) -> dict:
    for slug, model in (("chief", "@frontier"), ("writer", "@fast")):
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
            "is_ceo_role": slug == "chief"}).status_code == 200
    for model in ("deepseek-chat", "deepseek-reasoner"):
        client.put(f"{PRICES}/{model}", headers=ADMIN, json={"input": 0.3, "output": 0.5})
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "on-deepseek", "name": "On DeepSeek", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": credential,
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    return created.json()


def _health(client, project: dict) -> dict:
    checks = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    return {c["key"]: c for c in checks["checks"]}


def test_a_seat_on_a_model_the_key_no_longer_lists_is_on_the_health_checklist(
        client, monkeypatch):
    make_project(client, start=False)
    credential = _deepseek(client)
    project = _project_on_deepseek(client, credential)
    assert not [k for k in _health(client, project) if k.startswith("model-served:")], \
        "no list read yet: nothing to say"
    _serves(monkeypatch, "deepseek-chat", "deepseek-v4")
    assert client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN).status_code == 200
    health = _health(client, project)
    retired = health["model-served:deepseek-reasoner"]
    assert retired["status"] == "warn" and "CEO" in retired["detail"]
    assert "model-served:deepseek-chat" not in health
    # A list from a month ago says little: it is not held against a seat.
    with db_session() as session:
        row = session.query(PlatformSetting).filter_by(
            key=model_upkeep.DISCOVERY + credential).one()
        found = json.loads(row.value)
        found["at"] = (model_upkeep.utcnow() - timedelta(days=30)).isoformat()
        row.value = json.dumps(found)
    assert "model-served:deepseek-reasoner" not in _health(client, project)


FEED = {
    "deepseek/deepseek-chat": {"litellm_provider": "deepseek", "mode": "chat",
                               "input_cost_per_token": 3e-07, "output_cost_per_token": 5e-07},
    "deepseek/deepseek-reasoner": {"litellm_provider": "deepseek", "mode": "chat",
                                   "input_cost_per_token": 6e-07,
                                   "output_cost_per_token": 2e-06},
    "gpt-6.1-sol": {"litellm_provider": "openai", "mode": "chat",
                    "input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05},
}


class _Inline:
    """The pass's own thread, run where the test can wait for it."""

    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def test_the_daily_pass_is_off_until_switched_on(client, monkeypatch):
    make_project(client, start=False)
    _deepseek(client)
    monkeypatch.setattr(model_upkeep.threading, "Thread", _Inline)
    asked = _serves(monkeypatch, "deepseek-chat")
    model_upkeep.tick()
    assert asked == [], "an install that never opted in reached out to a provider"
    assert client.put(f"{MODELS}/upkeep", headers=ADMIN,
                      json={"enabled": True}).json()["upkeep"]["enabled"] is True
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=FEED)))
    model_upkeep.tick()
    assert len(asked) == 1
    model_upkeep.tick()
    assert len(asked) == 1, "once a day, not once a tick"


def test_a_refresh_moves_feed_prices_adds_needed_ones_and_never_touches_an_admins(
        client, monkeypatch):
    make_project(client, start=False)
    _deepseek(client)
    _serves(monkeypatch, "deepseek-chat", "deepseek-reasoner")
    # One price from an earlier import, one an administrator typed.
    client.post("/admin/settings/model-prices/import", headers=ADMIN, json={
        "source": "snapshot", "apply": True, "models": ["deepseek-chat"]})
    client.put(f"{PRICES}/gpt-6.1-sol", headers=ADMIN, json={"input": 1.0, "output": 4.0})
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=FEED)))
    summary = client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()
    assert sorted(summary["prices"]["changed"]) == ["deepseek-chat", "deepseek-reasoner"]
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["deepseek-chat"]["own"]["input"] == 0.3
    assert table["deepseek-reasoner"]["own"]["output"] == 2.0
    assert table["gpt-6.1-sol"]["own"]["input"] == 1.0, "an administrator's price moved"
    assert table["claude-sonnet-5-5"]["own"] is None, "the catalog's checked price moved"
    with db_session() as session:
        [audit] = session.query(AuditLog).filter_by(action="model_price.refresh").all()
        changes = {c["model"]: c for c in audit.detail["changes"]}
    assert changes["deepseek-chat"]["from"]["input"] == 0.28
    assert changes["deepseek-reasoner"]["from"] is None
    assert summary["credentials"][next(iter(summary["credentials"]))]["models"] == 2


def test_an_unreachable_provider_or_feed_is_recorded_not_raised(client, monkeypatch):
    make_project(client, start=False)
    credential = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "Authentication Fails"}})))
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(503, text="down")))
    summary = client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()
    assert "error" in summary["credentials"][credential]
    assert "could not be read" in summary["prices"]["error"]
    assert client.get(MODELS, headers=ADMIN).json()["upkeep"]["last"]["at"] == summary["at"]


def test_the_models_screen_shows_the_evidence_beside_each_choice(client, monkeypatch):
    make_project(client, start=False)
    credential = _deepseek(client)
    _serves(monkeypatch, "deepseek-chat")
    client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN)
    deepseek = next(r for r in client.get(MODELS, headers=ADMIN).json()["lineups"]
                    if r["provider"] == "deepseek")
    assert deepseek["served"]["count"] == 1
    assert deepseek["classes"]["fast"]["served"] is True
    assert deepseek["classes"]["frontier"]["served"] is False
    assert deepseek["classes"]["fast"]["check"] is None


def test_only_an_administrator_switches_it_on_or_runs_it(client):
    member = headers_of(person("member@example.com"))
    assert client.put(f"{MODELS}/upkeep", headers=member,
                      json={"enabled": True}).status_code == 403
    assert client.post(f"{MODELS}/upkeep/run", headers=member).status_code == 403


def test_an_odd_answer_is_recorded_and_the_pass_is_not_due_again_until_tomorrow(
        client, monkeypatch):
    """A custom server behind a login proxy answers its model list with an
    HTML page. Raised, it rolled the pass back with nothing recorded, so the
    pass was due again — every janitor tick, against every provider."""
    make_project(client, start=False)
    credential = _deepseek(client)
    monkeypatch.setattr(model_upkeep.threading, "Thread", _Inline)
    asked = []

    def login_page(request):
        asked.append(request)
        return httpx.Response(200, text="<html>Sign in</html>")

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(login_page))
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=FEED)))
    client.put(f"{MODELS}/upkeep", headers=ADMIN, json={"enabled": True})
    model_upkeep.tick()
    model_upkeep.tick()
    assert len(asked) == 1
    last = client.get(MODELS, headers=ADMIN).json()["upkeep"]["last"]
    assert "error" in last["credentials"][credential]
    assert "changed" in last["prices"], "one credential's odd answer stopped the price refresh"
