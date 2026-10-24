"""Invariant selftests: a seat chooses its harness, and no door seats one that cannot run.

docs/RUNTIMES.md M1. The choice lives where the model does — on the role
version, and per seat beside `model_override` — and every door a seat comes
through runs one check (yaaf.runtimes.seats.refusal): the harness must exist,
serve the seat's model, be able to take away what the role withholds (R2), and
authenticate with the project's credential (R1).

The two Claude CLI harnesses can do all of that, so R1 and R2 cannot fire on
them. The attacks therefore register a harness that cannot — exactly how the
rules are written, against the registry rather than against a name — and a
real one is held by the same doors with nobody editing them: `anthropic_api`
(M2), which pays only with an API key and prices its own runs, is the first,
and the section at the end proves it from the outside (R1, and R3: a model
with no price cannot be hired onto a harness that meters by price).
"""

from __future__ import annotations

import json

import pytest

from tests.conftest import (
    ADMIN,
    ROLE_MIN,
    agent_by_name,
    bearer,
    live_token,
    make_project,
    seed_role,
)
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, RoleVersion, Run, Team
from yaaf.runner.backends import get_backend
from yaaf.runner.daemon import lease_and_prepare
from yaaf.runtimes import catalog, harnesses
from yaaf.runtimes.harnesses import Harness
from yaaf.server.dispatcher import dispatch_once

CREDENTIALS = "/admin/settings/llm-credentials"


@pytest.fixture()
def picky(monkeypatch):
    """Two harnesses that cannot do what the Claude ones can: one that pays
    only with an API key (R1), one that always has a shell (R2). Both hold a
    conversation, so a CEO put on either is refused for the rule under test
    and not for R7."""
    monkeypatch.setitem(harnesses.HARNESSES, "key_only", Harness(
        name="key_only", label="Key-only harness", family="cli",
        providers=("anthropic",), accepts=("anthropic:api_key",), conversation=True))
    monkeypatch.setitem(harnesses.HARNESSES, "always_shell", Harness(
        name="always_shell", label="Always-a-shell harness", family="cli",
        providers=("anthropic",), accepts=("anthropic:api_key", "anthropic:subscription"),
        withholds=("files", "web"), conversation=True))


def _credential(client, name: str, mode: str) -> str:
    value = "sk-ant-oat-test-placeholder" if mode == "subscription" else "test-credential-value"
    response = client.post(CREDENTIALS, headers=ADMIN,
                           json={"name": name, "value": value, "mode": mode})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _role(client, slug: str, **fields):
    return client.post("/admin/roles", headers=ADMIN,
                       json={"slug": slug, "name": slug.title(), **ROLE_MIN, **fields})


def _project_body(slug: str, **fields) -> dict:
    return {"slug": slug, "name": slug.title(), "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo", "ceo_name": "CEO",
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
                       "lead": "Lead"}], **fields}


# ── the choice itself ───────────────────────────────────────────────────────────


def test_the_picker_offers_every_harness_a_seat_may_run_on_and_not_the_test_switch(client):
    """Stated as the rule rather than a list, because the list grows with every
    milestone: every registered harness, and never the scripted backend."""
    offered = {h["name"] for h in client.get("/admin/harnesses", headers=ADMIN).json()}
    assert "mock" not in offered
    assert offered == {name for name, h in harnesses.HARNESSES.items() if h.family != "mock"}
    assert {"claude_agent_sdk", "claude_cli", "yaaf_loop", "codex_cli", "gemini_cli",
            "opencode"} <= offered
    # A name a harness used to go by is understood, never offered.
    assert not offered & set(harnesses.ALIASES)


def test_a_role_on_the_cli_runs_its_seats_on_the_cli(client):
    """The whole of M1 from the outside: name the harness on the role, and the
    seat's runs are leased onto it, pinned as it, and executed by its backend.
    The CEO's role, because the CEO is the seat that wakes when a project
    starts."""
    make_project(client, start=False)  # the chairman and the base roles
    assert _role(client, "ceo", is_ceo_role=True, harness="claude_cli").status_code == 200
    assert client.get("/admin/roles/ceo", headers=ADMIN).json()["harness"] == "claude_cli"
    # Created directly: make_project re-seeds the roles, which would pin a
    # fresh version that names no harness.
    created = client.post("/admin/projects", headers=ADMIN, json=_project_body("on-the-cli"))
    assert created.status_code == 200, created.text
    project = created.json()
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=20)
               if j.agent_id == project["ceo_agent_id"])
    assert (job.provider, job.harness) == ("anthropic", "claude_cli")
    with db_session() as session:
        assert session.get(Run, job.run_id).harness == "claude_cli"
    assert get_backend(job.harness).entrypoint == "claude_cli_entry.py"


def test_a_seat_can_be_put_on_another_harness_than_its_role(client):
    make_project(client, start=False)
    created = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "one-seat-differs", ceo_harness_override="claude_cli",
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead", "Emp2"],
                "lead": "Lead", "harness_overrides": {"Emp2": "claude_cli"}}]))
    assert created.status_code == 200, created.text
    project = created.json()
    with db_session() as session:
        assert session.get(Agent, project["ceo_agent_id"]).harness_override == "claude_cli"
        assert session.get(Agent, agent_by_name(project, "Emp2")).harness_override == "claude_cli"
        assert session.get(Agent, agent_by_name(project, "Lead")).harness_override is None
    # It travels: an export carries it and a clone keeps it.
    exported = client.get(f"/admin/projects/{project['id']}/export", headers=ADMIN).json()
    assert exported["project"]["ceo_harness_override"] == "claude_cli"
    assert exported["project"]["teams"][0]["harness_overrides"] == {"Emp2": "claude_cli"}
    cloned = client.post(f"/admin/projects/{project['id']}/clone", headers=ADMIN,
                         json={"slug": "one-seat-differs-twin"})
    assert cloned.status_code == 200, cloned.text
    with db_session() as session:
        twin = session.query(Agent).filter(Agent.project_id == cloned.json()["id"],
                                           Agent.name == "Emp2").one()
        assert twin.harness_override == "claude_cli"


@pytest.mark.parametrize("harness", ["no-such-harness", "mock"])
def test_a_role_cannot_name_a_harness_a_seat_cannot_run_on(client, harness):
    """`mock` is the project-wide test switch, never a seat's choice: a seat on
    it would be a scripted agent in a real project."""
    refused = _role(client, "worker", harness=harness)
    assert refused.status_code == 422
    assert "unknown harness" in refused.text


def test_an_override_for_somebody_not_on_the_team_is_refused(client):
    make_project(client, start=False)
    refused = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "ghost", teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"],
                         "harness_overrides": {"Nobody": "claude_cli"}}]))
    assert refused.status_code == 422 and "harness_overrides" in refused.text


# ── R2: a grant the harness cannot take away ────────────────────────────────────


def test_a_role_without_a_shell_cannot_be_put_on_a_harness_that_always_has_one(client, picky):
    refused = _role(client, "scribe", harness="always_shell",
                    tool_permissions={"files": True, "web": True})
    assert refused.status_code == 422
    assert "cannot run without bash" in refused.text
    # Granting the shell is the honest fix, and it is accepted.
    assert _role(client, "scribe", harness="always_shell",
                 tool_permissions={"files": True, "bash": True}).status_code == 200


def test_a_seat_override_cannot_widen_its_role_either(client, picky):
    make_project(client, start=False)
    seed_role(client, "scribe", tool_permissions={"files": True})
    refused = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "widened", teams=[{"role_slug": "scribe", "name": "Words", "employees": ["Ada"],
                           "harness_overrides": {"Ada": "always_shell"}}]))
    assert refused.status_code == 422
    assert "Ada" in refused.text and "cannot run without bash" in refused.text
    with db_session() as session:
        assert session.query(Project).filter(Project.slug == "widened").count() == 0


# ── R1: a credential the harness cannot pay with ────────────────────────────────


def test_a_seat_cannot_be_created_on_a_harness_its_credential_cannot_pay(client, picky):
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    refused = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "wrong-money", billing_secret_id=subscription,
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"],
                "harness_overrides": {"Lead": "key_only"}}]))
    assert refused.status_code == 422
    assert "cannot pay with a Claude subscription" in refused.text


def test_the_money_cannot_move_under_a_seat_that_cannot_follow_it(client, picky):
    """R1 from the other side: the seats stay put and the credential changes."""
    make_project(client, start=False)
    key = _credential(client, "Key", "api_key")
    subscription = _credential(client, "Plan", "subscription")
    created = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "money-moves", billing_secret_id=key,
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"],
                "harness_overrides": {"Lead": "key_only"}}]))
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]
    moved = client.patch(f"/admin/projects/{project_id}", headers=ADMIN,
                         json={"billing_secret_id": subscription})
    assert moved.status_code == 422 and "Lead" in moved.text
    with db_session() as session:
        assert session.get(Project, project_id).billing_secret_id == key


def test_a_team_added_later_and_a_hire_by_the_ceo_meet_the_same_check(client, picky):
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    project = make_project(client, slug="later-doors", billing_secret_id=subscription)
    added = client.post(f"/admin/projects/{project['id']}/teams", headers=ADMIN, json={
        "role_slug": "worker", "name": "Beta", "employees": ["Kit"],
        "harness_overrides": {"Kit": "key_only"}})
    assert added.status_code == 422, added.text
    # The CEO cannot name a harness, but it hires a role, and the role can.
    assert _role(client, "keyed", harness="key_only").status_code == 200
    _, token = live_token(project["id"], project["ceo_agent_id"])
    hired = client.post("/agent/teams", headers=bearer(token), json={
        "role_slug": "keyed", "name": "Gamma", "employees": ["Ivy"],
        "reason": "A second desk for the fortnight the build half runs long."})
    assert hired.status_code == 422, hired.text


def test_a_repin_cannot_move_seats_onto_a_harness_their_credential_cannot_pay(client, picky):
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    project = make_project(client, slug="repinned", billing_secret_id=subscription)
    team_id = project["teams"][0]["id"]
    assert _role(client, "worker", harness="key_only").status_code == 200
    refused = client.post(f"/admin/teams/{team_id}/repin", headers=ADMIN)
    assert refused.status_code == 422
    with db_session() as session:
        pinned = session.get(RoleVersion, session.get(Team, team_id).role_version_id)
        assert pinned.harness is None, "the refused repin still moved the team"


def test_a_seat_changed_behind_every_door_is_failed_at_lease_not_run(client, picky):
    """The backstop: a seat nobody's door checked — a hand edit, a door not
    built yet — fails at lease with its reason, and no container starts."""
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    project = make_project(client, slug="backstop", billing_secret_id=subscription)
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "key_only"
    with db_session() as session:
        dispatch_once(session)
    jobs = [j for j in lease_and_prepare(limit=20) if j.project_id == project["id"]]
    assert not any(j.agent_id == project["ceo_agent_id"] for j in jobs)
    with db_session() as session:
        failed = session.query(Run).filter(Run.agent_id == project["ceo_agent_id"]).one()
        assert failed.status == "failed"
        assert failed.summary.startswith("seat cannot run:")


def test_a_seat_that_cannot_run_is_named_on_the_health_checklist(client, picky):
    """Found before its first run fails, in the place a chairman looks for
    "is this set up right" — and absent when nothing is wrong, because a
    healthy project's checklist shows nothing at all."""
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    project = make_project(client, slug="health", billing_secret_id=subscription)
    health = f"/observe/projects/{project['id']}/health"
    assert not [c for c in client.get(health, headers=ADMIN).json()["checks"]
                if c["key"] == "seats"]
    with db_session() as session:
        session.get(Agent, project["ceo_agent_id"]).harness_override = "key_only"
    seats = [c for c in client.get(health, headers=ADMIN).json()["checks"]
             if c["key"] == "seats"]
    assert seats and seats[0]["status"] == "fail"
    assert "cannot pay with a Claude subscription" in seats[0]["detail"]


# ── the first real harness the rules refuse: yaaf's own loop (M2) ──────────────


def test_a_subscription_cannot_pay_for_a_seat_on_the_api_loop(client):
    """R1 on a real harness. A subscription token is a credential for the
    Claude Code CLI, not for the Messages API: a seat on the loop paid with
    one would fail every run with an authentication error."""
    make_project(client, start=False)
    subscription = _credential(client, "Plan", "subscription")
    key = _credential(client, "Key", "api_key")
    teams = [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"],
              "harness_overrides": {"Lead": "anthropic_api"}}]
    refused = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "loop-on-a-plan", billing_secret_id=subscription, teams=teams))
    assert refused.status_code == 422
    assert ("yaaf agent loop (the provider's own API) cannot pay with a Claude subscription"
            in refused.text)
    accepted = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "loop-on-a-key", billing_secret_id=key, teams=teams))
    assert accepted.status_code == 200, accepted.text


def test_a_model_with_no_price_cannot_be_hired_onto_a_harness_that_prices_runs(client):
    """R3. The loop meters every call by the catalog's price, so a model the
    catalog cannot price is a run the per-run cap cannot stop."""
    make_project(client, start=False)
    refused = _role(client, "mystery", model="claude-mystery-9", harness="anthropic_api")
    assert refused.status_code == 422
    assert "claude-mystery-9 has no price on this install" in refused.text
    assert "Settings → Model prices" in refused.text
    # A harness that reports its own bill is not metered by the catalog.
    assert _role(client, "mystery", model="claude-mystery-9",
                 harness="claude_cli").status_code == 200
    # And the seat cannot be moved onto the loop either.
    moved = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "unpriced-seat", teams=[{"role_slug": "mystery", "name": "Odd", "employees": ["Ann"],
                                 "harness_overrides": {"Ann": "anthropic_api"}}]))
    assert moved.status_code == 422 and "Ann" in moved.text


def test_a_seat_on_the_api_loop_is_leased_with_its_price_and_runs_on_its_entrypoint(client):
    make_project(client, start=False)
    assert _role(client, "ceo", is_ceo_role=True, harness="anthropic_api").status_code == 200
    created = client.post("/admin/projects", headers=ADMIN, json=_project_body(
        "on-the-loop", billing_secret_id=_credential(client, "Key", "api_key")))
    assert created.status_code == 200, created.text
    project = created.json()
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    with db_session() as session:
        dispatch_once(session)
    job = next(j for j in lease_and_prepare(limit=20)
               if j.agent_id == project["ceo_agent_id"])
    # Named by its old name, stored and run under its new one.
    assert client.get("/admin/roles/ceo", headers=ADMIN).json()["harness"] == "yaaf_loop"
    assert (job.provider, job.harness) == ("anthropic", "yaaf_loop")
    row = catalog.model(job.model)
    assert job.price == row.price.as_dict()
    # And the window its history is compacted against (loop_harness), from
    # the shipped snapshot.
    from yaaf.core.price_feed import shipped_window

    assert job.model_flags == {"effort": row.effort, "web_tools": row.web_tools,
                               "efforts": None, "context": shipped_window(job.model)}
    assert get_backend(job.harness).entrypoint == "loop_entry.py"
    assert json.loads(json.dumps(job.provider_spec))["protocol"] == "anthropic"
