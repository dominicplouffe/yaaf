"""Invariant selftests: the Founder and the goal grader run on whatever pays the
platform (docs/PROVIDERS.md §7).

- Its model is a class: a key of any provider with a frontier model is enough,
  and it resolves there — never to a Claude model the key cannot pay for.
- It speaks the provider's own wire: Gemini natively, with the thought
  signatures Gemini requires replayed on the parts they came on.
- "Regrade goal" runs on the Founder's model. It used to grade on a default
  Claude model whatever the credential paid.
"""

from __future__ import annotations

import json
import time

import httpx

from tests.conftest import ADMIN, make_project, seed_role
from yaaf.runtimes import wire

PLATFORM = "/admin/settings/anthropic"
DRAFT = {
    "slug": "sell-something", "name": "Sell Something",
    "goal": "Find a product worth building and take money from strangers by 31 December. "
            "Revenue through the payment processor's signed webhook is the only result "
            "that counts; signups and traffic are not.",
    "mode": "explore", "ceo_role_slug": "ceo", "ceo_name": "Founder",
    "teams": [{"role_slug": "worker", "name": "Build", "employees": ["Mika"], "lead": "Mika"}],
    "constitution": {"monthly_usd_cap": 120.0},
}


def _settle(client, message: str) -> dict:
    thread_id = client.post("/admin/founder/threads", headers=ADMIN,
                            json={"message": message}).json()["id"]
    for _ in range(200):
        state = client.get(f"/admin/founder/threads/{thread_id}", headers=ADMIN).json()
        if not state["running"]:
            return state
        time.sleep(0.05)
    raise AssertionError("the turn never settled")


def test_the_founder_drafts_on_gemini_through_its_own_api(client, monkeypatch):
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair", "is_admin": True})
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    installed = client.put(PLATFORM, headers=ADMIN, json={
        "provider": "google", "value": "AIza-platform-gemini-key"})
    assert installed.status_code == 200, installed.text
    assert installed.json()["model"] == "gemini-3.1-pro-preview"
    signed = {"functionCall": {"name": "propose_bundle", "args": {"project": DRAFT}},
              "thoughtSignature": "c2lnbmVk"}
    replies = [[signed], [{"text": "Explore, because you are still choosing what to build."}]]
    sent = []

    def gemini(request: httpx.Request) -> httpx.Response:
        sent.append({"url": str(request.url), "key": request.headers.get("x-goog-api-key"),
                     "body": json.loads(request.content)})
        parts = replies[len(sent) - 1]
        return httpx.Response(200, json={
            "candidates": [{"content": {"role": "model", "parts": parts},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}})

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(gemini))
    thread = _settle(client, "I want to find an idea and sell it.")
    assert thread["last_error"] is None, thread
    assert thread["draft_bundle"]["project"]["slug"] == "sell-something"
    first, second = sent
    assert first["url"] == ("https://generativelanguage.googleapis.com/v1beta/models/"
                            "gemini-3.1-pro-preview:generateContent")
    assert first["key"] == "AIza-platform-gemini-key"
    from yaaf.founder.tools import TOOL_NAMES

    declared = first["body"]["tools"][0]["functionDeclarations"]
    assert sorted(d["name"] for d in declared) == sorted(TOOL_NAMES)
    # The signature goes back on the part it came on — Gemini refuses a
    # function call replayed without it.
    assert second["body"]["contents"][1] == {"role": "model", "parts": [signed]}
    answered = second["body"]["contents"][2]["parts"][0]["functionResponse"]
    assert answered["name"] == "propose_bundle" and '"ok": true' in answered["response"]["content"]


def test_regrading_runs_on_the_founders_model_not_a_default_claude(client, monkeypatch):
    project = make_project(client, slug="regrade-on-deepseek")
    assert client.put(PLATFORM, headers=ADMIN, json={
        "provider": "deepseek", "value": "sk-deepseek-platform"}).status_code == 200
    graded = {}

    async def grade(goal, *, env, model):
        from yaaf.founder.grade import ElementGrade, GoalGrade

        graded.update(model=model, env=env)
        return GoalGrade(elements=[ElementGrade(element="finish_line", verdict="present",
                                                note="named")], summary="fine")

    monkeypatch.setattr("yaaf.founder.grade.grade_goal", grade)
    response = client.post(f"/admin/projects/{project['id']}/regrade-goal", headers=ADMIN)
    assert response.status_code == 200, response.text
    assert graded["model"] == "deepseek-reasoner"
    assert graded["env"] == {"DEEPSEEK_API_KEY": "sk-deepseek-platform"}


def test_the_founder_names_any_credential_as_the_billing_one(client):
    """Its credential menu used to count only Anthropic credentials as able to
    pay for a project, so an install with only an OpenAI key was told it had
    none. Any credential pays its provider, and each shows what the roles'
    classes would run on."""
    from yaaf.core.db import db_session
    from yaaf.founder.tools import list_credentials

    make_project(client, start=False)
    assert client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": "OpenAI", "mode": "api_key", "provider": "openai",
        "value": "sk-proj-openai-test"}).status_code == 201
    with db_session() as session:
        menu = list_credentials(session)
    assert "Exactly one" in menu["note"] and "'OpenAI' (OpenAI)" in menu["note"]
    [row] = menu["credentials"]
    assert row["classes"]["frontier"]["model"] == "gpt-6-astra"
    assert row["classes"]["balanced"]["priced"] is False
