"""Invariant selftests: a provider that ships no lineup has its empty classes
filled from its key's own list, by price (yaaf.core.lineup_pick).

Every class on xAI, Groq or Moonshot started empty, so every seat on one was
refused until an administrator typed three models in. The promises:

- reading a key's list fills each empty class: Frontier from the dearest
  price group, Fast from the cheapest, Balanced from the middle, the newest of
  each — by the provider's own dates, else the version in the name;
- only a chat model with a price that takes tool calls is picked: never an
  image model, a multi-agent one, a `-latest` alias, a deprecated one;
- a class an administrator named, or yaaf ships, is never filled;
- a class yaaf filled stays on its model: when yaaf would now pick another the
  readiness report says so, and one press moves it — nothing moves on its own,
  because every seat on the class moves with it;
- naming a class's model by hand makes it the administrator's;
- with upkeep off, nothing is filled.
"""

from __future__ import annotations

import httpx

from tests.conftest import ADMIN, make_project
from yaaf.core import lineup_pick, price_feed
from yaaf.runtimes import wire

CREDENTIALS = "/admin/settings/llm-credentials"
MODELS = "/admin/settings/models"
READY = "/admin/settings/providers/readiness"


def _entry(mode: str, inp: float | None = None, out: float | None = None, *,
           tools: bool | None = True, reasoning: bool | None = True, **extra) -> dict:
    entry = {"litellm_provider": "xai", "mode": mode, "supports_function_calling": tools,
             "supports_reasoning": reasoning, **extra}
    if inp is not None:
        entry |= {"input_cost_per_token": inp / 1e6, "output_cost_per_token": out / 1e6}
    return entry


#: The models the key in the report listed, as the feed described them on 2026-11-04.
FEED = {f"xai/{name}": entry for name, entry in {
    "grok-4.20-0309-non-reasoning": _entry("chat", 1.25, 2.5, reasoning=None),
    "grok-4.20-0309-reasoning": _entry("chat", 1.25, 2.5),
    "grok-4.20-multi-agent-0309": _entry("responses", 1.25, 2.5, tools=False),
    "grok-4.3": _entry("chat", 1.25, 2.5),
    "grok-4.5": _entry("chat", 2.0, 6.0),
    "grok-4.6": _entry("chat", 2.0, 6.0),
    "grok-4.7": _entry("chat", 2.0, 6.0),
    "grok-build-0.1": _entry("chat", 1.0, 2.0),
    "grok-imagine-image": _entry("image_generation", tools=None, reasoning=None),
    "grok-imagine-video": _entry("video_generation", tools=None, reasoning=None),
}.items()}
#: When the list says each was created: March for the dated 4.20, later for the rest.
CREATED = {"grok-4.20-0309-non-reasoning": 1_773_000_000, "grok-4.20-0309-reasoning": 1_773_000_000,
           "grok-4.20-multi-agent-0309": 1_773_000_000, "grok-4.3": 1_776_000_000,
           "grok-4.5": 1_779_000_000, "grok-4.6": 1_782_000_000, "grok-4.7": 1_785_000_000,
           "grok-build-0.1": 1_784_000_000, "grok-imagine-image": 1_770_000_000,
           "grok-imagine-video": 1_771_000_000}
PICKED = {"frontier": "xai/grok-4.7", "balanced": "xai/grok-4.3", "fast": "xai/grok-build-0.1"}


def _xai(client, monkeypatch, created: dict | None = CREATED, feed: dict = FEED,
         names: list[str] | None = None) -> str:
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Grok", "mode": "api_key", "provider": "xai",
        "value": "xai-test-key"}).json()["id"]
    _lists(monkeypatch, names or sorted(CREATED), created)
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=feed)))
    return credential


def _lists(monkeypatch, names: list[str], created: dict | None) -> None:
    rows = [{"id": n, **({"created": created[n]} if created and n in created else {})}
            for n in names]
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json={"object": "list", "data": rows})))


def _read(client, credential: str) -> dict:
    listed = client.get(f"{CREDENTIALS}/{credential}/models", headers=ADMIN)
    assert listed.status_code == 200, listed.text
    return listed.json()


def _xai_ready(client) -> dict:
    return next(p for p in client.get(READY, headers=ADMIN).json()["providers"]
                if p["provider"] == "xai")


def _lineup(client) -> dict:
    row = next(r for r in client.get(MODELS, headers=ADMIN).json()["lineups"]
               if r["provider"] == "xai")
    return {cls: (c["model"], c["source"]) for cls, c in row["classes"].items()}


def test_a_keys_list_fills_the_empty_classes_by_price_and_newest(client, monkeypatch):
    make_project(client, start=False)
    credential = _xai(client, monkeypatch)
    assert _read(client, credential)["filled"] == PICKED
    assert _lineup(client) == {cls: (m, "auto") for cls, m in PICKED.items()}
    xai = _xai_ready(client)
    assert {t["class"]: t["source"] for t in xai["tiers"]} == dict.fromkeys(PICKED, "auto")
    assert not [i for i in xai["issues"] if i["severity"] == "block"], xai["issues"]
    from yaaf.core.db import db_session
    from yaaf.core.models import AuditLog

    with db_session() as session:
        [audit] = session.query(AuditLog).filter_by(action="models.lineup.auto").all()
    assert audit.detail["filled"] == PICKED


def test_with_no_dates_the_version_in_the_name_decides(client, monkeypatch):
    """`grok-4.20` read as a version is 4.2, below 4.3: a list with no dates
    still gives Balanced the 4.3 the dated list does."""
    make_project(client, start=False)
    credential = _xai(client, monkeypatch, created=None)
    assert _read(client, credential)["filled"] == PICKED


def test_of_twins_frontier_reasons_and_fast_does_not(client, monkeypatch):
    make_project(client, start=False)
    names = ["grok-4.20-0309-non-reasoning", "grok-4.20-0309-reasoning"]
    credential = _xai(client, monkeypatch, names=names)
    assert _read(client, credential)["filled"] == {
        "frontier": "xai/grok-4.20-0309-reasoning", "balanced": "xai/grok-4.20-0309-reasoning",
        "fast": "xai/grok-4.20-0309-non-reasoning"}


def test_only_a_priced_chat_model_that_takes_tools_is_ever_picked(client, monkeypatch):
    """The dearest names on this list are each something a seat cannot run on
    or must not be pinned to; Frontier passes over every one of them."""
    make_project(client, start=False)
    feed = {**FEED,
            "xai/grok-5-latest": _entry("chat", 9.0, 30.0),
            "xai/grok-4-old": _entry("chat", 8.0, 25.0, deprecation_date="2026-01-01"),
            "xai/grok-4-notools": _entry("chat", 7.0, 20.0, tools=False),
            "xai/grok-imagine-pro": _entry("image_generation", 6.0, 18.0, tools=None),
            "xai/grok-4.20-multi-agent-0309": _entry("responses", 5.0, 16.0, tools=False)}
    names = sorted({*CREATED, "grok-5-latest", "grok-4-old", "grok-4-notools",
                    "grok-imagine-pro", "grok-unpriced-9"})
    credential = _xai(client, monkeypatch, feed=feed, names=names)
    assert _read(client, credential)["filled"]["frontier"] == "xai/grok-4.7"


def test_a_class_somebody_named_and_a_shipped_lineup_are_never_filled(client, monkeypatch):
    make_project(client, start=False)
    assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "xai", "class": "frontier", "model": "xai/grok-4.5"}).status_code == 200
    credential = _xai(client, monkeypatch)
    assert _read(client, credential)["filled"] == {k: v for k, v in PICKED.items()
                                                    if k != "frontier"}
    assert _lineup(client)["frontier"] == ("xai/grok-4.5", "install")
    # DeepSeek ships its lineup: its list fills nothing.
    deepseek = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "DeepSeek", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-test"}).json()["id"]
    _lists(monkeypatch, ["deepseek-v4-flash", "deepseek-v4-pro"], None)
    assert _read(client, deepseek)["filled"] == {}


def test_a_newer_model_is_offered_and_moved_only_on_a_press(client, monkeypatch):
    make_project(client, start=False)
    credential = _xai(client, monkeypatch)
    _read(client, credential)
    # A newer model at Frontier's price joins the key's list.
    feed = {**FEED, "xai/grok-4.8": _entry("chat", 2.0, 6.0)}
    names = sorted({*CREATED, "grok-4.8"})
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=feed)))
    _lists(monkeypatch, names, {**CREATED, "grok-4.8": 1_788_000_000})
    client.post(f"{MODELS}/upkeep/run", headers=ADMIN)
    assert _lineup(client)["frontier"] == ("xai/grok-4.7", "auto"), \
        "a class moved without anybody choosing it"
    [offer] = [i for i in _xai_ready(client)["issues"] if i["code"] == "newer_pick"]
    assert offer["severity"] == "warn" and offer["fix"] == {
        "action": "repick", "class": "frontier", "model": "xai/grok-4.8"}
    moved = client.post(f"{MODELS}/lineup/repick", headers=ADMIN,
                        json={"provider": "xai", "class": "frontier"})
    assert moved.status_code == 200, moved.text
    assert _lineup(client)["frontier"] == ("xai/grok-4.8", "auto")
    assert not [i for i in _xai_ready(client)["issues"] if i["code"] == "newer_pick"]
    assert client.post(f"{MODELS}/lineup/repick", headers=ADMIN, json={
        "provider": "xai", "class": "frontier"}).status_code == 409


def test_naming_a_picked_class_by_hand_makes_it_the_administrators(client, monkeypatch):
    make_project(client, start=False)
    credential = _xai(client, monkeypatch)
    _read(client, credential)
    client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "xai", "class": "frontier", "model": "xai/grok-4.5"})
    assert _lineup(client)["frontier"] == ("xai/grok-4.5", "install")
    assert not [i for i in _xai_ready(client)["issues"] if i["code"] == "newer_pick"], \
        "yaaf offered to undo an administrator's choice"
    assert client.post(f"{MODELS}/lineup/repick", headers=ADMIN, json={
        "provider": "xai", "class": "frontier"}).status_code == 409


def test_with_upkeep_off_nothing_is_filled(client, monkeypatch):
    make_project(client, start=False)
    credential = _xai(client, monkeypatch)
    client.put(f"{MODELS}/upkeep", headers=ADMIN, json={"enabled": False})
    assert _read(client, credential)["filled"] == {}
    assert summary_lineups(client) == {}


def summary_lineups(client) -> dict:
    return client.post(f"{MODELS}/upkeep/run", headers=ADMIN).json()["lineups"]


def test_the_daily_pass_fills_too(client, monkeypatch):
    make_project(client, start=False)
    _xai(client, monkeypatch)
    assert summary_lineups(client) == {"xai": PICKED}


def test_versions_read_as_decimals_and_an_even_count_takes_the_lower_middle():
    assert lineup_pick.version("grok-4.20-0309") < lineup_pick.version("grok-4.3")
    assert lineup_pick.version("grok-4.7") > lineup_pick.version("grok-4.6")
    groups = [lineup_pick.Candidate(f"xai/m{i}", f"m{i}", float(i), float(i), 0, ())
              for i in (1, 2, 3, 4)]
    picked = lineup_pick.pick(groups)
    assert {cls: c.ref for cls, c in picked.items()} == {
        "fast": "xai/m1", "balanced": "xai/m2", "frontier": "xai/m4"}


def test_a_lists_dates_are_read_from_either_shape():
    openai = wire.Endpoint.from_spec({"name": "xai", "protocol": "openai_chat",
                                      "base_url": "https://api.x.ai/v1"}, "k")
    assert wire.model_dates(openai, {"data": [
        {"id": "grok-4.3", "created": 1_776_000_000}, {"id": "no-date"},
        {"id": "claude-x", "created_at": "2026-02-01T00:00:00Z"}]}) == {
        "grok-4.3": 1_776_000_000, "claude-x": 1_769_904_000}


# ── only a maker's own family ──────────────────────────────────────────────────


def _key(client, monkeypatch, provider: str, value: str, names: list[str], feed: dict) -> str:
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": provider, "mode": "api_key", "provider": provider, "value": value}).json()["id"]
    _lists(monkeypatch, names, None)
    monkeypatch.setattr(price_feed, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, json=feed)))
    return credential


def _chat(provider: str, inp: float, out: float) -> dict:
    return {"litellm_provider": provider, "mode": "chat", "supports_function_calling": True,
            "input_cost_per_token": inp / 1e6, "output_cost_per_token": out / 1e6}


def test_a_host_of_many_makers_models_is_never_filled(client, monkeypatch):
    """Together's dearest model is somebody's frontier, not Together's, and a
    Llama and a Qwen name say nothing about which is newer."""
    make_project(client, start=False)
    names = ["meta-llama/Llama-4-Maverick", "Qwen/Qwen3-235B", "deepseek-ai/DeepSeek-V4"]
    feed = {f"together_ai/{n}": _chat("together_ai", 0.2 * (i + 1), 0.8 * (i + 1))
            for i, n in enumerate(names)}
    credential = _key(client, monkeypatch, "together", "together-test-key", names, feed)
    listed = _read(client, credential)
    assert all(m["price"] for m in listed["models"]), "the list is priced all the same"
    assert listed["filled"] == {}
    together = next(p for p in client.get(READY, headers=ADMIN).json()["providers"]
                    if p["provider"] == "together")
    [unset] = [i for i in together["issues"] if i["code"] == "no_model"]
    assert "serves many makers' models" in unset["text"]
    assert summary_lineups(client) == {}


def test_a_reseller_is_filled_from_its_own_family_only(client, monkeypatch):
    """Model Studio sells DeepSeek beside Qwen. The dearest model on the list
    is DeepSeek's, and Qwen's Frontier is still a Qwen model."""
    make_project(client, start=False)
    feed = {f"dashscope/{n}": _chat("dashscope", inp, out) for n, inp, out in (
        ("deepseek-v4-pro", 4.0, 16.0), ("qwen3-max", 1.2, 6.0), ("qwen-plus", 0.4, 1.2),
        ("qwen-flash", 0.05, 0.4))}
    names = sorted(n.split("/", 1)[1] for n in feed)
    credential = _key(client, monkeypatch, "qwen", "sk-qwen-test-key", names, feed)
    assert _read(client, credential)["filled"] == {
        "frontier": "qwen/qwen3-max", "balanced": "qwen/qwen-plus", "fast": "qwen/qwen-flash"}
