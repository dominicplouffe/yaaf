"""Invariant selftests: a fix reaches the projects that already exist.

The Founder runs once, at creation, and what it writes becomes the text every
agent on that project reads for the project's whole life. So every improvement
to the Founder reaches the projects founded after it and no others. That is not
a small gap — it is the entire reason the same problem kept being fixed and kept
coming back:

    08-26  the FPL Gameweek Desk is founded
    08-30  a budget is a plan, not a wall
    08-31  plan-adversary created; `standard` added to the goal grammar
    09-03  "nobody was ever told to make the deliverable good"
    09-03  "the bar the reference goal did not meet is the bar now"
    09-05  the chairman: "we're not doing very hot and you want to take a break"

Five days of fixes, none of which reached the desk being watched, which would
have run to GW38 — nine months — on the doctrine of 26 August.

What reaches an existing project is each fix that applies to it, as a finding
about it with its way out. The fingerprint is kept as the record and is never
itself a finding: it moves for edits that cannot reach a founded project, and
a row saying only "the Founder changed" taught people to put it away unread.

Nothing here changes a running project. Every check is a warning the chairman
reads and decides on, because a platform that silently rewrote a live desk's
goal and roster mid-season would be a worse failure than the one it fixes.

All of them are arithmetic over rows, at zero tokens. Re-grading goal prose
against the current grammar needs a model and real money and is deliberately
not here — the structural half is what FPL was actually missing.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Project, ProjectMode, Recurrence, Requirement, RequirementStatus
from yaaf.founder import doctrine


def checks(client, project: dict) -> dict[str, dict]:
    response = client.get(f"/observe/projects/{project['id']}/health", headers=ADMIN)
    assert response.status_code == 200, response.text
    return {c["key"]: c for c in response.json()["checks"]}


# ── the stamp ───────────────────────────────────────────────────────────────────


def test_a_new_project_is_stamped_with_todays_doctrine(client):
    project = make_project(client, slug="fresh")
    with db_session() as session:
        stamp = session.get(Project, project["id"]).doctrine_stamp
    assert stamp == doctrine.fingerprint()
    assert "doctrine-drift" not in checks(client, project)


def test_older_doctrine_is_told_as_what_it_lacks_never_as_a_fingerprint(client):
    """The chairman cannot act on "the doctrine is 578387eceeca now". The row
    that said so lit on every project for an edit to how the Founder interviews,
    which no founded project can receive, and offered no way out. What he can
    act on is the specific thing his project lacks, so that is what he is told —
    and the stamp stays as it was, unknown or old."""
    project = make_project(client, slug="august", mode=ProjectMode.OPERATE)
    for stamp in (None, "0000deadbeef"):
        with db_session() as session:
            session.get(Project, project["id"]).doctrine_stamp = stamp
        found = checks(client, project)
        assert "doctrine-drift" not in found
        assert not any(doctrine.fingerprint() in c["detail"] for c in found.values())
        # The structural fixes still reach it, each in words about the project.
        assert "no-standing-scope" in found


def test_the_fingerprint_actually_tracks_the_doctrine_text(tmp_path, monkeypatch):
    """If this can be edited without the fingerprint moving, the whole check is
    decoration."""
    from yaaf.founder import doctrine as mod

    before = mod.fingerprint()
    original = (mod._HERE / "GOAL_GRAMMAR.md").read_bytes()
    try:
        (mod._HERE / "GOAL_GRAMMAR.md").write_bytes(original + b"\n<!-- a change -->\n")
        assert mod.fingerprint() != before
    finally:
        (mod._HERE / "GOAL_GRAMMAR.md").write_bytes(original)
    assert mod.fingerprint() == before


# ── nobody whose job is to disagree ─────────────────────────────────────────────


def test_a_deliver_project_with_no_challenge_seat_is_reported(client):
    """DESIGN_DOCTRINE: plan-adversary survives the cut on purpose and belongs
    on every Deliver and Operate project, because the CEO wrote the plan and on
    an autonomous project also accepted it — the only reader with no distance
    from it. FPL shipped seven analysts and no skeptic."""
    project = make_project(client, slug="noadv", mode=ProjectMode.DELIVER)
    check = checks(client, project).get("no-adversary")
    assert check and check["status"] == "warn", (
        "a Deliver project with an all-analyst roster reported nothing"
    )
    assert "disagree" in check["detail"]


def test_a_project_that_has_one_is_not_nagged(client):
    from yaaf.core.models import Role

    project = make_project(client, slug="hasadv", mode=ProjectMode.DELIVER)
    with db_session() as session:
        # The roster's role becomes a challenge seat: the check asks whether
        # anyone here can disagree, not which slug they are called.
        for role in session.execute(__import__("sqlalchemy").select(Role)).scalars():
            role.category = "challenge"
    assert "no-adversary" not in checks(client, project)


# ── an Operate project re-proposing the same thing forever ──────────────────────


def test_an_operate_project_with_no_standing_scope_is_reported(client):
    """`Recurrence.STANDING` names this failure in its own docstring: expressed
    as TERMINAL, a project that had shipped 1 of 38 memos looked finished. FPL's
    standing list was empty and its CEO hand-created a memo requirement weekly."""
    project = make_project(client, slug="operate", mode=ProjectMode.OPERATE)
    check = checks(client, project).get("no-standing-scope")
    assert check and check["status"] == "warn"
    assert "re-proposed by hand" in check["detail"]


def test_standing_scope_clears_it(client):
    from yaaf.core import ids

    project = make_project(client, slug="operate2", mode=ProjectMode.OPERATE)
    with db_session() as session:
        session.add(Requirement(
            id=ids.new_id(ids.REQUIREMENT), project_id=project["id"], key="R-09",
            statement="The gameweek memo is posted before the deadline",
            acceptance_test={"type": "command", "command": "test -s memo.md"},
            recurrence=Recurrence.STANDING, status=RequirementStatus.PENDING,
        ))
    assert "no-standing-scope" not in checks(client, project)


def test_a_deliver_project_is_not_asked_for_standing_scope(client):
    """Standing scope is the Operate shape. Reporting its absence on a Deliver
    project would be noise, and noise is how a health screen stops being read."""
    project = make_project(client, slug="deliver2", mode=ProjectMode.DELIVER)
    assert "no-standing-scope" not in checks(client, project)


# ── none of it touches the project ──────────────────────────────────────────────


def test_drift_is_reported_and_never_applied(client):
    """A platform that rewrote a live desk's goal and roster mid-season would be
    a worse failure than the one it is fixing. Every check here is a warning the
    chairman decides on."""
    project = make_project(client, slug="untouched", mode=ProjectMode.OPERATE)
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.doctrine_stamp = None
        before = (row.goal, row.mode, row.topology)

    reported = checks(client, project)
    assert "no-standing-scope" in reported

    with db_session() as session:
        row = session.get(Project, project["id"])
        assert (row.goal, row.mode, row.topology) == before
        assert row.doctrine_stamp is None, (
            "the health check re-stamped the project, which would mark it current "
            "without anyone having carried the doctrine across"
        )


# ── the on-demand re-grade ──────────────────────────────────────────────────────


def test_regrading_a_goal_reports_and_changes_nothing(client, monkeypatch):
    """The expensive half of drift, and the one that must never auto-apply.

    The Founder wrote this goal under the doctrine of the day it ran. Grading it
    against today's grammar is useful; rewriting a live project's objective
    because the rubric moved would be a worse failure than the drift.
    """
    from yaaf.founder.grade import ElementGrade, GoalGrade

    project = make_project(client, slug="regrade")
    with db_session() as session:
        before = session.get(Project, project["id"]).goal

    async def fake_grade(goal, *, env, model="claude-opus-5-5"):
        assert goal == before, "graded something other than the project's actual goal"
        return GoalGrade(
            elements=[
                ElementGrade(element="finish_line", verdict="present", note="named"),
                ElementGrade(element="standard", verdict="missing", note="no bar named"),
                ElementGrade(element="anti_metrics", verdict="weak", note="vague"),
            ],
            summary="two gaps",
        )

    monkeypatch.setattr("yaaf.founder.grade.grade_goal", fake_grade)
    monkeypatch.setattr("yaaf.core.platform.founder_env",
                        lambda session: {"ANTHROPIC_API_KEY": "test-key"})
    response = client.post(f"/admin/projects/{project['id']}/regrade-goal", headers=ADMIN)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["missing"] == ["standard"]
    assert body["weak"] == ["anti_metrics"]
    # `standard` is the element whose stated job is to give a project permission
    # to want more than it was told, so naming it as load-bearing is the point.
    assert "standard" in body["load_bearing_gaps"]
    assert body["ready"] is False
    assert body["doctrine_current"] == doctrine.fingerprint()

    with db_session() as session:
        assert session.get(Project, project["id"]).goal == before, (
            "re-grading rewrote the goal; it reports, it never applies"
        )


def test_regrading_without_a_credential_refuses_rather_than_crashing(client):
    """Grading calls a model. No platform credential is something the chairman
    fixes in Settings, not a stack trace."""
    project = make_project(client, slug="nocred")
    response = client.post(f"/admin/projects/{project['id']}/regrade-goal", headers=ADMIN)
    assert response.status_code == 409, response.text
    assert "credential" in response.json()["detail"]
