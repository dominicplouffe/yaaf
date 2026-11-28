"""Invariant selftests: a Code Factory's coding key.

A factory holds two model credentials. The billing one pays for the talking —
the lead, the boardroom, every seat that never touches the repo. The coding
one pays the seats that hold the repo, on its provider's own coding CLI. They
are kept apart so that the model worth writing code with is not also the price
of every message. Attacked here:

  * a factory that anything pays for does not start without a coding key, and
    one that started keyless cannot be handed a billing credential around it
  * the key is a credential for a provider with a coding CLI, and only a Code
    Factory holds one
  * the repo seats lease on the coding key — its provider, its CLI, its best
    model — and are handed that key and not the billing one; the lead and a
    seat without the repo stay on the billing credential, same provider or not
  * it cannot be taken away from a running factory, nor deleted from under it
  * it travels with a setup file by name, and nothing on the agent's side of
    the API can read or move it
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, ROLE_MIN, live_token
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Project, Run, RunQueue, utcnow
from yaaf.runner import daemon

CREDENTIALS = "/admin/settings/llm-credentials"
OAT = "sk-ant-oat01-coding-key-selftest"
API = "sk-ant-api03-coding-key-selftest"


def _credential(client, name: str, provider: str, value: str, mode: str = "api_key") -> str:
    created = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": name, "mode": mode, "provider": provider, "value": value})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _roles(client) -> None:
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair"})
    roles = (("tech-lead", {"is_ceo_role": True}),
             ("builder", {"secret_scopes": ["github"]}),
             ("scribe", {}))
    for slug, extra in roles:
        # On the default class, so each seat follows whatever pays it.
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": "@balanced",
            **extra}).status_code == 200
    client.post("/admin/settings/model-prices/import", headers=ADMIN,
                json={"source": "snapshot", "apply": True})


def _factory(client, slug="mill", **extra):
    return client.post("/admin/projects", headers=ADMIN, json={
        "slug": slug, "name": slug.title(), "goal": "Build a booking app for clinics.",
        "chairman_email": "chair@example.com", "mode": "code_factory", "ceo_name": "Lead",
        "teams": [{"role_slug": "builder", "name": "Build", "employees": ["Ada"]},
                  {"role_slug": "scribe", "name": "Notes", "employees": ["Sam"]}], **extra})


@pytest.fixture()
def world(client):
    """A factory billed on an Anthropic API key, with a builder who holds the
    repo and a scribe who does not, and a Claude subscription to code with."""
    _roles(client)
    billing = _credential(client, "Talk", "anthropic", API)
    coding = _credential(client, "Claude Max", "anthropic", OAT, mode="subscription")
    created = _factory(client, billing_secret_id=billing)
    assert created.status_code == 200, created.text
    body = created.json()
    agents = {a["name"]: a["id"] for team in body["teams"] for a in team["agents"]}
    agents["Lead"] = body["ceo_agent_id"]
    return {"client": client, "project": body, "billing": billing, "coding": coding,
            "agents": agents}


def _name(world, credential_id):
    return world["client"].put(
        f"/admin/projects/{world['project']['id']}/coding-credential",
        headers=ADMIN, json={"credential_id": credential_id})


def _start(world):
    return world["client"].post(f"/admin/projects/{world['project']['id']}/start", headers=ADMIN)


def _leased(world) -> dict:
    with db_session() as session:
        for agent_id in world["agents"].values():
            session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=world["project"]["id"],
                                 agent_id=agent_id, kind="agent_run",
                                 trigger={"type": "coding-key-test"}))
    jobs = daemon.lease_and_prepare(limit=20)
    names = {v: k for k, v in world["agents"].items()}
    out = {names[j.agent_id]: j for j in jobs if j.project_id == world["project"]["id"]}
    with db_session() as session:
        for run in session.query(Run).filter(Run.status == "running").all():
            run.status, run.finished_at = "done", utcnow()
    return out


# ── the start rule ──────────────────────────────────────────────────────────────


def test_a_factory_does_not_start_without_a_coding_key(world):
    refused = _start(world)
    assert refused.status_code == 409, refused.text
    assert "coding key" in refused.json()["detail"]
    assert "Claude Max" in refused.json()["detail"], "the refusal did not name the way out"
    assert _name(world, world["coding"]).status_code == 200
    assert _start(world).status_code == 200


def test_the_refusal_says_so_when_the_install_holds_no_key_that_could_code(client):
    _roles(client)
    billing = _credential(client, "DeepSeek", "deepseek", "sk-deepseek-selftest")
    created = _factory(client, billing_secret_id=billing,
                       ceo_model_override="deepseek-chat",
                       teams=[{"role_slug": "scribe", "name": "Notes", "employees": ["Sam"],
                               "model_overrides": {"Sam": "deepseek-chat"}}])
    assert created.status_code == 200, created.text
    refused = client.post(f"/admin/projects/{created.json()['id']}/start", headers=ADMIN)
    assert refused.status_code == 409
    assert "this install holds none" in refused.json()["detail"]


def test_a_billing_credential_cannot_be_slipped_under_a_running_keyless_factory(client):
    """Nothing pays a keyless factory, so it starts — it can run no model. The
    credential that would let it run one meets the rule the start skipped."""
    _roles(client)
    created = _factory(client).json()
    assert client.post(f"/admin/projects/{created['id']}/start", headers=ADMIN).status_code == 200
    billing = _credential(client, "Talk", "anthropic", API)
    slipped = client.patch(f"/admin/projects/{created['id']}", headers=ADMIN,
                           json={"billing_secret_id": billing})
    assert slipped.status_code == 409, slipped.text
    with db_session() as session:
        assert session.get(Project, created["id"]).billing_secret_id is None


def test_every_other_kind_of_project_starts_as_it_always_did(client):
    _roles(client)
    billing = _credential(client, "Talk", "anthropic", API)
    client.post("/admin/roles", headers=ADMIN, json={
        "slug": "chief", "name": "Chief", **ROLE_MIN, "is_ceo_role": True})
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "plain", "name": "Plain", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief",
        "billing_secret_id": billing,
        "teams": [{"role_slug": "builder", "name": "Build", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    assert client.post(f"/admin/projects/{project_id}/start", headers=ADMIN).status_code == 200
    # And it holds no coding key, however it is asked.
    coding = _credential(client, "Claude Max", "anthropic", OAT, mode="subscription")
    refused = client.put(f"/admin/projects/{project_id}/coding-credential", headers=ADMIN,
                         json={"credential_id": coding})
    assert refused.status_code == 422 and "only a Code Factory" in refused.json()["detail"]


# ── what may be a coding key ────────────────────────────────────────────────────


def test_a_key_for_a_provider_with_no_coding_cli_is_refused(world):
    deepseek = _credential(world["client"], "DeepSeek", "deepseek", "sk-deepseek-selftest")
    refused = _name(world, deepseek)
    assert refused.status_code == 422 and "no coding CLI" in refused.json()["detail"]
    assert _name(world, "sec_does_not_exist").status_code == 422
    assert _start(world).status_code == 409, "a refused key still opened the start"


def test_it_is_refused_at_create_on_a_project_that_is_not_a_factory(world):
    client = world["client"]
    client.post("/admin/roles", headers=ADMIN, json={
        "slug": "chief", "name": "Chief", **ROLE_MIN, "is_ceo_role": True})
    refused = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "plain", "name": "Plain", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief",
        "billing_secret_id": world["billing"], "coding_secret_id": world["coding"],
        "teams": [{"role_slug": "scribe", "name": "Notes", "employees": ["Sam"]}]})
    assert refused.status_code == 422, refused.text


# ── who runs on which ───────────────────────────────────────────────────────────


def test_repo_seats_code_on_the_coding_key_and_everyone_else_stays_on_billing(world):
    assert _name(world, world["coding"]).status_code == 200
    assert _start(world).status_code == 200
    jobs = _leased(world)
    assert set(jobs) == {"Lead", "Ada", "Sam"}, jobs
    ada, sam, lead = jobs["Ada"], jobs["Sam"], jobs["Lead"]
    # The builder: the login, and nothing of the billing key in its sandbox.
    assert ada.billing_env.get("CLAUDE_CODE_OAUTH_TOKEN") == OAT, sorted(ada.billing_env)
    assert API not in ada.billing_env.values()
    assert ada.provider == "anthropic" and ada.harness == "claude_agent_sdk"
    # The lead and the scribe: the API key, and nothing of the login.
    for job in (sam, lead):
        assert job.billing_env.get("ANTHROPIC_API_KEY") == API, sorted(job.billing_env)
        assert OAT not in job.billing_env.values()
    # A coding seat that names no model gets the best one; the talkers do not.
    assert ada.model != sam.model, "the coding seat ran on the talkers' model"
    with db_session() as session:
        paid = {r.agent_id: r.paid_in for r in session.query(Run).filter(
            Run.project_id == world["project"]["id"])}
    assert paid[world["agents"]["Ada"]] == "tokens"
    assert paid[world["agents"]["Sam"]] == "usd" and paid[world["agents"]["Lead"]] == "usd"


def test_a_coding_key_on_another_provider_moves_only_the_repo_seats(world):
    client = world["client"]
    codex = _credential(client, "Codex", "openai", "sk-proj-coding-key-selftest")
    assert _name(world, codex).status_code == 200, _name(world, codex).text
    assert _start(world).status_code == 200
    jobs = _leased(world)
    assert jobs["Ada"].provider == "openai" and jobs["Ada"].harness == "codex_cli"
    assert jobs["Sam"].provider == "anthropic" and jobs["Lead"].provider == "anthropic"
    assert API not in jobs["Ada"].billing_env.values()
    # The coding key pays the coders and nobody else: a talker put on its
    # provider is a seat nothing pays (R5), not a second way onto the key.
    moved = client.patch(f"/admin/agents/{world['agents']['Sam']}/seat", headers=ADMIN,
                         json={"model": "gpt-6.1-sol"})
    assert moved.status_code == 422, moved.text
    assert "holds no" in moved.json()["detail"]


def test_the_screens_say_who_codes_with_it(world):
    assert _name(world, world["coding"]).status_code == 200
    view = world["client"].get(
        f"/admin/projects/{world['project']['id']}/coding-credential", headers=ADMIN).json()
    assert view["applies"] and view["name"] == "Claude Max" and view["refusal"] is None
    assert view["seats"] == ["Ada"], view
    listed = world["client"].get(CREDENTIALS, headers=ADMIN).json()
    row = next(c for c in listed if c["id"] == world["coding"])
    assert [p["as"] for p in row["projects"]] == ["codes"]


# ── keeping it ──────────────────────────────────────────────────────────────────


def test_a_running_factory_keeps_its_coding_key(world):
    assert _name(world, world["coding"]).status_code == 200
    assert _start(world).status_code == 200
    assert _name(world, None).status_code == 409
    gone = world["client"].delete(f"{CREDENTIALS}/{world['coding']}", headers=ADMIN)
    assert gone.status_code in (409, 422), gone.text
    with db_session() as session:
        assert session.get(Project, world["project"]["id"]).coding_secret_id == world["coding"]
    # Replacing it is not removing it.
    other = _credential(world["client"], "Gemini", "google", "AIza-coding-key-selftest")
    assert _name(world, other).status_code == 200


def test_it_travels_by_name_and_a_clone_keeps_it(world):
    client = world["client"]
    assert _name(world, world["coding"]).status_code == 200
    bundle = client.get(f"/admin/projects/{world['project']['id']}/export", headers=ADMIN)
    assert bundle.status_code == 200, bundle.text
    assert bundle.json()["project"]["coding_credential_name"] == "Claude Max"
    assert OAT not in bundle.text


def test_an_agent_cannot_read_or_move_the_key(world):
    assert _name(world, world["coding"]).status_code == 200
    assert _start(world).status_code == 200
    _, token = live_token(world["project"]["id"], world["agents"]["Lead"])
    forged = {"Authorization": f"Bearer {token}"}
    path = f"/admin/projects/{world['project']['id']}/coding-credential"
    assert world["client"].put(path, headers=forged,
                               json={"credential_id": None}).status_code in (401, 403)
    assert world["client"].get(path, headers=forged).status_code in (401, 403)
    pack = world["client"].get("/agent/context", headers=forged)
    assert pack.status_code == 200 and OAT not in pack.text and "Claude Max" not in pack.text


# ── the Founder ─────────────────────────────────────────────────────────────────


def test_the_founder_is_told_a_factory_needs_a_coding_key_and_which_can_code(world):
    """A Founder drafting a factory used to see only "who pays": nothing in its
    prompt, its credential menu or its draft schema said a factory also needs
    a coding key, so it drafted factories that could not start and never
    asked about the second credential."""
    from yaaf.founder import prompt, tools

    _credential(world["client"], "DeepSeek", "deepseek", "sk-deepseek-coding-selftest")
    with db_session() as session:
        menu = tools.list_credentials(session)
    can_code = {row["name"]: row["can_code"] for row in menu["credentials"]}
    assert can_code == {"Claude Max": True, "Talk": True, "DeepSeek": False}
    assert "coding_secret_id" in menu["note"]
    assert "coding_secret_id" in prompt.system_prompt()
    assert "coding_secret_id" in str(tools.PROPOSE_SCHEMA)


def test_a_drafted_coding_key_arrives_on_the_form_by_name(world):
    """The form picks a coding key up by name, as from an export. A Founder
    draft names it by id, so the draft carries the name too."""
    from yaaf.founder import tools

    project = {"mode": "code_factory", "coding_secret_id": world["coding"]}
    with db_session() as session:
        assert tools.billing_problems(project, session) == []
    assert project["coding_secret_id"] == world["coding"]
    assert project["coding_credential_name"] == "Claude Max"
    assert tools.summarise({"project": project})["codes_with"] == "Claude Max"
