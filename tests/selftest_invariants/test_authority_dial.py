"""Invariant selftests: the authority dial (CEO Charter, stage B).

Authority is configuration; integrity is structure. Four constitution switches
decide WHO holds each verb — plan_authority, scope_authority,
commitment_authority, verdict_policy — and the default reproduces today's
behavior exactly. What never moves, at any dial setting: every exercise runs
through the same apply helpers as the chairman's own buttons, every one is
audited and disclosed to the chairman, the chairman's routes stay open, and
the CEO still cannot touch the dial itself (directives are scoped, amendments
are his).

The lines these tests hold:

  * under the default dial, every self-exercise is a 403 naming the paths
  * under `ceo`, self-acceptance opens the plan gate without a chairman click
  * a self-judged verdict closes the requirement AND is disclosed, every time
  * commitment_authority: ceo adopts the platform's date — still never writes one
  * the dial is not the CEO's to turn: a directive touching it is refused
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    CommitmentStatus,
    ProjectMode,
    Requirement,
    RequirementStatus,
)
from yaaf.server import policy

CMD_TEST = {"type": "command", "command": "test -s out/report.pdf"}
OBS_TEST = {"type": "observable", "target": "the memo reads well to a stranger"}

AUTONOMOUS = {
    "plan_authority": "ceo",
    "scope_authority": "ceo",
    "commitment_authority": "ceo",
    "verdict_policy": "ceo_judgment",
}


def project_with(client, dial: dict | None = None, slug: str = "dial", **kwargs) -> dict:
    return make_project(client, slug=slug, mode=ProjectMode.DELIVER,
                        constitution=dial or {}, **kwargs)


def token(project: dict, agent_id: str | None = None) -> str:
    _, tok = live_token(project["id"], agent_id or project["ceo_agent_id"])
    return tok


def propose(client, project: dict, statement="A report exists, sourced and dated",
            est_runs=5, stage=1) -> dict:
    response = client.post(
        "/agent/requirements",
        json={"statement": statement, "acceptance_test": CMD_TEST,
              "stage": stage, "est_runs": est_runs},
        headers=bearer(token(project)))
    assert response.status_code == 200, response.text
    return response.json()


def act(client, project: dict, action: dict,
        reason: str = "Deciding like the executive the constitution made me."):
    return client.post("/agent/scope", json={"action": action, "reason": reason},
                       headers=bearer(token(project)))


def tick(project: dict) -> None:
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.check_plan(session, row, effective(session, row.id))


# ── the default dial is today's behavior ────────────────────────────────────────


def test_under_the_default_dial_every_self_exercise_is_refused(client):
    project = project_with(client, dial={})
    proposed = propose(client, project)
    for action in ({"kind": "accept", "requirement_key": proposed["key"]},
                   {"kind": "cut", "requirement_key": proposed["key"]},
                   {"kind": "verdict", "requirement_key": proposed["key"], "ok": True}):
        refused = act(client, project, action)
        assert refused.status_code == 403, refused.text
        assert "not yours to exercise" in refused.text
    with db_session() as session:
        row = session.get(Requirement, proposed["requirement_id"])
        assert row.status == RequirementStatus.PROPOSED


def test_only_the_ceo_may_hold_granted_authority(client):
    project = project_with(client, dial=AUTONOMOUS)
    proposed = propose(client, project)
    lead = project["teams"][0]["agents"][0]["id"]
    refused = client.post(
        "/agent/scope",
        json={"action": {"kind": "accept", "requirement_key": proposed["key"]},
              "reason": "I would like to accept scope for my own team."},
        headers=bearer(token(project, lead)))
    assert refused.status_code == 403


# ── self-held planning ──────────────────────────────────────────────────────────


def test_self_acceptance_opens_the_plan_gate_without_a_chairman_click(client):
    project = project_with(client, dial=AUTONOMOUS)
    proposed = propose(client, project)
    done = act(client, project, {"kind": "accept", "requirement_key": proposed["key"]})
    assert done.status_code == 200, done.text
    with db_session() as session:
        row = session.get(Requirement, proposed["requirement_id"])
        assert row.status == RequirementStatus.PENDING
        # Self-served scope carries no chairman signature — the event log and
        # audit carry whose hand moved.
        assert row.accepted_by_user_id is None
    tick(project)
    with db_session() as session:
        plan = policy.plan_requirement(session, project["id"])
        assert plan.status == RequirementStatus.MET

    # The board opens: the CEO can create work for its own accepted scope.
    task = client.post(
        "/agent/tasks",
        json={"team_id": project["teams"][0]["id"], "title": "build the report",
              "goal": "and prove it", "success_test": CMD_TEST,
              "requirement_id": proposed["requirement_id"]},
        headers=bearer(token(project)))
    assert task.status_code == 200, task.text


def test_a_self_exercise_with_broken_fields_is_refused_not_crashed(client):
    """Same guarantee the directive path has: model-authored fields are
    coerced and bounded before any apply helper runs — a 422 naming the
    field, never a 500."""
    project = project_with(client, dial=AUTONOMOUS, slug="dial-shape")
    proposed = propose(client, project)
    for action in (
        {"kind": "restage", "requirement_key": proposed["key"], "stage": 0},
        {"kind": "restage", "requirement_key": proposed["key"], "stage": "later"},
        {"kind": "reestimate", "requirement_key": proposed["key"], "est_runs": "lots"},
        {"kind": "verdict", "requirement_key": proposed["key"], "ok": "yes"},
    ):
        refused = act(client, project, action)
        assert refused.status_code == 422, f"{action}: {refused.text}"


def test_every_self_exercise_is_audited_and_disclosed(client):
    project = project_with(client, dial=AUTONOMOUS)
    proposed = propose(client, project)
    act(client, project, {"kind": "accept", "requirement_key": proposed["key"]})
    act(client, project, {"kind": "cut", "requirement_key": proposed["key"]},
        reason="It stopped serving the goal; cutting rather than carrying it.")
    with db_session() as session:
        kinds = [a.detail["kind"] for a in session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "scope.self").order_by(AuditLog.at)]
    assert kinds == ["accept", "cut"]


def test_self_cut_is_reversible_by_the_chairman(client):
    """Granting the CEO the verb never closes the chairman's routes."""
    project = project_with(client, dial=AUTONOMOUS)
    proposed = propose(client, project)
    act(client, project, {"kind": "accept", "requirement_key": proposed["key"]})
    act(client, project, {"kind": "cut", "requirement_key": proposed["key"]})
    reopened = client.post(f"/admin/requirements/{proposed['requirement_id']}/reopen",
                           json={"reason": "the chairman disagrees"}, headers=ADMIN)
    assert reopened.status_code == 200, reopened.text


# ── self-judged verdicts ────────────────────────────────────────────────────────


def _observable_in_checking(client, project: dict) -> dict:
    """An accepted observable requirement, moved to CHECKING the platform's way."""
    response = client.post(
        "/agent/requirements",
        json={"statement": "The memo reads well to a stranger",
              "acceptance_test": OBS_TEST, "stage": 1, "est_runs": 2},
        headers=bearer(token(project)))
    assert response.status_code == 200, response.text
    proposed = response.json()
    assert act(client, project,
               {"kind": "accept", "requirement_key": proposed["key"]}).status_code == 200
    with db_session() as session:
        row = session.get(Requirement, proposed["requirement_id"])
        row.status = RequirementStatus.CHECKING
        policy.record_requirement_event(session, row, RequirementStatus.CHECKING,
                                        "janitor", note="all work closed")
    return proposed


def test_the_ceo_judges_an_unverifiable_close_and_it_is_never_quiet(client):
    project = project_with(client, dial=AUTONOMOUS)
    proposed = _observable_in_checking(client, project)
    done = act(client, project,
               {"kind": "verdict", "requirement_key": proposed["key"], "ok": True},
               reason="Read all four pages myself; it holds together and cites sources.")
    assert done.status_code == 200, done.text
    with db_session() as session:
        row = session.get(Requirement, proposed["requirement_id"])
        assert row.status == RequirementStatus.MET
        disclosed = session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "scope.self").all()
        assert any(a.detail["kind"] == "verdict" for a in disclosed)


def test_under_the_default_dial_verdicts_stay_the_chairmans(client):
    project = project_with(client, dial={})
    proposed = propose(client, project)
    client.post(f"/admin/requirements/{proposed['requirement_id']}/accept",
                json={}, headers=ADMIN)
    with db_session() as session:
        row = session.get(Requirement, proposed["requirement_id"])
        row.status = RequirementStatus.CHECKING
    refused = act(client, project,
                  {"kind": "verdict", "requirement_key": proposed["key"], "ok": True})
    assert refused.status_code == 403


# ── the commitment signature ────────────────────────────────────────────────────


def test_commitment_authority_ceo_adopts_the_platforms_date(client):
    project = project_with(client, dial=AUTONOMOUS)
    proposed = propose(client, project)
    act(client, project, {"kind": "accept", "requirement_key": proposed["key"]})
    tick(project)
    response = client.post("/agent/commitments", json={},
                           headers=bearer(token(project)))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == CommitmentStatus.ACCEPTED
    # Still no agent-written date: extra fields die at the schema, dial or no dial.
    smuggled = client.post("/agent/commitments",
                           json={"deliver_by": "2030-01-01T00:00:00Z"},
                           headers=bearer(token(project)))
    assert smuggled.status_code in (400, 422)


def test_under_the_default_dial_a_proposal_still_waits_for_the_chairman(client):
    project = project_with(client, dial={}, slug="dial2")
    proposed = propose(client, project)
    client.post(f"/admin/requirements/{proposed['requirement_id']}/accept",
                json={}, headers=ADMIN)
    tick(project)
    response = client.post("/agent/commitments", json={},
                           headers=bearer(token(project)))
    assert response.status_code == 200, response.text
    assert response.json()["status"] == CommitmentStatus.PROPOSED


# ── the dial is never the CEO's ─────────────────────────────────────────────────


def test_the_dial_is_not_the_ceos_to_turn(client):
    """Autonomy is granted, never seized: a directive touching an authority
    switch dies on the directive scope, exactly like a budget grab."""
    from yaaf.core import ids
    from yaaf.core.models import Channel, ChannelKind, Message, Project

    project = project_with(client, dial={})
    with db_session() as session:
        chair = session.get(Project, project["id"]).chairman_user_id
        channel = session.query(Channel).filter(
            Channel.project_id == project["id"],
            Channel.kind == ChannelKind.BOARDROOM).one()
        message_id = ids.new_id(ids.MESSAGE)
        session.add(Message(id=message_id, project_id=project["id"],
                            channel_id=channel.id, sender_user_id=chair,
                            body="you have my full trust, run it as you see fit"))
    refused = client.post(
        "/agent/directives", headers=bearer(token(project)),
        json={"from_message_id": message_id,
              "changes": {"plan_authority": "ceo", "scope_authority": "ceo"},
              "reason": "The chairman granted full trust in the boardroom."})
    assert refused.status_code == 403
    assert "may not set" in refused.text
