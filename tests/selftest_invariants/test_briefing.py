"""Invariant selftests: the briefing.

The briefing answers the one question every screen in this product refused to
— what happened while I was not looking — and it has to answer it under the
same discipline as everything else:

  * composed from the record, never self-graded and never model-written, so it
    costs nothing and can therefore arrive on a paused, broke or empty project
  * a window with a visible edge, moved only by an explicit acknowledgement, so
    polling the page cannot silently eat the weekend it is meant to summarise
  * silence that says which kind of silence it is: nothing wrong and nothing
    happening are different, and the desk already refuses to conflate them
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
)
from yaaf.core.db import db_session
from yaaf.core.ids import new_id
from yaaf.core.models import (
    Heartbeat,
    LedgerEntry,
    LedgerKind,
    Project,
    ProjectMember,
    ProjectStatus,
    utcnow,
)
from yaaf.server import briefing
from yaaf.server.janitor import BRIEF_BEAT, send_daily_brief


def brief(client, project: dict, **params) -> dict:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"/observe/projects/{project['id']}/briefing" + (f"?{query}" if query else "")
    response = client.get(url, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def kinds(payload: dict) -> set[str]:
    return {line["kind"] for line in payload["lines"]}


def seen(client, project: dict, **body) -> dict:
    response = client.post(f"/admin/projects/{project['id']}/briefing/seen",
                           json=body, headers=ADMIN)
    assert response.status_code == 200, response.text
    return response.json()


def close_a_task(client, project: dict, token: str, rid: str) -> str:
    task_id = make_task(client, token, project, rid).json()["id"]
    lead = agent_by_name(project, "Lead")
    _, lead_token = live_token(project["id"], lead)
    assert client.post(f"/agent/tasks/{task_id}/claim",
                       headers=bearer(lead_token)).status_code == 200
    done = client.post(f"/agent/tasks/{task_id}/complete",
                       json={"evidence": "https://example.org/report.pdf is live, checked"},
                       headers=bearer(lead_token))
    assert done.status_code == 200, done.text
    return task_id


# ── the window ──────────────────────────────────────────────────────────────────


def test_a_first_visit_reads_from_the_project_s_birth_not_an_empty_window(client):
    """NULL mark means never read, which must compose the whole project rather
    than the last nothing. A first briefing that said "nothing happened" about
    a week-old project would be the worst possible first impression: false."""
    project = deliver_project(client)
    payload = brief(client, project)
    assert payload["window_source"] == "since this project started"
    with db_session() as session:
        created = session.get(Project, project["id"]).created_at
    assert payload["since"][:10] == created.replace(tzinfo=UTC).isoformat()[:10]


def test_reading_the_briefing_never_moves_the_mark(client):
    """The desk polls. A mark that moved on GET would mean the window was
    always empty, and "since you were last here" would not survive a tab left
    open overnight — which is the exact situation it exists for."""
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    first = brief(client, project)
    assert "tasks_done" in kinds(first)
    for _ in range(3):
        again = brief(client, project)
        assert again["since"] == first["since"]
        assert "tasks_done" in kinds(again)
    with db_session() as session:
        member = session.execute(
            ProjectMember.__table__.select().where(
                ProjectMember.__table__.c.project_id == project["id"])
        ).first()
    assert member.last_briefed_at is None


def test_acknowledging_starts_the_next_window_and_goes_quiet(client):
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    assert not brief(client, project)["quiet"]
    seen(client, project)

    after = brief(client, project)
    assert after["window_source"] == "since you last read this"
    assert after["quiet"] is True
    assert kinds(after) <= {"runs"}


def test_the_mark_can_be_stamped_at_the_window_the_client_actually_showed(client):
    """A panel rendered a moment ago should stamp the edge it drew, not the
    instant the button was pressed — otherwise anything that lands while it is
    on screen is swallowed unread."""
    project = deliver_project(client)
    at = utcnow() - timedelta(hours=2)
    body = seen(client, project, until=at.isoformat())
    assert body["ok"] is True
    payload = brief(client, project)
    assert payload["since"][:16] == at.isoformat()[:16]


def test_an_explicit_window_overrides_the_mark(client):
    project = deliver_project(client)
    seen(client, project)
    payload = brief(client, project, hours=48)
    assert payload["window_source"] == "the window you asked for"
    assert 47.5 < payload["window_hours"] <= 48.5


# ── what it counts ──────────────────────────────────────────────────────────────


def test_it_separates_scope_settling_from_work_closing(client):
    """A week with nine tasks closed and no requirement met is a specific kind
    of bad week, and only legible if both numbers are printed. Tasks closing is
    activity; a requirement going MET is progress."""
    project = deliver_project(client)
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    assert "tasks_done" in kinds(payload)
    assert "requirement_met" not in kinds(payload)
    assert "requirement_accepted" in kinds(payload)
    # The count that matters is the one with a denominator: a task closed and
    # nothing settled is exactly the week this test is about.
    assert payload["movement"]["settled"] == 0
    assert payload["movement"]["total"] >= 1
    assert "still open" in payload["movement"]["text"]


def test_an_approval_that_timed_out_is_reported_as_having_decided_itself(client):
    """The platform's promise is that things resolve without the chairman. The
    cost of that promise is that he can miss all of it, so every automatic
    resolution in the window has to be named."""
    project = make_project(client, slug="autodecide")
    token = ceo_token(project)
    filed = client.post("/agent/approvals",
                        json={"kind": "spend", "title": "buy the data feed",
                              "body": "we need the quotes", "amount_usd": 200.0},
                        headers=bearer(token))
    assert filed.status_code == 200, filed.text

    from yaaf.core.models import Approval

    with db_session() as session:
        approval = session.get(Approval, filed.json()["id"])
        approval.expires_at = utcnow() - timedelta(minutes=1)

    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.expire_approvals(session, row, effective(session, row.id))

    payload = brief(client, project)
    assert "approvals_expired" in kinds(payload)
    line = next(x for x in payload["lines"] if x["kind"] == "approvals_expired")
    assert line["tone"] == "bad"
    assert "buy the data feed" in line["text"]


def test_a_quiet_window_says_which_kind_of_quiet_it_is(client):
    """*Nothing is wrong* and *nothing is happening* are different screens."""
    project = make_project(client, slug="silent")
    seen(client, project)
    payload = brief(client, project)
    assert payload["quiet"] is True
    assert payload["pulse"]["open_tasks"] == 0
    # Which kind of quiet: nothing was promised, and the pulse says the board is
    # empty rather than stuck. Both facts, or the reader cannot tell them apart.
    assert payload["verdict"]["state"] == "unpromised"
    assert "nothing can be on or off track" in payload["verdict"]["reason"]


def test_the_waiting_count_comes_from_the_same_definition_the_desk_uses(client):
    """One definition of what is waiting, or the brief and the desk will
    disagree about whether a project needs its chairman."""
    project = deliver_project(client)
    token = ceo_token(project)
    propose(client, token)

    payload = brief(client, project)
    assert payload["waiting"]["plan"] == 1
    desk = client.get("/observe/desk?live=true", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    assert row["awaiting_you"]["plan"] == payload["waiting"]["plan"]


# ── the daily push ──────────────────────────────────────────────────────────────


def _set_brief_time(client, project: dict, hhmm: str | None) -> None:
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "the chairman wants a morning briefing",
              "changes": {"brief_daily_at": hhmm}},
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text


def _fire(project: dict) -> bool:
    with db_session() as session:
        return send_daily_brief(session, session.get(Project, project["id"]))


def test_the_daily_briefing_fires_once_per_local_day(client, monkeypatch):
    project = make_project(client, slug="daily")
    _set_brief_time(client, project, "00:01")

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: sent.append((subject, body)),
    )
    assert _fire(project) is True
    assert len(sent) == 1
    assert sent[0][0].startswith("briefing — ")
    assert "composed from the record" in sent[0][1]
    # A second tick the same local day is not a second letter.
    assert _fire(project) is False
    assert len(sent) == 1


def test_the_briefing_reaches_a_project_the_platform_paused(client, monkeypatch):
    """The morning a budget ran out is exactly the morning a chairman needs to
    hear from his project — and exactly the morning a model-written status
    could not be afforded. Composed from rows, so it still arrives."""
    project = make_project(client, slug="broke")
    _set_brief_time(client, project, "00:01")
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "budget exhausted: 300/300 USD this month"},
                headers=ADMIN)

    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: sent.append(body),
    )
    assert _fire(project) is True
    assert "paused. budget exhausted" in sent[0]


def test_a_finished_project_stops_writing(client, monkeypatch):
    """A daily mail about a project that is over forever is how a chairman
    learns to filter this address."""
    project = make_project(client, slug="over")
    _set_brief_time(client, project, "00:01")
    client.post(f"/admin/projects/{project['id']}/stop",
                json={"reason": "shipped"}, headers=ADMIN)

    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: sent.append(body),
    )
    assert _fire(project) is False
    assert sent == []


def test_no_time_set_means_no_daily_briefing(client, monkeypatch):
    project = make_project(client, slug="unset")
    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: sent.append(body),
    )
    assert _fire(project) is False
    assert sent == []


def test_the_push_window_is_the_last_push_not_the_last_read(client, monkeypatch):
    """Two marks, two meanings. If the email consumed the chairman's READ mark,
    a briefing that arrived at 08:00 would empty the panel he opens at 09:00 —
    the mail would be eating the screen's window."""
    project = deliver_project(client, slug="twomarks")
    _set_brief_time(client, project, "00:01")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: None,
    )
    assert _fire(project) is True

    # The push moved its own heartbeat and nothing else.
    with db_session() as session:
        beat = session.execute(
            Heartbeat.__table__.select().where(
                Heartbeat.__table__.c.name == BRIEF_BEAT)
        ).first()
        assert beat is not None
        member = session.execute(
            ProjectMember.__table__.select().where(
                ProjectMember.__table__.c.project_id == project["id"])
        ).first()
        assert member.last_briefed_at is None

    # So the screen still has its story to tell.
    assert "tasks_done" in kinds(brief(client, project))


def test_the_briefing_is_composed_without_spending_a_run(client, monkeypatch):
    """The claim that makes every other property possible. If this ever starts
    costing a run, it stops being deliverable on a project that has run out of
    money — which is most of the point."""
    project = make_project(client, slug="free")
    _set_brief_time(client, project, "00:01")
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: None,
    )
    from yaaf.core.models import Run, RunQueue

    def counts() -> tuple[int, int]:
        with db_session() as session:
            return (
                session.query(Run).filter(Run.project_id == project["id"]).count(),
                session.query(RunQueue).filter(
                    RunQueue.project_id == project["id"]).count(),
            )

    before = counts()
    assert _fire(project) is True
    brief(client, project)
    client.get("/observe/briefing", headers=ADMIN)
    assert counts() == before


# ── the cross-project roll-up ───────────────────────────────────────────────────


def test_the_desk_briefing_reads_each_project_on_its_own_mark(client):
    """Reading one project's brief must not consume another's — which is the
    whole reason the mark is per project rather than per box."""
    first = deliver_project(client, slug="alpha")
    second = make_project(client, slug="beta")
    token = ceo_token(first)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, first)
    close_a_task(client, first, token, rid)
    seen(client, second)

    payload = client.get("/observe/briefing", headers=ADMIN).json()
    by_slug = {b["slug"]: b for b in payload["projects"]}
    assert by_slug["alpha"]["window_source"] == "since this project started"
    assert by_slug["beta"]["window_source"] == "since you last read this"
    assert by_slug["alpha"]["quiet"] is False


def test_a_finished_project_is_not_in_the_roll_up(client):
    make_project(client, slug="running-still")
    done = make_project(client, slug="finished")
    client.post(f"/admin/projects/{done['id']}/stop",
                json={"reason": "shipped"}, headers=ADMIN)
    payload = client.get("/observe/briefing", headers=ADMIN).json()
    assert [b["slug"] for b in payload["projects"]] == ["running-still"]


def test_a_forced_window_applies_to_every_project(client):
    make_project(client, slug="one")
    make_project(client, slug="two")
    payload = client.get("/observe/briefing?hours=6", headers=ADMIN).json()
    assert payload["forced_window"] is not None
    for project in payload["projects"]:
        assert 5.5 < project["window_hours"] <= 6.5


def test_a_malformed_since_is_refused_rather_than_guessed(client):
    make_project(client, slug="strict")
    response = client.get("/observe/briefing?since=last%20tuesday", headers=ADMIN)
    assert response.status_code == 422
    assert "ISO datetime" in response.json()["detail"]


# ── the text rendering ──────────────────────────────────────────────────────────


def test_the_email_and_the_panel_cannot_disagree(client):
    """One composer, two deliveries. The text is rendered FROM the payload, so
    there is no second query to drift."""
    project = deliver_project(client, slug="render")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    with db_session() as session:
        payload = briefing.project_briefing(
            session, session.get(Project, project["id"]))
    text = briefing.render_text(payload)
    assert payload["headline"] in text
    for line in payload["lines"]:
        assert line["text"] in text
    assert "Waiting on you" in text


def test_a_quiet_briefing_still_proves_the_project_is_alive(client):
    project = make_project(client, slug="pulse")
    with db_session() as session:
        row = session.get(Project, project["id"])
        payload = briefing.project_briefing(
            session, row, since=utcnow() - timedelta(minutes=1))
    text = briefing.render_text(payload)
    assert "nothing — no scope settled" in text
    assert "run(s) in flight" in text


def test_the_window_never_runs_backwards(client):
    """A `since` in the future would compose a negative window and print
    nonsense. Clamped, not refused: the caller is a UI passing a mark it
    stamped a moment ago, and clock skew is not an error worth a 422."""
    project = make_project(client, slug="skew")
    ahead = datetime.now(UTC) + timedelta(days=3)
    with db_session() as session:
        payload = briefing.project_briefing(
            session, session.get(Project, project["id"]), since=ahead)
    assert payload["window_hours"] == 0.0
    assert payload["since"] == payload["until"]


def test_a_draft_project_never_gets_a_daily_briefing(client, monkeypatch):
    """Nothing has run, so there is nothing to report and no chairman
    expectation to meet."""
    project = make_project(client, slug="draft-co", start=False)
    _set_brief_time(client, project, "00:01")
    with db_session() as session:
        assert session.get(Project, project["id"]).status == ProjectStatus.DRAFT
    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body: sent.append(body),
    )
    assert _fire(project) is False
    assert sent == []


# ── the verdict: reported, never formed ─────────────────────────────────────────
#
# The briefing's whole claim on a chairman's trust is that it does not grade
# itself, and a verdict is the closest this file comes to breaking that. These
# are the attacks on the distinction: the verdict is a status the platform
# stamped, read off a row; where nothing stamped one, the panel says so instead
# of inventing a green light.


def test_a_project_nobody_promised_anything_never_reads_as_on_track(client):
    """The one thing this panel must never learn to do. A green light nobody
    switched on is worse than no light: it is the panel asserting something the
    platform never checked, on the screen built to be the thing that cannot."""
    project = deliver_project(client, slug="unpromised")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    assert payload["verdict"]["state"] == "unpromised"
    assert payload["verdict"]["source"] == "none"
    assert payload["verdict"]["deliver_by"] is None
    reason = payload["verdict"]["reason"].lower()
    for green in ("on track", "healthy", "on schedule", "all clear"):
        assert green not in reason, f"invented {green!r} out of a project with no promise"


def test_the_verdict_is_read_off_the_row_and_not_recomputed(client):
    """A live projection can disagree with the stamped status between two
    janitor ticks. Choosing which to believe would be the briefing forming a
    verdict, so it does not choose: it prints what the janitor last stamped.

    The attack is a row the janitor has not caught up with — accepted, but with
    a projection already past its own date. A briefing that graded would say "at
    risk"; one that reports says what the row says.
    """
    from tests.selftest_invariants.test_commitments import chairman_accept, committed_project
    from yaaf.core.models import Commitment, CommitmentStatus

    project, _token, _rids, proposed = committed_project(client, slug="stale")
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    with db_session() as session:
        row = session.get(Commitment, proposed["commitment_id"])
        row.projected_done_at = utcnow() + timedelta(days=99)
        assert row.status == CommitmentStatus.ACCEPTED

    payload = brief(client, project)
    assert payload["verdict"]["state"] == "on_track"
    assert payload["verdict"]["source"] == "commitment"


def test_the_verdict_follows_the_janitor_without_a_run(client):
    """And when the janitor does move the status, the verdict moves with it —
    for nothing, on a project that may have no budget left to spend."""
    from tests.selftest_invariants.test_commitments import (
        chairman_accept,
        committed_project,
        run_commitment_check,
    )
    from yaaf.core.models import Commitment, Run

    project, _token, _rids, proposed = committed_project(client, slug="slips")
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    assert brief(client, project)["verdict"]["state"] == "on_track"

    with db_session() as session:
        session.get(Commitment, proposed["commitment_id"]).deliver_by = \
            utcnow() - timedelta(days=1)
    run_commitment_check(project)

    with db_session() as session:
        before = session.query(Run).filter_by(project_id=project["id"]).count()
    payload = brief(client, project)
    assert payload["verdict"]["state"] == "breached"
    assert payload["verdict"]["severity"] == "critical"
    assert payload["verdict"]["label"] == "past due"
    with db_session() as session:
        assert session.query(Run).filter_by(project_id=project["id"]).count() == before


def test_no_agent_can_move_the_verdict(client):
    """The verdict is only as trustworthy as the row under it. There is no
    route by which the CEO writes a commitment status, and this is that rule
    attacked from the briefing's side: the panel a chairman reads is downstream
    of a wall an agent's token cannot reach around."""
    from tests.selftest_invariants.test_commitments import chairman_accept, committed_project
    from yaaf.core.models import Commitment, CommitmentStatus

    project, token, _rids, proposed = committed_project(client, slug="forge")
    chairman_accept(client, proposed["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    cid = proposed["commitment_id"]
    # A 404 is a pass, and the strongest kind: the route does not exist, so
    # there is nothing to talk around. The rule is enforced by absence.
    for path, body in (
        (f"/agent/commitments/{cid}", {"status": "on_track"}),
        (f"/agent/commitments/{cid}/accept", {}),
        (f"/agent/commitments/{cid}/move", {"deliver_by": "2099-01-01T00:00:00Z"}),
        (f"/agent/commitments/{cid}/status", {"status": "met"}),
    ):
        response = client.post(path, json=body, headers=bearer(token))
        assert response.status_code >= 400, \
            f"{path} answered an agent token about a commitment status"

    with db_session() as session:
        assert session.get(Commitment, cid).status == CommitmentStatus.ACCEPTED
    assert brief(client, project)["verdict"]["state"] == "on_track"


# ── exceptions: ranked, capped, and silent when nothing is wrong ────────────────


def test_a_clean_window_interrupts_nobody(client):
    """Exception reporting is the shape a board runs on, and its whole content
    is the silence. The old panel gave a healthy project and a stalling one
    thirteen bullets each."""
    project = deliver_project(client, slug="calm")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    assert payload["exceptions"] == []
    assert payload["lines"], "the record is still composed — it just is not shouted"


def test_exceptions_are_ranked_by_one_definition_not_by_builder_order(client):
    """The bug this replaces: order was the order the line builders happened to
    be concatenated, which put 'the project had work and started nothing for
    hours' at position nine, underneath a list of file versions."""
    from yaaf.core.ids import new_id
    from yaaf.core.models import Notice, NoticeKind

    project = deliver_project(client, slug="ranked")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    cut = client.post(f"/admin/requirements/{rid}/cut",
                      json={"reason": "not worth the money after all"}, headers=ADMIN)
    assert cut.status_code == 200, cut.text
    with db_session() as session:
        session.add(Notice(
            id=new_id("ntc"), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.STALLED,
            body="had work and started nothing"))

    payload = brief(client, project)
    ranks = [e["rank"] for e in payload["exceptions"]]
    assert ranks == sorted(ranks), "the server must ship them worst-first"
    assert [e["rank"] for e in payload["exceptions"]] == \
        [briefing.exception_rank(e["kind"]) for e in payload["exceptions"]]
    assert payload["exceptions"][0]["kind"] == "notice_stalled", \
        "a stall outranks a cut requirement, whatever order the builders ran in"
    # And the headline carries it, so the alarm survives even a reader who
    # reads one line.
    assert "started nothing" in payload["headline"]


def test_an_exception_the_verdict_already_names_is_not_repeated(client):
    """One fact twice is how a list of three stops being read."""
    from yaaf.core.ids import new_id
    from yaaf.core.models import Notice, NoticeKind

    project = make_project(client, slug="doubled")
    with db_session() as session:
        session.add(Notice(
            id=new_id("ntc"), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_EXHAUSTED,
            body="the monthly budget ran out"))
    client.post(f"/admin/projects/{project['id']}/pause",
                json={"reason": "budget exhausted: 300/300 USD this month"},
                headers=ADMIN)

    payload = brief(client, project)
    assert payload["verdict"]["state"] == "paused"
    assert "notice_budget_exhausted" not in {e["kind"] for e in payload["exceptions"]}
    assert "notice_budget_exhausted" in kinds(payload), \
        "suppressed from the summary, never from the record"


def test_at_most_three_exceptions_reach_the_chairman(client):
    """Three is the number a person reads. A fourth is where a list becomes a
    table, and a table is what this panel was."""
    from yaaf.core.ids import new_id
    from yaaf.core.models import Notice, NoticeKind

    project = deliver_project(client, slug="swamped")
    with db_session() as session:
        for kind in (NoticeKind.STALLED, NoticeKind.BUDGET_WARNING,
                     NoticeKind.MEMORY_FULL, NoticeKind.CYCLE_ROLLED,
                     NoticeKind.EXPLORE_DEADLINE):
            session.add(Notice(
                id=new_id("ntc"), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind=kind, body="something"))

    payload = brief(client, project)
    assert len(payload["exceptions"]) == briefing.MAX_EXCEPTIONS
    assert len([k for k in kinds(payload) if k.startswith("notice_")]) >= 5


# ── what a chairman can actually read ───────────────────────────────────────────


def test_progress_always_carries_its_denominator(client):
    """"12 requirements settled" is a number nobody can judge: twelve of forty
    and twelve of thirteen are opposite weeks wearing the same sentence."""
    project = deliver_project(client, slug="fraction")
    token = ceo_token(project)
    for n in range(3):
        rid = propose(client, token, statement=f"Thing {n} exists and is real")
        accept(client, rid["requirement_id"], project)

    payload = brief(client, project)
    movement = payload["movement"]
    assert movement["total"] == 3
    assert movement["settled"] <= movement["total"]
    assert f"of {movement['total']}" in movement["text"]


def test_the_summary_prints_no_raw_token_count(client):
    """`73,738,051` is nine glyphs the eye slides off, and it was on the desk
    beside every project."""
    import re

    project = make_project(client, slug="bigspend")
    with db_session() as session:
        row = session.get(Project, project["id"])
        row.billing_mode = "subscription"
        session.add(LedgerEntry(
            id=new_id("led"), project_id=project["id"], kind=LedgerKind.MODEL_USAGE,
            tokens_in=40_000_000, tokens_out=33_738_051, at=utcnow()))

    payload = brief(client, project)
    assert payload["money"]["window"] == 73_738_051
    assert "73.7M" in payload["money"]["text"]
    prose = (payload["money"]["text"] + payload["headline"]
             + payload["movement"]["text"] + payload["verdict"]["reason"])
    assert re.search(r"\d[\d,]{5,}", prose) is None, \
        f"a raw six-figure number reached the chairman: {prose!r}"


def test_the_summary_speaks_no_internal_vocabulary(client):
    """The platform talking to itself in front of the board. Requirement keys
    stay legal in an exception's `cause`, where "R-07 has eaten 53 runs" is the
    most actionable sentence on the page — and nowhere else."""
    import re

    project = deliver_project(client, slug="jargon")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    prose = " ".join([
        payload["verdict"]["reason"], payload["movement"]["text"],
        payload["money"]["text"],
    ] + [e["text"] for e in payload["exceptions"]])
    for leak in ("plan gate", "standing obligation", "not_before",
                 "variance narrative", "R-00", "PENDING", "CHECKING"):
        assert leak not in prose, f"{leak!r} is the platform talking to itself"
    assert re.search(r"\bR-\d+\b", prose) is None


def test_a_stalled_project_is_never_collapsed_to_one_line(client):
    """A stall that began before the window logs nothing inside it, so the
    project is `quiet` — and the panel used to hush exactly those into a
    one-liner. `hushable` is the separate question, and this is why it exists."""
    from yaaf.core.ids import new_id
    from yaaf.core.models import Notice, NoticeKind

    project = deliver_project(client, slug="hushed")
    seen(client, project)
    with db_session() as session:
        session.add(Notice(
            id=new_id("ntc"), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.STALLED,
            body="had work and started nothing"))

    payload = brief(client, project)
    assert payload["hushable"] is False
    assert payload["exceptions"], "the reason it may not be hushed"


def test_the_desk_puts_the_worst_project_first(client):
    """Creation order — which is what this was — put a stalled project
    underneath three healthy ones for no reason but its age."""
    from tests.selftest_invariants.test_commitments import (
        chairman_accept,
        committed_project,
        run_commitment_check,
    )
    from yaaf.core.models import Commitment

    healthy, _t, _r, first = committed_project(client, slug="healthy")
    chairman_accept(client, first["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    sick, _t2, _r2, second = committed_project(client, slug="sick")
    chairman_accept(client, second["commitment_id"],
                    deliver_by=utcnow() + timedelta(days=30))
    with db_session() as session:
        session.get(Commitment, second["commitment_id"]).deliver_by = \
            utcnow() - timedelta(days=1)
    run_commitment_check(sick)

    desk = client.get("/observe/briefing", headers=ADMIN).json()
    order = [b["project_id"] for b in desk["projects"]
             if b["project_id"] in (healthy["id"], sick["id"])]
    assert order == [sick["id"], healthy["id"]]


def test_the_email_carries_the_summary_and_the_detail(client):
    """One composer, two deliveries — and the mail is the delivery whose reader
    cannot click through to the project's own page, so it keeps both."""
    project = deliver_project(client, slug="mail")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    with db_session() as session:
        payload = briefing.project_briefing(
            session, session.get(Project, project["id"]))
    text = briefing.render_text(payload)
    assert payload["headline"] in text
    assert payload["movement"]["text"] in text
    assert payload["money"]["text"] in text
    for line in payload["lines"]:
        assert line["text"] in text
    for exception in payload["exceptions"]:
        assert exception["text"] in text


def test_the_email_leads_with_the_same_exception_the_panel_does(client):
    """Ranking is done once, on the server. A panel and an email that disagreed
    about which line was the alarming one would be worse than either alone."""
    from yaaf.core.ids import new_id
    from yaaf.core.models import Notice, NoticeKind

    project = deliver_project(client, slug="agree")
    with db_session() as session:
        for kind in (NoticeKind.MEMORY_FULL, NoticeKind.STALLED,
                     NoticeKind.BUDGET_WARNING):
            session.add(Notice(
                id=new_id("ntc"), project_id=project["id"],
                agent_id=project["ceo_agent_id"], kind=kind, body="x"))
        payload = briefing.project_briefing(
            session, session.get(Project, project["id"]))
    text = briefing.render_text(payload)
    positions = [text.index(e["text"]) for e in payload["exceptions"]]
    assert positions == sorted(positions)
    assert payload["exceptions"][0]["kind"] == "notice_stalled"


def test_the_margin_on_a_promise_is_never_flattering(client):
    """`timedelta.days` floors toward negative infinity, which got this wrong in
    both directions and always in the project's favour: a projection two hours
    before the date read "1 day inside it", and one twenty hours past the date
    dropped the "days past" clause entirely. A delivery forecast may only ever
    be wrong in the pessimistic direction on the page a chairman decides from.
    """
    from tests.selftest_invariants.test_commitments import (
        chairman_accept,
        committed_project,
    )
    from yaaf.core.models import Commitment

    project, _token, _rids, proposed = committed_project(client, slug="margin")
    deliver_by = utcnow() + timedelta(days=30)
    chairman_accept(client, proposed["commitment_id"], deliver_by=deliver_by)

    # Two hours from missing is not "a day inside it".
    with db_session() as session:
        session.get(Commitment, proposed["commitment_id"]).projected_done_at = \
            deliver_by - timedelta(hours=2)
    payload = brief(client, project)
    assert payload["verdict"]["days_late"] == 0
    assert "inside it" not in payload["verdict"]["reason"]

    # And a day and a bit of margin rounds down to one, never up.
    with db_session() as session:
        session.get(Commitment, proposed["commitment_id"]).projected_done_at = \
            deliver_by - timedelta(hours=30)
    assert brief(client, project)["verdict"]["days_late"] == -1

    # Twenty hours past the signed date is a day late, and says so.
    with db_session() as session:
        session.get(Commitment, proposed["commitment_id"]).projected_done_at = \
            deliver_by + timedelta(hours=20)
    assert brief(client, project)["verdict"]["days_late"] == 1


def test_a_budget_warning_never_takes_a_chairman_s_exception_slot(client):
    """Five things write a BUDGET_WARNING notice and the briefing renders all of
    them as "the budget passed its warning line". Opening a measurement window
    on a project at 5% of budget must not put that sentence in front of the
    chairman, and must not spend one of his three slots on it."""
    from yaaf.core.models import Notice, NoticeKind

    project = deliver_project(client, slug="windowed")
    with db_session() as session:
        session.add(Notice(
            id=new_id("ntc"), project_id=project["id"],
            agent_id=project["ceo_agent_id"], kind=NoticeKind.BUDGET_WARNING,
            body="a measurement window opened"))

    payload = brief(client, project)
    assert "notice_budget_warning" not in {e["kind"] for e in payload["exceptions"]}
    assert "notice_budget_warning" in kinds(payload), \
        "still on the record — it is only kept out of the summary"
    assert "warning line" not in payload["headline"]
