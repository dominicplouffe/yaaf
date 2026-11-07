"""Invariant selftests: a long prompt pays the rate its vendor bills it at
(yaaf.runtimes.catalog.Tier).

Grok bills twice its rate once a call's prompt passes 200k tokens; GPT-6 and
Gemini Pro do the same past 272k and 200k. yaaf imported only the base rate
and let its loop fill the model's whole window, so a long run was metered at
half what it spent — and xAI states no per-call cost a drift check could
catch. The promises:

- a feed's long-prompt rates come with its price, onto every row it writes,
  and a row priced before they were known is refreshed with them;
- one call past the line pays that tier's rates for the whole call, the
  highest tier it passes; a bill that sums calls is never judged on the sum;
- a call is sized for what it can afford at the highest rate, since its
  prompt's size is the provider's to count;
- an administrator typing a base rate does not drop the long-prompt rate.

The harness-side halves — Codex and the Gemini CLI price call by call — live
beside their own stand-ins (test_codex_harness.py, test_gemini_harness.py).
"""

from __future__ import annotations

import importlib

import httpx
import pytest

from tests.conftest import ADMIN, make_project
from yaaf.core import price_feed

pytest.importorskip("claude_agent_sdk")

IMPORT = "/admin/settings/model-prices/import"
PRICES = "/admin/settings/model-prices"
MODELS = "/admin/settings/models"

GROK = {"litellm_provider": "xai", "mode": "chat",
        "input_cost_per_token": 1.25e-06, "output_cost_per_token": 2.5e-06,
        "cache_read_input_token_cost": 2e-07, "max_input_tokens": 1_000_000,
        "input_cost_per_token_above_200k_tokens": 2.5e-06,
        "output_cost_per_token_above_200k_tokens": 5e-06,
        "cache_read_input_token_cost_above_200k_tokens": 4e-07,
        # Other service levels and a cache lifetime, not prompt sizes.
        "input_cost_per_token_above_200k_tokens_batches": 1.25e-06,
        "cache_creation_input_token_cost_above_1hr": 9e-06}
TIER = {"above": 200_000, "input": 2.5, "output": 5.0, "cache_read": 0.4, "cache_write": None}


def _feed(monkeypatch, feed: dict) -> None:
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=feed)))


def _row(client, model: str) -> dict:
    return next(r for r in client.get(PRICES, headers=ADMIN).json() if r["model"] == model)


def test_an_import_carries_the_long_prompt_rate_to_the_meter(client, monkeypatch):
    from yaaf.core import prices
    from yaaf.core.db import db_session

    make_project(client, start=False)
    _feed(monkeypatch, {"xai/grok-4.3": GROK})
    assert client.post(IMPORT, headers=ADMIN, json={
        "apply": True, "models": ["xai/grok-4.3"]}).json()["applied"] == ["xai/grok-4.3"]
    assert _row(client, "xai/grok-4.3")["own"]["tiers"] == [TIER]
    # What the lease hands the sandbox (YAAF_PRICE) is what it meters by.
    with db_session() as session:
        handed = prices.price(session, "xai/grok-4.3").as_dict()
    assert handed["tiers"] == [TIER]


def test_a_row_priced_before_its_tier_was_known_is_refreshed_with_it(client, monkeypatch):
    """Every Grok price imported before this change had one rate. A feed that
    states a tier is a change to that row, not "the same": the daily pass
    moves it, like any feed price."""
    make_project(client, start=False)
    untiered = {k: v for k, v in GROK.items() if "above_" not in k}
    _feed(monkeypatch, {"xai/grok-4.3": untiered})
    client.post(IMPORT, headers=ADMIN, json={"apply": True, "models": ["xai/grok-4.3"]})
    assert _row(client, "xai/grok-4.3")["own"]["tiers"] == []
    _feed(monkeypatch, {"xai/grok-4.3": GROK})
    [row] = client.post(IMPORT, headers=ADMIN, json={"models": ["xai/grok-4.3"]}).json()["rows"]
    assert row["action"] == "update"
    # A model a lineup names is one the daily pass keeps priced.
    assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "xai", "class": "frontier", "model": "xai/grok-4.3"}).status_code == 200
    summary = client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()
    assert summary["prices"]["changed"] == ["xai/grok-4.3"]
    assert _row(client, "xai/grok-4.3")["own"]["tiers"] == [TIER]


@pytest.fixture()
def meter(monkeypatch):
    """The sandbox's own meter, which reads its run's environment on import."""
    for name in ("YAAF_API_BASE_URL", "YAAF_API_TOKEN", "YAAF_RUN_ID"):
        monkeypatch.setenv(name, "http://api:8080" if name == "YAAF_API_BASE_URL" else "test")
    import yaaf.runner.sandbox_entry as module

    return importlib.reload(module)


BASE = {"input": 1.25, "output": 2.5, "cache_read": 0.2, "cache_write_5m": 0.0,
        "cache_write_1h": 0.0, "web_search": 0.01, "tiers": [TIER]}


def _usd(rates: dict, *, fresh: int, cached: int, out: int) -> float:
    return (fresh * rates["input"] + cached * rates["cache_read"] + out * rates["output"]) / 1e6


def test_a_call_past_the_line_pays_the_higher_rate_for_the_whole_call(meter):
    at_line = meter.price_usage(BASE, tokens_in=150_000, tokens_out=1_000, cache_read=50_000,
                                cache_creation=0)
    assert at_line == pytest.approx(_usd(BASE, fresh=150_000, cached=50_000, out=1_000))
    past = meter.price_usage(BASE, tokens_in=150_001, tokens_out=1_000, cache_read=50_000,
                             cache_creation=0)
    assert past == pytest.approx(_usd(TIER, fresh=150_001, cached=50_000, out=1_000)), \
        "the cached part of the prompt counts toward the line, and pays the tier too"


def test_the_highest_tier_passed_wins_and_an_unstated_rate_stays_the_base(meter):
    two = {**BASE, "tiers": [TIER, {"above": 500_000, "input": 4.0, "output": 8.0,
                                    "cache_read": None, "cache_write": None}]}
    usd = meter.price_usage(two, tokens_in=400_000, tokens_out=100, cache_read=200_000,
                            cache_creation=0)
    assert usd == pytest.approx((400_000 * 4.0 + 200_000 * BASE["cache_read"] + 100 * 8.0) / 1e6)


def test_a_bill_summing_calls_is_never_judged_on_its_sum(meter):
    """A Claude result line is every call of an attempt in one: its total
    passes 200k long before any one prompt does."""
    usd = meter.price_usage(BASE, tokens_in=300_000, tokens_out=1_000, cache_read=0,
                            cache_creation=0, one_call=False)
    assert usd == pytest.approx(_usd(BASE, fresh=300_000, cached=0, out=1_000))
    summed = meter.usage_bill({"input_tokens": 300_000, "output_tokens": 1_000},
                              cost_usd=None, subtype="success", one_call=False)
    assert meter._priced(BASE, summed) == pytest.approx(usd)
    # yaaf's loop on the Messages API: one message, one call, judged on its own.
    one = meter.usage_bill({"input_tokens": 300_000, "output_tokens": 1_000},
                           cost_usd=None, subtype=None)
    assert meter._priced(BASE, one) == pytest.approx(_usd(TIER, fresh=300_000, cached=0,
                                                          out=1_000))


def test_a_call_is_sized_at_the_highest_output_rate(meter):
    assert meter.top_rate(BASE, "output") == TIER["output"]
    assert meter.top_rate({**BASE, "tiers": []}, "output") == BASE["output"]


def test_typing_a_base_rate_keeps_the_long_prompt_rate_unless_told(client, monkeypatch):
    make_project(client, start=False)
    _feed(monkeypatch, {"xai/grok-4.3": GROK})
    client.post(IMPORT, headers=ADMIN, json={"apply": True, "models": ["xai/grok-4.3"]})
    assert client.put(f"{PRICES}/xai/grok-4.3", headers=ADMIN, json={
        "input": 1.0, "output": 2.0}).status_code == 200
    row = _row(client, "xai/grok-4.3")
    assert (row["own"]["input"], row["own"]["tiers"], row["source"]) == (1.0, [TIER], None)
    tiers = [{"above": 500_000, "input": 3.0, "output": 6.0},
             {"above": 128_000, "input": 2.0, "output": 4.0}]
    client.put(f"{PRICES}/xai/grok-4.3", headers=ADMIN, json={
        "input": 1.0, "output": 2.0, "tiers": tiers})
    assert [t["above"] for t in _row(client, "xai/grok-4.3")["own"]["tiers"]] == [128_000, 500_000]
    client.put(f"{PRICES}/xai/grok-4.3", headers=ADMIN, json={
        "input": 1.0, "output": 2.0, "tiers": []})
    assert _row(client, "xai/grok-4.3")["own"]["tiers"] == []


def test_the_snapshot_carries_the_tiers_of_the_models_it_ships():
    """An install that cannot reach the feed imports the snapshot, and GPT-6
    is billed more past 272k there too."""
    feed, _ = price_feed.snapshot_feed()
    found = price_feed.lookup(feed, "gpt-6.1-sol")
    assert found is not None and [t["above"] for t in found.tiers] == [272_000]
