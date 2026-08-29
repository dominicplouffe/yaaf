"""Invariant selftests: the authority dial (CEO Charter, stage B).

Authority is configuration; integrity is structure. Four constitution switches
decide WHO holds each verb — plan_authority, scope_authority,
commitment_authority, verdict_policy. What never moves, at any dial setting:
every exercise runs through the same apply helpers as the chairman's own
buttons, every one is audited and disclosed to the chairman, the chairman's
routes stay open, and the CEO still cannot touch the dial itself (directives
are scoped, amendments are his).

"Supervised" below means the five rules set to `chairman`, which is what the
`make_project` fixture stamps and what a chairman gets by clicking that card.
It is not what silence gets: a spec that names none of the five is created
autonomous, and the last section here holds that line.

The lines these tests hold:

  * under the supervised dial, every self-exercise is a 403 naming the paths
  * under `ceo`, self-acceptance opens the plan gate without a chairman click
  * a self-judged verdict closes the requirement AND is disclosed, every time
  * commitment_authority: ceo adopts the platform's date — still never writes one
  * the dial is not the CEO's to turn: a directive touching it is refused
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project, seed_role
from yaaf.core.constitution import AUTONOMY_PRESETS, preset_name
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


# ── the supervised dial: every verb stays the chairman's ────────────────


def test_under_the_supervised_dial_every_self_exercise_is_refused(client):
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


def test_under_the_supervised_dial_verdicts_stay_the_chairmans(client):
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


def test_under_the_supervised_dial_a_proposal_still_waits_for_the_chairman(client):
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


# ── the verb the dial promised but did not have ─────────────────────────────────
#
# A granted authority with no verb behind it is not an authority. Under the
# autonomous dial the CEO could cut scope and could finish work, but if the
# honest answer to a slipped promise was "this date cannot hold" its only
# remaining move was an owner_decision approval — a blocker, with a
# default-deny clock, on the desk of the one person who had explicitly
# delegated exactly this. These tests hold the fix and its limits.


def slipped(client, slug: str, dial: dict):
    """An autonomous-by-default project whose promise has stopped holding.

    Three requirements in scope, one met, a two-day promise: the measured pace
    lands the projection weeks past the date, which is the state the whole of
    this section is about.
    """
    from datetime import timedelta

    from tests.selftest_invariants.test_commitments import (
        committed_project,
        fabricate_met,
        run_commitment_check,
    )
    from yaaf.core.models import utcnow

    project, tok, rids, proposed = committed_project(
        client, slug=slug, scope=3, constitution=dial)
    cid, tight = proposed["commitment_id"], utcnow() + timedelta(days=2)
    # Under the autonomous dial the CEO has already signed, so there is nothing
    # left to accept — the chairman tightens the date instead. Both paths end
    # in the same place: a live promise two days out that the pace cannot keep.
    if proposed["status"] == CommitmentStatus.PROPOSED:
        signed = client.post(f"/admin/commitments/{cid}/accept",
                             json={"deliver_by": tight.isoformat()}, headers=ADMIN)
    else:
        signed = client.post(
            f"/admin/commitments/{cid}/move",
            json={"deliver_by": tight.isoformat(),
                  "reason": "I want this in two days; show me what that costs."},
            headers=ADMIN)
    assert signed.status_code == 200, signed.text
    fabricate_met(project["id"], rids[0])
    run_commitment_check(project)
    return project, tok, cid


def test_an_autonomous_ceo_resets_a_slipped_date_itself(client):
    """The whole point: nothing lands on the chairman's desk, and the new date
    is the platform's arithmetic rather than anything the CEO chose."""
    from yaaf.core.models import Commitment

    project, tok, cid = slipped(client, "reset-ok", AUTONOMOUS)
    with db_session() as session:
        row = session.get(Commitment, cid)
        assert row.status == CommitmentStatus.AT_RISK
        projected, promised = row.projected_done_at, row.deliver_by

    response = client.post(
        "/agent/commitments/reset",
        json={"reason": "Three requirements at the measured pace do not fit two days. "
                        "The scope is right; the date was wrong."},
        headers=bearer(tok))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resets"] == 1

    with db_session() as session:
        row = session.get(Commitment, cid)
        # The date moved to the projection's own day, not to a number the CEO
        # liked — that is the integrity rule surviving the authority grant.
        assert row.deliver_by > promised
        assert row.deliver_by.date() == projected.date()
        assert row.status == CommitmentStatus.ACCEPTED, \
            "resetting to the projection must lift the freeze it caused"

    # New scope flows again, because the promise is honest again.
    allowed = client.post(
        "/agent/requirements",
        json={"statement": "The next deliverable, inside a date that holds",
              "acceptance_test": CMD_TEST, "est_runs": 2},
        headers=bearer(tok))
    assert allowed.status_code == 200, allowed.text


def test_the_reset_is_audited_and_disclosed_with_a_running_count(client):
    """Autonomy is never quiet, and a promise reset again and again is the one
    thing a chairman most needs counted for him."""
    project, tok, cid = slipped(client, "reset-count", AUTONOMOUS)
    reason = "The pace is the pace; I would rather the date said so."
    first = client.post("/agent/commitments/reset", json={"reason": reason},
                        headers=bearer(tok))
    assert first.status_code == 200, first.text

    with db_session() as session:
        rows = session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "commitment.self_move").all()
        assert len(rows) == 1
        assert rows[0].detail["reason"] == reason
        assert rows[0].detail["reset_count"] == 1
        assert rows[0].detail["from"] != rows[0].detail["to"]
        assert rows[0].actor.startswith("agent:")


def test_the_reset_is_refused_while_the_promise_still_holds(client):
    """A date moved while it is still good is a date that means nothing."""
    from tests.selftest_invariants.test_commitments import committed_project

    project, tok, _, proposed = committed_project(
        client, slug="reset-early", scope=1, constitution=AUTONOMOUS)
    from datetime import timedelta

    from yaaf.core.models import utcnow
    moved = client.post(
        f"/admin/commitments/{proposed['commitment_id']}/move",
        json={"deliver_by": (utcnow() + timedelta(days=400)).isoformat(),
              "reason": "Take all the room you need; I care about it being right."},
        headers=ADMIN)
    assert moved.status_code == 200, moved.text

    refused = client.post(
        "/agent/commitments/reset",
        json={"reason": "I would simply prefer more room than the plan needs."},
        headers=bearer(tok))
    assert refused.status_code == 409
    assert "still holds" in refused.json()["error"]


def test_under_the_supervised_dial_the_reset_is_the_chairmans(client):
    project, tok, cid = slipped(client, "reset-denied", {})
    refused = client.post(
        "/agent/commitments/reset",
        json={"reason": "The date cannot hold and I would like to fix it myself."},
        headers=bearer(tok))
    assert refused.status_code == 403
    error = refused.json()["error"]
    assert "commitment_authority" in error
    assert "directive" in error, "a refusal must name the paths that remain"


def test_the_reset_is_still_not_a_date_the_ceo_writes(client):
    """The authority grant moves the signature, never the arithmetic."""
    project, tok, cid = slipped(client, "reset-smuggle", AUTONOMOUS)
    smuggled = client.post(
        "/agent/commitments/reset",
        json={"reason": "A perfectly reasonable-sounding reason.",
              "deliver_by": "2031-01-01T00:00:00Z"},
        headers=bearer(tok))
    # Extra fields die at the schema; if they did not, the date must still be
    # the projection's, never the one that was smuggled.
    from yaaf.core.models import Commitment
    if smuggled.status_code == 200:
        with db_session() as session:
            assert session.get(Commitment, cid).deliver_by.year < 2031


def test_only_the_ceo_may_reset_the_promise(client):
    project, tok, cid = slipped(client, "reset-worker", AUTONOMOUS)
    from tests.conftest import agent_by_name

    refused = client.post(
        "/agent/commitments/reset", json={"reason": "I think the date should move."},
        headers=bearer(token(project, agent_by_name(project, "Lead"))))
    assert refused.status_code == 403
    assert "CEO" in refused.json()["error"]


# ── an approval is never the answer to a decision you hold ──────────────────────


def test_an_owner_decision_is_refused_when_the_dial_already_granted_it(client):
    """The bug this whole section exists for: a fully autonomous project could
    still park a direction decision on its chairman, with a clock on it."""
    project = project_with(client, dial=AUTONOMOUS, slug="appr-auto")
    refused = client.post(
        "/agent/approvals",
        json={"kind": "owner_decision",
              "title": "Reset the delivery date, keep the scope",
              "body": "My recommendation on the at-risk commitment."},
        headers=bearer(token(project)))
    assert refused.status_code == 403
    error = refused.json()["error"]
    assert "commitment_reset" in error, "the refusal must name the verb to use instead"
    assert "scope_act" in error
    assert "boardroom" in error, "and the non-blocking way to ask anyway"


def test_the_undelegable_kinds_still_reach_the_chairman_under_full_autonomy(client):
    """Money, legal weight, a credential he holds the value of, and a step only
    a human can take are his at every dial setting — the refusal above must not
    have swept them up."""
    project = project_with(client, dial=AUTONOMOUS, slug="appr-keep")
    for kind in sorted(policy.UNDELEGABLE_APPROVALS):
        response = client.post(
            "/agent/approvals",
            json={"kind": kind, "title": f"A {kind} decision",
                  "body": "Something the constitution does not let you delegate.",
                  **({"amount_usd": 900.0} if kind == "spend" else {})},
            headers=bearer(token(project)))
        assert response.status_code == 200, f"{kind}: {response.text}"


def test_under_the_supervised_dial_an_owner_decision_is_exactly_as_before(client):
    project = project_with(client, dial={}, slug="appr-super")
    response = client.post(
        "/agent/approvals",
        json={"kind": "owner_decision", "title": "A genuine direction question",
              "body": "This one really is the owner's."},
        headers=bearer(token(project)))
    assert response.status_code == 200, response.text


def test_the_slip_notice_tells_the_ceo_what_it_actually_holds(client):
    """The notice used to say "propose cuts to the chairman" at every setting —
    advice that is false on a project whose chairman delegated the scope and
    the date, and which pointed at the one move that could still stall it."""
    from yaaf.core.constitution import ConstitutionDoc

    autonomous = policy.slip_moves(ConstitutionDoc(**AUTONOMOUS))
    assert "commitment_reset" in autonomous
    assert "scope_act" in autonomous
    assert "Do NOT file an approval" in autonomous

    supervised = policy.slip_moves(ConstitutionDoc())
    assert "propose cuts to the chairman" in supervised
    assert "commitment_reset" not in supervised


def test_the_quiet_board_names_the_right_party(client):
    """The banner above every tab said "your call — waiting on you" for scope
    the CEO had been handed. Same three causes, same ladder; the sentence just
    has to read the constitution before it names a bottleneck."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project, utcnow
    from yaaf.server import dispatcher

    def gate(project: dict) -> str | None:
        # The reachability ladder itself, rather than the whole dispatcher:
        # this test is about which party a cause is attributed to, and the
        # outer ladder would answer "agents_busy" first because minting a run
        # token to propose anything is what makes an agent busy.
        with db_session() as session:
            row = session.get(Project, project["id"])
            return dispatcher._board_reachability(
                session, row, effective(session, row.id), utcnow())["unreachable"]

    # Supervised: a proposal really is his, and the ladder says so.
    supervised = project_with(client, dial={}, slug="gate-super")
    propose(client, supervised)
    assert gate(supervised) == "awaiting_chairman"

    # Autonomous: the same proposal is the CEO's own inbox.
    autonomous = project_with(client, dial=AUTONOMOUS, slug="gate-auto")
    propose(client, autonomous)
    assert gate(autonomous) == "awaiting_ceo"

    from tests.selftest_invariants.test_idle_legibility import _synthetic

    brief = dispatcher._activity_brief(_synthetic("awaiting_ceo"))
    assert "waiting on you" in brief["detail"], "it must say so in as many words"
    assert brief["banner"] is False, "a decision that is not yours is not a banner"


# ── what silence gets you ───────────────────────────────────────────────────


def _bare_project(client, slug: str, constitution: dict | None = None) -> dict:
    """A project created the way a spec file, the MCP surface and the Founder's
    draft create one: through the endpoint, without the fixture's supervised
    stamp."""
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    body = {"slug": slug, "name": slug.title(),
            "goal": "Prove the framework works end to end.",
            "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
            "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"]}]}
    if constitution is not None:
        body["constitution"] = constitution
    response = client.post("/admin/projects", json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_spec_with_no_opinion_about_the_dial_is_created_autonomous(client):
    """The chairman who describes a project in a paragraph wants it to run, not
    to spend the week clicking Accept on his own idea. Silence used to mean
    supervised, which is the one reading nobody ever asked for."""
    created = _bare_project(client, "silent")
    assert created["constitution"]["plan_authority"] == "ceo"
    assert created["constitution"]["scope_authority"] == "ceo"
    assert created["constitution"]["commitment_authority"] == "ceo"
    assert created["constitution"]["verdict_policy"] == "ceo_judgment"
    assert created["constitution"]["capability_authority"] == "ceo"
    assert preset_name(created["constitution"]) == "autonomous"


def test_a_spec_that_asks_for_supervised_gets_supervised(client):
    """The override is a line in the spec, not an argument with the platform."""
    created = _bare_project(client, "asked", AUTONOMY_PRESETS["supervised"])
    assert preset_name(created["constitution"]) == "supervised"


def test_a_dial_set_rule_by_rule_is_left_exactly_as_written(client):
    """Naming even one of the five is somebody choosing deliberately. Stamping
    the other four with a default would be the silent overwrite the New-project
    form already refuses to make."""
    created = _bare_project(client, "mixed", {"verdict_policy": "ceo_judgment"})
    assert created["constitution"]["verdict_policy"] == "ceo_judgment"
    assert created["constitution"]["plan_authority"] == "chairman"
    assert created["constitution"]["scope_authority"] == "chairman"
    assert preset_name(created["constitution"]) is None


def test_the_default_never_reaches_a_constitution_already_stored(client):
    """The field defaults stay `chairman` on purpose: they are what an omitted
    rule means for a document read back from the database. A project stored
    before the dial existed must not become autonomous by being loaded."""
    from yaaf.core.constitution import ConstitutionDoc

    doc = ConstitutionDoc.model_validate({"queue_ceiling": 4})
    assert doc.plan_authority == "chairman"
    assert doc.verdict_policy == "chairman"


def test_the_pack_tells_the_ceo_who_actually_accepts_scope(client):
    """The scope note said "the chairman accepts them" at every setting. On an
    autonomous project that is false, and it is now the default — a CEO reading
    it would wait for a click nobody is coming to make."""
    from yaaf.core.models import Agent, Project, Run, RunStatus
    from yaaf.server.contextpack import build_context_pack

    def note(project: dict) -> str:
        propose(client, project)
        with db_session() as session:
            run = Run(id="run_" + project["id"][-8:], project_id=project["id"],
                      agent_id=project["ceo_agent_id"], status=RunStatus.RUNNING,
                      trigger={"reasons": []})
            session.add(run)
            session.flush()
            pack = build_context_pack(
                session, run,
                session.get(Agent, project["ceo_agent_id"]),
                session.get(Project, project["id"]))
        return pack["scope"]["note"]

    assert "the chairman accepts them" in note(project_with(client, slug="note-super"))
    assert "you accept them yourself" in note(
        project_with(client, dial=AUTONOMOUS, slug="note-auto"))


def test_the_other_knobs_a_spec_sets_survive_the_stamp(client):
    """The dial is filled in beside a spec's own rules, never instead of them."""
    created = _bare_project(client, "knobs", {"queue_ceiling": 3})
    assert created["constitution"]["queue_ceiling"] == 3
    assert created["constitution"]["plan_authority"] == "ceo"
