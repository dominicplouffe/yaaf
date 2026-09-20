"""Invariant selftests: an autonomous project asks its chairman for nothing.

`policy.awaiting_chairman` states the contract this file defends: every member
of it is a decision the dial left with the chairman, "which is what lets an
autonomous project read empty and mean it". The failure it guards against is
not a missing feature — it is a screen or an email telling somebody that
something is waiting on them when nothing is, until they stop believing the
one count that matters.

Two things must hold for a project whose dial is fully autonomous:

  * nothing the chairman gave away is reported back to him as his
  * a flag or an open review is real news and still not a decision — the brief
    counts it separately, because a thing to read beside a thing that denies
    itself in six hours makes the second one unreadable

And one thing must hold everywhere: the dashboard has to carry the five
Autonomy rules, because every screen that tells a reader whose decision this is
reads them from there, and a payload that quietly stopped carrying them would
put every decision back on the chairman's desk with nothing failing.
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_requirements import ceo_token, deliver_project, propose
from yaaf.core.constitution import AUTONOMY_PRESETS, AUTONOMY_RULES
from yaaf.core.db import db_session
from yaaf.core.models import FlagKind, Project, utcnow
from yaaf.server import flags as flags_module


def dashboard(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def brief(client, project: dict) -> dict:
    response = client.get(f"/observe/projects/{project['id']}/briefing", headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def raise_a_flag(project_id: str, proposal: str = "carry it into the next cycle") -> None:
    """The platform opens one from arithmetic and the CEO brings it — the same
    two steps the janitor takes, without waiting for a janitor tick."""
    with db_session() as session:
        project = session.get(Project, project_id)
        flag = flags_module.open_flag(
            session, project, FlagKind.CYCLE_MISSED,
            "Cycle 4 closed with scope still open: 8 of 12 met, 2 carried forward "
            "(R-136, R-139).")
        assert flag is not None
        flag.status = "raised"
        flag.raised_at = utcnow()
        flag.proposal = proposal


# ── the dial reaches the screens that quote it ─────────────────────────────────


def test_the_dashboard_carries_the_authority_dial(client):
    """Every screen that says whose decision something is reads these five
    rules off the dashboard. A payload that stopped carrying them would not
    fail anything — it would silently answer "the chairman's" for all five,
    which is how an autonomous project came to show its owner a desk full of
    work he had explicitly given away."""
    project = make_project(client, slug="dialled",
                           constitution=AUTONOMY_PRESETS["autonomous"])
    constitution = dashboard(client, project)["constitution"]
    for rule in AUTONOMY_RULES:
        assert rule in constitution, f"the dashboard no longer carries {rule}"
        assert constitution[rule] != "chairman", (
            f"{rule} reads chairman on a project set to autonomous"
        )


def test_a_supervised_project_still_says_the_chairman_holds_them(client):
    """The other end of the dial, so the test above cannot pass by the rules
    being absent, constant, or wrong in the same direction."""
    project = make_project(client, slug="supervised-dial",
                           constitution=AUTONOMY_PRESETS["supervised"])
    constitution = dashboard(client, project)["constitution"]
    for rule in AUTONOMY_RULES:
        assert constitution[rule] == "chairman", f"{rule} is not the chairman's"


# ── what the brief may call "waiting on you" ───────────────────────────────────


def test_a_raised_flag_is_not_counted_as_waiting_on_the_chairman(client):
    """A flag proceeds on the CEO's proposal whether or not anybody answers.
    Counted into "N now waiting on you" it made an autonomous project mail its
    owner about a decision that was not his and that no action of his could
    resolve — while the desk it pointed at could only offer him a "Noted"
    button."""
    project = make_project(client, slug="flagged",
                           constitution=AUTONOMY_PRESETS["autonomous"])
    raise_a_flag(project["id"])

    waiting = brief(client, project)["waiting"]
    assert waiting["flags_raised"] == 1, "the flag is not being seen at all"
    assert waiting["total"] == 0, (
        "a raised flag was counted as waiting on the chairman; it decides "
        "nothing and expires never"
    )
    assert waiting["to_read"] == 1, "the flag must still be reported, just not as a decision"


def test_a_real_decision_is_still_counted(client):
    """Non-vacuity for the rule above: the count is not simply always zero.

    Proposed scope on a SUPERVISED project is a decision the dial kept with
    the chairman, and it has to keep showing up as one.
    """
    project = deliver_project(client, slug="waiting-real",
                              constitution=AUTONOMY_PRESETS["supervised"])
    token = ceo_token(project)
    propose(client, token, statement="Something that needs your signature exists")

    waiting = brief(client, project)["waiting"]
    assert waiting["total"] >= 1, (
        "proposed scope on a supervised project is the chairman's decision "
        "and must be counted as one"
    )


def test_the_same_scope_is_not_his_when_he_gave_it_away(client):
    """The pair to the test above, and the whole point of the dial: identical
    state, opposite answer, because the constitution says so."""
    project = deliver_project(client, slug="waiting-delegated",
                              constitution=AUTONOMY_PRESETS["autonomous"])
    token = ceo_token(project)
    propose(client, token, statement="Something the CEO accepts on its own authority")

    waiting = brief(client, project)["waiting"]
    assert waiting["total"] == 0, (
        "scope the CEO accepts itself was reported as waiting on the chairman"
    )


def test_an_open_review_is_news_rather_than_a_decision(client):
    """A review blocks nothing — the janitor simply will not open a second one
    while it is open. It belongs in the same column as the flag."""
    from yaaf.core import ids
    from yaaf.core.models import Review, ReviewStatus

    project = make_project(client, slug="in-review",
                           constitution=AUTONOMY_PRESETS["autonomous"])
    with db_session() as session:
        session.add(Review(
            id=ids.new_id(ids.REVIEW), project_id=project["id"], number=1,
            status=ReviewStatus.OPEN, opened_at=utcnow(),
            scheduled_for=utcnow() - timedelta(days=1), numbers={}))

    waiting = brief(client, project)["waiting"]
    assert waiting["open_reviews"] == 1
    assert waiting["total"] == 0, "an open review is not a decision that decides itself"
    assert waiting["to_read"] >= 1
