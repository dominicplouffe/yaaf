"""Invariant selftests: a role names a class of model, the project says whose
(docs/PROVIDERS.md §1, §2, §8).

The promise is that one role library runs on any provider: a project paid by
an OpenAI key hires the same roles a project paid by an Anthropic key does, and
each seat runs on its own provider's model for the class its role names. Each
way that could be got wrong is a refusal or a resolution, attacked here:

- a class resolves on the project's primary provider — the billing
  credential's — and moves with it; a pinned model never moves;
- R8 — a class with no model on its provider is refused at every door and at
  the lease, never filled in with another provider's model;
- a lineup names only its own provider's models;
- R6 — a class seat runs at its model's lowest rung at or above the effort it
  asked for; a pinned seat is still held to its model's list exactly;
- `stakes: high` lifts a class seat to the frontier class of the same provider;
- a provider at its install ceiling leaves its runs queued, and only its runs.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, ROLE_MIN, agent_by_name, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Run, RunQueue
from yaaf.runner.daemon import lease_and_prepare
from yaaf.server.dispatcher import dispatch_once

CREDENTIALS = "/admin/settings/llm-credentials"
PRICES = "/admin/settings/model-prices"
MODELS = "/admin/settings/models"


def _credential(client, name: str, provider: str, value: str, mode: str = "api_key",
                **extra) -> str:
    created = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": name, "mode": mode, "provider": provider, "value": value, **extra})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _price(client, model: str) -> None:
    priced = client.put(f"{PRICES}/{model}", headers=ADMIN, json={"input": 1.0, "output": 4.0})
    assert priced.status_code == 200, priced.text


def _role(client, slug: str, **fields):
    return client.post("/admin/roles", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), **ROLE_MIN, **fields})


def _library(client) -> None:
    """The chairman and a role library written once, for no provider: a CEO at
    the frontier, a writer balanced, a triager fast."""
    make_project(client, start=False)
    for slug, model, extra in (("chief", "@frontier", {"is_ceo_role": True}),
                               ("writer", "@balanced", {"category": "content"}),
                               ("triager", "@fast", {"category": "growth"})):
        created = _role(client, slug, model=model, **extra)
        assert created.status_code == 200, created.text


def _body(slug: str, billing: str | None = None, **fields) -> dict:
    return {"slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
            **({"billing_secret_id": billing} if billing else {}),
            "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]},
                      {"role_slug": "triager", "name": "Inbox", "employees": ["Tom"]}],
            **fields}


def _start_and_lease(client, project: dict) -> dict[str, object]:
    started = client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    assert started.status_code == 200, started.text
    with db_session() as session:
        for team in project["teams"]:
            for agent in team["agents"]:
                session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                                     agent_id=agent["id"], kind="agent_run",
                                     trigger={"type": "test"}))
    with db_session() as session:
        dispatch_once(session)
    return {j.agent_id: j for j in lease_and_prepare(limit=20)
            if j.project_id == project["id"]}


# ── one library, any provider ───────────────────────────────────────────────────


def test_the_same_roles_run_on_openai_when_openai_pays(client):
    """The case this whole design is for: an install whose only credential is
    an OpenAI key creates a project from roles written once, and every seat
    runs on OpenAI's model for its class — on yaaf's loop over OpenAI's own
    API, with no seat moved by hand."""
    _library(client)
    for model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"):
        _price(client, model)
    key = _credential(client, "OpenAI", "openai", "sk-proj-openai-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("on-openai", key))
    assert created.status_code == 200, created.text
    project = created.json()
    jobs = _start_and_lease(client, project)
    seats = {name: jobs[agent_by_name(project, name)] for name in ("CEO", "Ada", "Tom")}
    assert {n: (j.model, j.provider, j.harness) for n, j in seats.items()} == {
        "CEO": ("gpt-6-astra", "openai", "yaaf_loop"),
        "Ada": ("gpt-6.1-sol", "openai", "yaaf_loop"),
        "Tom": ("gpt-6-luna", "openai", "yaaf_loop")}
    for job in seats.values():
        assert job.billing_env == {"OPENAI_API_KEY": "sk-proj-openai-test"}
        assert job.provider_spec["protocol"] == "openai_responses"
    with db_session() as session:
        runs = {r.agent_id: r for r in session.query(Run).filter_by(project_id=project["id"])}
        # History says what ran: the model, never the class.
        assert all(not (r.model or "").startswith("@") for r in runs.values())


def test_the_same_roles_run_on_exactly_what_they_pinned_when_anthropic_pays(client):
    """Anthropic's lineup is the three models the shipped roles named before
    they named classes: a Claude project runs exactly as it did."""
    _library(client)
    key = _credential(client, "Claude", "anthropic", "sk-ant-api03-claude-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("on-claude", key))
    assert created.status_code == 200, created.text
    jobs = _start_and_lease(client, created.json())
    models = {j.model for j in jobs.values()}
    assert models <= {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"}
    assert {j.harness for j in jobs.values()} == {"claude_agent_sdk"}


def test_a_class_moves_with_the_billing_credential_and_a_pinned_model_does_not(client):
    """Swapping the billing credential is "switch this project to OpenAI":
    every class seat follows it. A seat the chairman pinned to a Claude model
    stays on it — and R5 then asks for Anthropic to be paid too."""
    _library(client)
    for model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"):
        _price(client, model)
    claude = _credential(client, "Claude", "anthropic", "sk-ant-api03-claude-test")
    openai = _credential(client, "OpenAI", "openai", "sk-proj-openai-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "switching", claude, teams=[{"role_slug": "writer", "name": "Words",
                                     "employees": ["Ada", "Pin"],
                                     "model_overrides": {"Pin": "claude-sonnet-5-5"}}]))
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    refused = client.patch(f"/admin/projects/{project_id}", headers=ADMIN,
                           json={"billing_secret_id": openai, "billing_mode": "api_key"})
    assert refused.status_code == 422
    assert "Pin" in refused.text and "holds no Anthropic credential" in refused.text
    moved = client.post("/admin/projects", headers=ADMIN, json=_body("switched", openai))
    assert moved.status_code == 200, moved.text


def test_a_named_providers_class_resolves_there_whatever_pays_first(client):
    """`openai/@fast` is OpenAI's fast model on a project Anthropic pays: a
    second provider's class, held to R5 like a pinned model of it."""
    _library(client)
    _price(client, "gpt-6-luna")
    claude = _credential(client, "Claude", "anthropic", "sk-ant-api03-claude-test")
    openai = _credential(client, "OpenAI", "openai", "sk-proj-openai-test")
    teams = [{"role_slug": "writer", "name": "Words", "employees": ["Ada"],
              "model_overrides": {"Ada": "openai/@fast"}}]
    unpaid = client.post("/admin/projects", headers=ADMIN, json=_body(
        "mixed-unpaid", claude, teams=teams))
    assert unpaid.status_code == 422 and "holds no OpenAI credential" in unpaid.text
    paid = client.post("/admin/projects", headers=ADMIN, json=_body(
        "mixed", claude, teams=teams, credentials={"openai": openai}))
    assert paid.status_code == 200, paid.text
    jobs = _start_and_lease(client, paid.json())
    ada = jobs[agent_by_name(paid.json(), "Ada")]
    assert (ada.model, ada.provider) == ("gpt-6-luna", "openai")


# ── R8: a class with no model on its provider ───────────────────────────────────


def test_a_class_on_a_provider_with_no_lineup_is_refused_and_a_lineup_fixes_it(client):
    """Groq ships no lineup — its models move monthly. A project it pays
    cannot hire a class seat until the install names one, and the refusal
    says where; it is never handed another provider's model instead."""
    _library(client)
    groq = _credential(client, "Groq", "groq", "gsk-groq-test-value")
    refused = client.post("/admin/projects", headers=ADMIN, json=_body("on-groq", groq))
    assert refused.status_code == 422
    assert "Groq has no Frontier model set" in refused.text
    assert "Settings → Providers" in refused.text
    for cls, model in (("frontier", "groq/kimi-k2"), ("balanced", "groq/llama-4"),
                       ("fast", "groq/llama-4-scout")):
        named = client.put(f"{MODELS}/lineup", headers=ADMIN,
                           json={"provider": "groq", "class": cls, "model": model})
        assert named.status_code == 200, named.text
        _price(client, model)
    created = client.post("/admin/projects", headers=ADMIN, json=_body("on-groq", groq))
    assert created.status_code == 200, created.text
    jobs = _start_and_lease(client, created.json())
    assert {(j.provider, j.harness) for j in jobs.values()} == {("groq", "yaaf_loop")}
    assert {j.provider_spec["base_url"] for j in jobs.values()} == {
        "https://api.groq.com/openai/v1"}


def test_unsetting_a_lineup_says_which_live_seats_it_strands(client):
    _library(client)
    _price(client, "deepseek-chat")
    _price(client, "deepseek-reasoner")
    deepseek = _credential(client, "DeepSeek", "deepseek", "sk-deepseek-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("on-deepseek", deepseek))
    assert created.status_code == 200, created.text
    assert client.post(f"/admin/projects/{created.json()['id']}/start",
                       headers=ADMIN).status_code == 200
    renamed = client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "deepseek", "class": "fast", "model": "deepseek/deepseek-v9-lite"})
    assert renamed.status_code == 200
    stranded = renamed.json()["unrunnable"]
    assert stranded and "deepseek/deepseek-v9-lite has no price" in stranded[0]


@pytest.mark.parametrize(("body", "says"), [
    ({"provider": "openai", "class": "balanced", "model": "claude-sonnet-5-5"},
     "is not OpenAI's"),
    ({"provider": "openai", "class": "huge", "model": "gpt-6-sol"}, "unknown class"),
    ({"provider": "openai", "class": "fast", "model": "@balanced"}, "names a model, not a class"),
    ({"provider": "nobody", "class": "fast", "model": "x"}, "unknown provider"),
])
def test_a_lineup_names_only_its_own_providers_models(client, body, says):
    """A lineup that named another vendor's model would move a seat across
    vendors behind a label that says it stayed."""
    refused = client.put(f"{MODELS}/lineup", headers=ADMIN, json=body)
    assert refused.status_code == 422 and says in refused.text


@pytest.mark.parametrize(("model", "says"), [
    ("@huge", "names no class"),
    ("nobody/@balanced", "not a provider here"),
])
def test_a_model_reference_that_names_nothing_is_refused_at_the_role(client, model, says):
    make_project(client, start=False)
    refused = _role(client, "odd", model=model)
    assert refused.status_code == 422 and says in refused.text


def test_a_class_role_is_not_refused_for_what_only_a_seat_can_know(client):
    """A class role is bound to no model until a project pays it, so its door
    does not ask for a price, a payer or a rung — only for what holds wherever
    it lands: here, that the harness it names can hold the boardroom."""
    make_project(client, start=False)
    assert _role(client, "anywhere", model="@balanced", effort="max",
                 tool_permissions={"web": True}).status_code == 200
    refused = _role(client, "talker", model="@balanced", is_ceo_role=True, harness="nowhere")
    assert refused.status_code == 422 and "unknown harness" in refused.text


# ── R6: a class seat runs at its model's rung ───────────────────────────────────


def test_a_class_seat_runs_at_its_models_rung_and_keeps_the_effort_it_asked_for(client):
    """Gemini 3 takes low and high. A class role written at xhigh runs at
    Gemini's top rung, and one at medium at the rung above it — never below
    what it was hired at while a higher rung exists. The run keeps the effort
    asked for, so reports read in yaaf's words."""
    make_project(client, start=False)
    assert _role(client, "chief", model="@frontier", effort="xhigh",
                 is_ceo_role=True).status_code == 200
    assert _role(client, "writer", model="@balanced", effort="medium").status_code == 200
    assert _role(client, "triager", model="@fast", effort="low").status_code == 200
    for model in ("gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-2.5-flash"):
        _price(client, model)
    google = _credential(client, "Gemini", "google", "AIza-gemini-test-key")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("on-gemini", google))
    assert created.status_code == 200, created.text
    project = created.json()
    jobs = _start_and_lease(client, project)
    by_name = {name: jobs[agent_by_name(project, name)] for name in ("CEO", "Ada", "Tom")}
    assert (by_name["CEO"].model, by_name["CEO"].effort) == ("gemini-3.1-pro-preview", "high")
    assert by_name["CEO"].model_flags["effort"] is True
    assert (by_name["Ada"].model, by_name["Ada"].effort) == ("gemini-3.8-flash", "high")
    # Gemini 2.5 keeps its own thinking budget: one rung, so no dial is sent.
    assert by_name["Tom"].model_flags["effort"] is False
    assert by_name["Ada"].provider_spec["protocol"] == "gemini"
    with db_session() as session:
        ada = session.query(Run).filter_by(agent_id=agent_by_name(project, "Ada")).one()
        assert ada.effort == "medium"


def test_a_pinned_gemini_seat_is_still_held_to_its_models_list(client):
    """Pinning is the chairman naming this model: its list is held exactly, as
    it always was (the original R6)."""
    make_project(client, start=False)
    _price(client, "gemini-3.1-pro-preview")
    assert _role(client, "pinned", model="gemini-3.1-pro-preview",
                 effort="low").status_code == 200
    refused = _role(client, "pinned", model="gemini-3.1-pro-preview", effort="medium")
    assert refused.status_code == 422 and "takes the efforts low, high" in refused.text


# ── stakes lift a class within its provider ─────────────────────────────────────


def test_high_stakes_lifts_a_class_seat_to_the_frontier_of_its_own_provider(client):
    _library(client)
    for model in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"):
        _price(client, model)
    openai = _credential(client, "OpenAI", "openai", "sk-proj-openai-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "matters", openai, constitution={"stakes": "high"}))
    assert created.status_code == 200, created.text
    project = created.json()
    with db_session() as session:
        ada = session.get(Agent, agent_by_name(project, "Ada"))
        tom = session.get(Agent, agent_by_name(project, "Tom"))
        # The class, not a model: the seat still follows the project's provider.
        assert ada.model_override == "@frontier"
        assert tom.model_override is None, "growth is not lifted"
    jobs = _start_and_lease(client, project)
    assert jobs[agent_by_name(project, "Ada")].model == "gpt-6-astra"


# ── the install's default provider, and its ceilings ────────────────────────────


def test_a_project_that_pays_nobody_yet_resolves_on_the_install_default(client):
    _library(client)
    assert client.put(f"{MODELS}/default-provider", headers=ADMIN,
                      json={"provider": "deepseek"}).status_code == 200
    # The classes resolve on DeepSeek, so its models need a price to be hired
    # at all (R3) — a draft is held to the rules its seats will run under.
    unpriced = client.post("/admin/projects", headers=ADMIN, json=_body("draft"))
    assert unpriced.status_code == 422 and "deepseek-reasoner has no price" in unpriced.text
    _price(client, "deepseek-chat")
    _price(client, "deepseek-reasoner")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("draft"))
    assert created.status_code == 200, created.text
    overview = client.get(f"{MODELS}", headers=ADMIN).json()
    assert overview["default_provider"] == "deepseek"
    from yaaf.core import model_policy
    from yaaf.core.models import Project

    with db_session() as session:
        project = session.get(Project, created.json()["id"])
        assert model_policy.resolve(session, "@balanced", project) == "deepseek-chat"
    refused = client.put(f"{MODELS}/default-provider", headers=ADMIN, json={"provider": "x"})
    assert refused.status_code == 422


def test_a_provider_at_its_ceiling_leaves_its_runs_queued_and_only_its_runs(client):
    """The install's rate limit with a provider is shared by every project on
    the box. At its ceiling, that provider's next run waits for a later tick —
    and a run on another provider behind it in the queue is not held up."""
    _library(client)
    _price(client, "deepseek-chat")
    _price(client, "deepseek-reasoner")
    deepseek = _credential(client, "DeepSeek", "deepseek", "sk-deepseek-test")
    claude = _credential(client, "Claude", "anthropic", "sk-ant-api03-claude-test")
    on_deepseek = client.post("/admin/projects", headers=ADMIN,
                              json=_body("ds", deepseek)).json()
    on_claude = client.post("/admin/projects", headers=ADMIN, json=_body("cl", claude)).json()
    assert client.put(f"{MODELS}/ceiling", headers=ADMIN,
                      json={"provider": "deepseek", "limit": 1}).status_code == 200
    for project in (on_deepseek, on_claude):
        assert client.post(f"/admin/projects/{project['id']}/start",
                           headers=ADMIN).status_code == 200
    with db_session() as session:
        for name in ("Ada", "Tom"):
            session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=on_deepseek["id"],
                                 agent_id=agent_by_name(on_deepseek, name), kind="agent_run",
                                 trigger={"type": "test"}))
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=on_claude["id"],
                             agent_id=agent_by_name(on_claude, "Ada"), kind="agent_run",
                             trigger={"type": "test"}))
    leased = lease_and_prepare(limit=10)
    deepseek_jobs = [j for j in leased if j.provider == "deepseek"]
    assert len(deepseek_jobs) == 1, "one at a time, as the ceiling says"
    assert any(j.provider == "anthropic" for j in leased), "the other provider ran anyway"
    with db_session() as session:
        assert session.query(RunQueue).filter_by(project_id=on_deepseek["id"]).count() >= 1
    lifted = client.put(f"{MODELS}/ceiling", headers=ADMIN,
                        json={"provider": "deepseek", "limit": None})
    assert lifted.status_code == 200 and lifted.json()["ceilings"] == {}


# ── the drafter and the bundle carry classes as they are ────────────────────────


def test_the_founders_draft_check_resolves_classes_on_the_drafts_provider(client):
    """The Founder's own check is the create endpoint's R5 and R8, read before
    the chairman is handed a draft that cannot be built."""
    from yaaf.founder.tools import seat_payment_problems

    _library(client)
    draft = {"ceo_role_slug": "chief", "teams": [
        {"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]}
    with db_session() as session:
        assert seat_payment_problems(dict(draft), session, "deepseek") == []
        assert seat_payment_problems(dict(draft), session, "openai") == []
        stranded = seat_payment_problems(dict(draft), session, "groq")
        assert stranded and "Groq has no Frontier model set" in stranded[0]
        pinned = {**draft, "ceo_model_override": "claude-opus-5-5"}
        unpaid = seat_payment_problems(pinned, session, "deepseek")
        assert unpaid and "holds no Anthropic credential" in unpaid[0]


def test_an_export_carries_the_class_and_a_clone_resolves_it_again(client):
    _library(client)
    key = _credential(client, "Claude", "anthropic", "sk-ant-api03-claude-test")
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "exported", key, teams=[{"role_slug": "writer", "name": "Words", "employees": ["Ada"],
                                 "model_overrides": {"Ada": "@fast"}}]))
    assert created.status_code == 200, created.text
    exported = client.get(f"/admin/projects/{created.json()['id']}/export", headers=ADMIN)
    assert exported.status_code == 200, exported.text
    assert '"@fast"' in exported.text
    clone = client.post(f"/admin/projects/{created.json()['id']}/clone", headers=ADMIN,
                        json={"slug": "cloned"})
    assert clone.status_code == 200, clone.text
    with db_session() as session:
        ada = session.query(Agent).filter_by(project_id=clone.json()["id"], name="Ada").one()
        assert ada.model_override == "@fast"


def test_a_ceo_whose_class_lost_its_model_holds_no_boardroom_session(client):
    """R8's backstop in the chat lane: a lineup unset after the doors last
    asked leaves the CEO's class with nothing behind it, and the supervisor
    starts no session on a model that does not exist."""
    from yaaf.core.models import Project
    from yaaf.runner.chat import converses

    _library(client)
    for cls, model in (("frontier", "groq/kimi-k2"), ("balanced", "groq/llama-4"),
                       ("fast", "groq/llama-4-scout")):
        assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
            "provider": "groq", "class": cls, "model": model}).status_code == 200
        _price(client, model)
    groq = _credential(client, "Groq", "groq", "gsk-groq-test-value")
    created = client.post("/admin/projects", headers=ADMIN, json=_body("groq-chat", groq))
    assert created.status_code == 200, created.text
    project_id, ceo_id = created.json()["id"], created.json()["ceo_agent_id"]
    with db_session() as session:
        assert converses(session, session.get(Project, project_id), session.get(Agent, ceo_id))
    assert client.put(f"{MODELS}/lineup", headers=ADMIN, json={
        "provider": "groq", "class": "frontier", "model": None}).status_code == 200
    with db_session() as session:
        assert not converses(session, session.get(Project, project_id),
                             session.get(Agent, ceo_id))
