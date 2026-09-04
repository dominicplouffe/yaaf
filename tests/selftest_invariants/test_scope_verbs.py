"""Invariant selftests: the missing verbs (CEO Charter, stage A).

Before these existed, MET and CUT were terminal for every principal including
the chairman, a stage could never be moved, an estimate could never honestly
grow, a dependency edge was a one-way ratchet, and chairman feedback could not
be executed by anyone but the chairman's own hands.

The lines these tests hold:

  * reopen is the chairman's undo — and because the open stage is derived,
    reopening a requirement reopens its stage with zero extra machinery
  * restage and reestimate move only what they say; the bias multiplier is
    always scored against the FIRST estimate
  * no agent route exists for any of these verbs — the CEO reaches them only
    through a directive citing the chairman's own boardroom message
  * the ask verb: asking is ask_chairman, not an override, and the cadence
    has never held it
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    AuditLog,
    Message,
    Project,
    ProjectMode,
    Requirement,
    RequirementEdge,
    RequirementStatus,
    Run,
    RunStatus,
    utcnow,
)
from yaaf.server import policy

CMD_TEST = {"type": "command", "command": "test -s out/report.pdf"}


def deliver_project(client, slug: str = "verbs", **kwargs) -> dict:
    return make_project(client, slug=slug, mode=ProjectMode.DELIVER, **kwargs)


def ceo_token(project: dict) -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return token


def add_requirement(client, project: dict, statement="A thing is true at the end",
                    stage=1, est_runs=5) -> dict:
    """Chairman-authored scope: accepted at birth, no janitor tick needed."""
    response = client.post(
        f"/admin/projects/{project['id']}/requirements",
        json={"statement": statement, "acceptance_test": CMD_TEST,
              "stage": stage, "est_runs": est_runs},
        headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def settle(requirement_id: str, status: str = RequirementStatus.MET) -> None:
    """Settle a requirement the way the platform does — directly in the DB,
    because no route may do it, which is the point."""
    with db_session() as session:
        row = session.get(Requirement, requirement_id)
        row.status = status
        if status == RequirementStatus.MET:
            row.met_at = utcnow()
        policy.record_requirement_event(session, row, status, "checker")


def chairman_says(project: dict, body: str) -> str:
    """A real chairman message in the boardroom, citable by a directive."""
    message_id = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        chair = session.get(Project, project["id"]).chairman_user_id
        # channel lookup needs the ORM, not the observe API, to keep this terse
        from yaaf.core.models import Channel, ChannelKind

        channel = session.query(Channel).filter(
            Channel.project_id == project["id"],
            Channel.kind == ChannelKind.BOARDROOM).one()
        session.add(Message(id=message_id, project_id=project["id"],
                            channel_id=channel.id, sender_user_id=chair, body=body))
    return message_id


def file_action(client, project: dict, message_id: str, action: dict,
                reason: str = "The chairman ordered this in the boardroom."):
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/directives", headers=bearer(token),
                       json={"from_message_id": message_id, "action": action,
                             "reason": reason})


# ── reopen ──────────────────────────────────────────────────────────────────────


def test_reopen_undoes_a_settlement_and_reopens_the_stage(client):
    project = deliver_project(client)
    early = add_requirement(client, project, "stage one thing", stage=1)
    add_requirement(client, project, "stage two thing", stage=2)
    settle(early["id"], RequirementStatus.MET)
    with db_session() as session:
        assert policy.open_stage(session, project["id"]) == 2

    reopened = client.post(f"/admin/requirements/{early['id']}/reopen",
                           json={"reason": "the verdict was wrong"}, headers=ADMIN)
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["status"] == RequirementStatus.PENDING
    assert reopened.json()["was"] == RequirementStatus.MET
    with db_session() as session:
        # The stage reopened by derivation — no stage machinery exists to update.
        assert policy.open_stage(session, project["id"]) == 1
        row = session.get(Requirement, early["id"])
        assert row.met_at is None and row.met_by_run_id is None


def test_reopen_keeps_the_settlement_in_the_event_log(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    settle(req["id"], RequirementStatus.CUT)
    assert client.post(f"/admin/requirements/{req['id']}/reopen",
                       json={"reason": "cut in error"}, headers=ADMIN).status_code == 200
    from yaaf.core.models import RequirementEvent

    with db_session() as session:
        statuses = [e.to_status for e in session.query(RequirementEvent).filter(
            RequirementEvent.requirement_id == req["id"]).order_by(RequirementEvent.at)]
    assert RequirementStatus.CUT in statuses
    assert statuses[-1] == RequirementStatus.PENDING
    with db_session() as session:
        row = session.get(Requirement, req["id"])
        assert row.cut_reason is None and row.cut_at is None


def test_only_settled_scope_can_reopen_and_r00_never(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    refused = client.post(f"/admin/requirements/{req['id']}/reopen",
                          json={"reason": "still open though"}, headers=ADMIN)
    assert refused.status_code == 409
    with db_session() as session:
        plan = policy.plan_requirement(session, project["id"])
        plan_id = plan.id
    gate = client.post(f"/admin/requirements/{plan_id}/reopen",
                       json={"reason": "poke the gate"}, headers=ADMIN)
    assert gate.status_code == 409


def test_no_agent_route_reopens_anything(client):
    """The verb exists for the chairman (and through his cited directives),
    never as a bare agent route."""
    project = deliver_project(client)
    req = add_requirement(client, project)
    settle(req["id"])
    token = ceo_token(project)
    response = client.post(f"/agent/requirements/{req['id']}/reopen",
                           json={"reason": "I want it back"}, headers=bearer(token))
    assert response.status_code in (404, 405)


# ── restage / reestimate ────────────────────────────────────────────────────────


def test_restage_moves_an_open_requirement_and_refuses_a_settled_one(client):
    project = deliver_project(client)
    req = add_requirement(client, project, stage=2)
    moved = client.post(f"/admin/requirements/{req['id']}/restage",
                        json={"stage": 1, "reason": "should have been first"},
                        headers=ADMIN)
    assert moved.status_code == 200, moved.text
    assert moved.json() == {"id": req["id"], "key": req["key"], "stage": 1, "was": 2}

    settle(req["id"])
    refused = client.post(f"/admin/requirements/{req['id']}/restage",
                          json={"stage": 3, "reason": "history is history"},
                          headers=ADMIN)
    assert refused.status_code == 409


def test_reestimate_moves_the_number_but_never_the_scored_one(client):
    project = deliver_project(client)
    req = add_requirement(client, project, est_runs=5)
    revised = client.post(f"/admin/requirements/{req['id']}/reestimate",
                          json={"est_runs": 15, "reason": "the data is 3x dirtier"},
                          headers=ADMIN)
    assert revised.status_code == 200, revised.text
    assert revised.json()["est_runs"] == 15
    assert revised.json()["est_runs_first"] == 5

    with db_session() as session:
        row = session.get(Requirement, req["id"])
        assert (row.est_runs, row.est_runs_first) == (15, 5)


def test_the_bias_multiplier_scores_the_first_estimate(client):
    """Reestimating 5 → 15 with 15 actual runs would read as perfectly
    calibrated if the ratio read the revision. It reads the original: 3x."""
    project = deliver_project(client)
    keys = []
    for n in (1, 2):
        req = add_requirement(client, project, f"deliverable number {n} exists", est_runs=5)
        keys.append(req["id"])
        client.post(f"/admin/requirements/{req['id']}/reestimate",
                    json={"est_runs": 15, "reason": "grew on contact with reality"},
                    headers=ADMIN)
        with db_session() as session:
            for _ in range(15):
                session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                                agent_id=project["ceo_agent_id"], trigger={},
                                requirement_id=req["id"], status=RunStatus.DONE,
                                started_at=utcnow(), finished_at=utcnow()))
        settle(req["id"])
    with db_session() as session:
        ratio, basis = policy.estimate_bias(session, project["id"])
    assert ratio == 3.0, basis


# ── unsequence ──────────────────────────────────────────────────────────────────


def test_unsequence_removes_an_edge_and_unblocks_the_work(client):
    project = deliver_project(client)
    first = add_requirement(client, project, "the blocker", stage=1)
    second = add_requirement(client, project, "the blocked", stage=1)
    with db_session() as session:
        session.add(RequirementEdge(id=ids.new_id(ids.REQUIREMENT_EDGE),
                                    project_id=project["id"],
                                    from_requirement_id=first["id"],
                                    to_requirement_id=second["id"]))
    with db_session() as session:
        blocked = policy.unmet_dependencies(session, session.get(Requirement, second["id"]))
        assert [r.id for r in blocked] == [first["id"]]

    removed = client.post("/admin/requirement_edges/remove",
                          json={"from_requirement_id": first["id"],
                                "to_requirement_id": second["id"],
                                "reason": "they are independent after all"},
                          headers=ADMIN)
    assert removed.status_code == 200, removed.text
    with db_session() as session:
        assert not policy.unmet_dependencies(
            session, session.get(Requirement, second["id"]))

    again = client.post("/admin/requirement_edges/remove",
                        json={"from_requirement_id": first["id"],
                              "to_requirement_id": second["id"],
                              "reason": "already gone"},
                        headers=ADMIN)
    assert again.status_code == 404


# ── scope directives: chairman orders execute ───────────────────────────────────


def test_a_chairman_order_executes_through_a_cited_directive(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    said = chairman_says(project, f"cut {req['key']} — we do not need it")
    done = file_action(client, project, said,
                       {"kind": "cut", "requirement_key": req["key"]})
    assert done.status_code == 200, done.text
    with db_session() as session:
        assert session.get(Requirement, req["id"]).status == RequirementStatus.CUT
        # Echoed back threaded to his own words, where a misread is one
        # sentence from correction.
        echo = session.query(Message).filter(
            Message.thread_id == said,
            Message.sender_agent_id == project["ceo_agent_id"]).one()
        assert "cut " + req["key"] in echo.body
        # And audited as a scope directive.
        assert session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "scope.directive").count() == 1


def test_a_reopen_order_round_trips(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    settle(req["id"])
    said = chairman_says(project, f"{req['key']} was not actually done — reopen it")
    done = file_action(client, project, said,
                       {"kind": "reopen", "requirement_key": req["key"]})
    assert done.status_code == 200, done.text
    with db_session() as session:
        assert session.get(Requirement, req["id"]).status == RequirementStatus.PENDING


def test_an_order_cannot_cite_the_ceos_own_message(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    posted = client.post("/agent/messages", headers=bearer(token),
                         json={"channel_id": channel_of(client, project["id"], "boardroom"),
                               "body": "I think we should cut it"})
    assert posted.status_code == 200, posted.text
    refused = file_action(client, project, posted.json()["id"],
                          {"kind": "cut", "requirement_key": req["key"]})
    assert refused.status_code == 422
    assert "not the chairman's own words" in refused.text


def test_an_order_cannot_cite_a_platform_boardroom_line(client):
    """Platform-authored boardroom lines (a filed need, an assist announcement,
    the session-ended notice) carry NEITHER sender. A CEO must not be able to
    cause one and then cite it as the chairman's own order — that would let it
    execute scope actions recorded as the chairman's act."""
    project = deliver_project(client)
    req = add_requirement(client, project)
    message_id = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        from yaaf.core.models import Channel, ChannelKind

        channel = session.query(Channel).filter(
            Channel.project_id == project["id"],
            Channel.kind == ChannelKind.BOARDROOM).one()
        # No sender_user_id and no sender_agent_id: exactly what
        # _boardroom_system_line writes.
        session.add(Message(id=message_id, project_id=project["id"],
                            channel_id=channel.id, body="📋 NEED: an Anthropic key"))
    refused = file_action(client, project, message_id,
                          {"kind": "cut", "requirement_key": req["key"]})
    assert refused.status_code == 422
    assert "not the chairman's own words" in refused.text


def test_an_order_with_broken_fields_is_refused_not_crashed(client):
    """The action dict arrives from a model. A stage of 0, a string where a
    number goes, a 'yes' where a bool goes — each must be a 422 naming the
    field, never a 500 from deep inside an apply helper."""
    project = deliver_project(client)
    req = add_requirement(client, project)
    said = chairman_says(project, "move it, grow the estimate, judge it")
    for action in (
        {"kind": "restage", "requirement_key": req["key"], "stage": 0},
        {"kind": "restage", "requirement_key": req["key"], "stage": "later"},
        {"kind": "reestimate", "requirement_key": req["key"], "est_runs": "lots"},
        {"kind": "reestimate", "requirement_key": req["key"], "est_runs": -3},
        {"kind": "verdict", "requirement_key": req["key"], "ok": "yes"},
    ):
        refused = file_action(client, project, said, action)
        assert refused.status_code == 422, f"{action}: {refused.text}"
    with db_session() as session:
        row = session.get(Requirement, req["id"])
        assert (row.stage, row.est_runs) == (1, 5), "nothing moved"


def test_an_order_must_be_a_known_verb_with_its_fields(client):
    project = deliver_project(client)
    req = add_requirement(client, project)
    said = chairman_says(project, "do something about it")
    unknown = file_action(client, project, said,
                          {"kind": "delete_project", "requirement_key": req["key"]})
    assert unknown.status_code == 422
    incomplete = file_action(client, project, said,
                             {"kind": "restage", "requirement_key": req["key"]})
    assert incomplete.status_code == 422
    both = client.post(
        "/agent/directives", headers=bearer(ceo_token(project)),
        json={"from_message_id": said, "reason": "carrying two shapes at once",
              "changes": {"chairman_report_interval_hours": 4},
              "action": {"kind": "cut", "requirement_key": req["key"]}})
    assert both.status_code == 422


# ── the ask verb ────────────────────────────────────────────────────────────────


def test_asking_is_not_an_override_and_the_cadence_never_held_it(client):
    """The override existed, worked, and bought nothing.

    'question' passed the cadence and left a message — no clock, no default, no
    row on his desk. A CEO used it exactly as designed and still sat six hours
    on a finished deliverable, because a message is not a thing anyone can be
    woken by. So the override is gone, it names its replacement, and the
    replacement was never a status in the first place.
    """
    project = make_project(
        client, slug="asks",
        constitution={"chairman_report_interval_hours": 4, "timezone": "UTC"})
    channel = channel_of(client, project["id"], "boardroom")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    first = client.post("/agent/messages", headers=bearer(token),
                        json={"channel_id": channel, "body": "status: all fine"})
    assert first.status_code == 200, first.text

    _, token = live_token(project["id"], project["ceo_agent_id"])
    silent = client.post("/agent/messages", headers=bearer(token),
                         json={"channel_id": channel, "body": "another status"})
    assert silent.status_code == 429

    _, token = live_token(project["id"], project["ceo_agent_id"])
    stale = client.post("/agent/messages", headers=bearer(token),
                        json={"channel_id": channel, "override": "question",
                              "body": "Do you want the audit deck in French too?"})
    assert stale.status_code == 422
    assert "ask_chairman" in stale.text

    # The verb it names goes through against the same closed slot.
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": "Audit deck in French too?",
        "body": "Costs one extra pass. Your call before we typeset.",
        "answer_by_hours": 6,
        "default_action": "Ship English only and offer French as a follow-up.",
    })
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        overrides = session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "boardroom.cadence_override").all()
        assert overrides == [], "asking never was a break-glass claim"
        opened = session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "ask.opened").all()
        assert len(opened) == 1
