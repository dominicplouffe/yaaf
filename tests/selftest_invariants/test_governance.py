"""Invariant selftests: automatic state changes — expiries, budgets, reaping,
amendments, drought, evidence verification."""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import (
    ADMIN,
    agent_by_name,
    backdate_project,
    bearer,
    live_token,
    make_project,
)
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Evidence,
    Hypothesis,
    LedgerEntry,
    Notice,
    Project,
    Run,
    Task,
    utcnow,
)
from yaaf.server.janitor import janitor_once

CMD_TEST = {"type": "command", "command": "echo everything is fine"}
OBS_TEST = {"type": "observable", "target": "https://example.org/thing exists and loads"}


def test_hypothesis_kill_date_is_automatic(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    hypothesis = client.post(
        "/agent/hypotheses",
        json={"statement": "People will pay for X", "kill_condition": "No signup in 30 days",
              "kill_date": (utcnow() + timedelta(days=1)).isoformat()},
        headers=bearer(ceo_token),
    ).json()
    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "bet work", "goal": "work under the bet",
              "success_test": CMD_TEST, "hypothesis_id": hypothesis["id"]},
        headers=bearer(ceo_token),
    ).json()

    # Time passes: the kill date arrives.
    with db_session() as session:
        row = session.get(Hypothesis, hypothesis["id"])
        row.kill_date = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Hypothesis, hypothesis["id"]).status == "expired"
        blocked = session.get(Task, task["id"])
        assert blocked.status == "blocked"
        assert blocked.blocked_reason == "hypothesis_expired"

    # New work under the dead bet is refused.
    refused = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "more bet work", "goal": "necromancy",
              "success_test": CMD_TEST, "hypothesis_id": hypothesis["id"]},
        headers=bearer(ceo_token),
    )
    assert refused.status_code == 422
    assert "expired" in refused.json()["error"]

    # The chairman extends once — tasks unblock.
    extend = client.post(
        f"/admin/hypotheses/{hypothesis['id']}/extend",
        json={"new_kill_date": (utcnow() + timedelta(days=7)).isoformat(),
              "reason": "two qualified replies arrived on the last day"},
        headers=ADMIN,
    )
    assert extend.status_code == 200
    with db_session() as session:
        assert session.get(Task, task["id"]).status == "ready"

    # ... and only once.
    with db_session() as session:
        session.get(Hypothesis, hypothesis["id"]).kill_date = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)
    again = client.post(
        f"/admin/hypotheses/{hypothesis['id']}/extend",
        json={"new_kill_date": (utcnow() + timedelta(days=30)).isoformat(),
              "reason": "just a little longer, I promise"},
        headers=ADMIN,
    )
    assert again.status_code == 409


def test_approval_expires_to_deny_and_wakes_requester(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    approval = client.post(
        "/agent/approvals",
        json={"kind": "spend", "title": "Big spend", "body": "I recommend it strongly.",
              "amount_usd": 500},
        headers=bearer(ceo_token),
    ).json()

    from yaaf.core.models import Approval

    with db_session() as session:
        session.get(Approval, approval["id"]).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        row = session.get(Approval, approval["id"])
        assert row.status == "expired"
        notices = session.query(Notice).filter(
            Notice.agent_id == project["ceo_agent_id"],
            Notice.kind == "approval_expired",
        ).all()
        assert notices, "requester must be woken with the default-deny"


def test_budget_cap_auto_pauses_and_revokes(client):
    project = make_project(client, constitution={"monthly_usd_cap": 100})
    run_id, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind="external_spend", usd=150.0, note="over the cap"))
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Project, project["id"]).status == "paused"
        assert session.get(Run, run_id).token_revoked is True

    # The revoked token is dead.
    assert client.get("/agent/board", headers=bearer(ceo_token)).status_code == 401


def test_reaper_releases_claims_and_preserves_side_effects(client):
    project = make_project(client)
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    lead_id = agent_by_name(project, "Lead")
    lead_run, lead_token = live_token(project["id"], lead_id)

    task = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "doomed run's task", "goal": "will be reaped",
              "success_test": OBS_TEST},
        headers=bearer(ceo_token),
    ).json()
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(lead_token))
    effect = client.post(
        "/agent/side_effects",
        json={"kind": "email", "idempotency_key": "reap-test:bob@example.com",
              "task_id": task["id"], "payload": {"to": "bob@example.com"}},
        headers=bearer(lead_token),
    ).json()
    client.post(f"/agent/side_effects/{effect['id']}/finish",
                json={"ok": True, "result": {"sent": True}}, headers=bearer(lead_token))

    # The run dies silently: stale heartbeat.
    with db_session() as session:
        run = session.get(Run, lead_run)
        run.heartbeat_at = utcnow() - timedelta(hours=1)
    with db_session() as session:
        janitor_once(session)

    with db_session() as session:
        assert session.get(Run, lead_run).status == "reaped"
        released = session.get(Task, task["id"])
        assert released.status == "ready"
        assert released.claimed_by_agent_id is None
        reap_notices = session.query(Notice).filter(
            Notice.agent_id == lead_id, Notice.kind == "agent_run_reaped"
        ).all()
        assert reap_notices

    # The retry sees the completed side effect in its context pack.
    retry_run, retry_token = live_token(project["id"], lead_id)
    client.post(f"/agent/tasks/{task['id']}/claim", headers=bearer(retry_token))
    pack = client.get("/agent/context", headers=bearer(retry_token)).json()
    fired = pack["task"]["side_effects_already_fired"]
    assert any(e["idempotency_key"] == "reap-test:bob@example.com"
               and e["status"] == "completed" for e in fired)


def test_drought_refuses_internal_tasks(client):
    project = make_project(client, constitution={"drought_hours": 1,
                                                 "external_ratio_min": 0.0})
    backdate_project(project["id"], hours=48)  # escape the grace period
    team_id = project["teams"][0]["id"]
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    internal = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "plumbing during drought",
              "goal": "internal work while starving", "success_test": CMD_TEST,
              "produces_external_evidence": False},
        headers=bearer(ceo_token),
    )
    assert internal.status_code == 409
    assert "drought" in internal.json()["error"].lower()

    external = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "reach outside", "goal": "external work",
              "success_test": OBS_TEST, "produces_external_evidence": True},
        headers=bearer(ceo_token),
    )
    assert external.status_code == 200

    # Verified external evidence breaks the drought.
    with db_session() as session:
        session.add(Evidence(
            id=ids.new_id(ids.EVIDENCE), project_id=project["id"], kind="external",
            provenance="platform_attested", verification="not_applicable",
            description="a stranger replied",
        ))
    ok_again = client.post(
        "/agent/tasks",
        json={"team_id": team_id, "title": "plumbing after rain",
              "goal": "internal work is legal again", "success_test": CMD_TEST},
        headers=bearer(ceo_token),
    )
    assert ok_again.status_code == 200


def test_amendments_are_in_force_immediately(client):
    """Chairman's ruling (2026-08-16): no rule waits. What binds the agents is
    that they cannot change these values themselves and are told when he does —
    not a delay he could always wait out or work around anyway."""
    from yaaf.core.constitution import effective

    project = make_project(client, constitution={"queue_ceiling": 5})
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "the team is drowning, raise the ceiling",
              "changes": {"queue_ceiling": 12, "monthly_usd_cap": 2500.0}},
        headers=ADMIN,
    ).json()

    # One amendment, applied in the request that proposed it — no janitor, no
    # split into an immediate half and a cooled half.
    assert len(response["amendments"]) == 1
    assert response["amendments"][0]["applied_now"] is True
    assert "NOW" in response["note"]

    with db_session() as session:
        cons = effective(session, project["id"])
        assert cons.queue_ceiling == 12
        assert cons.monthly_usd_cap == 2500.0

        # Disclosure rides along, naming both fields in the one notice.
        notices = session.query(Notice).filter(
            Notice.project_id == project["id"],
            Notice.kind == "constitution_amended",
        ).all()
        assert len(notices) == 1
        assert "queue_ceiling" in notices[0].body and "monthly_usd_cap" in notices[0].body
        assert "the team is drowning" in notices[0].body, "the reason travels with the change"

    listed = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN).json()
    assert [a["status"] for a in listed] == ["applied"], "nothing is ever left pending"

    # A janitor pass changes nothing — there is no second half waiting for it.
    with db_session() as session:
        janitor_once(session)
    with db_session() as session:
        assert effective(session, project["id"]).queue_ceiling == 12

    # An invalid amendment is still refused up front, whole — a bad overlay
    # must not land half-applied now that landing is instant.
    bad = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "break the schema", "changes": {"queue_ceiling": -1, "drought_hours": 1}},
        headers=ADMIN,
    )
    assert bad.status_code == 422
    with db_session() as session:
        cons = effective(session, project["id"])
        assert cons.queue_ceiling == 12 and cons.drought_hours != 1


def test_amendment_is_audited_and_logged(client):
    """The delay is gone, so the record is the whole accountability story: who
    changed which rule, when, and why — recoverable long after the fact."""
    from yaaf.core.models import AuditLog

    project = make_project(client)
    client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "this is a memory-heavy research desk; forty facts is not enough",
              "changes": {"memory_cap_team": 80, "wakes_per_agent_per_hour": 12}},
        headers=ADMIN,
    )
    with db_session() as session:
        rows = session.query(AuditLog).filter_by(action="constitution.amend").all()
        assert len(rows) == 1
        assert rows[0].detail["changes"] == {"memory_cap_team": 80,
                                             "wakes_per_agent_per_hour": 12}
        assert rows[0].detail["applied_now"] is True

    listed = client.get(f"/admin/projects/{project['id']}/amendments", headers=ADMIN).json()
    assert listed[0]["reason"].startswith("this is a memory-heavy")


def test_every_rule_describes_itself(client):
    """The settings page is generated from this schema. It used to carry its own
    list of six fields instead, which is why the memory caps — the ones a stuck
    company needs — were unreachable. A rule with no label cannot be rendered,
    so a new rule that forgets one fails here rather than going quietly missing."""
    fields = client.get("/admin/constitution/schema", headers=ADMIN).json()

    from yaaf.core.constitution import THEME_ORDER, ConstitutionDoc
    assert {f["key"] for f in fields} == set(ConstitutionDoc().model_dump())

    for field in fields:
        assert field["label"] and field["label"] != field["key"], field["key"]
        assert len(field["help"]) > 20, f"{field['key']} needs help a chairman can read"
        assert field["group"] != "Other", f"{field['key']} belongs in a named group"
        assert field["type"] in ("integer", "number", "boolean", "string"), field["key"]
        # A group with no theme lands on no tab, so the rule is unreachable in
        # the UI even though the schema describes it perfectly — the same
        # failure the hand-kept list used to cause, one layer up.
        assert field["theme"] in THEME_ORDER, (
            f"{field['key']}: group {field['group']!r} has no theme in GROUP_THEMES")

    assert {f["key"] for f in fields if f["group"] == "Memory"} == {
        "memory_cap_project", "memory_cap_team", "memory_warn_ratio"}

    # Every tab has something on it; an empty tab is a heading that lies.
    assert {f["theme"] for f in fields} == set(THEME_ORDER)


def test_string_rules_with_a_fixed_set_offer_it(client):
    """A rule whose validator accepts four words should not be a free text box.
    The choices ride the schema so the editor draws a menu, and the validator
    stays the authority — a value off the list is still refused server-side."""
    fields = {f["key"]: f for f in
              client.get("/admin/constitution/schema", headers=ADMIN).json()}

    assert fields["approval_timeout_decision"]["choices"] == ["deny", "approve"]
    assert fields["cycle_frequency"]["choices"] == [
        "daily", "weekly", "monthly", "quarterly"]

    # Defaults ride along too: the editor flags a rule that differs from stock.
    assert fields["queue_ceiling"]["default"] == 8
    assert fields["require_evidence_verification"]["default"] is True
    assert fields["ceo_daily_at"]["default"] is None

    project = make_project(client)
    refused = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "trying a frequency that is not on the list",
              "changes": {"cycle_frequency": "fortnightly"}},
        headers=ADMIN,
    )
    assert refused.status_code == 422


def test_evidence_verifier_rules(client):
    from yaaf.server.verifier import verify_pending_evidence

    project = make_project(client, controlled_domains=["ourproduct.example"])
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])

    own_domain = client.post(
        "/agent/evidence",
        json={"kind": "external", "description": "traffic on our own site",
              "source_url": "https://blog.ourproduct.example/post"},
        headers=bearer(ceo_token),
    ).json()
    stranger = client.post(
        "/agent/evidence",
        json={"kind": "external", "description": "a stranger reviewed us",
              "source_url": "https://independent.example.org/review"},
        headers=bearer(ceo_token),
    ).json()

    def fake_fetch(url: str) -> tuple[int, bytes]:
        return 200, b"<html>a real page about the project</html>"

    with db_session() as session:
        verify_pending_evidence(session, fetch=fake_fetch)

    with db_session() as session:
        assert session.get(Evidence, own_domain["id"]).verification == "failed"
        verified = session.get(Evidence, stranger["id"])
        assert verified.verification == "verified"
        assert verified.snapshot_sha256


def test_agents_cannot_reach_admin_or_observe(client):
    project = make_project(client)
    _, ceo_token = live_token(project["id"], project["ceo_agent_id"])
    assert client.get("/observe/projects", headers=bearer(ceo_token)).status_code == 401
    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=bearer(ceo_token)).status_code == 401
