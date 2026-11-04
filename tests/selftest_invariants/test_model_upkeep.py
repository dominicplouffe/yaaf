"""Invariant selftests: an install's models and prices kept current
(docs/PROVIDERS.md §5, §8, yaaf.core.model_upkeep).

A lineup and a price list rot. The promises:

- a seat whose model the provider no longer lists for its key is named on its
  project's health checklist — and a provider with no fresh list says
  nothing, because no list is not evidence of absence;
- a model the list leaves out but that has since answered a check on the same
  key stays named — an unlisted name that answers is a retired alias not yet
  switched off — but is no longer said to "be refused", because it is not;
- the daily pass is on until an administrator switches it off, and off means
  nothing is reached; on, it re-reads every key's model list and refreshes
  prices — moving only prices that came from a feed, adding ones a seat needs
  and ones a key's list offers, never an administrator's own, and auditing
  every change with what it was before;
- reading a key's list prices the chat models on it that have none yet and
  says what each one is, so a lineup is picked from priced models — filling
  gaps only, never moving a price, and never from a feed while upkeep is off;
- a provider or a feed that cannot be reached is recorded, not raised.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx

from tests.conftest import ADMIN, ROLE_MIN, headers_of, make_project, person
from yaaf.core import model_upkeep, price_feed
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, PlatformSetting, ProviderCheck
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
    for model in ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"):
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
    # A key never given the frontier model: its seats are named, the rest not.
    _serves(monkeypatch, "deepseek-v4-flash", "deepseek-v9")
    assert client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN).status_code == 200
    health = _health(client, project)
    named = health["model-served:deepseek-v4-pro"]
    assert named["status"] == "warn" and "CEO" in named["detail"]
    assert "model-served:deepseek-v4-flash" not in health
    # A list from a month ago says little: it is not held against a seat.
    with db_session() as session:
        row = session.query(PlatformSetting).filter_by(
            key=model_upkeep.DISCOVERY + credential).one()
        found = json.loads(row.value)
        found["at"] = (model_upkeep.utcnow() - timedelta(days=30)).isoformat()
        row.value = json.dumps(found)
    assert "model-served:deepseek-reasoner" not in _health(client, project)



def _checked(credential: str, model: str, *, ago: timedelta, answered: bool) -> None:
    """A finished provider check of `model` on the credential, `ago` back —
    every step passed, or every call refused as a model the key cannot find."""
    outcome = {"name": "instructions", "kind": "probe", "ok": answered, "calls": 1,
               "detail": "replied exactly as told" if answered
               else "the provider refused a call (fatal): model not found"}
    at = model_upkeep.utcnow() - ago
    with db_session() as session:
        session.add(ProviderCheck(
            id=f"chk-{model}-{int(ago.total_seconds())}-{answered}", secret_id=credential,
            provider="deepseek", model=model, status="passed" if answered else "failed",
            summary="1/1 probes" if answered else "0/1 probes", outcomes=[outcome],
            tasks=False, started_at=at, finished_at=at))


def test_an_unlisted_model_that_still_answers_stays_named_but_is_not_called_refused(
        client, monkeypatch):
    """The list is a forecast, a call is the fact: DeepSeek retired
    `deepseek-reasoner` and kept answering it. Silencing the warning on an
    answer would hide the day it is switched off; keeping "would be refused"
    tells a chairman something a check just proved false."""
    make_project(client, start=False)
    credential = _deepseek(client)
    # An install that set its frontier to the retired name, as installs did.
    assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "deepseek", "class": "frontier",
        "model": "deepseek-reasoner"}).status_code == 200
    project = _project_on_deepseek(client, credential)
    _serves(monkeypatch, "deepseek-v4-flash", "deepseek-v4-pro")
    # A check from before the list says nothing about a name since dropped,
    # and one whose every call was refused is no answer at all.
    _checked(credential, "deepseek-reasoner", ago=timedelta(days=3), answered=True)
    assert client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN).status_code == 200
    _checked(credential, "deepseek-reasoner", ago=timedelta(seconds=-5), answered=False)
    warned = _health(client, project)["model-served:deepseek-reasoner"]
    assert warned["status"] == "warn" and "would be refused" in warned["detail"]
    issue = _not_served(client, "deepseek-reasoner")
    assert "still answered" not in issue["text"]

    _checked(credential, "deepseek-reasoner", ago=timedelta(seconds=-10), answered=True)
    warned = _health(client, project)["model-served:deepseek-reasoner"]
    assert warned["status"] == "warn", "an answer must not clear a retired name"
    assert "would be refused" not in warned["detail"]
    assert "still answered a check" in warned["detail"] and "CEO" in warned["detail"]
    issue = _not_served(client, "deepseek-reasoner")
    assert issue["severity"] == "warn" and "still answered a check" in issue["text"]

    # Another key of the same provider answering proves nothing about this one.
    other = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "DeepSeek 2", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-other"}).json()["id"]
    with db_session() as session:
        assert model_upkeep.answered_since(
            session, other, "deepseek-reasoner",
            (model_upkeep.utcnow() - timedelta(days=1)).isoformat()) is None


def _not_served(client, model: str) -> dict:
    ready = client.get("/admin/settings/providers/readiness", headers=ADMIN).json()
    deepseek = next(p for p in ready["providers"] if p["provider"] == "deepseek")
    return next(i for i in deepseek["issues"]
                if i["code"] == "not_served" and model in i["text"])


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


def test_the_daily_pass_is_on_by_default_and_off_means_off(client, monkeypatch):
    """On by default: a lineup picked from names with no price beside them is
    refused the moment it is saved. Off is an install's word that it must not
    reach out daily, and is held to it — not a provider, not the feed."""
    make_project(client, start=False)
    _deepseek(client)
    monkeypatch.setattr(model_upkeep.threading, "Thread", _Inline)
    asked = _serves(monkeypatch, "deepseek-chat")
    feeds = []
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: feeds.append(r) or httpx.Response(200, json=FEED)))
    assert client.get(MODELS, headers=ADMIN).json()["upkeep"]["enabled"] is True
    assert client.put(f"{MODELS}/upkeep", headers=ADMIN,
                      json={"enabled": False}).json()["upkeep"]["enabled"] is False
    model_upkeep.tick()
    assert (asked, feeds) == ([], []), "an install that switched it off was reached out from"
    assert client.put(f"{MODELS}/upkeep", headers=ADMIN,
                      json={"enabled": True}).json()["upkeep"]["enabled"] is True
    model_upkeep.tick()
    assert (len(asked), len(feeds)) == (1, 1)
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
    _serves(monkeypatch, "deepseek-v4-flash")
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


# ── a key's list, priced and described as it is read ───────────────────────────


XAI_FEED = {
    "xai/grok-4.3": {"litellm_provider": "xai", "mode": "chat",
                     "input_cost_per_token": 1.25e-06, "output_cost_per_token": 2.5e-06,
                     "max_input_tokens": 1_000_000, "supports_reasoning": True},
    "xai/grok-4.5": {"litellm_provider": "xai", "mode": "chat",
                     "input_cost_per_token": 2e-06, "output_cost_per_token": 6e-06,
                     "max_input_tokens": 500_000, "supports_reasoning": True},
    "xai/grok-imagine-image": {"litellm_provider": "xai", "mode": "image_generation"},
}
#: What an xAI key lists: chat models, an image model, and a name newer than any feed.
XAI_SERVES = ("grok-4.3", "grok-4.5", "grok-imagine-image", "grok-next")


def _xai(client, monkeypatch, feed: dict | None = XAI_FEED) -> tuple[str, list]:
    """An xAI key whose list is XAI_SERVES, and the feed requests made."""
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Grok", "mode": "api_key", "provider": "xai",
        "value": "xai-test-key"}).json()["id"]
    _serves(monkeypatch, *XAI_SERVES)
    feeds: list = []

    def answer(request):
        feeds.append(request)
        if feed is None:
            raise httpx.ConnectError("no route to host")
        return httpx.Response(200, json=feed)

    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(answer))
    return credential, feeds


def _offered(client, credential: str) -> tuple[dict, dict]:
    listed = client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN)
    assert listed.status_code == 200, listed.text
    return {m["id"]: m for m in listed.json()["models"]}, listed.json()["feed"]


def test_reading_a_keys_list_prices_and_describes_what_it_offers(client, monkeypatch):
    """The case that made this: every model an xAI key listed was offered as
    "not in yaaf's catalog", with no price — and whichever was picked, the
    lineup was refused for want of one. Now the list is priced as it is read,
    an administrator's own price still wins, and an image model is said to be one."""
    make_project(client, start=False)
    credential, _ = _xai(client, monkeypatch)
    client.put(f"{PRICES}/xai/grok-4.5", headers=ADMIN, json={"input": 1.0, "output": 2.0})
    models, feed = _offered(client, credential)
    assert feed["live"] is True and feed["priced"] == ["xai/grok-4.3"]
    assert models["xai/grok-4.3"] == {
        "id": "xai/grok-4.3", "name": "grok-4.3", "listed": False, "in_lineup": False,
        "kind": "chat", "chat": True, "reasoning": True,
        "price": {"input": 1.25, "output": 2.5}, "context_window": 1_000_000}
    assert models["xai/grok-4.5"]["price"] == {"input": 1.0, "output": 2.0}, \
        "an administrator's price moved"
    assert models["xai/grok-imagine-image"]["chat"] is False
    assert (models["xai/grok-next"]["chat"], models["xai/grok-next"]["price"]) == (True, None)
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["xai/grok-4.3"]["source"].startswith("LiteLLM price feed 20")
    assert "xai/grok-imagine-image" not in table
    with db_session() as session:
        [audit] = session.query(AuditLog).filter_by(action="model_price.refresh").all()
    assert audit.detail["trigger"] == "model list"
    assert [c["model"] for c in audit.detail["changes"]] == ["xai/grok-4.3"]
    # Picked from that list, the class is not refused for want of a price.
    assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "xai", "class": "frontier", "model": "xai/grok-4.3"}).status_code == 200
    ready = client.get("/admin/settings/providers/readiness", headers=ADMIN).json()
    xai = next(p for p in ready["providers"] if p["provider"] == "xai")
    assert not [i for i in xai["issues"] if i["code"] == "unpriced"]


def test_a_list_read_fills_gaps_and_leaves_moving_prices_to_the_daily_pass(
        client, monkeypatch):
    """Reading a list only adds prices. Moving one is the daily pass's: a list
    read on a day the feed was down — the snapshot read in its place — must not
    put back an older price than the one the feed last gave."""
    make_project(client, start=False)
    credential, _ = _xai(client, monkeypatch, feed={
        "xai/grok-4.3": {**XAI_FEED["xai/grok-4.3"], "input_cost_per_token": 1e-06}})
    client.post("/admin/settings/model-prices/import", headers=ADMIN, json={
        "apply": True, "models": ["xai/grok-4.3"]})
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=XAI_FEED)))
    models, feed = _offered(client, credential)
    assert feed["priced"] == ["xai/grok-4.5"]
    assert models["xai/grok-4.3"]["price"]["input"] == 1.0, "a list read moved a price"
    summary = client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()
    assert summary["prices"]["changed"] == ["xai/grok-4.3"]
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["xai/grok-4.3"]["own"]["input"] == 1.25


def test_the_daily_pass_prices_every_chat_model_a_key_lists(client, monkeypatch):
    """Not only what seats already run on: a model priced only once it is
    picked is a lineup refused the moment it is saved."""
    make_project(client, start=False)
    _xai(client, monkeypatch)
    summary = client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()
    assert {"xai/grok-4.3", "xai/grok-4.5"} <= set(summary["prices"]["changed"])
    assert "xai/grok-imagine-image" not in summary["prices"]["changed"]
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["xai/grok-4.5"]["context_window"] == 500_000
    assert "xai/grok-imagine-image" not in table and "xai/grok-next" not in table


def test_with_upkeep_off_a_list_read_asks_no_feed_and_writes_no_price(client, monkeypatch):
    make_project(client, start=False)
    credential, feeds = _xai(client, monkeypatch)
    client.put(f"{MODELS}/upkeep", headers=ADMIN, json={"enabled": False})
    models, feed = _offered(client, credential)
    assert feeds == [], "an install that switched upkeep off was reached out from"
    assert (feed["live"], feed["priced"]) == (False, [])
    assert "snapshot" in feed["source"]
    assert set(models) == {f"xai/{n}" for n in XAI_SERVES}
    assert all(m["price"] is None for m in models.values())


def test_a_feed_that_cannot_be_read_still_lists_and_keeps_what_it_knew(client, monkeypatch):
    """The picker never waits on the feed to list a key's models: it reads
    the snapshot instead, and an image model it was told about yesterday is
    still one today."""
    make_project(client, start=False)
    credential, _ = _xai(client, monkeypatch)
    _offered(client, credential)

    def down(request):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(down))
    models, feed = _offered(client, credential)
    assert feed["live"] is False and "snapshot" in feed["source"]
    assert models["xai/grok-imagine-image"]["chat"] is False
    assert models["xai/grok-4.3"]["reasoning"] is True
