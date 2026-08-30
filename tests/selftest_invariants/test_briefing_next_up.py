"""Invariant selftest: the briefing says when something happens next.

It answered four of the five questions a chairman actually asks — what
shipped, what is running, what is waiting on me, what it cost — and was silent
on the fifth. The arithmetic existed the whole time, one screen over, in
`agenda.py`; it simply never reached the panel or the email.

That silence is what a quiet window sounds like from outside. A board that has
finished everything it knew to queue looks identical to a stopped one, and the
record of that ambiguity is a run of messages that each cost a full model turn
to answer:

    why are we idle? · are we on track? · what is the current status ·
    are we good for today? · nothing has been done on the project in 13 hours.
    What is going on?

Every one of those was answerable from rows, for nothing.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import Project, ProjectStatus
from yaaf.server.briefing import project_briefing, render_text


def _brief(project_id: str) -> dict:
    with db_session() as session:
        row = session.get(Project, project_id)
        return project_briefing(session, row, effective(session, project_id))


def test_a_running_project_names_its_next_scheduled_thing(client):
    project = make_project(client)

    next_up = _brief(project["id"])["next_up"]

    assert next_up is not None
    assert next_up["live"] is True
    assert next_up["at"], "a running project has a next wake, a review, or a reset"
    assert next_up["label"], "and it is named in words, not a bare timestamp"


def test_the_email_says_it_too_and_cannot_disagree_with_the_panel(client):
    """The existing suite already holds `render_text` and the structured brief
    to one definition; the fifth answer joins that promise rather than
    becoming a second place the schedule is computed."""
    project = make_project(client)

    brief = _brief(project["id"])
    text = render_text(brief)

    assert "Next:" in text
    if brief["next_up"]["at"]:
        assert brief["next_up"]["label"] in text
        assert brief["next_up"]["at"][:16].replace("T", " ") in text


def test_a_paused_project_says_nothing_is_scheduled_and_why(client):
    """The morning you most want to hear from it. "Nothing next" and "nothing
    next because the platform paused you" are different sentences."""
    project = make_project(client)
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "testing the quiet case"}, headers=ADMIN)
    with db_session() as session:
        assert session.get(Project, project["id"]).status == ProjectStatus.PAUSED

    next_up = _brief(project["id"])["next_up"]

    assert next_up["live"] is False
    assert next_up["at"] is None
    assert next_up["why"], "an absence a chairman can read beats a blank"
    assert "paused" in next_up["why"].lower()
    assert "Next: nothing scheduled" in render_text(_brief(project["id"]))


def test_it_costs_no_run_and_no_tokens(client):
    """The whole point of the briefing: it arrives on a project that cannot
    afford to tell you anything."""
    from sqlalchemy import func, select

    from yaaf.core.models import Run

    project = make_project(client)
    with db_session() as session:
        before = session.execute(
            select(func.count(Run.id)).where(Run.project_id == project["id"])
        ).scalar_one()

    _brief(project["id"])

    with db_session() as session:
        after = session.execute(
            select(func.count(Run.id)).where(Run.project_id == project["id"])
        ).scalar_one()
    assert after == before, "composing the schedule must never spend a run"
