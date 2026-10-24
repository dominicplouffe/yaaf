"""Invariant selftests: the control plane's side of every provider
(docs/PROVIDERS.md §4, §6, §8).

- A credential is checked live through the wire a run will use: the same
  address, the key presented the provider's own way.
- What a provider serves for a key is discoverable, by the names a seat uses.
- A local server takes no key; an addressable provider's credential carries
  its address, over TLS unless it is a server on the chairman's own network.
- A Vertex service account never reaches a sandbox: the control plane mints an
  hour-long token from it, and a run refreshes its own through the agent API —
  only for the provider it was leased on.
- The install's search backend is the only thing that lets a `web` role onto a
  provider that cannot search (R2), and only a run that searches through yaaf
  is ever handed its key.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import httpx
import pytest

from tests.conftest import ADMIN, ROLE_MIN, agent_by_name, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import RunQueue
from yaaf.runner.daemon import execute_job, lease_and_prepare
from yaaf.runner.types import RunResult
from yaaf.runtimes import wire
from yaaf.server.dispatcher import dispatch_once

CREDENTIALS = "/admin/settings/llm-credentials"
SEARCH = "/admin/settings/web-search"


def _credential(client, name: str, provider: str, value: str = "", **extra):
    return client.post(CREDENTIALS, headers=ADMIN, json={
        "name": name, "mode": "api_key", "provider": provider, "value": value, **extra})


def _service_account() -> tuple[str, object]:
    """A real RSA key in a service account's shape, and its public half."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return json.dumps({"type": "service_account", "project_id": "acme-ml",
                       "private_key_id": "kid-1", "private_key": pem,
                       "client_email": "yaaf@acme-ml.iam.gserviceaccount.com",
                       "token_uri": "https://oauth2.googleapis.com/token"}), key.public_key()


# ── the live check, through the wire ────────────────────────────────────────────


@pytest.mark.parametrize(("provider", "base_url", "url", "header", "value"), [
    ("anthropic", None, "https://api.anthropic.com/v1/models", "x-api-key", "the-key"),
    ("openai", None, "https://api.openai.com/v1/models", "authorization", "Bearer the-key"),
    ("google", None, "https://generativelanguage.googleapis.com/v1beta/models",
     "x-goog-api-key", "the-key"),
    ("azure_openai", "https://acme.openai.azure.com/openai/v1",
     "https://acme.openai.azure.com/openai/v1/models", "api-key", "the-key"),
    ("bedrock", "https://bedrock-runtime.eu-west-1.amazonaws.com",
     "https://bedrock.eu-west-1.amazonaws.com/foundation-models", "authorization",
     "Bearer the-key"),
    ("groq", None, "https://api.groq.com/openai/v1/models", "authorization", "Bearer the-key"),
    ("ollama", "http://ollama:11434/v1", "http://ollama:11434/v1/models", "authorization",
     None),
])
def test_a_live_check_asks_the_provider_its_own_way(monkeypatch, provider, base_url, url,
                                                    header, value):
    from yaaf.server.api.admin import live_check

    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [{"id": "m"}], "models": [{"name": "m"}],
                                         "modelSummaries": [{"modelId": "m"}]})

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(answer))
    secret = SimpleNamespace(provider=provider, llm_mode="api_key", base_url=base_url)
    result = live_check(secret, "the-key")
    assert result["ok"] is True, result
    assert str(seen[0].url) == url
    assert seen[0].headers.get(header) == value
    if provider == "anthropic":
        assert seen[0].headers["anthropic-version"] == "2023-06-01"


def test_a_live_check_reads_a_refusal_as_a_dead_key(monkeypatch):
    from yaaf.server.api.admin import live_check

    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(403, json={"error": {"message": "invalid api key"}})))
    result = live_check(SimpleNamespace(provider="groq", llm_mode="api_key", base_url=None),
                        "dead")
    assert result["ok"] is False and "rejected this key" in result["detail"]


def test_what_a_key_can_call_is_discoverable_by_the_names_a_seat_uses(client, monkeypatch):
    make_project(client, start=False)
    created = _credential(client, "DeepSeek", "deepseek", "sk-deepseek-test")
    assert created.status_code == 201, created.text
    monkeypatch.setattr(wire, "TRANSPORT", httpx.MockTransport(lambda r: httpx.Response(
        200, json={"data": [{"id": "deepseek-chat"}, {"id": "deepseek-v9"}]})))
    listed = client.get(f"{CREDENTIALS}/{created.json()['id']}/models", headers=ADMIN)
    assert listed.status_code == 200, listed.text
    assert listed.json()["models"] == [
        {"id": "deepseek-chat", "name": "deepseek-chat", "listed": True},
        {"id": "deepseek/deepseek-v9", "name": "deepseek-v9", "listed": False}]


# ── the credential door ─────────────────────────────────────────────────────────


def test_a_local_server_takes_no_key_and_its_address_is_on_the_sandbox_network(client):
    created = _credential(client, "Ollama box", "ollama", base_url="http://ollama:11434/v1/")
    assert created.status_code == 201, created.text
    assert created.json()["base_url"] == "http://ollama:11434/v1"
    refused = _credential(client, "No address", "ollama")
    assert refused.status_code == 422 and "carries the server's address" in refused.text


@pytest.mark.parametrize(("provider", "fields", "says"), [
    ("azure_openai", {"value": "azure-key-123456", "base_url": "http://acme.openai.azure.com"},
     "carries the server's address"),
    ("bedrock", {"value": "bedrock-key-123456"}, "carries the server's address"),
    ("vertex", {"value": "not-a-service-account",
                "base_url": "https://us-central1-aiplatform.googleapis.com/v1/projects/p/"
                            "locations/us-central1/publishers/google"},
     "service account key"),
    ("groq", {"value": "short"}, "at least 8 characters"),
    ("azure_openai", {"value": "azure-key-123456",
                      "base_url": "https://{resource}.openai.azure.com/openai/v1"},
     "carries the server's address"),
])
def test_an_addressable_credential_must_carry_a_real_address_over_tls(client, provider,
                                                                      fields, says):
    refused = _credential(client, "Wrong", provider, **fields)
    assert refused.status_code == 422 and says in refused.text, refused.text


def test_a_vertex_service_account_is_accepted_and_never_shown(client):
    account, _ = _service_account()
    created = _credential(client, "Vertex", "vertex", account, base_url=(
        "https://us-central1-aiplatform.googleapis.com/v1/projects/acme-ml/locations/"
        "us-central1/publishers/google"))
    assert created.status_code == 201, created.text
    assert "PRIVATE KEY" not in created.text
    listed = client.get(CREDENTIALS, headers=ADMIN).text
    assert "PRIVATE KEY" not in listed


# ── Vertex: minted in the control plane, refreshed through the agent API ────────


def test_a_minted_token_is_a_signed_assertion_google_can_verify():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    from yaaf.core import model_tokens

    account, public = _service_account()
    signed = model_tokens.assertion(json.loads(account), now=1_800_000_000)
    head, claims, signature = signed.split(".")

    def decode(part: str) -> bytes:
        import base64

        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))

    public.verify(decode(signature), f"{head}.{claims}".encode(), padding.PKCS1v15(),
                  hashes.SHA256())
    body = json.loads(decode(claims))
    assert body["iss"] == "yaaf@acme-ml.iam.gserviceaccount.com"
    assert body["scope"] == model_tokens.SCOPE
    assert body["exp"] - body["iat"] == 3600
    assert json.loads(decode(head))["kid"] == "kid-1"


def test_a_token_is_minted_once_an_hour_not_once_a_run():
    from yaaf.core import model_tokens

    model_tokens.forget()
    account, _ = _service_account()
    calls = []

    def google(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert b"grant_type=urn%3Aietf%3Aparams%3Aoauth%3Agrant-type%3Ajwt-bearer" in \
            request.content
        return httpx.Response(200, json={"access_token": "ya29.token", "expires_in": 3599})

    transport = httpx.MockTransport(google)
    first = model_tokens.google_token(account, transport=transport)
    second = model_tokens.google_token(account, transport=transport)
    assert first == second and first[0] == "ya29.token" and len(calls) == 1
    model_tokens.forget()


def test_a_service_account_google_refuses_is_a_dead_credential():
    from yaaf.core import model_tokens

    model_tokens.forget()
    account, _ = _service_account()
    with pytest.raises(model_tokens.MintError) as refused:
        model_tokens.google_token(account, transport=httpx.MockTransport(
            lambda r: httpx.Response(400, json={"error": "invalid_grant"})))
    assert refused.value.kind == "auth"


def _vertex_project(client) -> dict:
    make_project(client, start=False)
    assert client.put("/admin/settings/model-prices/vertex/gemini-3.1-pro-preview",
                      headers=ADMIN, json={"input": 2, "output": 12}).status_code == 200
    assert client.post("/admin/roles", headers=ADMIN, json={
        "slug": "analyst", "name": "Analyst", **ROLE_MIN,
        "model": "vertex/gemini-3.1-pro-preview", "is_ceo_role": True}).status_code == 200
    account, _ = _service_account()
    credential = _credential(client, "Vertex", "vertex", account, base_url=(
        "https://us-central1-aiplatform.googleapis.com/v1/projects/acme-ml/locations/"
        "us-central1/publishers/google")).json()["id"]
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "on-vertex", "name": "On Vertex", "goal": "Prove the framework works end to end.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "analyst", "ceo_name": "CEO",
        "billing_secret_id": credential,
        "teams": [{"role_slug": "analyst", "name": "Desk", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    assert client.post(f"/admin/projects/{created.json()['id']}/start",
                       headers=ADMIN).status_code == 200
    return created.json()


def test_a_vertex_run_is_handed_a_token_and_never_the_service_account(client, monkeypatch):
    from yaaf.core import model_tokens

    project = _vertex_project(client)
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10) if j.project_id == project["id"])
    assert (job.provider, job.harness) == ("vertex", "yaaf_loop")
    assert "PRIVATE KEY" not in json.dumps(job.billing_env)
    assert job.mint_secret_id and job.provider_spec["mints_tokens"] is True
    monkeypatch.setattr(model_tokens, "mint", lambda provider, key_file: (
        "ya29.minted", time.time() + 3600))
    seen = {}

    class Capture:
        def execute(self, job, stop_event=None):
            seen.update(job.billing_env)
            return RunResult(ok=True)

    execute_job(job, backend=Capture())
    assert seen["VERTEX_ACCESS_TOKEN"] == "ya29.minted"
    assert int(seen["YAAF_MODEL_TOKEN_EXPIRES"]) > time.time()
    assert "PRIVATE KEY" not in json.dumps(seen)


def test_a_run_refreshes_its_token_only_for_the_provider_it_was_leased_on(client, monkeypatch):
    from tests.conftest import live_token
    from yaaf.core import model_tokens

    project = _vertex_project(client)
    monkeypatch.setattr(model_tokens, "mint", lambda provider, key_file: (
        "ya29.fresh", time.time() + 3600))
    _, token = live_token(project["id"], agent_by_name(project, "Ada"))
    with db_session() as session:
        from yaaf.core.models import Run

        run = session.query(Run).filter_by(agent_id=agent_by_name(project, "Ada"),
                                           status="running").one()
        run.provider = "vertex"
    fresh = client.post("/agent/model-token", headers={"Authorization": f"Bearer {token}"})
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["token"] == "ya29.fresh"
    with db_session() as session:
        from yaaf.core.models import Run

        session.query(Run).filter_by(agent_id=agent_by_name(project, "Ada"),
                                     status="running").one().provider = "anthropic"
    refused = client.post("/agent/model-token", headers={"Authorization": f"Bearer {token}"})
    assert refused.status_code == 409
    forged = client.post("/agent/model-token", headers={"Authorization": "Bearer not-a-run"})
    assert forged.status_code == 401


# ── the platform's search backend, and R2 ───────────────────────────────────────


def test_a_search_backend_needs_its_key_or_its_address(client):
    assert client.put(SEARCH, headers=ADMIN, json={"backend": "brave"}).status_code == 422
    assert client.put(SEARCH, headers=ADMIN, json={"backend": "searxng"}).status_code == 422
    assert client.put(SEARCH, headers=ADMIN, json={"backend": "nope"}).status_code == 422
    configured = client.put(SEARCH, headers=ADMIN, json={
        "backend": "brave", "key": "brave-key-1234", "price_usd": 0.004})
    assert configured.status_code == 200, configured.text
    shown = configured.json()["configured"]
    assert (shown["backend"], shown["price_usd"], shown["has_key"]) == ("brave", 0.004, True)
    assert "brave-key-1234" not in configured.text
    # A blank key keeps the stored one: changing the price is not re-pasting.
    assert client.put(SEARCH, headers=ADMIN, json={
        "backend": "brave", "price_usd": 0.005}).status_code == 200


def _web_role_on_deepseek(client, slug: str):
    return client.post("/admin/projects", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
        "billing_secret_id": _credential(client, f"DeepSeek {slug}", "deepseek",
                                         "sk-deepseek-test").json()["id"],
        "ceo_model_override": "deepseek-chat",
        "teams": [{"role_slug": "researcher", "name": "Desk", "employees": ["Rey"],
                   "model_overrides": {"Rey": "deepseek-chat"}}]})


def test_a_web_role_on_a_provider_that_cannot_search_needs_the_platforms_backend(client):
    """R2's honour half: refused while nothing could search for the seat;
    honoured once the install has a backend; refused again once it is gone."""
    make_project(client, start=False)
    assert client.put("/admin/settings/model-prices/deepseek-chat", headers=ADMIN,
                      json={"input": 0.28, "output": 0.42}).status_code == 200
    assert client.post("/admin/roles", headers=ADMIN, json={
        "slug": "researcher", "name": "Researcher", **ROLE_MIN,
        "tool_permissions": {"web": True}}).status_code == 200
    refused = _web_role_on_deepseek(client, "searchless")
    assert refused.status_code == 422
    assert "cannot search the web on DeepSeek" in refused.text
    assert "Settings → Web search" in refused.text
    assert client.put(SEARCH, headers=ADMIN, json={
        "backend": "tavily", "key": "tvly-key-1234"}).status_code == 200
    created = _web_role_on_deepseek(client, "searching")
    assert created.status_code == 200, created.text
    project = created.json()
    assert client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN).status_code == 200
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=agent_by_name(project, "Rey"), kind="agent_run",
                             trigger={"type": "test"}))
    jobs = {j.agent_id: j for j in lease_and_prepare(limit=10)
            if j.project_id == project["id"]}
    rey = jobs[agent_by_name(project, "Rey")]
    assert json.loads(rey.billing_env["YAAF_SEARCH"])["backend"] == "tavily"
    assert rey.billing_env["YAAF_SEARCH_KEY"] == "tvly-key-1234"
    ceo = jobs.get(project["ceo_agent_id"])
    if ceo is not None:
        # The CEO's role has no web grant: it is never handed the key.
        assert "YAAF_SEARCH_KEY" not in ceo.billing_env
    removed = client.delete(SEARCH, headers=ADMIN)
    assert removed.status_code == 200
    assert any("cannot search the web" in line for line in removed.json()["unrunnable"])


def test_a_seat_whose_provider_searches_is_never_handed_the_platforms_key(client):
    """Claude searches on its own wire: the backend's key stays out of its run."""
    make_project(client, slug="claude-web", start=False)
    assert client.put(SEARCH, headers=ADMIN, json={
        "backend": "exa", "key": "exa-key-1234"}).status_code == 200
    assert client.post("/admin/roles", headers=ADMIN, json={
        "slug": "webceo", "name": "Web CEO", **ROLE_MIN, "is_ceo_role": True,
        "harness": "yaaf_loop", "tool_permissions": {"web": True}}).status_code == 200
    key = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "Claude key", "mode": "api_key", "provider": "anthropic",
        "value": "sk-ant-api03-claude-test"}).json()["id"]
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "searches-itself", "name": "Searches", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "webceo", "ceo_name": "CEO",
        "billing_secret_id": key,
        "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    assert client.post(f"/admin/projects/{project_id}/start", headers=ADMIN).status_code == 200
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10)
               if j.project_id == project_id and j.agent_id == created.json()["ceo_agent_id"])
    assert job.harness == "yaaf_loop" and job.provider == "anthropic"
    assert "YAAF_SEARCH_KEY" not in job.billing_env


def test_a_login_of_another_provider_is_never_sent_to_anthropics_plan_meter(client,
                                                                           monkeypatch):
    """The plan probe asks Anthropic. A ChatGPT login is a subscription too,
    and pressing Refresh on it must not hand it to Anthropic."""
    from yaaf.server import plan_probe

    monkeypatch.setattr(plan_probe, "refresh",
                        lambda *a, **k: pytest.fail("a ChatGPT login went to Anthropic"))
    login = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": "ChatGPT", "mode": "subscription", "provider": "openai",
        "value": '{"tokens": {"access_token": "a", "refresh_token": "r"}}'})
    assert login.status_code == 201, login.text
    meter = client.get(f"{CREDENTIALS}/{login.json()['id']}/usage", headers=ADMIN,
                       params={"refresh": "true"})
    assert meter.status_code == 200, meter.text
    assert meter.json()["available"] is False
    assert "OpenAI reports no plan meter" in meter.json()["reason"]
