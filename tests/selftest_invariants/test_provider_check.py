"""Invariant selftests: the provider check — can this model do yaaf's work?
(docs/PROVIDERS.md §8, yaaf.core.provider_check).

A smoke call proves a key answers. This proves a model can drive a seat, and
is held to four promises:

- it is graded on state, never on a model's say-so: a fake model that does
  the work passes, one that only talks fails, step by step, saying why;
- a dead key is one finding, not eleven — the check stops on it;
- it costs the credential's money, so only an administrator starts it, it
  says what it would spend first, and two cannot run on one key at once;
- a login, which pays only its CLI inside a sandbox, is refused, not faked.

The model is a scripted stand-in on DeepSeek's wire (OpenAI-compatible chat,
streamed), which reads its tool results back as a real agent would — the
aggregate task is answered from the pages it was actually sent.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest

from tests.conftest import ADMIN, headers_of, make_project, person
from yaaf.runtimes import wire

CREDENTIALS = "/admin/settings/llm-credentials"


def _sse(delta: dict, finish: str, usage=(400, 30)) -> httpx.Response:
    lines = [{"model": "deepseek-chat", "choices": [{"index": 0, "delta": delta}]},
             {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
             {"choices": [], "usage": {"prompt_tokens": usage[0],
                                       "completion_tokens": usage[1]}}]
    body = "".join(f"data: {json.dumps(line)}\n\n" for line in lines) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=body.encode())


def _say(text: str) -> httpx.Response:
    return _sse({"role": "assistant", "content": text}, "stop")


def _calls(*calls: tuple[str, dict]) -> httpx.Response:
    return _sse({"role": "assistant", "tool_calls": [
        {"index": i, "id": f"call_{i}_{name}", "type": "function",
         "function": {"name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)]}, "tool_calls")


def _competent(request: httpx.Request) -> httpx.Response:
    """A model that does each job properly, from what it was actually sent."""
    body = json.loads(request.content)
    messages = body["messages"]
    tools = {t["function"]["name"] for t in body.get("tools") or []}
    asked = next(m["content"] for m in messages if m["role"] == "user")
    results = [m["content"] for m in messages if m["role"] == "tool"]
    if not tools:
        return _say("CHECK-4417" if "CHECK-4417" in asked else "ready")
    if "create_ticket" in tools:
        return _calls(("create_ticket", {"title": "Login returns 500", "priority": "urgent",
                                         "tags": ["auth", "outage"],
                                         "assignee": {"name": "Priya", "team": "platform"}}))
    if "lookup_order" in tools:
        return _say("It shipped with DHL: JD014600003829.") if results else \
            _calls(("lookup_order", {"order_id": "A-1182"}))
    if "get_weather" in tools:
        return _say("Paris 14°C, Tokyo 22°C, Lima 19°C.") if results else \
            _calls(*(("get_weather", {"city": c}) for c in ("Paris", "Tokyo", "Lima")))
    step = len(results)
    if "list_orders" in tools:
        pages = [json.loads(r) for r in results]
        if not pages or pages[-1]["orders"]:
            return _calls(("list_orders", {"page": step + 1}))
        refunded = [o for p in pages for o in p["orders"] if o["status"] == "refunded"]
        return _calls(("submit", {"answer": f"{len(refunded)} refunded, total "
                                            f"{sum(o['amount'] for o in refunded):.2f}"}))
    if "INC-2291" in asked:
        script = [("read_file", {"path": "notes/2026-09-16.md"}),
                  ("submit", {"answer": "notes/2026-09-16.md — Teodora Vance"})]
    elif "config.json" in asked:
        config = {"service": "ledger", "replicas": 3, "regions": ["us-east-1", "eu-west-1"],
                  "timeout_s": 30}
        script = [("read_file", {"path": "README.md"}), ("read_file", {"path": "config.json"}),
                  ("write_file", {"path": "config.json", "content": json.dumps(config)}),
                  ("submit", {"answer": "done"})]
    elif "reports/weekly.md" in asked:
        report = "# Weekly report\n- Shipped search\n- Fixed export\n- Hired a designer\n"
        script = [("write_file", {"path": "reports/weekly.md", "content": report}),
                  ("write_file", {"path": "out/report.md", "content": report}),
                  ("submit", {"answer": "done"})]
    else:
        names = "Chen Alvarez\nAiko Brandt\nDmitri Nakamura\nInes Okafor\nBram de Wit\n"
        script = [("read_file", {"path": "team.md"}),
                  ("write_file", {"path": "out/names.txt", "content": names}),
                  ("submit", {"answer": "done"})]
    return _calls(script[step])


def _chatty(request: httpx.Request) -> httpx.Response:
    """A model that answers every ping and does no work."""
    return _say("Sure! I'd be happy to help with that.")


def _deepseek(client) -> str:
    make_project(client, start=False)
    client.put("/admin/settings/model-prices/deepseek-chat", headers=ADMIN,
               json={"input": 0.28, "output": 0.42})
    return client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "DeepSeek", "mode": "api_key", "provider": "deepseek",
        "value": "sk-deepseek-test"}).json()["id"]


def _settled(client, credential: str) -> list[dict]:
    for _ in range(400):
        checks = client.get(f"{CREDENTIALS}/{credential}/checks", headers=ADMIN).json()
        if checks and all(c["status"] != "running" for c in checks):
            return checks
        time.sleep(0.02)
    raise AssertionError(f"the check never settled: {checks}")


def test_a_model_that_does_the_work_passes_every_probe_and_task(client, monkeypatch):
    credential = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(_competent))
    started = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                          json={"models": ["deepseek-chat"]})
    assert started.status_code == 200, started.text
    assert started.json()["checks"][0]["status"] == "running"
    [check] = _settled(client, credential)
    by_name = {o["name"]: o for o in check["outcomes"]}
    assert check["status"] == "passed", check
    assert check["summary"] == "4/4 probes, 5/5 tasks"
    # Not every probe applies to every model, and the ones that do not say why.
    assert by_name["effort"]["ok"] is None and "no effort dial" in by_name["effort"]["detail"]
    assert by_name["search"]["ok"] is None and "Web search" in by_name["search"]["detail"]
    assert by_name["parallel"]["detail"] == "called the tool for all three at once"
    assert by_name["aggregate"]["calls"] == 6, "five pages and a submit"
    # Metered at the install's price, step by step and in total.
    assert by_name["instructions"]["cost_usd"] == pytest.approx((400 * 0.28 + 30 * 0.42) / 1e6)
    assert check["cost_usd"] == pytest.approx(sum(o["cost_usd"] or 0 for o in check["outcomes"]))
    board = client.get("/admin/settings/provider-checks", headers=ADMIN).json()
    assert [(r["model"], r["status"]) for r in board] == [("deepseek-chat", "passed")]
    assert board[0]["task_calls"] is not None


def test_a_model_that_only_talks_fails_and_each_step_says_why(client, monkeypatch):
    credential = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(_chatty))
    client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                json={"models": ["deepseek-chat"]})
    [check] = _settled(client, credential)
    by_name = {o["name"]: o for o in check["outcomes"]}
    assert check["status"] == "failed" and check["passed"] == 0
    assert by_name["tool_arguments"]["detail"] == "did not call the tool"
    assert by_name["format"]["detail"] == "stopped without submitting"
    assert "replied" in by_name["instructions"]["detail"]


def test_a_dead_key_is_one_finding_and_the_check_stops(client, monkeypatch):
    credential = _deepseek(client)
    sent = []

    def dead(request):
        sent.append(request)
        return httpx.Response(401, json={"error": {"message": "Authentication Fails"}})

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(dead))
    client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                json={"models": ["deepseek-chat"]})
    [check] = _settled(client, credential)
    assert check["status"] == "error" and "refused the key" in check["summary"]
    assert len(sent) == 1, "the check kept spending calls on a dead key"


def test_it_says_what_it_would_spend_before_spending_it(client, monkeypatch):
    credential = _deepseek(client)
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: pytest.fail("a dry run made a call")))
    planned = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                          json={"dry_run": True}).json()
    # No models named: what DeepSeek's classes resolve to, once each.
    assert planned["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert planned["checks"] == [] and planned["steps"] == 11
    assert planned["estimate_usd"] is None, "deepseek-reasoner has no price to estimate by"
    probes = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN, json={
        "dry_run": True, "models": ["deepseek-chat"], "tasks": False}).json()
    assert probes["steps"] == 6 and probes["estimate_usd"] > 0


def test_only_an_administrator_can_spend_a_credential_on_a_check(client):
    credential = _deepseek(client)
    member = headers_of(person("member@example.com"))
    for method, path in (("post", f"{CREDENTIALS}/{credential}/check"),
                         ("get", f"{CREDENTIALS}/{credential}/checks"),
                         ("get", "/admin/settings/provider-checks")):
        response = getattr(client, method)(path, headers=member,
                                           **({"json": {}} if method == "post" else {}))
        assert response.status_code == 403, (path, response.status_code)


def test_one_check_at_a_time_and_only_on_the_credentials_own_provider(client, monkeypatch):
    credential = _deepseek(client)
    elsewhere = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                            json={"models": ["gpt-6.1-sol"]})
    assert elsewhere.status_code == 422 and "not served by" in elsewhere.text
    from yaaf.core import provider_check_runs

    monkeypatch.setattr(provider_check_runs, "submit", lambda ids: None)  # stays running
    assert client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                       json={"models": ["deepseek-chat"]}).status_code == 200
    again = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                        json={"models": ["deepseek-chat"]})
    assert again.status_code == 409 and "already running" in again.text


def test_a_login_is_refused_not_faked(client):
    make_project(client, start=False)
    credential = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Plan", "mode": "subscription", "provider": "anthropic",
        "value": "sk-ant-oat-test-placeholder"}).json()["id"]
    refused = client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN, json={})
    assert refused.status_code == 422 and "only its own CLI" in refused.text


def test_a_step_that_fails_partway_is_priced_at_the_calls_it_made(client, monkeypatch):
    """Ten calls in and refused on the eleventh, a task was paid for ten — the
    check's cost is what the credential spent, never the zero a fresh
    failure record would say."""
    credential = _deepseek(client)

    def flaky(request):
        body = json.loads(request.content)
        tools = {t["function"]["name"] for t in body.get("tools") or []}
        pages = [m for m in body["messages"] if m["role"] == "tool"]
        if "list_orders" in tools and len(pages) >= 2:
            return httpx.Response(500, json={"error": {"message": "upstream exploded"}})
        return _competent(request)

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(flaky))
    client.post(f"{CREDENTIALS}/{credential}/check", headers=ADMIN,
                json={"models": ["deepseek-chat"]})
    [check] = _settled(client, credential)
    aggregate = next(o for o in check["outcomes"] if o["name"] == "aggregate")
    assert aggregate["ok"] is False and "refused a call (transient)" in aggregate["detail"]
    assert aggregate["calls"] == 2
    assert aggregate["cost_usd"] == pytest.approx(2 * (400 * 0.28 + 30 * 0.42) / 1e6)
