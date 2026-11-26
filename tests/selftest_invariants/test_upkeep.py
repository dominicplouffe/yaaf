"""Invariant selftests: a Code Factory's upkeep promises, measured by the
platform from what the repo reported — never from what an agent said.

  * entering upkeep (by hand-over or by adopting a repo) writes one accepted
    standing promise per upkeep rule left on, and none for a rule turned off
  * every measure reads only signals the platform wrote from signed webhooks
    or registries; a forged webhook writes none
  * a promise is judged once, at the roll, over the whole cycle: a good
    Monday-morning reading does not settle it
  * a week the repo said nothing about is unchecked, not honoured
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN
from tests.selftest_invariants.test_code_factory import _built, adopted, factory
from tests.selftest_invariants.test_merge_gate import _factory, _hook
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import (
    Cycle,
    CycleStatus,
    FactorySignal,
    Project,
    Recurrence,
    Requirement,
    RequirementStatus,
    utcnow,
)
from yaaf.server import janitor, metrics, upkeep


def _promises(project_id) -> dict[str, Requirement]:
    with db_session() as session:
        rows = [r for r in session.query(Requirement).filter_by(project_id=project_id)
                if upkeep.is_upkeep_promise(r)]
        for r in rows:
            session.expunge(r)
        return {r.acceptance_test["source"]["measure"]: r for r in rows}


def _sig(project_id, kind, key, data, hours_ago):
    with db_session() as session:
        upkeep.signal(session, session.get(Project, project_id), kind, key, data,
                      at=utcnow() - timedelta(hours=hours_ago))


def _measure(project_id, name, start_hours_ago=168):
    with db_session() as session:
        project = session.get(Project, project_id)
        return upkeep.measure(session, project, name,
                              utcnow() - timedelta(hours=start_hours_ago), utcnow())


# ── the promises ────────────────────────────────────────────────────────────────


def test_the_hand_over_writes_the_promises_the_rules_left_on(client):
    project = factory(client, slug="kept-weekly")
    _built(client, project)
    assert _promises(project["id"]) == {}, "no promise is owed while it is being built"
    assert client.post(f"/admin/projects/{project['id']}/complete",
                       headers=ADMIN).status_code == 200
    promises = _promises(project["id"])
    assert set(promises) == set(upkeep.MEASURES)
    for p in promises.values():
        assert p.recurrence == Recurrence.STANDING and p.accepted_at is not None
        assert p.status == RequirementStatus.PENDING
    assert promises["main_green_pct"].acceptance_test["target"] == 95


def test_an_adopted_repo_owes_its_promises_from_day_one_and_off_means_absent(client):
    project = adopted(client, slug="kept-now",
                      constitution={"upkeep_flaky_enabled": False, "upkeep_issue_hours": 0})
    promises = _promises(project["id"])
    assert "flaky_checks" not in promises and "issue_wait_hours_max" not in promises
    assert "main_green_pct" in promises and "advisories_overdue" in promises


# ── the measures ────────────────────────────────────────────────────────────────


def test_main_green_is_time_weighted_over_main_heads(client):
    project = adopted(client, slug="green")
    pid = project["id"]
    _sig(pid, "main_head", "a", {"sha": "a"}, 100)
    _sig(pid, "main_check", "a:test", {"sha": "a", "name": "test", "conclusion": "success"}, 99)
    _sig(pid, "main_head", "b", {"sha": "b"}, 25)
    _sig(pid, "main_check", "b:test", {"sha": "b", "name": "test", "conclusion": "failure"}, 24)
    value, note = _measure(pid, "main_green_pct", start_hours_ago=100)
    assert 74 < value < 76, note  # green for 75 of 100 hours


def test_a_week_main_reported_nothing_about_is_unchecked_not_green(client):
    project = adopted(client, slug="silent")
    with pytest.raises(ValueError):
        _measure(project["id"], "main_green_pct")


def test_an_issue_waits_until_someone_with_write_access_answers(client):
    project = adopted(client, slug="issues")
    pid = project["id"]
    _sig(pid, "issue_opened", "7", {"number": 7, "member": False}, 40)
    _sig(pid, "issue_answered", "7", {"number": 7, "by": "noor"}, 10)
    value, _ = _measure(pid, "issue_wait_hours_max")
    assert 29.5 < value < 30.5

    _sig(pid, "issue_opened", "8", {"number": 8, "member": False}, 50)
    value, note = _measure(pid, "issue_wait_hours_max")
    assert value > 49 and "#8" in note, "an unanswered issue keeps waiting"


def test_the_reporters_own_comment_is_not_an_answer(client):
    project = _factory(client, mode="code_factory", slug="answers")
    payload = {"action": "created", "sender": {"login": "alice"},
               "issue": {"number": 3, "user": {"login": "alice"}},
               "comment": {"user": {"login": "alice"}, "author_association": "NONE"}}
    assert _hook(client, project, "issue_comment", payload, "c1").json() == {"issue": None}
    payload["comment"] = {"user": {"login": "rhea-bot"}, "author_association": "MEMBER"}
    assert _hook(client, project, "issue_comment", payload, "c2").json() == {"issue": "answered"}


def test_advisories_are_overdue_by_githubs_own_dates(client):
    project = adopted(client, slug="advisories")
    pid = project["id"]
    old = (utcnow() - timedelta(days=3)).isoformat()
    _sig(pid, "advisory", "1", {"severity": "critical", "state": "open", "created_at": old,
                                "package": "pillow"}, 1)
    _sig(pid, "advisory", "2", {"severity": "high", "state": "open", "created_at": old,
                                "package": "jinja2"}, 1)
    value, note = _measure(pid, "advisories_overdue")
    assert value == 1 and "pillow" in note, "critical is past 2 days; high is inside 7"
    _sig(pid, "advisory", "1", {"severity": "critical", "state": "fixed", "created_at": old}, 0)
    assert _measure(pid, "advisories_overdue")[0] == 0


def test_a_check_that_failed_and_passed_on_one_commit_is_flaky(client):
    project = adopted(client, slug="flaky")
    pid = project["id"]
    for conclusion, ago in (("failure", 5), ("success", 4)):
        _sig(pid, "main_check", "a:e2e", {"sha": "a", "name": "e2e",
                                          "conclusion": conclusion}, ago)
    _sig(pid, "main_check", "a:unit", {"sha": "a", "name": "unit", "conclusion": "success"}, 4)
    value, note = _measure(pid, "flaky_checks")
    assert value == 1 and "e2e" in note


def test_packages_are_measured_against_the_registry(client, monkeypatch):
    project = adopted(client, slug="packages")
    monkeypatch.setattr(upkeep, "_latest_major",
                        lambda dep: {"npm:react": 19, "pypi:django": 5}.get(dep))
    with db_session() as session:
        row = session.get(Project, project["id"])
        n = upkeep.check_packages(session, row, {
            "package.json": '{"dependencies": {"react": "^17.0.2"}}',
            "requirements.txt": "django==4.2.11\n"})
    assert n == 2
    value, note = _measure(project["id"], "packages_majors_behind")
    assert value == 2 and "react" in note


def test_a_forged_webhook_writes_no_signal(client):
    project = _factory(client, mode="code_factory", slug="forged")
    raw = b'{"action": "opened", "issue": {"number": 1}}'
    response = client.post(f"/webhooks/github/{project['id']}", content=raw, headers={
        "X-GitHub-Event": "issues", "X-GitHub-Delivery": "x",
        "X-Hub-Signature-256": "sha256=" + "0" * 64, "Content-Type": "application/json"})
    assert response.status_code == 403
    with db_session() as session:
        assert session.query(FactorySignal).filter_by(project_id=project["id"]).count() == 0


# ── the verdict ─────────────────────────────────────────────────────────────────


def _roll(project_id, days_open=8):
    with db_session() as session:
        cycle = session.query(Cycle).filter_by(project_id=project_id,
                                               status=CycleStatus.OPEN).one()
        cycle.opened_at = utcnow() - timedelta(days=days_open)
    with db_session() as session:
        row = session.get(Project, project_id)
        janitor.roll_cycles(session, row, effective(session, row.id))


def test_a_promise_is_judged_at_the_roll_not_by_a_good_monday(client):
    project = adopted(client, slug="judged", constitution={
        "upkeep_issue_hours": 0, "upkeep_flaky_enabled": False,
        "upkeep_packages_enabled": False, "upkeep_critical_days": 0, "upkeep_high_days": 0,
        "upkeep_health_enabled": False})
    pid = project["id"]
    (green,) = _promises(pid).values()
    _sig(pid, "main_head", "a", {"sha": "a"}, 24 * 8)
    _sig(pid, "main_check", "a:test", {"sha": "a", "name": "test", "conclusion": "success"},
         24 * 8)
    with db_session() as session:
        metrics.settle_metrics(session, pid)
        assert session.get(Requirement, green.id).status != RequirementStatus.MET, \
            "a reading mid-week settles nothing"

    _roll(pid)
    with db_session() as session:
        closed = session.query(Cycle).filter_by(project_id=pid, ordinal=1).one()
        assert green.key in closed.scorecard["met_keys"], closed.scorecard
        reopened = session.get(Requirement, green.id)
        assert reopened.status == RequirementStatus.PENDING, "owed again next cycle"
        assert len(_promises(pid)) == 1, "the roll does not write the promise twice"


def test_a_red_week_is_missed_at_the_roll(client):
    project = adopted(client, slug="red-week", constitution={
        "upkeep_issue_hours": 0, "upkeep_flaky_enabled": False,
        "upkeep_packages_enabled": False, "upkeep_critical_days": 0, "upkeep_high_days": 0,
        "upkeep_health_enabled": False})
    pid = project["id"]
    (green,) = _promises(pid).values()
    _sig(pid, "main_head", "a", {"sha": "a"}, 24 * 8)
    _sig(pid, "main_check", "a:test", {"sha": "a", "name": "test", "conclusion": "failure"},
         24 * 8)
    _roll(pid)
    with db_session() as session:
        closed = session.query(Cycle).filter_by(project_id=pid, ordinal=1).one()
        assert green.key in closed.scorecard["missed_keys"]
