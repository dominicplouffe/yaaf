"""Invariant selftests: answering a flag, and what happens afterwards.

A flag is the platform's obligation to speak. `acknowledge` is the chairman's
answer, and for a HISTORICAL kind — a cycle that rolled with scope unmet, an
obligation that failed on a date — his answer closes it, because "a moment has
nothing left to clear".

It did not stay closed, and the second attempt to close it was an HTTP 500 on
the "noted" button. Two defects stacked:

  * `open_flag` dedupes against LIVE flags, so the moment his answer moved one
    to RESOLVED the next janitor tick opened a fresh flag for the same cycle.
    The card he had just cleared came straight back, for a condition whose
    arithmetic can never turn.
  * `flags` carried UNIQUE (project_id, kind, subject_id, status), which binds
    resolved rows as well as live ones — so closing that second flag collided
    with the first one's row and the write blew up. The same collision waits
    for any state flag that flickers: opened, resolved, opened, resolved is an
    IntegrityError inside the janitor, where nobody is catching it.

The lines these tests hold:

  * answering a historical flag closes it, and it does not come back
  * answering twice never 500s, whatever the history
  * a state flag may still open, close, and open again — that IS new news
  * the uniqueness that stops a tick-by-tick duplicate still binds live flags
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from tests.conftest import ADMIN, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import Flag, FlagKind, FlagStatus, Project, utcnow
from yaaf.server import flags as flag_lib

CYCLE = "cyc_00000000000000000001"


def _add(project, *, kind=FlagKind.CYCLE_MISSED, status=FlagStatus.RAISED,
         subject_id=CYCLE, summary="Cycle 1 closed with scope still open.") -> str:
    with db_session() as session:
        row = Flag(id=ids.new_id(ids.FLAG), project_id=project["id"], kind=kind,
                   status=status, subject_id=subject_id, summary=summary,
                   opened_at=utcnow(), raised_at=utcnow(),
                   acknowledged_at=utcnow() if status == FlagStatus.RESOLVED else None,
                   closed_at=utcnow() if status == FlagStatus.RESOLVED else None)
        session.add(row)
        session.flush()
        return row.id


def _ack(client, flag_id: str, note: str = ""):
    return client.post(f"/admin/flags/{flag_id}/ack", json={"note": note}, headers=ADMIN)


def _operate(client, slug="acme"):
    return make_project(client, slug=slug, mode="operate",
                        constitution={"cycle_frequency": "weekly"})


# ── the button ──────────────────────────────────────────────────────────────────


def test_answering_a_missed_cycle_closes_it(client):
    project = _operate(client)
    response = _ack(client, _add(project), "Understood, cycle 2 caught both up.")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == FlagStatus.RESOLVED


def test_answering_the_same_cycle_twice_is_not_a_500(client):
    """The reported bug, exactly. The chairman had already answered cycle 1;
    the janitor re-raised it; his second "noted" was an HTTP 500 — the unique
    constraint refusing a second resolved row for the same subject."""
    project = _operate(client)
    _add(project, status=FlagStatus.RESOLVED, summary="cycle 1, answered before")
    response = _ack(client, _add(project, summary="cycle 1, raised again"))
    assert response.status_code == 200, response.text


def test_an_open_flag_still_cannot_be_answered(client):
    """Unchanged, and worth holding: an OPEN flag is news the CEO has not
    brought him yet, and the count of those is what makes the mechanism
    trustworthy. Clearing it from the desk would let a chairman quietly absolve
    a CEO of speaking."""
    project = _operate(client)
    response = _ack(client, _add(project, status=FlagStatus.OPEN))
    assert response.status_code == 409
    assert "raise" in response.text


# ── and what the janitor does next ──────────────────────────────────────────────


def test_an_answered_cycle_is_not_flagged_again(client):
    """`acknowledge` says his answer is the end of it. It was the end of it
    until the next tick, because the dedupe only looked at live flags."""
    project = _operate(client)
    assert _ack(client, _add(project)).status_code == 200

    with db_session() as session:
        row = session.get(Project, project["id"])
        reopened = flag_lib.open_flag(
            session, row, FlagKind.CYCLE_MISSED,
            "Cycle 1 closed with scope still open.", subject_id=CYCLE)
    assert reopened is None


def test_a_state_condition_going_bad_again_is_new_news(client):
    """The other half of the same rule. A forecast that recovers and then goes
    bad again has something to say; a cycle that rolled has not."""
    project = _operate(client)
    with db_session() as session:
        row = session.get(Project, project["id"])
        first = flag_lib.open_flag(session, row, FlagKind.DEADLINE_AT_RISK,
                                   "the arithmetic does not reach it",
                                   subject_id=CYCLE)
        assert first is not None
        flag_lib.resolve_subject(session, row.id, FlagKind.DEADLINE_AT_RISK,
                                 CYCLE, "the arithmetic turned")
    with db_session() as session:
        row = session.get(Project, project["id"])
        again = flag_lib.open_flag(session, row, FlagKind.DEADLINE_AT_RISK,
                                   "and now it does not again", subject_id=CYCLE)
        assert again is not None


def test_a_flickering_forecast_does_not_break_the_janitor(client):
    """Open, resolve, open, resolve on one subject. The second resolve wrote a
    second RESOLVED row, which the old constraint refused — an IntegrityError
    raised inside a janitor tick, with nobody to catch it."""
    project = _operate(client)
    for _ in range(3):
        with db_session() as session:
            row = session.get(Project, project["id"])
            flag_lib.open_flag(session, row, FlagKind.DEADLINE_AT_RISK,
                               "not feasible", subject_id=CYCLE)
        with db_session() as session:
            row = session.get(Project, project["id"])
            flag_lib.resolve_subject(session, row.id, FlagKind.DEADLINE_AT_RISK,
                                     CYCLE, "feasible again")
    with db_session() as session:
        resolved = session.query(Flag).filter(
            Flag.project_id == project["id"],
            Flag.status == FlagStatus.RESOLVED).count()
    assert resolved == 3


def test_two_live_flags_for_one_condition_are_still_refused(client):
    """What the uniqueness is actually for — a forecast recomputed every tick
    must not open a flag every tick — and it still binds."""
    project = _operate(client)
    _add(project, kind=FlagKind.DEADLINE_AT_RISK, status=FlagStatus.RAISED)
    with pytest.raises(IntegrityError), db_session() as session:
        session.add(Flag(
            id=ids.new_id(ids.FLAG), project_id=project["id"],
            kind=FlagKind.DEADLINE_AT_RISK, status=FlagStatus.RAISED,
            subject_id=CYCLE, summary="a duplicate the janitor must not make",
            opened_at=utcnow()))
