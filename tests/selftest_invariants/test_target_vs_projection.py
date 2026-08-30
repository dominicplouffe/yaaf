"""Invariant selftest: a target date never reaches the CEO without its arithmetic.

A CEO handed a date and no arithmetic treats it as physics. One that had
already shipped every finish-line condition on day one wrote this, and then
went quiet for thirteen hours:

    CEO:      Only R-07 remains open — the dogfooding weekly-note cadence. It's
              calendar-gated … Given the 6 Nov commitment, I'm not pulling that
              timeline forward artificially; it accrues honestly week by week.
    CHAIRMAN: I haven't see any evidence that you will wow me anytime soon and
              nothing has been done on the project in 13 hours. What is going on?

The install that produced that had a measured record of eight commitments, all
met the day they were made, against promises of 0, 0, 0, 0, 2, 42, 106 and 127
days. The dates were not estimates and nothing ever reconciled them; they only
paced the work.

So a target arrives beside the platform's own projection, with the gap named
and the provenance attached — because a booking window really does bind, and a
date somebody liked the sound of is a ceiling, never a pace.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, live_token, make_project, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Run, utcnow
from yaaf.server.contextpack import build_context_pack


def _deliver_with_target(client, slug: str, days: int, source: dict) -> dict:
    client.post("/admin/users",
                json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    created = client.post("/admin/projects", json={
        "slug": slug, "name": slug.title(), "goal": "Ship the thing.",
        "chairman_email": "chair@example.com", "ceo_role_slug": "ceo",
        "mode": "deliver",
        "deadline": (utcnow() + timedelta(days=days)).isoformat(),
        "deadline_source": source,
        "teams": [{"role_slug": "worker", "name": "A", "employees": ["E1"]}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
    project = created.json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    return project


def _pack(project: dict) -> dict:
    agent_id = project["ceo_agent_id"]
    run_id, _ = live_token(project["id"], agent_id)
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id), session.get(Agent, agent_id),
            session.get(Project, project["id"]),
        )


def test_a_chairmans_wish_is_named_a_ceiling_and_never_a_pace(client):
    project = _deliver_with_target(
        client, "wished", 127, {"kind": "chairman", "note": "before the holidays"})

    target = _pack(project)["target"]

    assert target["is_external_fact"] is False
    assert "TARGET somebody chose" in target["note"]
    assert "CEILING, never a pace" in target["note"]
    assert "do not invent calendar gates" in target["note"], (
        "the exact behaviour that cost thirteen silent hours"
    )


def test_a_date_the_world_imposes_is_named_a_fact(client):
    project = _deliver_with_target(
        client, "booked", 30,
        {"kind": "external", "note": "the client's board meeting",
         "url": "https://example.com/agenda"})

    target = _pack(project)["target"]

    assert target["is_external_fact"] is True
    assert "FACT the outside world imposes" in target["note"]
    assert "CEILING, never a pace" not in target["note"], (
        "a real deadline is not advice to go faster; it is a wall"
    )
    assert target["source"]["url"] == "https://example.com/agenda"


def test_the_target_always_arrives_with_the_platforms_own_projection(client):
    """The half that was missing. A date with no arithmetic beside it is the
    only number the CEO has, so it becomes the plan."""
    project = _deliver_with_target(
        client, "arith", 90, {"kind": "chairman", "note": "feels right"})

    target = _pack(project)["target"]

    assert "projected_done_at" in target
    assert target["projection_basis"], "a projection must say where it came from"


def test_a_wide_gap_is_stated_in_days_rather_than_left_to_be_noticed(client):
    project = _deliver_with_target(
        client, "gappy", 120, {"kind": "chairman", "note": "no reason"})

    target = _pack(project)["target"]

    if target["projected_done_at"] is not None:
        assert target["days_of_slack"] is not None
        assert "INSIDE" in target["note"] or "PAST" in target["note"]


def test_a_project_with_no_target_carries_no_block(client):
    """Most projects have no date at all, and a block explaining the absence of
    one is pure token cost on every wake."""
    project = make_project(client)

    assert "target" not in _pack(project)


def test_an_operate_desk_is_untouched_by_any_of_this(client):
    """The desks work. Their dates live on cycles, which already carry their
    own due-by and provenance, and nothing here may reach them."""
    project = make_project(client, slug="desk", mode="operate")

    pack = _pack(project)

    assert "target" not in pack
    with db_session() as session:
        assert session.get(Project, project["id"]).deadline is None


# ── the founder: where the invented dates came from ─────────────────────────────


def test_the_founder_cannot_draft_a_deliver_date_with_no_provenance():
    """A draft the create endpoint would reject is worse than no draft — the
    review screen would show a project that cannot be created. `validate` uses
    the endpoint's own model, which cannot express "required only on Deliver",
    so the date rule is checked alongside it."""
    from yaaf.founder.tools import as_bundle, validate

    problems = validate(as_bundle({
        "slug": "dated", "name": "X", "goal": "Ship the thing properly.", "mode": "deliver",
        "deadline": "2027-01-02T00:00:00+00:00",
    }))

    assert any("deadline_source" in p for p in problems), problems
    assert any("write no date" in p for p in problems), (
        "the refusal must name the option the founder keeps missing"
    )


def test_the_founder_may_draft_a_sourced_deliver_date():
    from yaaf.founder.tools import as_bundle, validate

    problems = validate(as_bundle({
        "slug": "dated", "name": "X", "goal": "Ship the thing properly.", "mode": "deliver",
        "deadline": "2027-01-02T00:00:00+00:00",
        "deadline_source": {"kind": "external", "note": "the client's launch"},
    }))

    assert problems == []


def test_the_founder_may_still_draft_an_undated_project():
    """The outcome this whole change is trying to make ordinary."""
    from yaaf.founder.tools import as_bundle, validate

    assert validate(as_bundle({
        "slug": "undated", "name": "X", "goal": "Ship the thing properly.", "mode": "deliver",
    })) == []


def test_the_founder_prompt_states_the_measured_pace():
    """The founder invented ten-week schedules because nothing ever told it how
    fast agents actually are. If that calibration goes missing, the habit comes
    straight back."""
    from yaaf.founder.prompt import SYSTEM

    assert "under a day" in SYSTEM
    assert "127" in SYSTEM, "the measured record, not an adjective"
    assert "do not write a date at all" in SYSTEM
