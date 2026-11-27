"""Invariant selftests: every seat reads the requirement it works on, and the
chairman's yes reaches the change it was asked for.

26 November 2026, the Split app's first hour. Only the CEO could read the
requirements. The test writer was woken to write hidden scenarios for R-01 and
R-02, could not read either, guessed "the calculator" and "bad input", and hung
nine browser tests on the CI requirement and the build requirement; the
architect and the builder said the same that hour and worked from the goal. The
CEO then asked the chairman's yes on the CI change as an assist; he said yes,
the change still had no yes, and he merged it by hand, which froze merging.

Each test attacks it as the seat would: with its own run token, over HTTP.
"""

from __future__ import annotations

import pytest

from tests.conftest import ADMIN, bearer, live_token
from tests.selftest_invariants.test_merge_gate import (
    FakeGitHub,
    _agent,
    _change,
    _claimed_task,
    _factory,
    _green,
    _open,
    _sign,
)
from yaaf.core.db import db_session
from yaaf.core.models import Change, ChangeStatus, Run
from yaaf.server import github

STATEMENT = "A patient can hold a free slot"   # what _claimed_task proposes


@pytest.fixture
def gh(monkeypatch):
    fake = FakeGitHub()
    monkeypatch.setattr(github, "_request", fake)
    monkeypatch.setattr(github, "platform_token", lambda session: "platform-token")
    return fake


def _woken(project: dict, who: str, reasons: list[dict]) -> str:
    run_id, token = live_token(project["id"], _agent(project, who))
    with db_session() as session:
        session.get(Run, run_id).trigger = {"type": "wake", "reasons": reasons}
    return token


# ── every seat reads the plan ──────────────────────────────────────────────────


def test_the_builder_reads_the_requirement_its_task_serves(client, gh):
    project = _factory(client)
    _claimed_task(client, project)
    _, mika = live_token(project["id"], _agent(project, "Mika"))
    pack = client.get("/agent/context", headers=bearer(mika)).json()
    assert pack["task"]["id"]
    assert [r["statement"] for r in pack["requirements"]] == [STATEMENT]
    assert "acceptance_test" in pack["requirements"][0]


def test_a_test_writer_woken_for_scenarios_reads_that_requirement(client, gh):
    project = _factory(client, scenarios=True)
    _claimed_task(client, project)
    rid = client.get("/agent/requirements", headers=bearer(
        live_token(project["id"], _agent(project, "Rhea"))[1])).json()["requirements"][0]["id"]
    rhea = _woken(project, "Rhea", [{"kind": "scenarios_owed", "requirement": "R-01",
                                     "requirement_id": rid}])
    pack = client.get("/agent/context", headers=bearer(rhea)).json()
    assert [r["id"] for r in pack["requirements"]] == [rid]
    assert pack["requirements"][0]["statement"] == STATEMENT


def test_a_reviewer_woken_for_a_change_reads_what_the_change_is_for(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    rhea = _woken(project, "Rhea", [{"kind": "change_review_owed", "number": change["number"],
                                     "change_id": change["id"]}])
    pack = client.get("/agent/context", headers=bearer(rhea)).json()
    assert [r["statement"] for r in pack["requirements"]] == [STATEMENT]


def test_any_seat_lists_the_whole_plan(client, gh):
    project = _factory(client)
    _claimed_task(client, project)
    for who in ("Mika", "Rhea"):
        _, token = live_token(project["id"], _agent(project, who))
        listed = client.get("/agent/requirements", headers=bearer(token))
        assert listed.status_code == 200
        assert STATEMENT in [r["statement"] for r in listed.json()["requirements"]]


# ── a hidden scenario quotes the requirement it tests ──────────────────────────


def _scenario(client, project, checks: str):
    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    rid = client.get("/agent/requirements", headers=bearer(rhea)).json()["requirements"][0]["id"]
    return client.post("/agent/scenarios", headers=bearer(rhea), json={
        "requirement_id": rid, "name": "tip buttons", "checks": checks,
        "command": "node scenarios/tip_buttons.js"})


@pytest.mark.parametrize("checks", [
    "the 15%, 18% and 20% buttons give the right tip and share",   # the Split mistake
    "WHEN the system shall, the system shall",                    # words every one shares
])
def test_a_scenario_that_does_not_quote_its_requirement_is_refused(client, gh, checks):
    project = _factory(client, scenarios=True)
    _claimed_task(client, project)
    refused = _scenario(client, project, checks)
    assert refused.status_code == 422
    assert "does not quote R-01" in refused.json()["error"]
    assert STATEMENT in refused.json()["error"], "the refusal hands it the text"


def test_a_scenario_that_quotes_its_requirement_is_filed(client, gh):
    project = _factory(client, scenarios=True)
    _claimed_task(client, project)
    filed = _scenario(client, project, "a patient can hold a free slot, from two tabs")
    assert filed.status_code == 200, filed.text
    assert filed.json()["statement"] == STATEMENT


# ── his yes on an assist reaches the change it asked about ──────────────────────


def _waits_on_him(client, project, gh) -> dict:
    gh.files = [{"filename": ".github/workflows/ci.yml", "status": "added",
                 "patch": "@@\n+on: [push, pull_request]"}]
    change = _open(client, project, _claimed_task(client, project)).json()
    _green(client, project, change["head_sha"])
    _sign(client, project, change["id"])
    row = _change(change["id"])
    assert row.status == ChangeStatus.OPEN and not row.gates["ci_config"]["ok"]
    return change


def _assist(client, project, number, **extra):
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/assists", headers=bearer(ceo), json={
        "what": f"Please give your yes to the first change (pull request {number}, the CI "
                "setup).", "where": f"Changes tab, change #{number}", **extra})


def test_an_assist_about_a_change_waiting_on_him_must_carry_it(client, gh):
    project = _factory(client, ci_changes_need_chairman=True)
    change = _waits_on_him(client, project, gh)
    refused = _assist(client, project, change["number"])
    assert refused.status_code == 409
    assert f"change_number: {change['number']}" in refused.json()["error"]


def test_his_yes_on_the_assist_is_his_yes_on_the_change(client, gh):
    project = _factory(client, ci_changes_need_chairman=True)
    change = _waits_on_him(client, project, gh)
    filed = _assist(client, project, change["number"], change_number=change["number"])
    assert filed.status_code == 200, filed.text

    decided = client.post(f"/admin/approvals/{filed.json()['assist_id']}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    assert decided.json()["change"]["status"] == ChangeStatus.READY
    assert _change(change["id"]).chairman_sha == change["head_sha"]


def test_a_push_after_he_was_asked_is_not_approved_by_that_yes(client, gh):
    project = _factory(client, ci_changes_need_chairman=True)
    change = _waits_on_him(client, project, gh)
    filed = _assist(client, project, change["number"], change_number=change["number"]).json()
    with db_session() as session:
        session.get(Change, change["id"]).head_sha = "9" * 40

    decided = client.post(f"/admin/approvals/{filed['assist_id']}/decide",
                          json={"approve": True}, headers=ADMIN).json()
    assert decided["change"] is None and "newer commit" in decided["change_not_approved"]
    assert _change(change["id"]).chairman_sha is None


def test_an_assist_cannot_carry_a_change_that_does_not_wait_on_him(client, gh):
    project = _factory(client)
    change = _open(client, project, _claimed_task(client, project)).json()
    refused = _assist(client, project, change["number"], change_number=change["number"])
    assert refused.status_code == 409
    assert "nothing only the chairman can give" in refused.json()["error"]


# ── his yes on an assist reaches the alert it asked about ───────────────────────


def _frozen(client, project) -> str:
    from tests.selftest_invariants.test_merge_gate import _hook
    from yaaf.core.models import Flag, FlagKind, FlagStatus, Project
    from yaaf.server import changes

    _hook(client, project, "push", {
        "ref": "refs/heads/main", "after": "c" * 40, "pusher": {"name": "dplouffe"},
        "sender": {"login": "dplouffe"}}, "push-hand-merge")
    with db_session() as session:
        changes.reconcile_main(session, session.get(Project, project["id"]))
        flag = session.query(Flag).filter_by(project_id=project["id"],
                                             kind=FlagKind.MAIN_BYPASSED).one()
        flag.status = FlagStatus.RAISED
        return flag.id


def _freeze_assist(client, project, **extra):
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/assists", headers=bearer(ceo), json={
        "what": "Please clear the alert that is freezing all merging: main moved without "
                "the platform.", "where": "Your Inbox", **extra})


def test_an_assist_about_the_merge_freeze_must_carry_the_flag(client, gh):
    project = _factory(client)
    flag_id = _frozen(client, project)
    refused = _freeze_assist(client, project)
    assert refused.status_code == 409 and f"flag_id: {flag_id}" in refused.json()["error"]


def test_his_yes_on_the_assist_answers_the_freeze(client, gh):
    from yaaf.core.models import Project
    from yaaf.server import changes

    project = _factory(client)
    flag_id = _frozen(client, project)
    filed = _freeze_assist(client, project, flag_id=flag_id)
    assert filed.status_code == 200, filed.text
    decided = client.post(f"/admin/approvals/{filed.json()['assist_id']}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert decided.status_code == 200 and decided.json()["flag_closed"] is True
    with db_session() as session:
        assert changes.merges_frozen(session, session.get(Project, project["id"])) is None


# ── a hidden scenario's author reads why it failed, and can withdraw it ─────────


def test_a_failing_scenarios_author_is_told_why_and_can_retire_it(client, gh):
    from yaaf.core.models import Notice, Scenario
    from yaaf.server import changes as changes_lib

    project = _factory(client, scenarios=True)
    task_id = _claimed_task(client, project)
    filed = _scenario(client, project, "a patient can hold a free slot, and the CLI too")
    sid = filed.json()["id"]
    change = _open(client, project, task_id).json()
    with db_session() as session:
        row = session.get(Change, change["id"])
        changes_lib.record_scenarios(session, row, row.head_sha, {sid: False},
                                     {sid: "exit 1: FAIL: bin/durations.js missing"})
        told = [n.body for n in session.query(Notice).filter_by(project_id=project["id"])
                if "fails on change" in n.body]
    assert told and "bin/durations.js missing" in told[0]

    _, rhea = live_token(project["id"], _agent(project, "Rhea"))
    listed = client.get("/agent/scenarios", headers=bearer(rhea)).json()["scenarios"]
    assert listed[0]["failing_on"][0]["printed"].endswith("bin/durations.js missing")
    _, mika = live_token(project["id"], _agent(project, "Mika"))
    seen = client.get("/agent/scenarios", headers=bearer(mika)).text
    assert "durations.js" not in seen, "the builder never reads why"
    assert client.post(f"/agent/scenarios/{sid}/retire", headers=bearer(mika),
                       json={"reason": "the builder would love to drop this one"}
                       ).status_code == 403
    retired = client.post(f"/agent/scenarios/{sid}/retire", headers=bearer(rhea),
                          json={"reason": "It tests the CLI, which a later requirement builds."})
    assert retired.status_code == 200, retired.text
    with db_session() as session:
        assert session.get(Scenario, sid).retired_at is not None
