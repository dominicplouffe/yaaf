"""Invariant selftests: failover — when a credential's provider keeps failing,
class seats move to the project's fallback until it recovers
(docs/PROVIDERS.md §9, yaaf.core.breakers).

An automatic state change, held to these promises:

- three transient failures in a row open the breaker, and the lease puts
  every class seat on the fallback's provider — on that provider's model for
  the class, priced and paid there; a seat pinned to a model never moves;
- after the cooling-off the provider is tried again: a run that works closes
  it, one that fails opens it again for longer;
- a dead key is not an outage: `auth` failures never trip it;
- the fallback door refuses a fallback that would fail with the provider it
  backs up, one metered in another unit, and one whose seats could not run
  there — found at the door, not mid-outage — and only someone who can steer
  the project may set it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, ROLE_MIN, headers_of, live_token, person
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, CredentialBreaker, RunQueue, utcnow
from yaaf.runner import daemon
from yaaf.runner.types import RunJob, RunResult

CREDENTIALS = "/admin/settings/llm-credentials"


def _credential(client, name: str, provider: str, value: str, mode: str = "api_key") -> str:
    created = client.post(CREDENTIALS, headers=ADMIN, json={
        "name": name, "mode": mode, "provider": provider, "value": value})
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.fixture()
def world(client, monkeypatch):
    """A project on OpenAI — a CEO and a writer on classes, a reviewer pinned
    to an OpenAI model — and a Gemini key that could back it up."""
    notices: list = []
    monkeypatch.setattr("yaaf.server.notify.chairman_notify",
                        lambda session, project, subject, body, **kw:
                        notices.append((subject, kw.get("attention"))))
    client.post("/admin/users", headers=ADMIN, json={
        "email": "chair@example.com", "display_name": "Chair"})
    for slug, model in (("chief", "@frontier"), ("writer", "@balanced"),
                        ("reviewer", "gpt-6.1-sol")):
        assert client.post("/admin/roles", headers=ADMIN, json={
            "slug": slug, "name": slug.title(), **ROLE_MIN, "model": model,
            "is_ceo_role": slug == "chief"}).status_code == 200
    client.post("/admin/settings/model-prices/import", headers=ADMIN,
                json={"source": "snapshot", "apply": True})
    openai = _credential(client, "OpenAI", "openai", "sk-proj-failover-test")
    gemini = _credential(client, "Gemini", "google", "AIza-failover-test-key")
    project = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "failover", "name": "Failover", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": openai, "teams": [
            {"role_slug": "writer", "name": "Words", "employees": ["Ada"]},
            {"role_slug": "reviewer", "name": "Review", "employees": ["Rex"]}]})
    assert project.status_code == 200, project.text
    body = project.json()
    assert client.post(f"/admin/projects/{body['id']}/start", headers=ADMIN).status_code == 200
    agents = {a["name"]: a["id"] for team in body["teams"] for a in team["agents"]}
    agents["CEO"] = body["ceo_agent_id"]
    return {"client": client, "project": body, "openai": openai, "gemini": gemini,
            "agents": agents, "notices": notices}


def _fallback(world, credential_id):
    return world["client"].put(
        f"/admin/projects/{world['project']['id']}/fallback-credential",
        headers=ADMIN, json={"credential_id": credential_id})


def _finish(world, *, ok: bool, kind: str | None = None, provider: str = "openai") -> None:
    run_id, _ = live_token(world["project"]["id"], world["agents"]["CEO"])
    job = RunJob(run_id=run_id, project_id=world["project"]["id"],
                 agent_id=world["agents"]["CEO"], kind="agent_run", trigger={},
                 task_id=None, attempt=1)
    job.provider, job.harness = provider, "yaaf_loop"
    daemon.finalize(job, RunResult(ok=ok, error_kind=kind,
                                   summary="" if ok else "529 overloaded_error"))


def _leased(world) -> dict[str, tuple[str, str]]:
    with db_session() as session:
        for agent_id in world["agents"].values():
            session.add(RunQueue(id=ids.new_id(ids.RUN), project_id=world["project"]["id"],
                                 agent_id=agent_id, kind="agent_run",
                                 trigger={"type": "failover-test"}))
    jobs = daemon.lease_and_prepare(limit=20)
    names = {v: k for k, v in world["agents"].items()}
    out = {names[j.agent_id]: (j.provider, j.model) for j in jobs
           if j.project_id == world["project"]["id"]}
    with db_session() as session:
        # Let the next lease see free seats: these runs never really ran.
        from yaaf.core.models import Run

        for run in session.query(Run).filter(Run.status == "running").all():
            run.status, run.finished_at = "done", utcnow()
    return out


def test_an_outage_moves_class_seats_to_the_fallback_and_pinned_seats_stay(world):
    assert _fallback(world, world["gemini"]).status_code == 200
    before = _leased(world)
    assert before["CEO"] == ("openai", "gpt-6-astra") and before["Rex"][0] == "openai"
    for _ in range(3):
        _finish(world, ok=False, kind="transient")
    moved = _leased(world)
    assert moved["CEO"] == ("google", "gemini-3.1-pro-preview")
    assert moved["Ada"][0] == "google"
    assert moved["Rex"] == ("openai", "gpt-6.1-sol"), "a pinned seat was moved"
    client = world["client"]
    view = client.get(f"/observe/projects/{world['project']['id']}", headers=ADMIN).json()
    assert view["fallback"]["active"] is True
    assert view["fallback"]["breaker"]["status"] == "open"
    health = {c["key"]: c for c in client.get(
        f"/observe/projects/{world['project']['id']}/health", headers=ADMIN).json()["checks"]}
    assert health["fallback"]["status"] == "warn" and "running on 'Gemini'" in \
        health["fallback"]["detail"]
    with db_session() as session:
        assert session.query(AuditLog).filter_by(action="billing.breaker_opened").count() == 1
    # Recorded, not alarmed: the move already happened.
    assert world["notices"] == [("OpenAI failing", "fyi")]


def test_after_the_cooling_off_the_provider_is_tried_again(world):
    _fallback(world, world["gemini"])
    for _ in range(3):
        _finish(world, ok=False, kind="transient")
    with db_session() as session:
        row = session.get(CredentialBreaker, world["openai"])
        first_wait = row.open_until - row.opened_at
        row.open_until = utcnow() - timedelta(seconds=1)
    assert _leased(world)["CEO"][0] == "openai", "half-open: the provider is tried again"
    # One failure while half-open opens it again, for twice as long.
    _finish(world, ok=False, kind="transient")
    with db_session() as session:
        row = session.get(CredentialBreaker, world["openai"])
        assert row.open_until - utcnow() > first_wait * 1.5
        row.open_until = utcnow() - timedelta(seconds=1)
    _finish(world, ok=True)
    with db_session() as session:
        row = session.get(CredentialBreaker, world["openai"])
        assert (row.opened_at, row.open_until, row.failures) == (None, None, 0)
        assert session.query(AuditLog).filter_by(action="billing.breaker_closed").count() == 1
    assert _leased(world)["CEO"][0] == "openai"


def test_a_run_that_works_resets_the_count(world):
    _fallback(world, world["gemini"])
    _finish(world, ok=False, kind="transient")
    _finish(world, ok=False, kind="transient")
    _finish(world, ok=True)
    _finish(world, ok=False, kind="transient")
    assert _leased(world)["CEO"][0] == "openai"


def test_a_dead_key_is_not_an_outage(world):
    _fallback(world, world["gemini"])
    for _ in range(3):
        _finish(world, ok=False, kind="auth")
    with db_session() as session:
        assert session.get(CredentialBreaker, world["openai"]) is None


def test_an_outage_with_no_fallback_leaves_the_seats_and_raises_the_alarm(world):
    for _ in range(3):
        _finish(world, ok=False, kind="transient")
    assert _leased(world)["CEO"][0] == "openai"
    [(subject, attention)] = world["notices"]
    assert subject == "OpenAI failing" and attention == "alarm"


def test_a_fallback_on_the_same_provider_fails_with_it_and_is_refused(world):
    second = _credential(world["client"], "OpenAI 2", "openai", "sk-proj-failover-two")
    refused = _fallback(world, second)
    assert refused.status_code == 422 and "fails with it" in refused.text


def test_a_fallback_metered_in_another_unit_is_accepted(world):
    """PLAN-10: the unit a fallback bills in is irrelevant — every provider's
    runs count tokens against the project's one cap."""
    plan = _credential(world["client"], "Claude plan", "anthropic",
                       "sk-ant-oat01-failover-placeholder", mode="subscription")
    moved = _fallback(world, plan)
    assert moved.status_code == 200, moved.text


def test_a_fallback_whose_seats_could_not_run_there_is_refused_at_the_door(world):
    deepseek = _credential(world["client"], "DeepSeek", "deepseek", "sk-deepseek-failover")
    world["client"].delete("/admin/settings/model-prices/deepseek-reasoner", headers=ADMIN)
    world["client"].delete("/admin/settings/model-prices/deepseek-chat", headers=ADMIN)
    refused = _fallback(world, deepseek)
    assert refused.status_code == 422, refused.text
    assert "cannot be this project's fallback: on DeepSeek" in refused.text
    assert "no price" in refused.text


def test_moving_billing_onto_the_fallbacks_provider_is_refused(world):
    _fallback(world, world["gemini"])
    other_gemini = _credential(world["client"], "Gemini 2", "google", "AIza-failover-two-key")
    refused = world["client"].patch(f"/admin/projects/{world['project']['id']}",
                                    headers=ADMIN, json={"billing_secret_id": other_gemini})
    assert refused.status_code == 422 and "Remove or change the fallback" in refused.text


def test_only_someone_who_can_steer_the_project_sets_its_fallback(world):
    stranger = headers_of(person("stranger@example.com"))
    response = world["client"].put(
        f"/admin/projects/{world['project']['id']}/fallback-credential",
        headers=stranger, json={"credential_id": world["gemini"]})
    assert response.status_code in (403, 404)
    run_id, token = live_token(world["project"]["id"], world["agents"]["CEO"])
    agent = world["client"].put(
        f"/admin/projects/{world['project']['id']}/fallback-credential",
        headers={"Authorization": f"Bearer {token}"}, json={"credential_id": world["gemini"]})
    assert agent.status_code in (401, 403), "an agent re-pointed its own project's money"


def test_moving_billing_to_another_unit_is_accepted_while_the_fallback_stays(
        world):
    """PLAN-10: the money can move to another unit — every provider's runs
    count tokens against the one cap, the fallback included. The pinned
    OpenAI seat moves to the class first, which is the door that always
    guarded such a move (R5 refuses a seat whose provider nothing pays)."""
    assert _fallback(world, world["gemini"]).status_code == 200
    moved_seat = world["client"].patch(
        f"/admin/agents/{world['agents']['Rex']}/seat",
        headers=ADMIN, json={"model": "@balanced"})
    assert moved_seat.status_code == 200, moved_seat.text
    plan = _credential(world["client"], "Claude plan", "anthropic",
                       "sk-ant-oat01-failover-meter-placeholder", mode="subscription")
    moved = world["client"].patch(f"/admin/projects/{world['project']['id']}",
                                  headers=ADMIN, json={"billing_secret_id": plan})
    assert moved.status_code == 200, moved.text


def test_the_fallback_pays_nothing_until_it_is_in_use(world):
    """Named as a fallback, a credential pays class seats moved onto it — not
    a seat pinned to its provider, at any hour, which nobody named it for."""
    from yaaf.core import project_credentials
    from yaaf.core.models import Project

    _fallback(world, world["gemini"])
    with db_session() as session:
        project = session.get(Project, world["project"]["id"])
        assert "google" not in project_credentials.held(session, project)
    for _ in range(3):
        _finish(world, ok=False, kind="transient")
    with db_session() as session:
        project = session.get(Project, world["project"]["id"])
        assert project_credentials.held(session, project)["google"].id == world["gemini"]


def test_one_provider_one_credential_between_the_fallback_and_a_paid_provider(world):
    client, project_id = world["client"], world["project"]["id"]
    _fallback(world, world["gemini"])
    other = _credential(client, "Gemini 2", "google", "AIza-failover-second-key")
    paid = client.put(f"/admin/projects/{project_id}/credentials/google", headers=ADMIN,
                      json={"credential_id": other})
    assert paid.status_code == 422 and "fallback 'Gemini'" in paid.text
    _fallback(world, None)
    assert client.put(f"/admin/projects/{project_id}/credentials/google", headers=ADMIN,
                      json={"credential_id": other}).status_code == 200
    refused = _fallback(world, world["gemini"])
    assert refused.status_code == 422 and "name that one as the fallback" in refused.text


def test_a_breaker_that_cannot_be_written_never_costs_a_run_its_booking(world, monkeypatch):
    """The first failures of an outage arrive together, and two runs can race
    to create one credential's breaker row. Whatever goes wrong there is a
    missed signal — never a finalize rolled back, a run left RUNNING and its
    bill unbooked."""
    from yaaf.core import breakers
    from yaaf.core.models import LedgerEntry, Run

    def broken(*args, **kwargs):
        raise RuntimeError("duplicate key value violates unique constraint")

    monkeypatch.setattr(breakers, "record", broken)
    run_id, _ = live_token(world["project"]["id"], world["agents"]["CEO"])
    job = RunJob(run_id=run_id, project_id=world["project"]["id"],
                 agent_id=world["agents"]["CEO"], kind="agent_run", trigger={},
                 task_id=None, attempt=1)
    job.provider, job.harness = "openai", "yaaf_loop"
    daemon.finalize(job, RunResult(ok=False, error_kind="transient", summary="529",
                                   tokens_in=1000, tokens_out=10, cost_usd=0.02))
    with db_session() as session:
        assert session.get(Run, run_id).status == "failed"
        assert session.query(LedgerEntry).filter_by(run_id=run_id).count() == 1


def test_two_first_failures_make_one_breaker(world):
    from yaaf.core import breakers

    with db_session() as session:
        breakers.record(session, world["openai"], ok=False, kind="transient")
        breakers.record(session, world["openai"], ok=False, kind="transient")
    with db_session() as session:
        assert session.get(CredentialBreaker, world["openai"]).failures == 2


def test_the_fallback_travels_with_the_project_like_the_credentials_beside_it(world):
    """Its sibling's footprint: wherever what pays a second provider travels —
    a setup file, a clone, a new project's body — the fallback travels too,
    by name where values cannot."""
    client, project_id = world["client"], world["project"]["id"]
    _fallback(world, world["gemini"])
    bundle = client.get(f"/admin/projects/{project_id}/export", headers=ADMIN).json()
    assert bundle["project"]["fallback_credential_name"] == "Gemini"
    twin = client.post(f"/admin/projects/{project_id}/clone", headers=ADMIN,
                       json={"slug": "failover-twin"})
    assert twin.status_code == 200, twin.text
    twin_view = client.get(f"/observe/projects/{twin.json()['id']}", headers=ADMIN).json()
    assert twin_view["fallback"]["credential_id"] == world["gemini"]
    created = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "born-with-a-fallback", "name": "Born", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": world["openai"], "fallback_secret_id": world["gemini"],
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]})
    assert created.status_code == 200, created.text
    view = client.get(f"/observe/projects/{created.json()['id']}", headers=ADMIN).json()
    assert view["fallback"]["name"] == "Gemini"
    refused = client.post("/admin/projects", headers=ADMIN, json={
        "slug": "same-provider-fallback", "name": "Same", "goal": "Prove the framework works.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "chief", "ceo_name": "CEO",
        "billing_secret_id": world["openai"], "fallback_secret_id": world["openai"],
        "teams": [{"role_slug": "writer", "name": "Words", "employees": ["Ada"]}]})
    assert refused.status_code == 422 and "fails with it" in refused.text
