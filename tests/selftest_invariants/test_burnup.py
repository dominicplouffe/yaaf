"""Invariant selftests: the burn-up says only what it measured.

The chart on the Plan screen answers one question — will the work be finished
by the date you signed — and for a long time it answered it wrongly in four
compounding ways. Every one of them is a rule here, attacked over HTTP against
the real endpoint the screen reads.

  * a pace is a measurement or it is not offered: no date is projected from a
    sample too small to have measured one, and no weekly rate is stated over a
    window the project has not lived through
  * the line and the forecast count the same work — one `countable_scope`, so
    the solid line and the dashed ray drawn from its tip cannot come from two
    different datasets
  * the line can never climb above its own ceiling, which is what a burn-up of
    recurring work used to do every cycle — so recurring work has none
  * the floor refuses a bad date without refusing the promise: a CEO on a fresh
    plan can still commit, from arithmetic that says what it is

The bug this file exists for was visible in the product's own fire drill, which
raised a false "commitment at risk" on every run — a pace of 0.33/week measured
over three weeks a seconds-old project had not lived through.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, backdate_project, bearer, make_project
from tests.selftest_invariants.test_commitments import (
    chairman_accept,
    committed_project,
    fabricate_met,
)
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.models import Recurrence, Requirement, utcnow
from yaaf.server.velocity import MIN_MET_FOR_PACE


def plan_view(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/plan", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


# ── a pace is a measurement, or it is not offered ──────────────────────────────


def test_one_settled_requirement_is_not_a_measured_pace(client):
    """The reported bug, as arithmetic.

    One requirement landing used to produce "the measured pace of 0.33/week"
    and, from that, a confident calendar date six weeks out — a number that
    does not stay on the screen. It flips the commitment to AT_RISK and
    hard-refuses the CEO's next requirement with a 409, so a promise was being
    frozen on the strength of a single observation.

    The projection may still name a date here; what it may not do is claim to
    have measured a pace it has not.
    """
    project, _, rids, proposed = committed_project(client, scope=4)
    backdate_project(project["id"], hours=14 * 24)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    fabricate_met(project["id"], rids[0])

    basis = plan_view(client, project)["commitment"]["projection_basis"]
    # "at the measured pace of" is the velocity branch's own wording. The
    # fallback ends "not a measured pace", so the substring alone proves
    # nothing — it is the claim that has to be absent, not the phrase.
    assert "at the measured pace of" not in basis, (
        f"one settled requirement was reported as a measured pace: {basis!r}"
    )
    assert "not a measured pace" in basis or "no arithmetic" in basis, (
        f"the projection must say what it is standing on; got {basis!r}"
    )


def test_enough_settled_requirements_do_make_a_pace(client):
    """The floor is a floor, not a ban — the non-vacuity half of the rule
    above. Cross MIN_MET_FOR_PACE and the projection goes back to saying it
    measured something, and shows the arithmetic it measured."""
    project, _, rids, proposed = committed_project(client, scope=6)
    backdate_project(project["id"], hours=14 * 24)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    for rid in rids[:MIN_MET_FOR_PACE]:
        fabricate_met(project["id"], rid)

    commitment = plan_view(client, project)["commitment"]
    assert "measured pace" in commitment["projection_basis"]
    assert "/week" in commitment["projection_basis"], "the basis must show its arithmetic"
    assert commitment["projected_done_at"] is not None


def test_a_young_project_is_not_scored_over_a_window_it_never_lived(client):
    """A project thirteen days old was scored as though eight days of silence
    had been measured and found empty. Dividing by the nominal three weeks
    understated the pace of every young project — pessimism that lands hardest
    on the newest work, which is what manufactured the false at-risk in the
    drill.

    Two identical projects, same requirements met, different ages: the younger
    must not be reported as the slower.
    """
    paces = {}
    for slug, age_days in (("young", 8), ("old", 60)):
        project = deliver_project(client, slug=slug)
        backdate_project(project["id"], hours=age_days * 24)
        token = ceo_token(project)
        rids = []
        for n in range(4):
            r = propose(client, token, statement=f"{slug} deliverable {n} exists")
            accept(client, r["requirement_id"], project)
            rids.append(r["requirement_id"])
        for rid in rids[:2]:
            fabricate_met(project["id"], rid)
        paces[slug] = plan_view(client, project)["velocity"]["per_week_3w"]

    assert paces["young"] > paces["old"], (
        "the same two requirements in eight days is a faster pace than in "
        f"sixty; got young={paces['young']} old={paces['old']}"
    )


def test_under_a_week_old_no_weekly_rate_is_claimed(client):
    """A weekly rate needs a week. Below one the honest answer is that there is
    no measured pace yet, not a rate with an error bar the screen has no way to
    draw."""
    project = deliver_project(client, slug="hours-old")
    token = ceo_token(project)
    for n in range(3):
        accept(client, propose(client, token, statement=f"Thing {n} is true")["requirement_id"],
               project)
    view = plan_view(client, project)
    assert view["velocity"]["per_week_3w"] == 0
    assert view["velocity"]["weeks_left_at_pace"] is None


# ── the floor refuses a date, never the promise ────────────────────────────────


def test_the_sample_floor_does_not_stop_a_ceo_committing(client):
    """The one way this change could have broken a working verb.

    `policy.check_commitment_propose` refuses the whole proposal when the
    projection is not known, so a floor that quietly pushed fresh projects into
    that branch would have left every new CEO unable to promise anything. It
    must fall through to the plan's own arithmetic instead — a date that says
    out loud it is a best case rather than a measurement.
    """
    project, _, _, proposed = committed_project(client, scope=3)
    assert proposed["commitment_id"]
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))

    commitment = plan_view(client, project)["commitment"]
    assert commitment["projected_done_at"] is not None, \
        "a fresh plan must still get a date, from arithmetic that names itself"
    assert "not a measured pace" in commitment["projection_basis"]


# ── the line and the forecast count the same work ──────────────────────────────


def test_the_line_ends_where_the_settled_count_says_it_does(client):
    """One `countable_scope` behind both. The line used to count every MET
    event bar R-00 while the pace behind the dashed ray dropped standing and
    metric requirements too — one picture, two definitions of the same word,
    and a forecast drawn from numbers the solid line beside it did not agree
    with."""
    project, _, rids, proposed = committed_project(client, scope=5)
    backdate_project(project["id"], hours=20 * 24)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    for rid in rids[:3]:
        fabricate_met(project["id"], rid)

    view = plan_view(client, project)
    burnup, velocity = view["burnup"], view["velocity"]
    assert burnup["settled"] == 3
    assert burnup["met_points"][-1]["count"] == burnup["settled"], \
        "the top of the line and the settled count are the same number"
    assert burnup["settled"] + velocity["remaining"] == burnup["scope_total"], \
        "what is settled and what is open must add up to the plan"


def test_a_standing_obligation_does_not_move_the_burn_up(client):
    """Work owed again every cycle is met again every cycle by design. Counted
    on the line it would climb forever; counted in the pace it would divide the
    remaining scope into an ever-nearer date. `velocity._met_count` has always
    excluded it — the chart had not, which is how the two came apart."""
    project, _, rids, proposed = committed_project(client, scope=4)
    backdate_project(project["id"], hours=20 * 24)
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    with db_session() as session:
        session.get(Requirement, rids[0]).recurrence = Recurrence.STANDING
    for rid in rids:
        fabricate_met(project["id"], rid)

    burnup = plan_view(client, project)["burnup"]
    assert burnup["scope_total"] == 3, "a standing obligation is not part of the burn-up"
    assert burnup["settled"] == 3
    assert max(p["count"] for p in burnup["met_points"]) == 3


def test_the_line_never_climbs_above_its_own_ceiling(client):
    """The invariant the whole of this file protects, stated once.

    An Operate project's ceiling is the open cycle's scope while its met-events
    span every cycle it has ever run, so the cumulative line walked straight
    through the top of its own axis — every turn of the rhythm, forever. That
    was first hidden on screen, then patched in the payload; it is now closed
    at the root. A burn-up is the line toward a promised date, and Operate has
    no promised date (the CEO's proposal is refused, test_commitments), so the
    payload carries no burn-up for it at all — for the screen, the CLI and the
    MCP reader alike. Two turns of the rhythm with work met in both is the
    shape that used to break it, so that is the shape held here.
    """
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server import janitor

    project = make_project(client, slug="rhythm", mode="operate",
                           cycle_frequency="weekly")
    token = ceo_token(project)
    first = []
    for n in range(3):
        row = propose(client, token, statement=f"Cycle output {n} exists")
        accept(client, row["requirement_id"], project)
        first.append(row["requirement_id"])
    refused = client.post("/agent/commitments", json={}, headers=bearer(token))
    assert refused.status_code == 422, refused.text

    for rid in first:
        fabricate_met(project["id"], rid)
    # A week of rhythm, so the roll actually fires — `roll_cycles` returns
    # early until the cycle's time has come, and a test that skipped that
    # would never reach the second cycle this rule is about.
    from yaaf.core.models import Cycle
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(project_id=project["id"]).one()
        cycle.opened_at = utcnow() - timedelta(days=8)
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.roll_cycles(session, row, effective(session, row.id))
    with db_session() as session:
        assert session.query(Cycle).filter_by(project_id=project["id"]).count() == 2, \
            "the rhythm did not turn; this test would prove nothing"
    row = propose(client, token, statement="Next turn output exists")
    accept(client, row["requirement_id"], project)
    fabricate_met(project["id"], row["requirement_id"])

    view = plan_view(client, project)
    assert view["planless"] is False, "Operate still has a plan; only the promise is gone"
    assert view["commitment"] is None
    assert view["burnup"] is None, (
        "work that comes round again has no line to burn up — a cumulative count "
        "across cycles against one cycle's ceiling is what walked out through the "
        "top of its own axis"
    )
