"""Invariant selftests: one status sentence per project, and its order.

Every surface used to compose its own summary of a project, and they
disagreed. The verdict is decided once now (`yaaf.server.status_line`) and
rides the desk rollup that Home, `yaaf projects` and the MCP `desk` all read.
What is attacked here is the order — what a reader must hear first — and that
a project that cannot run is never "on track".
"""

from __future__ import annotations

from datetime import timedelta

from tests.conftest import ADMIN, make_project
from yaaf.core.models import CommitmentStatus, ProjectStatus, utcnow
from yaaf.server.status_line import status_line


class _Commitment:
    def __init__(self, status, due, projected=None):
        self.status, self.deliver_by, self.projected_done_at = status, due, projected


def _line(**over) -> dict:
    facts = dict(status=ProjectStatus.RUNNING, status_reason=None, unpaid=False,
                 commitment=None, waiting=0, open_tasks=2, runs_running=1,
                 last_run_at=utcnow(), stall_hours=3.0, unit="usd", spent=148.0, cap=400.0)
    return status_line(**{**facts, **over})


def test_a_project_that_cannot_pay_is_blocked_whatever_else_is_true():
    line = _line(unpaid=True, waiting=3,
                 commitment=_Commitment(CommitmentStatus.ACCEPTED, utcnow() + timedelta(days=9)))
    assert line["verdict"] == "blocked"
    assert "nothing is set to pay" in line["sentence"]


def test_a_broken_promise_comes_before_a_decision_waiting():
    due = utcnow() + timedelta(days=3)
    line = _line(waiting=2, commitment=_Commitment(
        CommitmentStatus.AT_RISK, due, projected=due + timedelta(days=4)))
    assert line["verdict"] == "at_risk"
    assert f"promised {due.day} {due:%b}" in line["sentence"]
    assert "$148 of $400 this month." in line["sentence"]


def test_a_decision_comes_before_on_track():
    assert _line(waiting=1)["sentence"].startswith("1 decision waiting on you.")


def test_work_open_and_nothing_running_for_too_long_is_stalled():
    line = _line(runs_running=0, last_run_at=utcnow() - timedelta(hours=7))
    assert line["verdict"] == "stalled"
    assert "Quiet for 7h with 2 task(s) open" in line["sentence"]


def test_a_kept_promise_names_its_date():
    due = utcnow() + timedelta(days=10)
    line = _line(commitment=_Commitment(CommitmentStatus.ACCEPTED, due))
    assert line["verdict"] == "on_track"
    assert line["headline"] == f"On track for {due.day} {due:%b}."
    assert line["sentence"] == f"On track for {due.day} {due:%b}. $148 of $400 this month."


def test_every_row_of_the_rollup_carries_the_sentence(client):
    project = make_project(client)
    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    # The suite's projects start with no billing credential.
    assert row["status_line"]["verdict"] == "blocked"
