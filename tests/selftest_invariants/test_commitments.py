"""Invariant selftests: the commitment — a date, and what may not happen past it.

The chairman's ask was "a delivery date and anything of substance that can
offer me some sort of guarantee". The guarantee is not that the date holds —
nobody can promise that. It is that when the date stops holding the project
cannot quietly add work: new scope is refused with the arithmetic in the
refusal, the chairman is emailed, and what happens next is his decision.

Four rules make it real, and each is attacked here with a live run token, over
HTTP, the way an agent would:

  * no agent writes a date — the proposal has no date field (extra fields die
    at the schema) and the platform stamps its own projection
  * no agent route accepts, moves or closes a commitment
  * new scope is refused while the projection is past the promise, and past a
    breach — while EXISTING work continues
  * the janitor re-validates on state, not events: an amendment, a scope
    change or a failure storm re-projects the date and tells the CEO, without
    rewriting what was frozen at acceptance
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import (
    ADMIN,
    agent_by_name,
    amend,
    backdate_project,
    bearer,
    live_token,
    make_project,
)
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Commitment,
    CommitmentStatus,
    Notice,
    NoticeKind,
    Requirement,
    RequirementEvent,
    RequirementStatus,
    utcnow,
)


def committed_project(client, slug="acme", scope=1, **kwargs):
    """A Deliver project with an accepted plan and a proposed commitment."""
    project = deliver_project(client, slug=slug, **kwargs)
    token = ceo_token(project)
    rids = []
    for n in range(scope):
        rid = propose(client, token, statement=f"Deliverable {n + 1} exists and is real")
        accept(client, rid["requirement_id"], project)
        rids.append(rid["requirement_id"])
    response = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert response.status_code == 200, response.text
    return project, token, rids, response.json()


def chairman_accept(client, commitment_id: str, deliver_by=None) -> dict:
    body = {}
    if deliver_by is not None:
        body["deliver_by"] = deliver_by.isoformat()
    response = client.post(f"/admin/commitments/{commitment_id}/accept",
                           json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def run_commitment_check(project: dict) -> None:
    """One janitor look at the promise — the transitions live there, on a tick,
    because the pace can slide without anyone touching a commitment."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.check_commitments(session, row, effective(session, row.id))


def fabricate_met(project_id: str, requirement_id: str) -> None:
    """Backdate-free MET, written the way the platform would have. Tests use
    this to give a project a measurable pace without running a sandbox."""
    from yaaf.core import ids

    with db_session() as session:
        row = session.get(Requirement, requirement_id)
        row.status = RequirementStatus.MET
        row.met_at = utcnow()
        session.add(RequirementEvent(
            id=ids.new_id(ids.REQUIREMENT_EVENT),
            project_id=project_id,
            requirement_id=requirement_id,
            from_status=RequirementStatus.IN_PROGRESS,
            to_status=RequirementStatus.MET,
            actor="platform",
        ))


# ── the date is never an agent's to write ───────────────────────────────────────


def test_the_ceo_cannot_smuggle_a_date_into_the_proposal(client):
    """There is no date field. A proposal carrying one dies at the schema —
    not a rule the agent can argue with, a field it was never given."""
    project = deliver_project(client)
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)

    smuggled = client.post("/agent/commitments",
                           json={"deliver_by": "2030-01-01T00:00:00Z"},
                           headers=bearer(token))
    assert smuggled.status_code == 422, smuggled.text

    clean = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert clean.status_code == 200, clean.text
    body = clean.json()
    assert body["deliver_by"], "the platform stamps its own projection as the date"
    assert "arithmetic" in body["basis"], "a date must say where it came from"


def test_no_agent_route_accepts_moves_or_closes_a_commitment(client):
    project, token, _, proposed = committed_project(client)
    cid = proposed["commitment_id"]
    attempts = [
        ("POST", f"/agent/commitments/{cid}/accept", {}),
        ("POST", f"/agent/commitments/{cid}/move", {"deliver_by": "2030-01-01"}),
        ("POST", f"/agent/commitments/{cid}/met", {}),
        ("PATCH", f"/agent/commitments/{cid}", {"status": "accepted"}),
    ]
    for method, path, body in attempts:
        response = client.request(method, path, json=body, headers=bearer(token))
        assert response.status_code in (404, 405), \
            f"{method} {path} answered {response.status_code} — that is a verb an agent has"
    with db_session() as session:
        assert session.get(Commitment, cid).status == CommitmentStatus.PROPOSED


def test_a_commitment_needs_a_plan_that_holds(client):
    project = deliver_project(client)
    token = ceo_token(project)
    refused = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert refused.status_code == 409
    assert "plan" in refused.json()["error"]


def test_only_the_ceo_proposes_and_explore_cannot(client):
    project = deliver_project(client)
    _, lead_token = live_token(project["id"], agent_by_name(project, "Lead"))
    assert client.post("/agent/commitments", json={},
                       headers=bearer(lead_token)).status_code == 403

    explore = make_project(client, slug="explorer-cmt")
    refused = client.post("/agent/commitments", json={},
                          headers=bearer(ceo_token(explore)))
    assert refused.status_code == 422
    assert "Explore" in refused.json()["error"]


def test_operate_cannot_promise_a_delivery_date(client):
    """A rhythm has no finish line, so there is nothing for one date to promise.

    Creating and editing an Operate project already refused a date; the CEO's
    proposal did not, and under an autonomous dial it signed itself — which is
    how a daily brief came to show "the date you signed passed 2 days ago" to a
    chairman who never signed anything. Refused with a plan that holds, so the
    refusal is the mode's and not the plan gate's."""
    project = make_project(client, slug="rhythm-cmt", mode="operate",
                           constitution={"commitment_authority": "ceo"})
    token = ceo_token(project)
    accept(client, propose(client, token)["requirement_id"], project)

    refused = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert refused.status_code == 422, refused.text
    assert "Operate" in refused.json()["error"]
    with db_session() as session:
        assert session.query(Commitment).filter_by(project_id=project["id"]).count() == 0


def test_an_operate_promise_that_got_in_before_the_refusal_is_voided(client):
    """Installs carry rows from before the refusal, breached and freezing scope.
    The janitor voids them — kept for the record, binding nothing — and the
    freeze they held lifts with them."""
    from yaaf.core import ids
    from yaaf.core.models import AuditLog

    project = make_project(client, slug="rhythm-void", mode="operate")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    with db_session() as session:
        session.add(Commitment(
            id=ids.new_id(ids.COMMITMENT), project_id=project["id"],
            status=CommitmentStatus.BREACHED, deliver_by=utcnow() - timedelta(days=2),
            accepted_at=utcnow() - timedelta(days=5), scope_snapshot=["R-01"]))

    run_commitment_check(project)
    run_commitment_check(project)  # state-based: a second look changes nothing

    with db_session() as session:
        row = session.query(Commitment).filter_by(project_id=project["id"]).one()
        assert row.status == CommitmentStatus.VOID
        assert "Operate" in row.status_reason
        assert session.query(AuditLog).filter_by(
            project_id=project["id"], action="commitment.void").count() == 1

    view = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN).json()
    assert view["commitment"] is None, "a void promise is not drawn as one"
    assert view["burnup"] is None
    # The breach froze new scope; with the promise gone, so is the freeze.
    more = propose(client, token, statement="A second report exists, sourced and dated")
    assert more.get("requirement_id"), more


def test_an_operate_ceo_is_not_told_it_may_sign_a_date(client):
    """The prompt states the verbs the dial grants. Telling an Operate CEO it may
    sign a delivery date is an invitation to a refusal."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server.contextpack import constitution_prose

    for mode in ("operate", "deliver"):
        project = make_project(client, slug=f"prose-{mode}", mode=mode,
                               constitution={"commitment_authority": "ceo"})
        with db_session() as session:
            row = session.get(Project, project["id"])
            prose = constitution_prose(effective(session, row.id), row)
        assert ("commitment_propose" in prose) == (mode == "deliver"), mode


def test_one_promise_at_a_time(client):
    project, token, _, _ = committed_project(client)
    second = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert second.status_code == 409
    assert "already exists" in second.json()["error"]


# ── acceptance freezes the ground ───────────────────────────────────────────────


def test_acceptance_freezes_the_rules_and_the_scope(client):
    project, token, _, proposed = committed_project(client, scope=2)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.status == CommitmentStatus.ACCEPTED
        assert row.constitution_version == 1, "the rulebook the promise was made under"
        assert row.scope_snapshot == ["R-01", "R-02"], "the keys in force at acceptance"
        assert row.accepted_at is not None


# ── the guarantee: new scope is refused past the wall ───────────────────────────


def test_new_scope_is_refused_past_a_breached_commitment(client):
    project, token, rids, proposed = committed_project(client)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    with db_session() as session:
        session.get(Commitment, proposed["commitment_id"]).deliver_by = \
            utcnow() - timedelta(days=1)
    run_commitment_check(project)
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.status == CommitmentStatus.BREACHED
        breach_notices = session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_BREACHED, project_id=project["id"]).count()
        assert breach_notices == 1, "the breach is disclosed to the CEO exactly once"

    refused = client.post(
        "/agent/requirements",
        json={"statement": "One more thing, while nobody is counting",
              "acceptance_test": {"type": "command", "command": "test -f done.txt"},
              "est_runs": 3},
        headers=bearer(token))
    assert refused.status_code == 409
    assert "breached" in refused.json()["error"]

    # Existing work continues — the freeze is on scope, never on finishing.
    allowed = make_task(client, token, project, rids[0])
    assert allowed.status_code == 200, allowed.text


def test_at_risk_freezes_scope_and_recovery_unfreezes_it(client):
    """The rule under test is the freeze: past the promise, new scope is
    refused with the arithmetic showing, and moving the date lifts it.

    The *setup* moved when the projection got a sample floor. It used to reach
    at-risk on one met requirement — a "measured pace of 0.33/week" that was a
    single observation wearing a decimal point, and the exact arithmetic the
    floor exists to refuse. Reaching a freeze that way is no longer possible,
    and should not be: a promise frozen on the strength of one data point is
    the platform manufacturing the lateness it is warning about.

    So the project is now old enough to have a pace and has met enough to have
    measured one, and every assertion about the freeze itself is unchanged.
    """
    project, token, rids, proposed = committed_project(client, scope=6)
    # Two weeks of history, so a weekly rate is a measurement rather than a
    # division by a window the project has not lived through.
    backdate_project(project["id"], hours=14 * 24)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=2))

    # Two met over a fortnight is 1.0/week; four still open therefore land about
    # four weeks out, against a two-day promise.
    fabricate_met(project["id"], rids[0])
    fabricate_met(project["id"], rids[1])
    run_commitment_check(project)
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.status == CommitmentStatus.AT_RISK
        assert row.projected_done_at > row.deliver_by

    refused = client.post(
        "/agent/requirements",
        json={"statement": "Ambition, unpriced and late",
              "acceptance_test": {"type": "command", "command": "test -f more.txt"},
              "est_runs": 2},
        headers=bearer(token))
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert "does not hold" in error
    assert "1.0/week" in error, "the refusal must show its arithmetic"

    # The chairman moves the date past the projection; the freeze lifts on the
    # spot — through the same producer the janitor uses, not a hand edit.
    moved = client.post(
        f"/admin/commitments/{proposed['commitment_id']}/move",
        json={"deliver_by": (utcnow() + timedelta(days=120)).isoformat(),
              "reason": "the pace is what it is; I would rather be late than lied to"},
        headers=ADMIN)
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == CommitmentStatus.ACCEPTED

    allowed = client.post(
        "/agent/requirements",
        json={"statement": "The same ambition, now inside an honest date",
              "acceptance_test": {"type": "command", "command": "test -f more.txt"},
              "est_runs": 2},
        headers=bearer(token))
    assert allowed.status_code == 200, allowed.text


# ── re-validation is state-based, and the freeze is not rewritten ───────────────


def test_an_amendment_revalidates_without_rewriting_the_freeze(client):
    project, token, _, proposed = committed_project(client)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    amend(client, project["id"], reason="fewer runs a day",
          runs_per_project_per_day=20)
    run_commitment_check(project)

    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.constitution_version == 1, \
            "what the promise was accepted under is frozen, or drift is deniable"
        assert row.projection["constitution_version"] == 2, \
            "what the janitor last saw must track the amendment"
        revalidated = session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_REVALIDATED, project_id=project["id"]).all()
        assert len(revalidated) == 1
        assert "rules moved" in revalidated[0].body


def test_a_failure_storm_crosses_the_threshold_and_revalidates(client):
    project, token, rids, proposed = committed_project(client)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    with db_session() as session:
        session.get(Requirement, rids[0]).failure_load = 5  # the default threshold
    run_commitment_check(project)
    with db_session() as session:
        revalidated = session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_REVALIDATED, project_id=project["id"]).all()
        assert len(revalidated) == 1
        assert "died against the plan" in revalidated[0].body
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.projection["failure_load"] == 5, \
            "the baseline advances when the trigger fires, so it fires per storm, not per tick"

    # The same load again does not re-fire: state-based means diffed, not nagged.
    run_commitment_check(project)
    with db_session() as session:
        assert session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_REVALIDATED, project_id=project["id"]).count() == 1


def test_accepting_scope_into_a_live_commitment_revalidates(client):
    """The chairman may add scope past the freeze — that is the whole point of
    'a new requirement needs the chairman'. The platform's job is to notice
    and re-project, not to stop him."""
    project, token, _, proposed = committed_project(client)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    more = propose(client, token, statement="One more thing he actually wants")
    accept(client, more["requirement_id"], project)
    run_commitment_check(project)
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.scope_snapshot == ["R-01"], "the acceptance-time snapshot is frozen"
        assert row.projection["scope_keys"] == ["R-01", "R-02"]
        revalidated = session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_REVALIDATED, project_id=project["id"]).all()
        assert len(revalidated) == 1
        assert "scope changed" in revalidated[0].body


# ── met is computed, like everything else ───────────────────────────────────────


def test_the_commitment_is_met_when_the_scope_settles_true(client):
    project, token, rids, proposed = committed_project(client, scope=2)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    fabricate_met(project["id"], rids[0])
    run_commitment_check(project)   # half-done is not met, whatever else it is
    with db_session() as session:
        assert session.get(Commitment, proposed["commitment_id"]).status \
            != CommitmentStatus.MET

    fabricate_met(project["id"], rids[1])
    run_commitment_check(project)
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        assert row.status == CommitmentStatus.MET
        assert row.met_at is not None
        met_notices = session.query(Notice).filter_by(
            kind=NoticeKind.COMMITMENT_MET, project_id=project["id"]).count()
        assert met_notices == 1


# ── the screen reads the same producer ──────────────────────────────────────────


def test_the_plan_screen_carries_the_promise_and_the_burnup(client):
    project, token, rids, proposed = committed_project(client, scope=2)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    fabricate_met(project["id"], rids[0])

    view = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN)
    assert view.status_code == 200
    data = view.json()
    assert data["commitment"]["status"] == CommitmentStatus.ACCEPTED
    assert data["commitment"]["deliver_by"]
    assert data["commitment"]["projection_basis"]
    assert data["burnup"]["scope_total"] == 2
    assert [p["count"] for p in data["burnup"]["met_points"]] == [1]
