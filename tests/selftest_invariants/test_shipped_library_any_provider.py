"""Acceptance: the shipped role library and every shipped crew run on any
provider (docs/PROVIDERS.md).

This is the sentence the whole design answers to — *a chairman who has never
held an Anthropic key creates a project from what ships, on the key they do
have* — checked against the real files in `roles/` and `crews/`, not test
fixtures. If a role is ever pinned back to one vendor's model, or grants
something only one vendor can honour, a crew stops being buildable on the
others and this fails naming it.

Prices come the way an install gets them (the dated snapshot, imported); a
provider whose API cannot search gets the install's search backend, because
35 of the 38 shipped roles grant `web`.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from tests.conftest import ADMIN
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import RunQueue
from yaaf.runner.daemon import lease_and_prepare

REPO = pathlib.Path(__file__).resolve().parents[2]
CREWS = sorted((REPO / "crews").glob("*.json"))

#: provider → (a key of its shape, whether its own API searches, the models
#: its lineup resolves the three classes to)
PROVIDERS = {
    "anthropic": ("sk-ant-api03-acceptance", True,
                  {"claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"}),
    "openai": ("sk-proj-acceptance", True, {"gpt-6-astra", "gpt-6.1-sol", "gpt-6-luna"}),
    "google": ("AIza-acceptance-key", True,
               {"gemini-3.1-pro-preview", "gemini-3.8-flash", "gemini-2.5-flash"}),
    "deepseek": ("sk-deepseek-acceptance", False, {"deepseek-reasoner", "deepseek-chat"}),
}


@pytest.fixture()
def library(client):
    """The install a new chairman has: the shipped roles, prices imported from
    the snapshot, and — for the providers that do not search — a backend."""
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair"})
    for path in sorted((REPO / "roles").glob("*.json")):
        seeded = client.post("/admin/roles", headers=ADMIN, json=json.loads(path.read_text()))
        assert seeded.status_code == 200, f"{path.name}: {seeded.text}"
    imported = client.post("/admin/settings/model-prices/import", headers=ADMIN,
                           json={"source": "snapshot", "apply": True})
    assert imported.status_code == 200, imported.text
    return client


def _crew_project(client, crew: dict, slug: str, billing: str) -> dict:
    # A team with nobody on it is the solo shape: the CEO is the team, as the
    # New Project form reads such a crew.
    solo = len(crew["teams"]) == 1 and not crew["teams"][0].get("employees")
    teams = [{**team, "employees": team.get("employees") or []} for team in crew["teams"]]
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": slug, "name": crew["name"][:60], "chairman_email": "chair@example.com",
        "goal": crew["outcome"] + " Measured by what ships, not by activity.",
        "ceo_role_slug": crew["ceo_role_slug"], "ceo_name": "CEO", "solo": solo,
        "billing_secret_id": billing, "teams": teams})
    assert created.status_code == 200, f"{crew['slug']}: {created.text}"
    return created.json()


@pytest.mark.parametrize("provider", list(PROVIDERS))
def test_every_shipped_crew_is_buildable_on_this_provider(library, provider):
    """Slow by design — every crew, on every provider — and still only
    project creation: the seats resolve, are paid, priced and can do what
    their roles grant, or the door says which cannot."""
    client = library
    key, searches, lineup = PROVIDERS[provider]
    if not searches:
        assert client.put("/admin/settings/web-search", headers=ADMIN, json={
            "backend": "brave", "key": "brave-acceptance-key"}).status_code == 200
    billing = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": f"{provider} key", "mode": "api_key", "provider": provider,
        "value": key}).json()["id"]
    for path in CREWS:
        crew = json.loads(path.read_text())
        _crew_project(client, crew, f"{provider}-{crew['slug']}"[:60], billing)


@pytest.mark.parametrize("provider", ["openai", "google", "deepseek"])
def test_a_crew_on_another_provider_starts_and_every_seat_runs_on_it(library, provider):
    client = library
    key, searches, lineup = PROVIDERS[provider]
    if not searches:
        assert client.put("/admin/settings/web-search", headers=ADMIN, json={
            "backend": "tavily", "key": "tvly-acceptance-key"}).status_code == 200
    billing = client.post("/admin/settings/llm-credentials", headers=ADMIN, json={
        "name": f"{provider} key", "mode": "api_key", "provider": provider,
        "value": key}).json()["id"]
    crew = json.loads((REPO / "crews" / "build-it-then-break-it.json").read_text())
    project = _crew_project(client, crew, f"{provider}-starts", billing)
    started = client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    assert started.status_code == 200, started.text
    with db_session() as session:
        for team in project["teams"]:
            for agent in team["agents"]:
                session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=project["id"],
                                     agent_id=agent["id"], kind="agent_run",
                                     trigger={"type": "acceptance"}))
    jobs = [j for j in lease_and_prepare(limit=50) if j.project_id == project["id"]]
    assert jobs, "nothing was leased"
    for job in jobs:
        assert job.provider == provider and job.harness == "yaaf_loop", job.agent_id
        assert job.model in lineup, job.model
        assert job.price is not None, f"{job.model} was leased with no price"
        # The window its history is compacted against travels with it.
        assert job.model_flags["context"] > 100_000, job.model
        assert set(job.billing_env) - {"YAAF_SEARCH", "YAAF_SEARCH_KEY"} == {
            {"openai": "OPENAI_API_KEY", "google": "GEMINI_API_KEY",
             "deepseek": "DEEPSEEK_API_KEY"}[provider]}
