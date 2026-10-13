"""Invariant selftests: the first providers that are not Anthropic (docs/RUNTIMES.md, M3).

A seat on DeepSeek, OpenRouter or a custom OpenAI-compatible server runs on
yaaf's own loop, pays with its own provider's credential, and is metered in the
same unit as everything else on its project. A role names no provider: the
billing credential's provider runs every seat, and a seat moves to a second
provider only by being named onto one of its models. Each of those is a
refusal at a door, attacked here:

- R3 — a model with no price is refused on a harness that meters by price, and
  an install's own price (Settings → Model prices) is what lifts the refusal;
- R2 — a role granting `web` on a provider that cannot search fetches only,
  and says so to the agent and on the seat;
- R4 — one project, one meter: no credential metered in another unit;
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
    """A project whose critic, Dee, is put on DeepSeek by name — the one way a
    seat leaves the project's own provider."""
    return {"slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "teams": [{"role_slug": "critic", "name": "Red", "employees": ["Dee"],
                       "model_overrides": {"Dee": "deepseek-chat"}}], **fields}


@pytest.fixture()
def critic(client):
    """The chairman, the base roles, a priced DeepSeek model and a critic role
    that does not need the web."""
    make_project(client, start=False)
    assert _price(client, "deepseek-chat").status_code == 200
    created = _role(client, "critic", tool_permissions=OFFLINE)
    assert created.status_code == 200, created.text


def _seat(client, project: dict, name: str) -> dict:
    """What the seat runs on, as the agent drawer reads it."""
    return client.get(f"/observe/agents/{agent_by_name(project, name)}",
                      headers=ADMIN).json()["runs_on"]


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
    assert _role(client, "critic", tool_permissions=OFFLINE).status_code == 200
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    body = _body("unpriced", billing_secret_id=deepseek,
                 teams=[{"role_slug": "critic", "name": "Red", "employees": ["Dee"],
                         "tier_overrides": {"Dee": "strong"}}])
    refused = client.post("/admin/projects", headers=ADMIN, json=body)
    assert refused.status_code == 422
    assert "CEO" in refused.text and "deepseek-chat has no price on this install" in refused.text
    assert _price(client, "deepseek-chat").status_code == 200
    refused = client.post("/admin/projects", headers=ADMIN, json=body)
    assert refused.status_code == 422
    assert "Dee" in refused.text and "deepseek-reasoner has no price on this install" in refused.text
    assert _price(client, "deepseek-reasoner", input=0.55, output=2.19).status_code == 200
    created = client.post("/admin/projects", headers=ADMIN, json=body)
    assert created.status_code == 200, created.text
    listed = {row["model"]: row for row in client.get(PRICES, headers=ADMIN).json()}
    assert listed["deepseek-reasoner"]["own"]["output"] == 2.19
    assert listed["deepseek-reasoner"]["shipped"] is None
    assert listed["claude-opus-5-5"]["shipped"]["output"] == 20.0


def test_a_price_can_be_zero_and_a_local_model_named_freely(client, critic):
    assert _price(client, "custom/llama3.1:8b", input=0, output=0).json()["provider"] == "custom"
    ollama = _id(_credential(client, "Ollama", provider="custom",
                             base_url="http://ollama:11434/v1"))
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "local", billing_secret_id=ollama, seat_models={"standard": "custom/llama3.1:8b"},
        teams=[{"role_slug": "critic", "name": "Red", "employees": ["Dee"]}]))
    assert created.status_code == 200, created.text
    assert _seat(client, created.json(), "Dee")["model"] == "custom/llama3.1:8b"


def test_a_provider_that_states_its_costs_needs_no_price(client, critic):
    router = _id(_credential(client, "OpenRouter key", provider="openrouter"))
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "routed", billing_secret_id=router,
        seat_models={"standard": "openrouter/qwen/qwen3-coder"},
        teams=[{"role_slug": "critic", "name": "Red", "employees": ["Dee"]}]))
    assert created.status_code == 200, created.text


def test_an_install_price_wins_over_the_catalog_for_the_lease(client):
    _price(client, catalog.DEFAULT_MODEL, input=1.0, output=3.0)
    project = make_project(client, slug="repriced")
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=10) if j.project_id == project["id"])
    assert (job.price["input"], job.price["output"]) == (1.0, 3.0)
    assert client.delete(f"{PRICES}/{catalog.DEFAULT_MODEL}", headers=ADMIN).status_code == 200


# ── R2: a grant the harness cannot honour ───────────────────────────────────────


def test_a_role_that_needs_the_web_fetches_on_a_provider_that_cannot_search(client):
    """DeepSeek runs no search of its own, and yaaf's loop offers search only
    where the provider does. A role names no provider, so refusing the role
    here refused it on whichever key the project happened to hold: instead the
    seat fetches, and says so — to the agent, so it does not hunt for a tool it
    was never handed, and on the seat, so the chairman sees it (open
    question 6, decided)."""
    make_project(client, start=False)
    _price(client, "deepseek-chat")
    assert _role(client, "researcher",
                 tool_permissions={"files": True, "web": True}).status_code == 200
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "fetches", billing_secret_id=deepseek,
        teams=[{"role_slug": "researcher", "name": "Look", "employees": ["Rae"]}]))
    assert project.status_code == 200, project.text
    project = project.json()
    assert _seat(client, project, "Rae")["web"] == "fetch"
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    from yaaf.core import ids
    from yaaf.core.models import RunQueue

    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=agent_by_name(project, "Rae"), kind="agent_run",
                             trigger={"type": "test"}))
    job = next(j for j in lease_and_prepare(limit=20)
               if j.agent_id == agent_by_name(project, "Rae"))
    assert "this seat has no web search" in job.system_prompt
    # On Anthropic's key the same grant searches, and nothing is said.
    claude = _id(_credential(client, "Claude key"))
    searched = client.post("/admin/projects", headers=ADMIN, json=_body(
        "searches", billing_secret_id=claude,
        teams=[{"role_slug": "researcher", "name": "Look", "employees": ["Rae"]}])).json()
    assert _seat(client, searched, "Rae")["web"] == "search"


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
    ({"provider": "deepseek", "base_url": "http://elsewhere/v1"}, "only for a custom endpoint"),
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


# ── R4: one project, one meter ──────────────────────────────────────────────────


def test_a_credential_metered_in_another_unit_cannot_join_a_project(client, critic):
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    refused = client.post("/admin/projects", headers=ADMIN, json=_body(
        "two-units", billing_secret_id=plan, credentials={"deepseek": deepseek}))
    assert refused.status_code == 422 and "one project, one meter" in refused.text


def test_the_money_cannot_move_to_another_unit_under_a_second_provider(client, critic):
    claude = _id(_credential(client, "Claude key"))
    plan = _id(_credential(client, "Claude plan", mode="subscription"))
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "meter-moves", billing_secret_id=claude, credentials={"deepseek": deepseek})).json()
    moved = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                         json={"billing_secret_id": plan})
    assert moved.status_code == 422 and "one project, one meter" in moved.text
    with db_session() as session:
        assert session.get(Project, project["id"]).billing_secret_id == claude


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
    assert (critic_job.provider, critic_job.harness) == ("deepseek", "openai_compatible")
    assert critic_job.billing_env.get("DEEPSEEK_API_KEY") == "sk-deepseek-test-value"
    assert "ANTHROPIC_API_KEY" not in critic_job.billing_env
    assert critic_job.provider_spec == {
        "name": "deepseek", "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY", "reports_cost": False, "search": False,
        "failure_markers": ["authentication fails", "insufficient balance"]}
    assert critic_job.price["output"] == 0.42
    assert get_backend(critic_job.harness).entrypoint == "openai_compatible_entry.py"


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
    assert _role(client, "critic", tool_permissions=OFFLINE).status_code == 200
    model = f"{provider}/some-model"
    team = [{"role_slug": "critic", "name": "Red", "employees": ["Dee"],
             "model_overrides": {"Dee": model}}]
    claude = _id(_credential(client, "Claude key", value="sk-ant-api03-claude-test"))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "unpaid", billing_secret_id=claude, teams=team))
    label = providers.provider(provider).label
    assert project.status_code == 422 and f"no {label} credential" in project.text  # R5
    theirs = _id(_credential(client, f"{provider} key", provider=provider,
                             value=f"key-for-{provider}"))
    paid = _body("paid", billing_secret_id=claude, credentials={provider: theirs}, teams=team)
    refused = client.post("/admin/projects", headers=ADMIN, json=paid)
    assert refused.status_code == 422 and "no price on this install" in refused.text  # R3
    assert _price(client, model).status_code == 200
    project = client.post("/admin/projects", headers=ADMIN, json=paid).json()
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
    assert (job.provider, job.harness) == (provider, "openai_compatible")
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
    """The billing credential pays whichever provider it is for, and that
    provider runs every seat: a DeepSeek-only project bills DeepSeek with it,
    holds nothing else, and needs no seat moved by hand."""
    _price(client, "deepseek-reasoner", input=0.55, output=2.19)
    assert _role(client, "ceo", is_ceo_role=True, tier="strong",
                 tool_permissions=OFFLINE).status_code == 200
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "deepseek-only", billing_secret_id=deepseek,
        teams=[{"role_slug": "critic", "name": "Red", "employees": ["Dee"]}]))
    assert created.status_code == 200, created.text
    assert _seat(client, created.json(), "CEO")["model"] == "deepseek-reasoner"
    assert _seat(client, created.json(), "Dee")["model"] == "deepseek-chat"


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


#: A ChatGPT plan: metered in tokens, so an unpriced OpenAI model runs on it.
CHATGPT = '{"tokens": {"access_token": "a", "refresh_token": "r"}}'


def test_a_role_effort_the_model_does_not_take_runs_at_the_nearest_it_does(client):
    """A role's effort is a rung on one ladder, not a vendor's word: a role
    asking `max` of a model that stops at `xhigh` runs at `xhigh`, and the seat
    says what it asked for beside what it runs at. Read off the presets of the
    Codex CLI the image pins."""
    make_project(client, start=False)
    assert _role(client, "coder", effort="max").status_code == 200
    login = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                            value=CHATGPT))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "fitted", billing_secret_id=login, seat_models={"standard": "gpt-5.5"},
        teams=[{"role_slug": "coder", "name": "Build", "employees": ["Ned"]}]))
    assert project.status_code == 200, project.text
    seat = _seat(client, project.json(), "Ned")
    assert (seat["model"], seat["effort"], seat["effort_asked"]) == ("gpt-5.5", "xhigh", "max")


def test_an_effort_typed_for_one_seat_is_refused_with_the_ones_the_model_takes(client):
    """R6, where it still holds: an effort somebody named for this seat. Fitting
    it would be quietly overruling the person who typed it."""
    make_project(client, start=False)
    assert _role(client, "coder", effort="high").status_code == 200
    login = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                            value=CHATGPT))
    refused = client.post("/admin/projects", headers=ADMIN, json=_body(
        "effort-over", billing_secret_id=login, seat_models={"standard": "gpt-5.5"},
        teams=[{"role_slug": "coder", "name": "Build", "employees": ["Ned"],
                "effort_overrides": {"Ned": "max"}}]))
    assert refused.status_code == 422 and "Ned" in refused.text
    assert "gpt-5.5 takes the efforts low, medium, high, xhigh — not 'max'" in refused.text


def test_a_role_that_withholds_files_cannot_run_on_codex(client):
    """Codex hands the models it knows best a file editor whatever the config
    says, so a seat on it whose role leaves files off is refused rather than
    widened — at the project's door, now that the role names no provider."""
    make_project(client, start=False)
    login = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                            value=CHATGPT))
    team = [{"role_slug": "talker", "name": "Talk", "employees": ["Tam"]}]
    assert _role(client, "talker", tool_permissions={"files": False}).status_code == 200
    refused = client.post("/admin/projects", headers=ADMIN,
                          json=_body("no-files", billing_secret_id=login, teams=team))
    assert refused.status_code == 422 and "cannot run without files" in refused.text
    assert _role(client, "talker", tool_permissions={"files": True}).status_code == 200
    created = client.post("/admin/projects", headers=ADMIN,
                          json=_body("files", billing_secret_id=login, teams=team))
    assert created.status_code == 200, created.text


def test_an_openai_seat_needs_a_price_only_under_a_dollar_meter(client):
    """R3 is about stopping a run in dollars. A ChatGPT plan is metered in
    tokens, so an unpriced model runs on it; an API key is not."""
    make_project(client, start=False)
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
    login = CHATGPT
    credential = _id(_credential(client, "ChatGPT", provider="openai", mode="subscription",
                                 value=login))
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "on-codex", billing_secret_id=credential,
        teams=[{"role_slug": "ceo", "name": "Solo", "employees": ["Ada"]}])).json()
    with db_session() as session:
        assert _billing_env(session, session.get(Project, project["id"]), "openai") == {
            "CODEX_AUTH_JSON": login}
    assert get_backend("codex_cli").entrypoint == "codex_cli_entry.py"


def test_a_key_for_another_provider_runs_the_same_roles_on_that_provider(client):
    """The case a chairman met: a DeepSeek key picked for a project of the
    library's roles. It used to be refused — the roles named Claude models —
    and the fix was moving every seat by hand. A role names a tier now, so the
    key's provider answers it: the same roster is created, and each seat runs
    on DeepSeek's model for its tier, priced like any other."""
    make_project(client, start=False)
    deepseek = _id(_credential(client, "DeepSeek key", provider="deepseek"))
    body = {"slug": "on-deepseek", "name": "On DeepSeek", "chairman_email": "chair@example.com",
            "goal": "Prove the framework works end to end.", "ceo_role_slug": "ceo",
            "ceo_name": "CEO", "billing_secret_id": deepseek,
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Ada", "Lin"]}]}
    refused = client.post("/admin/projects", headers=ADMIN, json=body)
    assert refused.status_code == 422
    assert "Anthropic" not in refused.text  # nothing asks for Claude any more
    assert "deepseek-chat has no price on this install" in refused.text  # R3, the only bar
    assert _price(client, "deepseek-chat").status_code == 200
    created = client.post("/admin/projects", headers=ADMIN, json=body)
    assert created.status_code == 200, created.text
    assert client.post(f"/admin/projects/{created.json()['id']}/start",
                       headers=ADMIN).status_code == 200
    assert {_seat(client, created.json(), n)["model"] for n in ("CEO", "Ada", "Lin")} == {
        "deepseek-chat"}
