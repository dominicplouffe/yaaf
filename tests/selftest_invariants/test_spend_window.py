"""Invariant selftest: a funded question, answered by arithmetic.

Every budget conversation on this platform was two guesses meeting. A CEO with
no cost model, no lever it could pull on itself, and no permission to say "I
don't know" — every money sentence the platform owns is an instruction to CUT —
produced three estimates in two hours: 3M a day, then 5M, then 35M.

The chairman's reply was the design:

    "It's okay to say I don't know, so let's add 1 billion tokens for the next
    30 days and let's evaluate what we are doing right, what we are doing
    wrong, and adjust as time goes on. Then we refine the budget together, the
    CEO and chairman."

    "I like that as long as we can adjust along the way based on our findings."

So the two properties this file exists to defend: **the platform writes the
findings** — the commitment doctrine applied to money, no agent verb anywhere
near them — and **the chairman can adjust mid-flight**, on the record.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.conftest import ADMIN, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    Flag,
    FlagStatus,
    LedgerEntry,
    Project,
    Run,
    SpendWindow,
    SpendWindowStatus,
    utcnow,
)
from yaaf.server import spendwindow
from yaaf.server.contextpack import build_context_pack


def _open(client, project: dict, *, allowance=1_000_000_000, days=30,
          checkpoint_hours=168.0, question="What does a trading day actually cost?"):
    return client.post(
        f"/admin/projects/{project['id']}/spend-window",
        json={"question": question, "allowance": allowance, "days": days,
              "checkpoint_hours": checkpoint_hours},
        headers=ADMIN,
    )


def _spend(project_id: str, *, tokens: int, days_ago: float = 0) -> None:
    with db_session() as session:
        session.add(LedgerEntry(
            id=ids.new_id(ids.LEDGER), project_id=project_id, kind="model_usage",
            tokens_in=0, tokens_out=tokens, cache_creation_tokens=0,
            cache_read_tokens=0, usd=None,
            at=utcnow() - timedelta(days=days_ago),
        ))


def _sweep(project_id: str) -> None:
    from yaaf.server import janitor

    with db_session() as session:
        janitor.sweep_spend_window(session, session.get(Project, project_id))


def _window_id(project_id: str) -> str:
    with db_session() as session:
        return session.execute(
            select(SpendWindow.id).where(SpendWindow.project_id == project_id)
        ).scalars().first()


def _end_it_now(project_id: str, started_days_ago: float = 0) -> None:
    """Wind the clock past the window's end date, as the janitor would find it.

    `started_days_ago` also backdates the start, because a window measures the
    spend INSIDE itself — ledger entries written before it opened are somebody
    else's spend and are correctly excluded.
    """
    with db_session() as session:
        window = session.get(SpendWindow, _window_id(project_id))
        if started_days_ago:
            window.started_at = utcnow() - timedelta(days=started_days_ago)
        window.ends_at = utcnow() - timedelta(minutes=1)


def _pack(project: dict) -> dict:
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id),
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )


# ── opening one ─────────────────────────────────────────────────────────────────


def test_the_chairman_funds_a_question_not_just_an_allowance(client):
    project = make_project(client, billing_mode="subscription")

    opened = _open(client, project)

    assert opened.status_code == 200, opened.text
    body = opened.json()
    assert body["allowance"] == 1_000_000_000
    assert body["unit"] == "tokens", "a subscription project has no dollars to fund"


def test_a_window_without_a_question_is_refused(client):
    """A window with no question is a larger cap with extra steps."""
    project = make_project(client, billing_mode="subscription")

    refused = _open(client, project, question="more")

    assert refused.status_code == 422


def test_only_one_window_runs_at_a_time(client):
    """Two overlapping answers to "what does this cost" is the state this
    object replaces."""
    project = make_project(client, billing_mode="subscription")
    assert _open(client, project).status_code == 200

    second = _open(client, project, question="A different question entirely")

    assert second.status_code == 409
    assert "already open" in second.json()["detail"]


# ── the CEO's side ──────────────────────────────────────────────────────────────


def test_the_ceo_is_told_the_window_is_not_permission_to_be_frugal(client):
    """The whole complaint: an LLM optimising to please, on the only axis it
    was ever graded on, which is not overspending."""
    project = make_project(client, billing_mode="subscription")
    _open(client, project)

    block = _pack(project)["spend_window"]

    assert block["allowance"] == 1_000_000_000
    assert block["question"]
    assert "not permission to be frugal" in block["note"]
    assert "cost of being careful" in block["note"]


def test_a_project_with_no_window_carries_no_block(client):
    project = make_project(client, billing_mode="subscription")

    assert "spend_window" not in _pack(project)


# ── the findings: written by the platform, never by an agent ────────────────────


def test_the_platform_writes_the_findings_when_the_window_ends(client):
    project = make_project(client, billing_mode="subscription")
    _open(client, project, days=4)
    for days_ago in range(4):
        _spend(project["id"], tokens=18_000_000, days_ago=days_ago)
    _end_it_now(project["id"], started_days_ago=5)

    _sweep(project["id"])

    with db_session() as session:
        window = session.get(SpendWindow, _window_id(project["id"]))
        assert window.status == SpendWindowStatus.CLOSED
        found = window.findings
    assert found["measured_median_per_day"] == 18_000_000
    assert found["spent"] == 72_000_000
    assert len(found["series"]) == 4
    assert "cost_per_run" in found
    assert "No agent wrote any number here" in found["note"]


def test_no_agent_route_can_touch_the_findings(client):
    """The commitment doctrine, applied to money. If an agent could write these
    the object would be worth nothing."""
    import inspect

    from yaaf.server.api import agent as agent_api

    source = inspect.getsource(agent_api)
    assert "SpendWindow" not in source, (
        "the agent API must not import the window at all — measuring what the "
        "work cost is not a thing the worker gets to report"
    )
    assert "findings" not in source


def test_the_closing_notice_tells_the_ceo_not_to_replace_it_with_an_estimate(client):
    from yaaf.core.models import Notice

    project = make_project(client, billing_mode="subscription")
    _open(client, project, days=1)
    _spend(project["id"], tokens=5_000_000)
    _end_it_now(project["id"])

    _sweep(project["id"])

    with db_session() as session:
        bodies = list(session.execute(select(Notice.body)).scalars())
    closing = [b for b in bodies if "measurement window closed" in b]
    assert closing, bodies
    assert "MEASURED, by the platform" in closing[0]
    assert "Do not replace these numbers with a fresh estimate" in closing[0]


# ── adjusting along the way ─────────────────────────────────────────────────────


def test_the_chairman_can_adjust_mid_flight_and_it_lands_on_the_record(client):
    """"As long as we can adjust along the way based on our findings" was the
    condition attached to funding one at all."""
    project = make_project(client, billing_mode="subscription")
    _open(client, project, allowance=1_000_000_000, days=30)

    patched = client.patch(
        f"/admin/projects/{project['id']}/spend-window",
        json={"allowance": 1_500_000_000, "extend_days": 10,
              "reason": "week one showed the desk is bigger than we thought"},
        headers=ADMIN,
    )

    assert patched.status_code == 200, patched.text
    assert patched.json()["allowance"] == 1_500_000_000
    with db_session() as session:
        window = session.get(SpendWindow, _window_id(project["id"]))
        assert len(window.adjustments) == 1
        entry = window.adjustments[0]
    assert entry["from"]["allowance"] == 1_000_000_000
    assert entry["to"]["allowance"] == 1_500_000_000
    assert "bigger than we thought" in entry["reason"]


def test_an_adjustment_needs_a_reason(client):
    """An adjustable number nobody can audit is not a budget."""
    project = make_project(client, billing_mode="subscription")
    _open(client, project)

    refused = client.patch(
        f"/admin/projects/{project['id']}/spend-window",
        json={"allowance": 2_000_000_000}, headers=ADMIN)

    assert refused.status_code == 422


def test_stopping_early_still_writes_what_was_measured(client):
    """Four measured days beat any estimate, so a window stopped on day four
    still hands back its four days."""
    project = make_project(client, billing_mode="subscription")
    _open(client, project, days=30)
    with db_session() as session:
        session.get(SpendWindow, _window_id(project["id"])).started_at = (
            utcnow() - timedelta(days=3))
    _spend(project["id"], tokens=20_000_000, days_ago=1)
    _spend(project["id"], tokens=22_000_000, days_ago=0)

    stopped = client.post(
        f"/admin/projects/{project['id']}/spend-window/stop", headers=ADMIN)

    assert stopped.status_code == 200, stopped.text
    findings = stopped.json()["findings"]
    assert findings["spent"] == 42_000_000
    assert len(findings["series"]) == 2
    with db_session() as session:
        assert session.get(
            SpendWindow, _window_id(project["id"])).status == SpendWindowStatus.STOPPED


# ── the checkpoint: an obligation, not a reminder ───────────────────────────────


def test_a_checkpoint_opens_a_flag_the_ceo_cannot_close_by_silence(client):
    project = make_project(client, billing_mode="subscription")
    _open(client, project, days=30, checkpoint_hours=1.0)
    _spend(project["id"], tokens=30_000_000)
    with db_session() as session:
        session.get(SpendWindow, _window_id(project["id"])).started_at = (
            utcnow() - timedelta(hours=3))

    _sweep(project["id"])

    with db_session() as session:
        flags = list(session.execute(
            select(Flag).where(Flag.kind == "spend_window")).scalars())
    assert flags, "a checkpoint must be an obligation to speak, not a notice"
    assert flags[0].status == FlagStatus.OPEN
    assert "Report the measured numbers so far" in flags[0].summary
    assert "the series, not an average" in flags[0].summary


def test_a_window_on_course_to_overspend_raises_early(client):
    project = make_project(client, billing_mode="subscription")
    _open(client, project, allowance=100_000_000, days=30, checkpoint_hours=999.0)
    for days_ago in range(4):
        _spend(project["id"], tokens=20_000_000, days_ago=days_ago)

    _sweep(project["id"])

    with db_session() as session:
        flags = list(session.execute(
            select(Flag).where(Flag.kind == "spend_window")).scalars())
    assert flags, "80M of 100M in four days of thirty is not a quiet state"
    assert "on course to spend" in flags[0].summary


def test_an_unproven_rate_does_not_cry_overspend_on_day_two(client):
    """The window exists BECAUSE the pace is unknown. Reporting a confident
    overrun from two lumpy days would be the same failure in a new costume."""
    project = make_project(client, billing_mode="subscription")
    _open(client, project, allowance=100_000_000, days=30, checkpoint_hours=999.0)
    _spend(project["id"], tokens=1_000_000, days_ago=1)
    _spend(project["id"], tokens=60_000_000, days_ago=0)

    with db_session() as session:
        detail = spendwindow.progress(
            session, session.get(SpendWindow, _window_id(project["id"])))

    assert detail["measured"]["projectable"] is False
    assert detail["on_course_to_overspend"] is False, (
        "two days, one of them an outlier, is not a course"
    )


def test_the_sweep_costs_no_run(client):
    """Like every janitor sweep — so the findings land on a project that has
    paused, gone broke, or simply stopped being interesting."""
    from sqlalchemy import func, select

    project = make_project(client, billing_mode="subscription")
    _open(client, project, days=1)
    _spend(project["id"], tokens=5_000_000)
    with db_session() as session:
        before = session.execute(
            select(func.count(Run.id)).where(Run.project_id == project["id"])
        ).scalar_one()
    _end_it_now(project["id"])

    _sweep(project["id"])

    with db_session() as session:
        after = session.execute(
            select(func.count(Run.id)).where(Run.project_id == project["id"])
        ).scalar_one()
    assert after == before


def test_the_chairman_can_read_the_window_and_its_history(client):
    project = make_project(client, billing_mode="subscription")
    _open(client, project)
    client.patch(f"/admin/projects/{project['id']}/spend-window",
                 json={"extend_days": 5, "reason": "one more week"}, headers=ADMIN)
    _spend(project["id"], tokens=12_000_000)

    read = client.get(f"/admin/projects/{project['id']}/spend-window", headers=ADMIN)

    assert read.status_code == 200, read.text
    body = read.json()
    assert body["open"] is True
    assert body["spent"] == 12_000_000
    assert len(body["adjustments"]) == 1


def test_the_window_closing_takes_its_flag_with_it(client):
    """The flag is about THIS window overspending, so it dies with the window.

    Nothing resolved it before: `flags.evaluate` never sees this kind, and the
    only other path to RESOLVED is the chairman acknowledging one. So a raised
    spend_window sat on his desk permanently — including after he had already
    done the thing it asked for and raised the ceiling.
    """
    project = make_project(client, billing_mode="subscription")
    _open(client, project, allowance=100_000_000, days=30, checkpoint_hours=999.0)
    for days_ago in range(4):
        _spend(project["id"], tokens=20_000_000, days_ago=days_ago)
    _sweep(project["id"])

    with db_session() as session:
        live = list(session.execute(
            select(Flag).where(Flag.kind == "spend_window")).scalars())
        assert live and live[0].status == FlagStatus.OPEN

    _end_it_now(project["id"], started_days_ago=31)
    _sweep(project["id"])

    with db_session() as session:
        window = session.get(SpendWindow, _window_id(project["id"]))
        assert window.status == SpendWindowStatus.CLOSED
        closed = list(session.execute(
            select(Flag).where(Flag.kind == "spend_window")).scalars())
        assert [f.status for f in closed] == [FlagStatus.RESOLVED]
        assert "the window closed" in closed[0].resolution
