"""Invariant selftests: the first providers that are not Anthropic (docs/RUNTIMES.md, M3).

A seat on DeepSeek, OpenRouter or a custom OpenAI-compatible server runs on
yaaf's own loop, pays with its own provider's credential, and is metered in the
same unit as everything else on its project. Each of those is a refusal at a
door, attacked here:

- R3 — a model with no price is refused on a harness that meters by price, and
  an install's own price (Settings → Model prices) is what lifts the refusal;
- R2 — a role granting `web` is refused on a provider that cannot search;
- R4 — one project, one meter: a credential billed in another unit joins it,
  and its runs are counted in the project's unit (test_mixed_payers.py);
- R5 — no seat on a provider the project holds no credential for.

And the done-when: a Claude seat and a DeepSeek seat on one project, both
metered against one cap, and the receipt says which spent what.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, ROLE_MIN, agent_by_name, make_project
from yaaf.core.db import db_session
from yaaf.core.models import LedgerEntry, Project, ProjectLlmCredential, Run
from yaaf.runner.backends import get_backend
from yaaf.runner.daemon import execute_job, lease_and_prepare
from yaaf.runner.types import RunResult
from yaaf.runtimes import catalog, providers
from yaaf.server.dispatcher import dispatch_once

CREDENTIALS = "/admin/settings/llm-credentials"
PRICES = "/admin/settings/model-prices"
#: A role with no web grant: what a DeepSeek seat can honour.
OFFLINE = {"files": True, "bash": False, "web": False}


def _credential(client, name: str, *, provider: str = "anthropic", mode: str = "api_key",
                value: str | None = None, base_url: str | None = None):
    body = {"name": name, "mode": mode, "provider": provider,
            "value": value or ("sk-ant-oat-test-placeholder" if mode == "subscription"
                               else f"test-{provider}-credential")}
    if base_url:
        body["base_url"] = base_url
    return client.post(CREDENTIALS, headers=ADMIN, json=body)


def _id(response) -> str:
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _role(client, slug: str, **fields):
    return client.post("/admin/roles", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), **ROLE_MIN, **fields})


def _price(client, model: str, **rates):
    return client.put(f"{PRICES}/{model}", headers=ADMIN,
                      json={"input": 0.28, "output": 0.42, **rates})


def _body(slug: str, **fields) -> dict:
    return {"slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "teams": [{"role_slug": "critic", "name": "Red", "employees": ["Dee"]}], **fields}


@pytest.fixture()
def critic(client):
    """The chairman, the base roles, a priced DeepSeek model and a critic role
    on it that does not need the web."""
    make_project(client, start=False)
    assert _price(client, "deepseek-chat").status_code == 200
    created = _role(client, "critic", model="deepseek-chat", tool_permissions=OFFLINE)
    assert created.status_code == 200, created.text


# ── a model is named with its provider ──────────────────────────────────────────


@pytest.mark.parametrize(("model", "provider", "api"), [
    ("claude-opus-5-5", "anthropic", "claude-opus-5-5"),
    ("deepseek-chat", "deepseek", "deepseek-chat"),
    ("openrouter/qwen/qwen3-coder", "openrouter", "qwen/qwen3-coder"),
    ("custom/llama3.1:8b", "custom", "llama3.1:8b"),
    # A slash is not a provider unless it names one.
    ("meta/llama", "anthropic", "meta/llama"),
])
def test_a_model_the_catalog_does_not_list_names_its_provider(model, provider, api):
    assert catalog.provider_of(model) == provider
    assert catalog.api_name(model) == api


# ── R3: prices are the install's, and an unpriced model is refused ──────────────


def test_a_model_with_no_price_cannot_be_hired_until_the_install_prices_it(client):
    make_project(client, start=False)
    refused = _role(client, "critic", model="deepseek-reasoner", tool_permissions=OFFLINE)
    assert refused.status_code == 422
    assert "deepseek-reasoner has no price on this install" in refused.text
    assert _price(client, "deepseek-reasoner", input=0.55, output=2.19).status_code == 200
    assert _role(client, "critic", model="deepseek-reasoner",
                 tool_permissions=OFFLINE).status_code == 200
    listed = {row["model"]: row for row in client.get(PRICES, headers=ADMIN).json()}
    assert listed["deepseek-reasoner"]["own"]["output"] == 2.19
    assert listed["deepseek-reasoner"]["shipped"] is None
    assert listed["claude-opus-5-5"]["shipped"]["output"] == 20.0


def test_a_price_can_be_zero_and_a_local_model_named_freely(client):
    make_project(client, start=False)
    assert _price(client, "custom/llama3.1:8b", input=0, output=0).json()["provider"] == "custom"
    assert _role(client, "local", model="custom/llama3.1:8b",
                 tool_permissions=OFFLINE).status_code == 200


def test_a_provider_that_states_its_costs_needs_no_price(client):
    make_project(client, start=False)
    assert _role(client, "router", model="openrouter/qwen/qwen3-coder",
                 tool_permissions=OFFLINE).status_code == 200


def test_an_install_price_wins_over_the_catalog_for_the_lease(client):
    default = catalog.resolve(catalog.DEFAULT_MODEL, "anthropic")
    _price(client, default, input=1.0, output=3.0)
    project = make_project(client, slug="repriced")
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10) if j.project_id == project["id"])
    assert (job.price["input"], job.price["output"]) == (1.0, 3.0)
    assert client.delete(f"{PRICES}/{default}", headers=ADMIN).status_code == 200
    # A price is for a model; a class names no model until it is resolved.
    assert _price(client, catalog.DEFAULT_MODEL).status_code == 422


# ── R2: a grant the harness cannot honour ───────────────────────────────────────


def test_a_role_that_needs_the_web_cannot_be_hired_onto_a_provider_that_cannot_search(client):
    """DeepSeek runs no search of its own, and yaaf's loop offers search only
    where the provider does — so a role granting `web` would get half of what
    it was hired with. Refused, not quietly halved (open question 6)."""
    make_project(client, start=False)
    _price(client, "deepseek-chat")
    refused = _role(client, "critic", model="deepseek-chat",
                    tool_permissions={"files": True, "web": True})
    assert refused.status_code == 422
    assert "cannot search the web on DeepSeek" in refused.text
    # On Anthropic's API the same grant is honoured: it searches.
    assert _role(client, "researcher", model="claude-sonnet-5-5", harness="anthropic_api",
                 tool_permissions={"files": True, "web": True}).status_code == 200


# ── credentials name their provider ─────────────────────────────────────────────


def test_a_credential_names_the_provider_it_pays(client):
    created = _credential(client, "DeepSeek key", provider="deepseek")
    assert created.status_code == 201, created.text
    assert created.json()["provider"] == "deepseek"
    assert "DeepSeek API key" in created.json()["bills"]
    listed = {c["name"]: c for c in client.get(CREDENTIALS, headers=ADMIN).json()}
    assert listed["DeepSeek key"]["provider"] == "deepseek"


@pytest.mark.parametrize(("fields", "says"), [
    ({"provider": "nobody"}, "unknown provider"),
    ({"provider": "deepseek", "mode": "subscription"}, "DeepSeek takes"),
    ({"provider": "custom"}, "carries the server's address"),
    ({"provider": "custom", "base_url": "ollama:11434"}, "carries the server's address"),
    ({"provider": "deepseek", "base_url": "http://elsewhere/v1"}, "a base URL is only for"),
    ({"provider": "deepseek", "value": "sk-ant-api03-not-deepseek"}, "Anthropic credential"),
])
def test_a_credential_that_cannot_be_what_it_says_is_refused(client, fields, says):
    refused = _credential(client, "Wrong", **fields)
    assert refused.status_code == 422 and says in refused.text


def test_a_custom_endpoint_carries_its_address(client):
    created = _credential(client, "Ollama", provider="custom", base_url="http://ollama:11434/v1/")
    assert created.status_code == 201, created.text
    assert created.json()["base_url"] == "http://ollama:11434/v1"


# ── R5: no seat on a provider nobody pays ───────────────────────────────────────


def test_a_seat_on_a_provider_the_project_cannot_pay_is_refused_at_creation(client, critic):
    claude = _id(_credential(client, "Claude key"))
    refused = client.post("/admin/projects", headers=ADMIN,
                          json=_body("unpaid", billing_secret_id=claude))
    assert refused.status_code == 422
    assert "Dee: runs on deepseek-chat, a DeepSeek model" in refused.text
    assert "no DeepSeek credential" in refused.text
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "paid", billing_secret_id=claude, credentials={"deepseek": deepseek}))
    assert created.status_code == 200, created.text
    view = client.get(f"/admin/projects/{created.json()['id']}/credentials", headers=ADMIN).json()
    assert [(c["provider"], c["name"], c["billing"]) for c in view] == [
        ("anthropic", "Claude key", True), ("deepseek", "DeepSeek key", False)]


def test_a_credential_a_seat_still_needs_cannot_be_taken_away(client, critic):
    claude = _id(_credential(client, "Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "kept", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    removed = client.delete(f"/admin/projects/{project['id']}/credentials/deepseek",
                            headers=ADMIN)
    assert removed.status_code == 422 and "Dee" in removed.text
    with db_session() as session:
        assert session.query(ProjectLlmCredential).filter_by(project_id=project["id"]).count() == 1


def test_a_project_with_a_seat_nobody_pays_does_not_start(client, critic):
    claude = _id(_credential(client, "Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "stalled", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    with db_session() as session:  # a door nobody has built yet
        session.query(ProjectLlmCredential).filter_by(project_id=project["id"]).delete()
    started = client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    assert started.status_code == 422 and "no DeepSeek credential" in started.text
    health = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    seats = [c for c in health["checks"] if c["key"] == "seats"]
    assert seats and "no DeepSeek credential" in seats[0]["detail"]


# ── R4: one project, one meter — whoever pays each run ──────────────────────────


def test_a_key_billed_in_usd_joins_a_token_metered_project(client, critic):
    """R4 keeps one meter per project and no longer refuses a second unit:
    every run is counted in the project's unit at its own price
    (test_mixed_payers.py has the arithmetic)."""
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    made = client.post("/admin/projects", headers=ADMIN, json=_body(
        "two-units", billing_secret_id=plan, credentials={"deepseek": deepseek}))
    assert made.status_code == 200, made.text
    dash = client.get(f"/observe/projects/{made.json()['id']}", headers=ADMIN).json()
    assert [c["provider"] for c in dash["credentials"]] == ["anthropic", "deepseek"]


def test_the_money_can_move_to_another_unit_under_a_second_provider(client, critic):
    claude = _id(_credential(client, "Claude key"))
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "meter-moves", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    moved = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                         json={"billing_secret_id": plan})
    assert moved.status_code == 200, moved.text
    with db_session() as session:
        assert session.get(Project, project["id"]).billing_secret_id == plan


@pytest.mark.parametrize("which", ["wrong-provider", "billing-provider"])
def test_a_credential_pays_only_its_own_provider_and_only_once(client, critic, which):
    claude = _id(_credential(client, "Claude key"))
    other = _id(_credential(client, "Another Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "once", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    provider, credential = (("deepseek", other) if which == "wrong-provider"
                            else ("anthropic", other))
    refused = client.put(f"/admin/projects/{project['id']}/credentials/{provider}",
                         headers=ADMIN, json={"credential_id": credential})
    assert refused.status_code == 422
    assert ("pays Anthropic, not DeepSeek" if which == "wrong-provider"
            else "already pays Anthropic with its billing credential") in refused.text


def test_a_credential_paying_a_second_provider_is_in_use(client, critic):
    claude = _id(_credential(client, "Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    client.post("/admin/projects", headers=ADMIN, json=_body(
        "in-use", billing_secret_id=claude, credentials={"deepseek": deepseek}))
    listed = {c["name"]: c for c in client.get(CREDENTIALS, headers=ADMIN).json()}
    assert listed["DeepSeek key"]["in_use"] is True
    assert client.delete(f"{CREDENTIALS}/{deepseek}", headers=ADMIN).status_code == 409
    hidden = client.post(f"{CREDENTIALS}/hide", headers=ADMIN, json={"ids": [deepseek]}).json()
    assert [r["id"] for r in hidden["refused"]] == [deepseek]
    assert "still paying for In-Use" in hidden["refused"][0]["reason"]


# ── the lease: a seat pays with its own provider's credential ───────────────────


def test_a_deepseek_seat_is_leased_onto_the_loop_with_its_own_key(client, critic):
    claude = _id(_credential(client, "Claude key", value="sk-ant-api03-claude-test"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek",
                               value="sk-deepseek-test-value"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "mixed", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    from yaaf.core import ids
    from yaaf.core.models import RunQueue

    with db_session() as session:
        dispatch_once(session)
        # The critic wakes on its own schedule; queued here rather than waited for.
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=agent_by_name(project, "Dee"), kind="agent_run",
                             trigger={"type": "test"}))
    jobs = {j.agent_id: j for j in lease_and_prepare(limit=20) if j.project_id == project["id"]}
    ceo, critic_job = jobs[project["ceo_agent_id"]], jobs[agent_by_name(project, "Dee")]
    assert ceo.billing_env.get("ANTHROPIC_API_KEY") == "sk-ant-api03-claude-test"
    assert "DEEPSEEK_API_KEY" not in ceo.billing_env
    assert (critic_job.provider, critic_job.harness) == ("deepseek", "yaaf_loop")
    assert critic_job.billing_env.get("DEEPSEEK_API_KEY") == "sk-deepseek-test-value"
    assert "ANTHROPIC_API_KEY" not in critic_job.billing_env
    assert critic_job.provider_spec == {
        "name": "deepseek", "protocol": "openai_chat", "auth": "bearer",
        "base_url": "https://api.deepseek.com/v1", "key_env": "DEEPSEEK_API_KEY",
        "reports_cost": False, "search": False, "search_usd": 0.0, "mints_tokens": False,
        "headers": {}, "failure_markers": ["authentication fails", "insufficient balance"]}
    assert critic_job.price["output"] == 0.42
    assert get_backend(critic_job.harness).entrypoint == "loop_entry.py"


@pytest.mark.parametrize(("provider", "key_env", "address"), [
    ("moonshot", "MOONSHOT_API_KEY", "https://api.moonshot.ai/v1"),
    ("qwen", "DASHSCOPE_API_KEY", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    ("mistral", "MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    ("xai", "XAI_API_KEY", "https://api.x.ai/v1"),
])
def test_a_long_tail_provider_is_a_row_and_nothing_else(client, provider, key_env, address):
    """M5's promise: Kimi, Qwen, Mistral and Grok are each a provider entry. A
    seat on one is hired, paid with its own key, and leased onto the loop at
    its vendor's address — through the doors DeepSeek's seat goes through."""
    make_project(client, start=False)
    model = f"{provider}/some-model"
    refused = _role(client, "critic", model=model, tool_permissions=OFFLINE)
    assert refused.status_code == 422 and "no price on this install" in refused.text  # R3
    assert _price(client, model).status_code == 200
    assert _role(client, "critic", model=model, tool_permissions=OFFLINE).status_code == 200
    claude = _id(_credential(client, "Claude key", value="sk-ant-api03-claude-test"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "unpaid", billing_secret_id=claude))
    label = providers.provider(provider).label
    assert project.status_code == 422 and f"no {label} credential" in project.text  # R5
    theirs = _id(_credential(client, f"{provider} key", provider=provider,
                             value=f"key-for-{provider}"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "paid", billing_secret_id=claude, credentials={provider: theirs})).json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    from yaaf.core import ids
    from yaaf.core.models import RunQueue

    with db_session() as session:
        dispatch_once(session)
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=agent_by_name(project, "Dee"), kind="agent_run",
                             trigger={"type": "test"}))
    job = next(j for j in lease_and_prepare(limit=20)
               if j.agent_id == agent_by_name(project, "Dee"))
    assert (job.provider, job.harness) == (provider, "yaaf_loop")
    assert job.billing_env == {key_env: f"key-for-{provider}"}
    assert (job.provider_spec["base_url"], job.provider_spec["key_env"]) == (address, key_env)
    assert job.provider_spec["failure_markers"]


def test_a_custom_servers_address_comes_from_its_credential(client):
    make_project(client, start=False)
    claude = _id(_credential(client, "Claude key"))
    ollama = _id(_credential(client, "Ollama", provider="custom",
                             base_url="http://ollama:11434/v1"))
    project = make_project(client, slug="local", start=False, billing_secret_id=claude)
    assert client.put(f"/admin/projects/{project['id']}/credentials/custom", headers=ADMIN,
                      json={"credential_id": ollama}).status_code == 200
    from yaaf.runner.daemon import _provider_spec

    with db_session() as session:
        spec = _provider_spec(session, session.get(Project, project["id"]), "custom")
    assert spec["base_url"] == "http://ollama:11434/v1"
    assert spec["key_env"] == "CUSTOM_LLM_API_KEY"


def test_a_project_paid_entirely_by_another_provider_needs_no_anthropic_key(client, critic):
    """The billing credential pays whichever provider it is for: a DeepSeek-only
    project bills DeepSeek with it and holds nothing else."""
    _price(client, "deepseek-reasoner", input=0.55, output=2.19)
    assert _role(client, "ceo", is_ceo_role=True, model="deepseek-reasoner",
                 tool_permissions=OFFLINE).status_code == 200
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    created = client.post("/admin/projects", headers=ADMIN,
                          json=_body("deepseek-only", billing_secret_id=deepseek))
    assert created.status_code == 200, created.text


# ── the done-when: two providers, one cap, one receipt ──────────────────────────


class _Spends:
    def __init__(self, usd: float):
        self.usd = usd

    def execute(self, job, stop_event=None):
        return RunResult(ok=True, tokens_out=100, cost_usd=self.usd, priced_usd=self.usd)


def test_two_providers_are_metered_against_one_cap_and_the_receipt_says_which(client, critic):
    from yaaf.server.receipt import receipt, render

    claude = _id(_credential(client, "Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "two-vendors", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10) if j.project_id == project["id"])
    execute_job(job, backend=_Spends(1.25))
    # The critic's run, as the loop would book it: provider pinned, dollars on
    # the same ledger the budget meter reads.
    from yaaf.core import ids

    with db_session() as session:
        run = Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                  agent_id=agent_by_name(project, "Dee"), kind="agent_run", trigger={},
                  status="done", model="deepseek-chat", provider="deepseek",
                  harness="openai_compatible", cost_usd=0.08)
        session.add(run)
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="model_usage", usd=0.08, run_id=run.id,
                                tokens_in=0, tokens_out=0, cache_creation_tokens=0,
                                cache_read_tokens=0))
    with db_session() as session:
        data = receipt(session, session.get(Project, project["id"]))
        text = render(data)
    assert data["cost"]["usd"] == pytest.approx(1.33)
    assert data["cost"]["by_provider"] == {"Anthropic": 1.25, "DeepSeek": 0.08}
    assert "$1.25 on Anthropic" in text and "$0.08 on DeepSeek" in text
    spend = client.get(f"/observe/projects/{project['id']}/spend?group_by=provider",
                       headers=ADMIN).json()
    assert {r["label"]: round(r["usd"], 2) for r in spend["rows"]} == {
        "Anthropic": 1.25, "DeepSeek": 0.08}


# ── travels: export, clone, the dashboard ───────────────────────────────────────


def test_what_pays_each_provider_travels_with_the_project(client, critic):
    claude = _id(_credential(client, "Claude key"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "travels", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    assert exported["project"]["credential_names"] == {"deepseek": "DeepSeek key"}
    twin = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                       json={"slug": "travels-twin"})
    assert twin.status_code == 200, twin.text
    with db_session() as session:
        rows = session.query(ProjectLlmCredential).filter_by(project_id=twin.json()["id"]).all()
        assert [(r.provider, r.secret_id) for r in rows] == [("deepseek", deepseek)]
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert [c["provider"] for c in dash["credentials"]] == ["anthropic", "deepseek"]
    health = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN).json()
    assert any(c["key"] == "billing:deepseek" and "DeepSeek key" in c["detail"]
               for c in health["checks"])


# ── M4: OpenAI on the Codex CLI, and R6 ─────────────────────────────────────────


def test_an_effort_the_model_does_not_take_is_refused_with_the_ones_it_does(client):
    """R6. Codex hands the API whatever effort it is given, so the list is the
    model's — read off the presets of the Codex CLI the image pins."""
    make_project(client, start=False)
    refused = _role(client, "coder", model="gpt-5.5", effort="max")
    assert refused.status_code == 422
    assert "gpt-5.5 takes the efforts low, medium, high, xhigh — not 'max'" in refused.text
    assert _role(client, "coder", model="gpt-6-sol", effort="max").status_code == 200
    # A Claude seat names efforts yaaf's way; nothing to refuse there.
    assert _role(client, "writer", model="claude-opus-5-5", effort="max").status_code == 200


def test_an_effort_override_meets_the_same_check(client):
    make_project(client, start=False)
    assert _role(client, "coder", model="gpt-5.5", effort="high").status_code == 200
    refused = client.post("/admin/projects", headers=ADMIN, json=_body(
        "effort-over", teams=[{"role_slug": "coder", "name": "Build", "employees": ["Ned"],
                               "effort_overrides": {"Ned": "max"}}]))
    assert refused.status_code == 422 and "Ned" in refused.text and "not 'max'" in refused.text


def test_a_role_that_withholds_files_cannot_run_on_codex(client):
    """Codex hands the models it knows best a file editor whatever the config
    says, so a role that leaves files off is refused rather than widened."""
    make_project(client, start=False)
    refused = _role(client, "talker", model="gpt-6-sol", harness="codex_cli",
                    tool_permissions={"files": False})
    assert refused.status_code == 422 and "cannot run without files" in refused.text
    assert _role(client, "talker", model="gpt-6-sol", harness="codex_cli",
                 tool_permissions={"files": True}).status_code == 200
    # yaaf's own loop withholds every grant — the road an OpenAI key takes by
    # default now (docs/PROVIDERS.md §3) — so the same role runs there once
    # its model has a price to be metered by.
    assert _price(client, "gpt-6-sol").status_code == 200
    assert _role(client, "talker", model="gpt-6-sol", harness="yaaf_loop",
                 tool_permissions={"files": False}).status_code == 200


def test_an_openai_seat_needs_a_price_only_under_a_dollar_meter(client):
    """R3 is about stopping a run in dollars. A ChatGPT plan is metered in
    tokens, so an unpriced model runs on it; an API key is not."""
    make_project(client, start=False)
    assert _role(client, "ceo", is_ceo_role=True, model="gpt-6-sol").status_code == 200
    key = _id(_credential(client, "OpenAI key", provider="openai", value="sk-proj-test-value"))
    login = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                            value='{"tokens": {"access_token": "a", "refresh_token": "r"}}'))
    team = [{"role_slug": "ceo", "name": "Solo", "employees": ["Ada"]}]
    refused = client.post("/admin/projects", headers=ADMIN,
                          json=_body("openai-key", billing_secret_id=key, teams=team))
    assert refused.status_code == 422 and "gpt-6-sol has no price" in refused.text
    paid = client.post("/admin/projects", headers=ADMIN,
                       json=_body("openai-plan", billing_secret_id=login, teams=team))
    assert paid.status_code == 200, paid.text


@pytest.mark.parametrize(("mode", "value", "says"), [
    ("subscription", "not-json-at-all", "auth.json"),
    ("subscription", '{"OPENAI_API_KEY": null}', "auth.json"),
    ("api_key", '{"tokens": {}}', "looks like a ChatGPT login"),
])
def test_an_openai_credential_of_the_wrong_shape_is_refused(client, mode, value, says):
    refused = _credential(client, "Wrong", provider="openai", mode=mode, value=value)
    assert refused.status_code == 422 and says in refused.text


def test_a_chatgpt_plan_reaches_the_sandbox_as_the_login_file_codex_reads(client):
    from yaaf.runner.daemon import _billing_env

    make_project(client, start=False)
    assert _role(client, "ceo", is_ceo_role=True, model="gpt-6-sol").status_code == 200
    login = '{"tokens": {"access_token": "a", "refresh_token": "r"}}'
    credential = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                                 value=login))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "on-codex", billing_secret_id=credential,
        teams=[{"role_slug": "ceo", "name": "Solo", "employees": ["Ada"]}])).json()
    with db_session() as session:
        assert _billing_env(session, session.get(Project, project["id"]), "openai") == {
            "CODEX_AUTH_JSON": login}
    assert get_backend("codex_cli").entrypoint == "codex_cli_entry.py"


def test_a_key_for_another_provider_over_claude_roles_says_both_ways_out(client):
    """The case a chairman met: a DeepSeek key picked for a project whose
    roles run on Claude. Refused — nothing pays Anthropic — in a sentence that
    says what the key does pay and both moves; then created once every seat
    is moved onto a DeepSeek model, as the New Project form's "Run every seat
    on" sends it."""
    make_project(client, start=False)
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    body = {"slug": "on-deepseek", "name": "On DeepSeek", "chairman_email": "chair@example.com",
            "goal": "Prove the framework works end to end.", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "billing_secret_id": deepseek,
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Ada", "Lin"]}]}
    refused = client.post("/admin/projects", headers=ADMIN, json=body)
    assert refused.status_code == 422
    assert "an Anthropic model, and this project holds no Anthropic credential" in refused.text
    assert "it pays DeepSeek" in refused.text and "or pay Anthropic too" in refused.text
    assert _price(client, "deepseek-chat").status_code == 200
    moved = {**body, "ceo_model_override": "deepseek-chat",
             "teams": [{**body["teams"][0],
                        "model_overrides": {"Ada": "deepseek-chat", "Lin": "deepseek-chat"}}]}
    created = client.post("/admin/projects", headers=ADMIN, json=moved)
    assert created.status_code == 200, created.text
    assert client.post(f"/admin/projects/{created.json()['id']}/start",
                       headers=ADMIN).status_code == 200
