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

from base64 import b64encode
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
from yaaf.core.models import (
    Heartbeat,
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
    assert "closed" in payload["headline"]


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
    assert "Nothing" in payload["headline"]
    assert payload["pulse"]["open_tasks"] == 0
    assert "nothing on the board" in payload["headline"]


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
    assert "is paused — budget exhausted" in sent[0]


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


# ── reading order: the triage the panel exists to do ────────────────────────────


def cut(client, rid: str, reason: str = "the client already has this") -> None:
    response = client.post(f"/admin/requirements/{rid}/cut",
                           json={"reason": reason}, headers=ADMIN)
    assert response.status_code == 200, response.text


def test_every_line_is_filed_under_one_of_three_questions(client):
    """A briefing that prints thirteen facts at one weight, in the order the
    composer happened to query them, hands the reader the triage the panel
    exists to do. Every line therefore declares which question it answers."""
    project = deliver_project(client, slug="grouped")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)
    cut(client, propose(client, token)["requirement_id"])

    payload = brief(client, project)
    assert payload["lines"], "this window did things"
    for line in payload["lines"]:
        assert line["group"] in ("attention", "moved", "record"), line
    by_kind = {line["kind"]: line for line in payload["lines"]}
    assert by_kind["requirement_cut"]["group"] == "attention"
    assert by_kind["tasks_done"]["group"] == "moved"
    assert by_kind["runs"]["group"] == "record"
    assert payload["groups"]["attention"] >= 1
    assert sum(payload["groups"].values()) == len(payload["lines"])


def test_the_lines_arrive_worst_first_not_in_query_order(client):
    """The composer runs scope, then work, then proof, then runs — a sensible
    order to write and a useless one to read. It put a stall between "20
    deliverables published" and "the CEO exercised 26 decisions"."""
    project = deliver_project(client, slug="ordered")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)
    cut(client, propose(client, token)["requirement_id"])

    groups = [line["group"] for line in brief(client, project)["lines"]]
    assert groups == sorted(groups, key=["attention", "moved", "record"].index)


def test_nothing_bad_is_filed_as_bookkeeping(client):
    """The record group folds away on the panel. A setback the reader has to
    unfold to find is one he will not find, so tone outranks kind."""
    for kind in briefing.RECORD:
        assert briefing._line(kind, "x", tone="bad")["group"] == "attention"
        assert briefing._line(kind, "x")["group"] == "record"


def test_a_setback_reaches_the_headline_even_in_a_week_that_shipped(client):
    """The headline used to promote a setback only when *nothing* landed, so a
    window that shipped scope AND cut scope announced the shipping and left the
    cut as a grey line ten. A good week with a hole in it is not a good week."""
    project = deliver_project(client, slug="mixed")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)
    cut(client, propose(client, token)["requirement_id"])

    headline = brief(client, project)["headline"]
    assert "closed" in headline, "what landed still leads"
    assert "but" in headline and "cut from scope" in headline


def test_the_headline_does_not_reprint_the_money_row(client):
    """It sat directly above a line that prints the same number: "…13 proofs
    verified — 101,638,431 tokens spent" over "101.6M this window". A number
    you are about to read twice is a number that stops being read."""
    project = deliver_project(client, slug="nomoney")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    assert "spent" not in payload["headline"]
    assert "$" not in payload["headline"] and "token" not in payload["headline"]
    assert payload["money"]["window"] is not None, "it is still one row lower"


def test_the_standing_explanation_is_not_welded_onto_the_fact(client):
    """"15 requirements settled true (R-30, R-33…) — the platform ran the
    acceptance test itself" is one fact and one sentence the reader has seen
    forty times. The second is kept, and kept out of the first."""
    project = deliver_project(client, slug="noted")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    gate = next(line for line in brief(client, project)["lines"]
                if line["kind"] == "plan_gate_closed")
    assert gate["note"] == "the plan is staged, estimated and fits the money"
    assert "staged" not in gate["text"], "the standing half lives in the note"
    assert gate["text"] == "the plan gate closed — work may now be created"


def test_a_document_revised_four_times_is_one_deliverable(client):
    """"20 deliverables published (ticket.md v4, ticket.md v3, ticket.md v2…)"
    overstated the week by four and buried what was actually new. The count is
    documents; the revisions are the note."""
    project = deliver_project(client, slug="revised")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    for body in (b"draft", b"second pass", b"final"):
        response = client.post(
            "/agent/artifacts",
            json={"filename": "ticket.md", "content_base64": b64encode(body).decode()},
            headers=bearer(token))
        assert response.status_code == 200, response.text

    line = next(line for line in brief(client, project)["lines"]
                if line["kind"] == "published")
    assert line["count"] == 1, "one document, whatever its version number"
    assert line["text"].startswith("1 deliverable published")
    assert "ticket.md v3" in line["text"], "the latest version, not the first"
    assert "ticket.md v2" not in line["text"]
    assert line["note"] == "2 of the 3 versions in the window were revisions of these"


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
        if line["note"]:
            assert line["note"] in text, "the explanation travels with the fact"
    for group, heading in briefing.GROUP_HEADINGS.items():
        if payload["groups"][group]:
            assert heading in text, "the email groups the way the panel does"
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
