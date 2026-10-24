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
from pathlib import Path

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
)
from yaaf.core.constitution import AUTONOMY_PRESETS, ConstitutionDoc
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
                              "body": "we need the quotes",
                              "if_declined": "the model runs on yesterday's quotes",
                              "amount_usd": 200.0},
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
    assert "declined, as your rules say silence means" in line["text"]


def test_a_question_that_ran_out_of_time_is_named_here_too(client):
    """`_self_acting_lines` promises "every automatic resolution in the window
    is named here", and a lapsed ask is exactly one: the CEO asked, the window
    closed, and it did the thing it had told him it would do. Shipping the
    mechanism without this line would have made that docstring false, and a
    section that claims completeness and is not is worse than no section."""
    from yaaf.core.models import Ask
    from yaaf.server import janitor

    project = make_project(client, slug="lapsed-brief")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": "which candidate for the launch post",
        "body": "Five ranked, my pick is first. Your call before we write.",
        "why": "The launch post goes out Friday and the writing takes two days.",
        "answer_by_hours": 6,
        "default_action": "Take the top-ranked one and tell you I did."})
    assert asked.status_code == 200, asked.text

    with db_session() as session:
        session.get(Ask, asked.json()["ask_id"]).answer_by = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        janitor.expire_asks(session, session.get(Project, project["id"]))

    payload = brief(client, project)
    assert "asks_lapsed" in kinds(payload)
    line = next(x for x in payload["lines"] if x["kind"] == "asks_lapsed")
    assert line["tone"] == "bad"
    assert "which candidate for the launch post" in line["text"]


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
        lambda session, proj, subject, body, **kw: sent.append((subject, body)),
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
        lambda session, proj, subject, body, **kw: sent.append(body),
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
        lambda session, proj, subject, body, **kw: sent.append(body),
    )
    assert _fire(project) is False
    assert sent == []


def test_no_time_set_means_no_daily_briefing(client, monkeypatch):
    # Set to nothing on purpose. A project created with no opinion now gets the
    # morning letter (`constitution.default_briefing`); one that says null is
    # the chairman turning it off, and that is what this guards.
    project = make_project(client, slug="unset", constitution={"brief_daily_at": None})
    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body, **kw: sent.append(body),
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
        lambda session, proj, subject, body, **kw: None,
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
        lambda session, proj, subject, body, **kw: None,
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
        assert line["group"] in ("attention", "moved", "delegated"), line
    by_kind = {line["kind"]: line for line in payload["lines"]}
    assert by_kind["requirement_cut"]["group"] == "attention"
    assert by_kind["tasks_done"]["group"] == "moved", "supervised: the plan is his"
    assert by_kind["runs"]["group"] == "delegated", "a run tally is his at no dial"
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
    assert groups == sorted(groups, key=list(briefing.GROUPS).index)


def test_a_setback_he_still_owns_is_never_folded(client):
    """The delegated group folds away on the panel. A setback he still holds
    the authority over must not be inside it, so among his own lines tone
    decides — and a line he handed over is folded whatever its tone, which is
    the entire request the autonomy dial makes."""
    supervised = ConstitutionDoc.model_validate(AUTONOMY_PRESETS["supervised"])
    autonomous = ConstitutionDoc.model_validate(AUTONOMY_PRESETS["autonomous"])
    his_setback = briefing._triage(
        [briefing._line("evidence_failed", "x", tone="bad")], supervised)[0]
    assert his_setback["group"] == "attention"
    handed_over = briefing._triage(
        [briefing._line("evidence_failed", "x", tone="bad")], autonomous)[0]
    assert handed_over["group"] == "delegated"
    # Money and liveness are nobody's to delegate, at either end of the dial.
    for dial in (supervised, autonomous):
        stall = briefing._triage(
            [briefing._line("notice_stalled", "x", tone="bad")], dial)[0]
        assert stall["group"] == "attention"


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


# ── the autonomy dial: whose problem is it ──────────────────────────────────────


def autonomous_project(client, slug: str) -> dict:
    return deliver_project(client, slug=slug,
                           constitution=dict(AUTONOMY_PRESETS["autonomous"]))


def test_the_same_window_reads_differently_at_each_end_of_the_dial(client):
    """The briefing printed the same lines whether the chairman had kept every
    authority or handed over all five, which makes the dial a lie on the one
    screen where it should be loudest. A chairman who set it to autonomous said
    *deal with it, I have given it to you*."""
    for slug, dial in (("kept", "supervised"), ("gave", "autonomous")):
        project = deliver_project(client, slug=slug,
                                  constitution=dict(AUTONOMY_PRESETS[dial]))
        token = ceo_token(project)
        rid = propose(client, token)["requirement_id"]
        accept(client, rid, project)
        close_a_task(client, project, token, rid)
        payload = brief(client, project)
        assert payload["autonomy"] == dial
        by_kind = {line["kind"]: line for line in payload["lines"]}
        if dial == "supervised":
            assert by_kind["tasks_done"]["yours"] is True
            assert by_kind["requirement_accepted"]["yours"] is True
            kept = payload
        else:
            assert by_kind["tasks_done"]["yours"] is False
            assert by_kind["requirement_accepted"]["yours"] is False
            assert payload["needs_you"] < kept["needs_you"], \
                "handing over authority must buy a shorter briefing"


def test_an_outcome_is_material_even_where_its_management_is_delegated(client):
    """The split worth stating outright. A requirement settling true is what he
    gets; a requirement entering scope is how he gets it, and he said he did
    not want to watch that part."""
    autonomous = ConstitutionDoc.model_validate(AUTONOMY_PRESETS["autonomous"])
    yours = {kind: briefing._yours(kind, autonomous) for kind in (
        "requirement_met", "requirement_cut", "published", "notice_revenue",
        "notice_budget_exhausted", "notice_stalled", "notice_commitment_breached",
        "approvals_expired", "asks_lapsed", "rules_changed",
        "requirement_accepted", "tasks_done", "evidence_verified", "ceo_authority",
        "plan_gate_closed", "runs", "runs_dead",
    )}
    assert all(yours[kind] for kind in (
        "requirement_met", "requirement_cut", "published", "notice_revenue",
        "notice_budget_exhausted", "notice_stalled", "notice_commitment_breached",
        "approvals_expired", "asks_lapsed", "rules_changed")), yours
    assert not any(yours[kind] for kind in (
        "requirement_accepted", "tasks_done", "evidence_verified", "ceo_authority",
        "plan_gate_closed", "runs", "runs_dead")), yours


def test_a_line_nobody_classified_arrives_rather_than_vanishing(client):
    """The failure mode of a filter is silence, and silence is the one thing
    this panel exists to prevent. A kind missing from the table is his."""
    autonomous = ConstitutionDoc.model_validate(AUTONOMY_PRESETS["autonomous"])
    assert briefing._yours("some_future_line_kind", autonomous) is True


def test_every_kind_the_composer_emits_has_been_classified(client):
    """Not a style rule: an unclassified kind is loud forever, so the table has
    to keep up with the composer rather than the other way around."""
    import re
    source = (Path(briefing.__file__)).read_text()
    emitted = set(re.findall(r'_line\(\s*\n?\s*"([a-z_]+)"', source))
    emitted |= {f"notice_{kind}" for kind in briefing.SELF_ACTING_NOTICES}
    unclassified = emitted - set(briefing.LINE_OWNER) - {"x"}
    assert not unclassified, f"classify these in LINE_OWNER: {sorted(unclassified)}"


def test_a_project_running_itself_says_so_rather_than_reporting_in(client):
    """Zero lines of his is the answer an autonomous project should usually
    give. A blank panel cannot be told from a dead project, and a management
    report is the thing he asked not to receive — so it is one sentence."""
    project = autonomous_project(client, "handsoff")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    payload = brief(client, project)
    assert payload["needs_you"] == 0
    assert payload["quiet"] is False, "the window was not empty — it was not HIS"
    assert payload["lines"], "the CEO did do things"
    assert all(line["group"] == "delegated" for line in payload["lines"])
    assert payload["headline"] == (
        f"Nothing needed you — the CEO handled {len(payload['lines'])} things itself.")
    assert payload["groups"]["delegated"] == len(payload["lines"]), \
        "the sentence names the number the fold beside it shows"


def test_the_desk_gives_a_full_block_only_to_what_needs_you(client):
    """Splitting on `quiet` gave a busy autonomous project a full block anyway,
    so the reward for delegating well was a longer desk."""
    project = autonomous_project(client, "busy")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)

    row = next(p for p in client.get("/observe/briefing", headers=ADMIN).json()["projects"]
               if p["slug"] == "busy")
    assert row["quiet"] is False, "the window was not empty"
    assert row["needs_you"] == 0, "and none of it was his"


def test_the_headline_never_contradicts_the_list_beneath_it(client):
    """Found by running it, not by reading it. The headline's progress ladder
    counts four kinds — requirements settled, deliverables, tasks, proof — and
    an autonomy dial that delegates the last three left a window whose only
    visible progress was a publish. It printed "Nothing settled" two inches
    above "2 deliverables published". A headline may be brief; it may not
    disagree with the panel."""
    project = autonomous_project(client, "shipped")
    token = ceo_token(project)
    rid = propose(client, token)["requirement_id"]
    accept(client, rid, project)
    close_a_task(client, project, token, rid)
    response = client.post(
        "/agent/artifacts",
        json={"filename": "pack.md", "content_base64": b64encode(b"final").decode()},
        headers=bearer(token))
    assert response.status_code == 200, response.text

    payload = brief(client, project)
    moved = [line for line in payload["lines"] if line["group"] == "moved"]
    assert moved, "the publish is his at every dial"
    assert "Nothing settled" not in payload["headline"]
    assert "deliverable published" in payload["headline"]


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
        lambda session, proj, subject, body, **kw: sent.append(body),
    )
    assert _fire(project) is False
    assert sent == []


def test_a_new_project_with_no_opinion_gets_the_morning_letter(client):
    from yaaf.core.constitution import DEFAULT_BRIEF_AT

    project = make_project(client, slug="letter")
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["constitution"]["brief_daily_at"] == DEFAULT_BRIEF_AT


def test_saying_no_letter_is_kept(client):
    project = make_project(client, slug="noletter", constitution={"brief_daily_at": None})
    dash = client.get(f"/observe/projects/{project['id']}", headers=ADMIN).json()
    assert dash["constitution"]["brief_daily_at"] is None


def test_the_morning_letter_names_its_window_the_way_a_person_would(client, monkeypatch):
    project = deliver_project(client, slug="named")
    _set_brief_time(client, project, "00:01")
    sent: list[str] = []
    monkeypatch.setattr(
        "yaaf.server.janitor.chairman_notify",
        lambda session, proj, subject, body, **kw: sent.append(body),
    )
    assert _fire(project) is True
    assert "the window you asked for" not in sent[0]
    assert "since this project started" in sent[0]


def _publish(client, project: dict, filename: str, content: bytes) -> None:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/artifacts", headers=bearer(token), json={
        "filename": filename, "content_base64": b64encode(content).decode()})
    assert response.status_code == 200, response.text


def test_the_letter_opens_on_what_was_delivered_with_a_way_to_open_it(client, monkeypatch):
    """A publish never mails, so the morning letter is where a result reaches
    somebody who did not open the app. It used to say "1 deliverable
    published (memo.md)" halfway down; it now leads with the result."""
    from yaaf.core import config

    monkeypatch.setattr(config.settings(), "public_base_url", "https://yaaf.example.com")
    project = make_project(client, slug="results")
    _publish(client, project, "memo.md", b"# The answer\nPause Generic - broad.")
    _publish(client, project, "memo.md", b"# The answer, v2\nPause Generic - broad.")

    with db_session() as session:
        row = session.get(Project, project["id"])
        brief = briefing.project_briefing(session, row)
    delivered = brief["delivered"]
    assert [d["name"] for d in delivered] == ["memo.md"]
    assert delivered[0]["version"] == 2
    assert delivered[0]["link"].startswith(
        f"https://yaaf.example.com/app/#/projects/{project['id']}/details/deliverables?open=")

    text = briefing.render_text(brief)
    assert text.index("Delivered") < text.index("Window:")
    assert "memo.md (v2) — https://yaaf.example.com/app/#/projects/" in text


def test_without_a_public_origin_the_letter_names_results_and_links_nowhere(client, monkeypatch):
    from yaaf.core import config

    monkeypatch.setattr(config.settings(), "public_base_url", "")
    project = make_project(client, slug="nolink")
    _publish(client, project, "memo.md", b"# The answer")
    with db_session() as session:
        brief = briefing.project_briefing(session, session.get(Project, project["id"]))
    assert brief["delivered"][0]["link"] is None
    assert "+ memo.md\n" in briefing.render_text(brief)


def test_the_letter_leads_with_what_is_his_and_speaks_his_clock(client):
    """Decisions first, then results, then the account; and no UTC stamp for
    him to convert — the constitution already names his timezone."""
    project = make_project(client, slug="clock", constitution={"timezone": "America/New_York"})
    with db_session() as session:
        brief = briefing.project_briefing(session, session.get(Project, project["id"]))
    text = briefing.render_text(brief)
    assert text.index("Waiting on you") < text.index("Window:")
    assert "UTC" not in text
    assert ("EDT" in text) or ("EST" in text)
