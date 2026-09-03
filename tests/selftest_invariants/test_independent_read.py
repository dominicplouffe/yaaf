"""Invariant selftests: work with an observable test stops closing on its author's word.

A command test is judged by the platform running it. An observable — which is
what every prose deliverable gets, because a blog post has no exit code — used
to go straight to DONE on ten characters of self-report, with a 20% sampled
audit that explicitly never blocks anything. So the only reader of a
deliverable before the chairman was the agent that wrote it.

Where the constitution asks for an independent read, such a task now waits in
REVIEW for somebody who did not write it. Off by default, so an install that
upgrades is unchanged.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Notice, Task, TaskStatus

CHAIR = {"email": "chair@example.com", "display_name": "Chair"}
OBSERVABLE = {"type": "observable", "target": "deliverables/post.md exists and cites its sources"}


def _project(client, *, review: str) -> dict:
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership")
    seed_role(client, "scribe", category="content")
    seed_role(client, "reader", category="challenge")
    teams = [{"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
             {"role_slug": "reader", "name": "Review", "employees": ["Juno"]}]
    created = client.post("/admin/projects", json={
        "slug": "desk", "name": "Desk", "goal": "Publish something worth reading.",
        "chairman_email": CHAIR["email"],
        "constitution": {"deliverable_review": review}, "teams": teams,
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
    project = created.json()
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    return project


def _agent(project: dict, name: str) -> str:
    for team in project["teams"]:
        for agent in team["agents"]:
            if agent["name"] == name:
                return agent["id"]
    raise AssertionError(name)


def _finished_task(client, project: dict, *, success_test=None) -> str:
    """A task written and completed by Ada, the way a deliverable arrives."""
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    editorial = next(t for t in project["teams"] if t["name"] == "Editorial")
    created = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": editorial["id"], "title": "Write the launch post",
        "goal": "One post answering the question buyers actually ask.",
        "success_test": success_test or OBSERVABLE,
    })
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]

    _, ada = live_token(project["id"], _agent(project, "Ada"))
    assert client.post(f"/agent/tasks/{task_id}/claim", headers=bearer(ada)).status_code == 200
    done = client.post(f"/agent/tasks/{task_id}/complete", headers=bearer(ada),
                       json={"evidence": "Wrote deliverables/post.md, 900 words, 6 sources."})
    assert done.status_code == 200, done.text
    return task_id, done.json()["status"]


def test_without_the_rule_nothing_changes(client):
    """The default, and what every existing project keeps doing."""
    project = _project(client, review="none")
    _, status = _finished_task(client, project)
    assert status == "done"


def test_an_observable_close_waits_for_somebody_who_did_not_write_it(client):
    project = _project(client, review="independent_read")
    task_id, status = _finished_task(client, project)
    assert status == "review"

    # The author cannot sign off their own work. That is the whole state.
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    mine = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(ada),
                       json={"ok": True, "notes": "Looks good to me, shipping it."})
    assert mine.status_code == 403 and "did not write it" in mine.text

    _, juno = live_token(project["id"], _agent(project, "Juno"))
    signed = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(juno),
                         json={"ok": True, "notes": "Checked both claims against the sources."})
    assert signed.status_code == 200 and signed.json()["status"] == "done"
    with db_session() as session:
        assert session.get(Task, task_id).status == TaskStatus.DONE


def test_a_returned_task_goes_back_to_its_author_with_the_reasons(client):
    """Returning work is ordinary, not an escalation — and the author has to be
    told, or a returned task looks exactly like one they already finished."""
    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)

    _, juno = live_token(project["id"], _agent(project, "Juno"))
    sent_back = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(juno), json={
        "ok": False,
        "notes": "Nothing here a reader could not get from the first page of results."})
    assert sent_back.status_code == 200 and sent_back.json()["status"] == "returned"

    with db_session() as session:
        task = session.get(Task, task_id)
        assert task.status == TaskStatus.CLAIMED
        assert task.claimed_by_agent_id == _agent(project, "Ada"), "still hers to fix"
        notice = session.query(Notice).filter_by(
            agent_id=task.claimed_by_agent_id).order_by(Notice.id.desc()).first()
        assert notice, "the author must be told, or a returned task looks finished"
        assert "first page of results" in notice.body and task_id in notice.body


def test_a_command_test_is_still_judged_by_the_platform(client):
    """A reviewer would be a second opinion about an exit code. Only the
    observable close — the one that used to be taken on trust — is read."""
    project = _project(client, review="independent_read")
    _, status = _finished_task(client, project, success_test={
        "type": "command", "command": "test -f /workspace/deliverables/post.md"})
    assert status == "checking"


def test_only_a_reviewing_seat_signs_off(client):
    """An opinion in the team room is free. Closing somebody's work is not."""
    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)

    added = client.post(f"/admin/projects/{project['id']}/teams", json={
        "role_slug": "scribe", "name": "Second Desk", "employees": ["Kit"]}, headers=ADMIN)
    assert added.status_code == 200, added.text
    _, kit = live_token(project["id"], added.json()["agents"][0]["id"])
    refused = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(kit),
                          json={"ok": True, "notes": "Read it, seems fine to me."})
    assert refused.status_code == 403 and "discipline" in refused.text


def test_a_project_that_lost_its_reviewer_still_closes_work(client):
    """Creation refuses a roster with no reader, but a project can lose one
    afterwards — an agent paused, a role refiled. A task stuck in REVIEW
    forever would be worse than the self-report it replaced."""
    project = _project(client, review="independent_read")
    paused = client.post(f"/admin/agents/{_agent(project, 'Juno')}/pause",
                         headers=ADMIN, json={"paused": True})
    assert paused.status_code == 200, paused.text
    _, status = _finished_task(client, project)
    assert status == "done"


def test_the_reviewer_is_woken_for_work_that_is_waiting(client):
    """A REVIEW task that wakes nobody is a hole the board falls into."""
    from yaaf.server import dispatcher

    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)
    with db_session() as session:
        reasons = dispatcher._work_reasons(
            session, session.get(Agent, _agent(project, "Juno")),
            __import__("yaaf.core.models", fromlist=["utcnow"]).utcnow())
    assert any(r["kind"] == "review_owed" and r["task_id"] == task_id for r in reasons), reasons


# ── the depth floor ─────────────────────────────────────────────────────────────


def _claimed_evidence_task(client, project: dict, *, external: bool = True) -> str:
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    editorial = next(t for t in project["teams"] if t["name"] == "Editorial")
    created = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": editorial["id"], "title": "Find out what buyers actually ask",
        "goal": "The question, and where the answer comes from.",
        "success_test": OBSERVABLE, "produces_external_evidence": external,
    })
    assert created.status_code == 200, created.text
    task_id = created.json()["id"]
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    assert client.post(f"/agent/tasks/{task_id}/claim",
                       headers=bearer(ada)).status_code == 200
    return task_id


def _complete(client, project: dict, task_id: str, evidence: str):
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    return client.post(f"/agent/tasks/{task_id}/complete", headers=bearer(ada),
                       json={"evidence": evidence})


def test_research_that_never_says_what_it_missed_does_not_close(client):
    """A summary of whatever ranks well is a fact about search engines, not
    about the world, and it is indistinguishable from real work unless the
    empty results are written down."""
    project = _project(client, review="none")
    client.post(f"/admin/projects/{project['id']}/amendments", headers=ADMIN, json={
        "reason": "shallow passes are reading identically to deep ones",
        "changes": {"require_search_record": True}})

    task_id = _claimed_evidence_task(client, project)
    thin = _complete(client, project, task_id, "Read the top results and summarised them.")
    assert thin.status_code == 422
    assert "EMPTY" in thin.text

    # The same task, once the record is there — the agent adds it and closes.
    full = _complete(client, project, task_id, (
        "I looked for published pricing for the three competitors. I searched their "
        "sites, G2 and two industry reports. Two publish nothing — every figure "
        "quoted online traces back to one 2023 blog post, so that number came back "
        "empty as an independent source."))
    assert full.status_code == 200, full.text


def test_the_middle_of_the_floor_is_not_free(client):
    """The three groups were matched as substrings, and "read" is inside
    "already", "ready" and "reader" — so the "where you looked" third was
    satisfied by almost any sentence in English, including the exact shallow
    note this rule exists to refuse."""
    project = _project(client, review="none")
    client.post(f"/admin/projects/{project['id']}/amendments", headers=ADMIN, json={
        "reason": "turning the floor on", "changes": {"require_search_record": True}})

    task_id = _claimed_evidence_task(client, project)
    # "already" carries "read"; "nothing" and "looked for" carry the other two.
    sneaky = _complete(client, project, task_id, (
        "I looked for the pricing and already had nothing useful on file."))
    assert sneaky.status_code == 422, sneaky.text

    honest = _complete(client, project, task_id, (
        "I looked for published pricing. I searched their sites and two industry "
        "reports; two of the three came back empty as an independent source."))
    assert honest.status_code == 200, honest.text


def test_the_floor_is_off_by_default_and_only_touches_evidence_work(client):
    """An install that upgrades is unchanged, and a task that produces no
    outside evidence is not a research pass to be graded."""
    project = _project(client, review="none")
    off = _claimed_evidence_task(client, project)
    assert _complete(client, project, off,
                     "Summarised the top results.").status_code == 200

    client.post(f"/admin/projects/{project['id']}/amendments", headers=ADMIN, json={
        "reason": "turning the floor on", "changes": {"require_search_record": True}})
    inward = _claimed_evidence_task(client, project, external=False)
    assert _complete(client, project, inward,
                     "Moved the drafts into one folder.").status_code == 200


# ── the fixes the first pass of this feature needed ─────────────────────────────


def test_the_reviewer_can_actually_see_the_work_it_was_woken_for(client):
    """The board is filtered to the agent's own team, and a review seat sits on
    its own team by design — so the reviewer was woken for a task that did not
    appear on the only screen it has. A wake with nothing on it is worse than
    no wake: it buys a run to look at an empty board."""
    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)

    _, juno = live_token(project["id"], _agent(project, "Juno"))
    board = client.get("/agent/board", headers=bearer(juno))
    assert board.status_code == 200, board.text
    waiting = [t for t in board.json()["open"] if t["id"] == task_id]
    assert waiting, board.json()

    # And not to everyone: an ordinary seat still sees only its own team.
    added = client.post(f"/admin/projects/{project['id']}/teams", json={
        "role_slug": "scribe", "name": "Second Desk", "employees": ["Kit"]}, headers=ADMIN)
    _, kit = live_token(project["id"], added.json()["agents"][0]["id"])
    other = client.get("/agent/board", headers=bearer(kit))
    assert not [t for t in other.json()["open"] if t["id"] == task_id]


def test_the_sandbox_can_reach_the_review_route(client):
    """A rule the agent has no tool for is a rule that never fires. This route
    shipped with no way in from the sandbox, which made the whole state dead in
    production while every test here passed over HTTP."""
    import os
    from unittest import mock

    # The module reads its run's identity at import; none of it is used here.
    with mock.patch.dict(os.environ, {"YAAF_API_BASE_URL": "http://localhost",
                                      "YAAF_API_TOKEN": "t", "YAAF_RUN_ID": "run_x"}):
        from yaaf.runner import sandbox_entry

    names = {getattr(t, "name", None) or t.__name__ for t in sandbox_entry.ALL_TOOLS}
    assert any("task_review" in str(n) for n in names), sorted(str(n) for n in names)


def test_work_in_review_still_occupies_its_author(client):
    """One task at a time is counted from CLAIMED and CHECKING. A task in
    REVIEW is finished but still the author's — a return hands it straight
    back — so without REVIEW in that count the author frees a slot, claims
    something else, and then owns two."""
    project = _project(client, review="independent_read")
    _finished_task(client, project)

    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    editorial = next(t for t in project["teams"] if t["name"] == "Editorial")
    second = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": editorial["id"], "title": "Write the second post",
        "goal": "Another one, while the first is being read.",
        "success_test": OBSERVABLE})
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    refused = client.post(f"/agent/tasks/{second.json()['id']}/claim", headers=bearer(ada))
    assert refused.status_code == 409, refused.text


def test_a_review_that_keeps_returning_becomes_a_question_not_a_loop(client):
    """Two agents disagreeing costs two runs a cycle and re-arms forever. The
    command-check loop already has a ceiling for exactly this; the read uses
    the same counter and the same number."""
    from yaaf.core.constitution import effective

    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)
    with db_session() as session:
        cap = effective(session, project["id"]).task_check_failures_before_block

    _, juno = live_token(project["id"], _agent(project, "Juno"))
    for round_no in range(1, cap + 1):
        sent_back = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(juno),
                                json={"ok": False, "notes": f"Still thin, round {round_no}."})
        assert sent_back.status_code == 200, sent_back.text
        expected = "blocked" if round_no == cap else "returned"
        assert sent_back.json()["status"] == expected, (round_no, sent_back.text)
        if expected == "returned":
            _complete(client, project, task_id, "Rewrote it, same shape.")

    with db_session() as session:
        task = session.get(Task, task_id)
        assert task.status == TaskStatus.BLOCKED
        assert "whoever planned the work" in task.blocked_reason


def test_whitespace_is_not_a_review(client):
    """`min_length` counts spaces, and ten spaces is a sign-off with no reasons
    in it — which is precisely the rubber stamp this whole state exists to
    prevent."""
    project = _project(client, review="independent_read")
    task_id, _ = _finished_task(client, project)
    _, juno = live_token(project["id"], _agent(project, "Juno"))
    blank = client.post(f"/agent/tasks/{task_id}/review", headers=bearer(juno),
                        json={"ok": True, "notes": "            "})
    assert blank.status_code == 422, blank.text


# ── the ways a REVIEW task could be a hole instead of a step ────────────────────


def test_a_task_stranded_in_review_is_closed_rather_than_wedged(client):
    """`review_is_owed` asks whether a reader exists before the fork, so a
    project with none never sends work to REVIEW. This is the other order: the
    task went in while a reader existed and then the reader was paused. Nothing
    else clears it — the claim reaper only looks at CLAIMED, `lost_checks` only
    at CHECKING — and REVIEW holds its author's one-at-a-time slot, so the
    wedge is not one task but that seat, permanently."""
    from yaaf.core.constitution import effective
    from yaaf.server import janitor

    project = _project(client, review="independent_read")
    task_id, status = _finished_task(client, project)
    assert status == "review"

    paused = client.post(f"/admin/agents/{_agent(project, 'Juno')}/pause",
                         headers=ADMIN, json={"paused": True})
    assert paused.status_code == 200, paused.text

    with db_session() as session:
        row = session.get(__import__("yaaf.core.models", fromlist=["Project"]).Project,
                          project["id"])
        janitor.release_unreadable_reviews(session, row)
        effective(session, row.id)
    with db_session() as session:
        task = session.get(Task, task_id)
        assert task.status == TaskStatus.DONE
        assert "no reviewing seat" in _last_event_note(session, task_id)

    # And the author's slot is free again.
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    editorial = next(t for t in project["teams"] if t["name"] == "Editorial")
    nxt = client.post("/agent/tasks", headers=bearer(ceo), json={
        "team_id": editorial["id"], "title": "Write the next post",
        "goal": "The seat has to be usable again.", "success_test": OBSERVABLE})
    _, ada = live_token(project["id"], _agent(project, "Ada"))
    assert client.post(f"/agent/tasks/{nxt.json()['id']}/claim",
                       headers=bearer(ada)).status_code == 200


def _last_event_note(session, task_id: str) -> str:
    from yaaf.core.models import TaskEvent

    event = session.query(TaskEvent).filter_by(task_id=task_id) \
        .order_by(TaskEvent.id.desc()).first()
    return event.note or ""


def test_a_board_of_nothing_but_reads_does_not_render_as_empty(client):
    """The exact bug CHECKING was added to this ladder to fix, one state along:
    a REVIEW row wakes nobody in the ordinary lane and is claimed by nobody, so
    without a rung of its own a project whose whole board is waiting on a
    reader reported "Nothing waiting. The board is empty" — to the chairman and
    to the stall watchdog."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Project
    from yaaf.server import dispatcher

    project = _project(client, review="independent_read")
    _finished_task(client, project)
    with db_session() as session:
        row = session.get(Project, project["id"])
        state = dispatcher.activity_state(session, row, effective(session, row.id))
    assert state["board"]["reviewing"] == 1
    assert state["waiting"]["reviewing_tasks"] == 1, (
        "the stall watchdog reads this; at zero it returns early and says nothing")
    assert state["board"]["unreachable"] != "nothing_waiting"


def test_only_one_reviewer_is_woken_for_a_piece(client):
    """Two reviewing seats — the roster this project's own doctrine argues for
    — both woke for the same piece, and the second one paid full price to find
    it already closed."""
    from yaaf.core.models import Agent, Project, utcnow
    from yaaf.server import dispatcher

    project = _project(client, review="independent_read")
    added = client.post(f"/admin/projects/{project['id']}/teams", json={
        "role_slug": "reader", "name": "Second Read", "employees": ["Nia"]},
        headers=ADMIN)
    assert added.status_code == 200, added.text
    task_id, _ = _finished_task(client, project)

    with db_session() as session:
        row = session.get(Project, project["id"])
        woken = [
            agent.name for agent in session.query(Agent).filter_by(project_id=row.id)
            if any(r["kind"] == "review_owed" and r["task_id"] == task_id
                   for r in dispatcher._work_reasons(session, agent, utcnow()))
        ]
    assert len(woken) == 1, woken
