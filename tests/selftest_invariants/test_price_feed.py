"""Invariant selftests: prices an install imports, and the smoke call that
proves a credential (docs/PROVIDERS.md §5, §8).

The rule stands: yaaf ships only prices checked against the vendor's own list.
An install imports the rest from a feed or its dated snapshot, and the import
is held to four promises:

- nothing is written without `apply` — the default is a preview;
- a price an administrator typed, and a price the catalog ships checked, are
  never replaced unless asked;
- every imported row says where it came from, and typing over one makes it
  the administrator's again;
- a model the feed does not list is reported, never filled in.
"""

from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import ADMIN, ROLE_MIN, make_project
from yaaf.core import price_feed
from yaaf.runtimes import wire

IMPORT = "/admin/settings/model-prices/import"
PRICES = "/admin/settings/model-prices"
CREDENTIALS = "/admin/settings/llm-credentials"


def _rows(response) -> dict[str, dict]:
    assert response.status_code == 200, response.text
    return {row["model"]: row for row in response.json()["rows"]}


def test_a_snapshot_import_previews_and_writes_nothing_until_applied(client):
    make_project(client, start=False)
    rows = _rows(client.post(IMPORT, headers=ADMIN, json={"source": "snapshot"}))
    assert rows["gpt-6.1-sol"]["action"] == "add"
    assert rows["gpt-6.1-sol"]["proposed"]["input"] == pytest.approx(2.0)
    assert rows["gemini-3.8-flash"]["action"] == "add"
    # The vendor's own checked list wins over a feed's copy of it.
    assert rows["claude-sonnet-5-5"]["action"] == "shipped"
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["gpt-6.1-sol"]["own"] is None, "a preview writes nothing"


def test_an_applied_import_prices_a_provider_so_its_seats_can_be_hired(client):
    """The case a chairman meets: an OpenAI key over the shipped role library
    is refused while OpenAI's models have no price (R3), and one import later
    the same project is created."""
    make_project(client, start=False)
    for slug, model in (("chief", "@frontier"), ("writer", "@balanced")):
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
            "is_ceo_role": slug == "chief"}).status_code == 200
    key = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "OpenAI", "mode": "api_key", "provider": "openai",
        "value": "sk-proj-openai-test"}).json()["id"]
    body = {"slug": "on-openai", "name": "On OpenAI", "goal": "Prove the framework works.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
            "billing_secret_id": key,
            "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]}
    refused = client.post("/admin/projects", headers=ADMIN, json=body)
    assert refused.status_code == 422 and "has no price" in refused.text
    applied = client.post(IMPORT, headers=ADMIN, json={"source": "snapshot", "apply": True})
    assert "gpt-6-astra" in applied.json()["applied"]
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["gpt-6-astra"]["source"].startswith("LiteLLM price feed snapshot ")
    created = client.post("/admin/projects", headers=ADMIN, json=body)
    assert created.status_code == 200, created.text


def test_a_price_an_administrator_typed_is_never_replaced_unless_asked(client):
    make_project(client, start=False)
    assert client.put(f"{PRICES}/gpt-6.1-sol", headers=ADMIN,
                      json={"input": 1.5, "output": 9.0}).status_code == 200
    rows = _rows(client.post(IMPORT, headers=ADMIN, json={"source": "snapshot",
                                                          "apply": True}))
    assert rows["gpt-6.1-sol"]["action"] == "own"
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["gpt-6.1-sol"]["own"]["input"] == 1.5 and table["gpt-6.1-sol"]["source"] is None
    forced = _rows(client.post(IMPORT, headers=ADMIN, json={
        "source": "snapshot", "apply": True, "overwrite_own": True, "models": ["gpt-6.1-sol"]}))
    assert forced["gpt-6.1-sol"]["action"] == "update"


def test_typing_over_an_imported_price_makes_it_the_administrators(client):
    make_project(client, start=False)
    client.post(IMPORT, headers=ADMIN, json={"source": "snapshot", "apply": True,
                                             "models": ["deepseek-chat"]})
    assert client.put(f"{PRICES}/deepseek-chat", headers=ADMIN,
                      json={"input": 0.3, "output": 0.5}).status_code == 200
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["deepseek-chat"]["source"] is None
    again = _rows(client.post(IMPORT, headers=ADMIN, json={
        "source": "snapshot", "models": ["deepseek-chat"]}))
    assert again["deepseek-chat"]["action"] == "own"


FEED = {
    "groq/llama-4-maverick": {"litellm_provider": "groq", "mode": "chat",
                              "input_cost_per_token": 2e-07, "output_cost_per_token": 6e-07,
                              "max_input_tokens": 131072},
    "groq/whisper-large": {"litellm_provider": "groq", "mode": "audio_transcription",
                           "input_cost_per_token": 1e-07, "output_cost_per_token": 0},
    "gpt-6.1-sol": {"litellm_provider": "openai", "mode": "chat",
                    "input_cost_per_token": 2e-06, "output_cost_per_token": 1e-05,
                    "cache_read_input_token_cost": 1e-07},
}


def test_a_live_feed_maps_onto_yaafs_names_and_reports_what_it_lacks(client, monkeypatch):
    make_project(client, start=False)
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda request: httpx.Response(200, json=FEED)))
    rows = _rows(client.post(IMPORT, headers=ADMIN, json={"provider": "groq"}))
    # A chat model, by the name a seat would use; never the audio one.
    assert set(rows) == {"groq/llama-4-maverick"}
    # The window comes with the price: yaaf's own loop compacts against it.
    assert rows["groq/llama-4-maverick"]["proposed"] == {
        "input": 0.2, "output": 0.6, "cache_read": 0.0, "cache_write": 0.0,
        "context_window": 131072}
    named = _rows(client.post(IMPORT, headers=ADMIN, json={
        "models": ["gpt-6.1-sol", "groq/no-such-model"]}))
    assert named["gpt-6.1-sol"]["proposed"]["cache_read"] == pytest.approx(0.1)
    assert named["groq/no-such-model"]["action"] == "missing"
    assert named["gpt-6.1-sol"]["source"].startswith("LiteLLM price feed 20")


def test_a_models_window_reaches_the_run_that_compacts_against_it(client):
    """The loop compacts a run's history before the model's window fills
    (loop_harness). It learns the window from the lease: the install's own
    figure where an administrator typed one, else the shipped snapshot's."""
    from yaaf.core.db import db_session
    from yaaf.core.prices import context_window

    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["deepseek-chat"]["context_window"] == 131072, "the snapshot's figure"
    assert client.put(f"{PRICES}/deepseek-chat", headers=ADMIN, json={
        "input": 0.3, "output": 0.5, "context_window": 64_000}).status_code == 200
    with db_session() as session:
        assert context_window(session, "deepseek-chat") == 64_000
        assert context_window(session, "custom/never-heard-of-it") is None
    # Typing a price again without a window keeps the window.
    client.put(f"{PRICES}/deepseek-chat", headers=ADMIN, json={"input": 0.3, "output": 0.6})
    with db_session() as session:
        assert context_window(session, "deepseek-chat") == 64_000


def test_an_unreachable_feed_says_so_and_points_at_the_snapshot(client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(down))
    refused = client.post(IMPORT, headers=ADMIN, json={})
    assert refused.status_code == 502 and "import the snapshot" in refused.text


def test_a_feed_is_read_over_https(client):
    assert client.put("/admin/settings/price-feed", headers=ADMIN,
                      json={"url": "http://mirror.local/prices.json"}).status_code == 422
    moved = client.put("/admin/settings/price-feed", headers=ADMIN,
                       json={"url": "https://mirror.example.com/prices.json"})
    assert moved.status_code == 200 and moved.json()["url"].startswith("https://mirror")


def test_the_snapshot_covers_every_model_yaaf_names():
    """A release refreshes it (`yaaf prices`); this fails when a catalog or
    lineup model is added without it."""
    from yaaf.runtimes import catalog
    from yaaf.runtimes.providers import PROVIDERS

    feed, label = price_feed.snapshot_feed()
    named = {m.id for m in catalog.MODELS}
    for provider in PROVIDERS.values():
        named |= set((provider.lineup or {}).values())
    missing = sorted(m for m in named if price_feed.lookup(feed, m) is None)
    assert not missing, f"the price snapshot has no row for {missing}"
    assert label.startswith("LiteLLM price feed snapshot ")


# ── the smoke call ──────────────────────────────────────────────────────────────


def _sse(lines: list[dict]) -> httpx.Response:
    body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body.encode())


def test_a_smoke_call_proves_the_key_the_address_and_the_wire(client, monkeypatch):
    make_project(client, start=False)
    client.put(f"{PRICES}/deepseek-chat", headers=ADMIN, json={"input": 0.28, "output": 0.42})
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "DeepSeek", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-test"}).json()["id"]
    sent = []

    def deepseek(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return _sse([{"model": "deepseek-chat", "choices": [{"index": 0, "delta": {
            "tool_calls": [{"index": 0, "id": "c1", "type": "function", "function": {
                "name": "report_ready", "arguments": '{"ready": true}'}}]}}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 8}}])

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(deepseek))
    result = client.post(f"{CREDENTIALS}/{credential}/smoke", headers=ADMIN, json={})
    assert result.status_code == 200, result.text
    body = result.json()
    # DeepSeek's fast class is deepseek-chat, the cheapest thing that proves it.
    assert (body["ok"], body["model"], body["called_tool"]) == (True, "deepseek-chat", True)
    assert body["usage"] == {"input": 120, "output": 8, "cache_read": 0}
    assert body["priced_usd"] == pytest.approx((120 * 0.28 + 8 * 0.42) / 1e6)
    assert sent[0]["tools"][0]["function"]["name"] == "report_ready"


def test_a_smoke_call_on_a_dead_key_says_auth(client, monkeypatch):
    make_project(client, start=False)
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Dead", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-dead"}).json()["id"]
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(401, json={"error": {"message": "Authentication Fails"}})))
    body = client.post(f"{CREDENTIALS}/{credential}/smoke", headers=ADMIN, json={}).json()
    assert body["ok"] is False and body["kind"] == "auth"


def test_a_login_is_not_smoked_from_the_control_plane(client):
    make_project(client, start=False)
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Plan", "mode": "subscription", "provider": "anthropic",
        "value": "sk-ant-oat-test-placeholder"}).json()["id"]
    body = client.post(f"{CREDENTIALS}/{credential}/smoke", headers=ADMIN, json={}).json()
    assert body["ok"] is None and "only its own CLI" in body["detail"]


def test_a_window_corrected_alone_leaves_a_shipped_price_shipped(client):
    """The loop compacts against a model's window; correcting it must not turn
    the catalog's checked price into one typed by hand, which a later release's
    corrected price would then no longer reach."""
    from tests.conftest import headers_of, person

    set_window = client.put("/admin/settings/model-context/claude-sonnet-5-5", headers=ADMIN,
                            json={"context_window": 500_000})
    assert set_window.status_code == 200 and set_window.json()["context_window"] == 500_000
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["claude-sonnet-5-5"]["own"] is None
    assert table["claude-sonnet-5-5"]["context_window"] == 500_000
    client.put("/admin/settings/model-context/claude-sonnet-5-5", headers=ADMIN,
               json={"context_window": None})
    table = {r["model"]: r for r in client.get(PRICES, headers=ADMIN).json()}
    assert table["claude-sonnet-5-5"]["context_window"] != 500_000
    member = headers_of(person("member@example.com"))
    assert client.put("/admin/settings/model-context/claude-sonnet-5-5", headers=member,
                      json={"context_window": 1000}).status_code == 403
