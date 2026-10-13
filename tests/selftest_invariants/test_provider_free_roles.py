"""Invariant selftests: a role says how hard, the project says on what.

A role used to name its model, and the model named its vendor, so every role
in the library was an Anthropic role: a project billed with any other key was
refused seat by seat until the chairman moved each one by hand. Now a role
names a tier (light, standard, strong) and an effort; the project's provider —
the one its billing credential pays — answers the tier with its lineup, or the
project's own (`seat_models`). These attack that from the outside, on the
library's own roles:

- the same roster runs on DeepSeek's key and on Anthropic's, each seat on its
  provider's model for its tier — and on Anthropic, exactly what it ran before;
- a provider that ships no lineup is refused until the project names a model,
  and one model is enough;
- moving the money moves the seats; a seat named onto a provider nobody pays
  is still refused (R5), naming it;
- `stakes: high` lifts to the strong rung of whatever the project pays.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.conftest import ADMIN, agent_by_name
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Project, RunQueue
from yaaf.runner.daemon import lease_and_prepare

REPO = pathlib.Path(__file__).resolve().parents[2]
CREDENTIALS = "/admin/settings/llm-credentials"


def _library(client, *slugs: str) -> None:
    """The chairman, and the named roles exactly as the library ships them."""
    client.post("/admin/users", headers=ADMIN,
                json={"email": "chair@example.com", "display_name": "Chair"})
    for slug in slugs:
        role = json.loads((REPO / "roles" / f"{slug}.json").read_text())
        assert client.post("/admin/roles", headers=ADMIN, json=role).status_code == 200


def _credential(client, provider: str, **fields) -> str:
    created = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": f"{provider} key", "provider": provider, "mode": "api_key",
        "value": "sk-ant-api03-test-value" if provider == "anthropic"
        else f"test-{provider}-credential", **fields})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _price(client, *models: str) -> None:
    for model in models:
        assert client.put(f"/admin/settings/model-prices/{model}", headers=ADMIN,
                          json={"input": 0.5, "output": 1.5}).status_code == 200


def _body(slug: str, credential: str, **fields) -> dict:
    """The CEO, a researcher and support: one role on each tier."""
    return {"slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "billing_secret_id": credential,
            "teams": [{"role_slug": "researcher", "name": "Research", "employees": ["Rae"]},
                      {"role_slug": "support", "name": "Support", "employees": ["Sam"]}],
            **fields}


def _runs_on(client, project: dict) -> dict[str, dict]:
    return {name: client.get(f"/observe/agents/{agent_by_name(project, name)}",
                             headers=ADMIN).json()["runs_on"]
            for name in ("CEO", "Rae", "Sam")}


def _models(client, project: dict) -> dict[str, str]:
    return {name: seat["model"] for name, seat in _runs_on(client, project).items()}


@pytest.fixture()
def library(client):
    _library(client, "ceo", "researcher", "support", "writer")


def test_the_librarys_roles_run_on_a_deepseek_key_with_nothing_moved_by_hand(client, library):
    """The case that started this. A DeepSeek key, the library's roles, no
    override anywhere: created, started, and every seat leased on DeepSeek —
    its reasoner for the strong tier, its chat model for the rest."""
    _price(client, "deepseek-chat", "deepseek-reasoner")
    created = client.post("/admin/projects", headers=ADMIN,
                          json=_body("on-deepseek", _credential(client, "deepseek")))
    assert created.status_code == 200, created.text
    project = created.json()
    assert _models(client, project) == {
        "CEO": "deepseek-reasoner", "Rae": "deepseek-chat", "Sam": "deepseek-chat"}
    seats = _runs_on(client, project)
    # The loop has no effort dial, and DeepSeek no search: both shown, not hidden.
    assert all(seat["effort"] is None and seat["effort_asked"] == "high"
               for seat in seats.values())
    assert all(seat["web"] == "fetch" for seat in seats.values())

    assert client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN).status_code == 200
    with db_session() as session:
        session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                             agent_id=agent_by_name(project, "Sam"), kind="agent_run",
                             trigger={"type": "test"}))
    jobs = {j.agent_id: j for j in lease_and_prepare(limit=20) if j.project_id == project["id"]}
    sam = jobs[agent_by_name(project, "Sam")]
    assert (sam.model, sam.provider, sam.harness) == (
        "deepseek-chat", "deepseek", "openai_compatible")
    assert sam.billing_env == {"DEEPSEEK_API_KEY": "test-deepseek-credential"}


def test_on_an_anthropic_key_every_seat_runs_on_what_it_always_did(client, library):
    """The other half of the promise: the library's tiers were read off the
    models it named, and Anthropic's lineup is those models — so a Claude
    project is byte-for-byte what it was."""
    created = client.post("/admin/projects", headers=ADMIN,
                          json=_body("on-claude", _credential(client, "anthropic")))
    assert created.status_code == 200, created.text
    assert _models(client, created.json()) == {
        "CEO": "claude-opus-5-5", "Rae": "claude-sonnet-5-5", "Sam": "claude-haiku-4-5"}
    assert all(seat["web"] == "search" for seat in _runs_on(client, created.json()).values())


def test_a_provider_that_ships_no_lineup_needs_one_model_named(client, library):
    """Mistral's lineup moves monthly and yaaf ships none, so a project on it
    names its model — refused until it does, at the door, with where to do it.
    One model for `standard` serves every tier."""
    mistral = _credential(client, "mistral")
    refused = client.post("/admin/projects", headers=ADMIN, json=_body("unnamed", mistral))
    assert refused.status_code == 422
    assert "CEO: its role asks for the strong tier" in refused.text
    assert "Mistral ships no model lineup" in refused.text
    assert "Settings → Models" in refused.text

    _price(client, "mistral/mistral-large-latest")
    named = client.post("/admin/projects", headers=ADMIN, json=_body(
        "named", mistral, seat_models={"standard": "mistral/mistral-large-latest"}))
    assert named.status_code == 200, named.text
    assert set(_models(client, named.json()).values()) == {"mistral/mistral-large-latest"}


def test_a_project_can_name_its_own_model_for_one_tier(client, library):
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "own-light", _credential(client, "anthropic"),
        seat_models={"light": "claude-sonnet-5"}))
    assert created.status_code == 200, created.text
    seats = _runs_on(client, created.json())
    assert seats["Sam"]["model"] == "claude-sonnet-5" and seats["Sam"]["from"] == "project"
    assert seats["CEO"]["model"] == "claude-opus-5-5" and seats["CEO"]["from"] == "provider"


@pytest.mark.parametrize(("seat_models", "says"), [
    ({"heavy": "claude-opus-5-5"}, "the tiers are light, standard, strong"),
    ({"standard": "deepseek-chat"}, "the standard tier: runs on deepseek-chat"),
])
def test_a_lineup_the_project_cannot_run_is_refused(client, library, seat_models, says):
    refused = client.post("/admin/projects", headers=ADMIN, json=_body(
        "bad-lineup", _credential(client, "anthropic"), seat_models=seat_models))
    assert refused.status_code == 422 and says in refused.text


def test_moving_the_money_moves_the_seats(client, library):
    """The point of tiers: a project moved from a Claude key to an OpenAI one
    runs every seat on OpenAI from its next run — its own lineup entries for
    the old provider go back to the new one's, and the response says which.
    A seat somebody named onto Claude by hand is the one thing that cannot
    follow, and it is refused by name (R5) rather than left to fail at 3am."""
    claude = _credential(client, "anthropic")
    login = _credential(client, "openai", mode="subscription",
                        value='{"tokens": {"access_token": "a", "refresh_token": "r"}}')
    project = client.post("/admin/projects", headers=ADMIN, json=_body(
        "moves", claude, seat_models={"light": "claude-sonnet-5"})).json()
    moved = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN,
                         json={"billing_secret_id": login})
    assert moved.status_code == 200, moved.text
    assert moved.json()["changed"]["seat_models_reset"] == ["light"]
    assert _models(client, project) == {
        "CEO": "gpt-6-astra", "Rae": "gpt-6-sol", "Sam": "gpt-5.6-luna"}

    pinned = client.post("/admin/projects", headers=ADMIN, json=_body(
        "pinned", claude, ceo_model_override="claude-opus-5-5")).json()
    refused = client.patch(f"/admin/projects/{pinned['id']}", headers=ADMIN,
                           json={"billing_secret_id": login})
    assert refused.status_code == 422
    assert "CEO: runs on claude-opus-5-5, an Anthropic model" in refused.text
    assert "Drop this seat's model override" in refused.text
    with db_session() as session:
        assert session.get(Project, pinned["id"]).billing_secret_id == claude


def test_a_lineup_named_with_the_move_is_kept(client, library):
    """Moving to a provider with no lineup is one request, not two: the models
    named beside the new credential are what the seats are checked against."""
    project = client.post("/admin/projects", headers=ADMIN,
                          json=_body("to-mistral", _credential(client, "anthropic"))).json()
    _price(client, "mistral/mistral-small-latest")
    moved = client.patch(f"/admin/projects/{project['id']}", headers=ADMIN, json={
        "billing_secret_id": _credential(client, "mistral"),
        "seat_models": {"standard": "mistral/mistral-small-latest"}})
    assert moved.status_code == 200, moved.text
    assert set(_models(client, project).values()) == {"mistral/mistral-small-latest"}


def test_high_stakes_lifts_to_the_strong_rung_of_whatever_the_project_pays(client, library):
    _price(client, "deepseek-chat", "deepseek-reasoner")
    created = client.post("/admin/projects", headers=ADMIN, json=_body(
        "matters", _credential(client, "deepseek"), constitution={"stakes": "high"},
        teams=[{"role_slug": "writer", "name": "Words", "employees": ["Rae"]},
               {"role_slug": "support", "name": "Support", "employees": ["Sam"]}]))
    assert created.status_code == 200, created.text
    seats = _runs_on(client, created.json())
    assert (seats["Rae"]["tier"], seats["Rae"]["model"]) == ("strong", "deepseek-reasoner")
    assert seats["Sam"]["model"] == "deepseek-chat", "support is not the deliverable"


def test_the_providers_say_what_each_tier_runs_on(client, library):
    providers = {p["name"]: p for p in client.get("/admin/providers", headers=ADMIN).json()}
    assert providers["anthropic"]["lineup"] == {
        "light": "claude-haiku-4-5", "standard": "claude-sonnet-5-5", "strong": "claude-opus-5-5"}
    assert providers["mistral"]["lineup"] is None
    assert providers["deepseek"]["searches"] is False and providers["openai"]["searches"]


def test_a_role_names_no_model_and_reads_an_old_one_as_its_tier(client, library):
    role = client.get("/admin/roles/support", headers=ADMIN).json()
    assert (role["tier"], role["effort"]) == ("light", "high")
    assert "model" not in role and "harness" not in role
    legacy = client.post("/admin/roles", headers=ADMIN, json={
        "slug": "old", "name": "Old", "description": "d", "system_prompt": "p",
        "model": "claude-haiku-4-5"})
    assert legacy.json()["tier"] == "light"
    refused = client.post("/admin/roles", headers=ADMIN, json={
        "slug": "odd", "name": "Odd", "description": "d", "system_prompt": "p", "tier": "huge"})
    assert refused.status_code == 422
