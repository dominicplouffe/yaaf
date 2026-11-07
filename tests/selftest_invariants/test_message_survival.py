"""A message a dead run was woken for is read by somebody, or it is unread.

Building a context pack marks mail read: it moves the agent's cursor past what
the pack shows. A run can die before any model reads that pack — a sandbox
that will not start dies in a tenth of a second — and the runner retries it.
Two things went wrong from there. The retries were shown "recent" mail with no
cursor in the query, which, oldest first, meant the channel's ten oldest
messages ever. And when the runner gave up, it gave back the run's notices and
suggestions (test_suggestion_survival) but not its mail: the cursor stayed
where the dead run left it, so a chairman's "are you there?" was read by
nobody, and nothing would ever wake the CEO for it again.

The sibling of test_suggestion_survival; the same shape, for messages.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import ADMIN, ceo_read_the_boardroom, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Run, RunQueue, RunStatus, utcnow
from yaaf.runner import daemon
from yaaf.runner.daemon import execute_job, lease_and_prepare
from yaaf.runner.types import RunResult
from yaaf.server import dispatcher
from yaaf.server.contextpack import build_context_pack


def _say(client, project: dict, body: str) -> None:
    response = client.post(f"/admin/projects/{project['id']}/boardroom",
                           json={"body": body}, headers=ADMIN)
    assert response.status_code == 200, response.text


def _attempt(project: dict, prior: str | None = None, attempt: int = 1) -> tuple[str, list[str]]:
    """One attempt of a CEO wake builds its pack, as the runner does at lease;
    returns the run and the message bodies the pack showed."""
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        run = session.get(Run, run_id)
        run.attempt, run.prior_run_id = attempt, prior
        pack = build_context_pack(session, run, session.get(Agent, project["ceo_agent_id"]),
                                  session.get(Project, project["id"]))
    return run_id, [m["body"] for channel in pack["unread"] for m in channel["messages"]]


def _dies(run_id: str, **left_behind) -> None:
    """The attempt fails, and the runner does what it does with a failure.
    A queued retry is taken by the next attempt, which the test builds."""
    with db_session() as session:
        run = session.get(Run, run_id)
        run.status, run.finished_at = RunStatus.FAILED, utcnow()
        for name, value in left_behind.items():
            setattr(run, name, value)
        daemon._retry_failed_run(session, run)
    with db_session() as session:
        session.query(RunQueue).delete()


def _given_up(project: dict, **first_attempt_left) -> None:
    """A whole wake: three attempts, every one dead, the runner giving up."""
    first, _ = _attempt(project)
    _dies(first, **first_attempt_left)
    second, _ = _attempt(project, prior=first, attempt=2)
    _dies(second)
    third, _ = _attempt(project, prior=second, attempt=daemon.MAX_RUN_ATTEMPTS)
    _dies(third)


def _unread_for_the_ceo(project: dict) -> list[str]:
    with db_session() as session:
        ceo = session.get(Agent, project["ceo_agent_id"])
        return [m.body for m, _ in dispatcher.unread_messages_for(
            session, session.get(Project, project["id"]), ceo)]


class _Sandbox:
    """A backend standing in for the sandbox: broken, the way host mode was
    on a uv-made venv, or working."""

    def __init__(self, works: bool):
        self.works = works

    def execute(self, job, stop_event=None):
        if self.works:
            return RunResult(ok=True, summary="answered", tokens_in=900, tokens_out=40)
        return RunResult(ok=False, summary="sandbox exited 1 without a result file; tail:\n"
                                            "bwrap: execvp .venv/bin/python: No such file "
                                            "or directory\n")


def _ceo_job(project: dict):
    jobs = [j for j in lease_and_prepare(limit=10) if j.agent_id == project["ceo_agent_id"]]
    assert len(jobs) == 1, jobs
    return jobs[0]


def test_the_night_this_was_found(client, caplog):
    """End to end, through the runner: a CEO on two wakes an hour, the
    chairman's message, a sandbox that dies three times, the runner giving
    up — and then the sandbox fixed. She must wake for the message she never
    read, be shown it, and the runner's log must say each run happened."""
    import logging

    caplog.set_level(logging.INFO, logger="yaaf.runner")
    project = make_project(client, constitution={"wakes_per_agent_per_hour": 2})
    with db_session() as session:
        dispatcher.dispatch_once(session)  # the project's first planning wake
        session.query(RunQueue).delete()
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")

    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1
    for attempt in range(1, daemon.MAX_RUN_ATTEMPTS + 1):
        job = _ceo_job(project)
        assert job.attempt == attempt
        execute_job(job, backend=_Sandbox(works=False))
        with db_session() as session:  # the retry's backoff, elapsed
            for queued in session.query(RunQueue).all():
                queued.not_before = utcnow() - timedelta(seconds=1)

    # Fixed. One wake of two spent, and the message unread: she wakes now.
    with db_session() as session:
        assert dispatcher.dispatch_once(session) == 1, \
            "the chairman's message must wake the CEO once the sandbox works"
    job = _ceo_job(project)
    assert job.attempt == 1
    shown = [m["body"] for channel in job.context_pack["unread"] for m in channel["messages"]]
    assert "are you there?" in shown
    execute_job(job, backend=_Sandbox(works=True))

    said = caplog.text
    assert said.count("started: CEO, attempt 1") == 2 and "attempt 3" in said
    assert "before any model acted" in said and "unread again" in said
    assert f"run {job.run_id} ended done" in said, "a run that worked says so"
    assert "No such file or directory" in said, "and one that died says why"


def test_a_retry_is_shown_the_message_that_woke_it(client):
    """Not the channel's oldest mail: the one the first attempt was shown."""
    project = make_project(client)
    for n in range(12):
        _say(client, project, f"old {n}")
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")

    first, shown = _attempt(project)
    assert "are you there?" in shown
    _dies(first)
    _, retried = _attempt(project, prior=first, attempt=2)
    assert "are you there?" in retried, "the retry must see what woke the wake"
    assert not [b for b in retried if b.startswith("old ")], \
        "mail read long ago is not what the retry is for"


def test_a_wake_given_up_before_any_model_acted_leaves_its_mail_unread(client):
    """The night this was found: three attempts, each dead at the sandbox's
    door, and the chairman's message marked read by none of them."""
    project = make_project(client)
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")
    _given_up(project)

    assert _unread_for_the_ceo(project) == ["are you there?"], \
        "no model read it, so it is not read"
    with db_session() as session:
        session.query(RunQueue).delete()  # the project's own startup wakes
        # Past the wake brake's hour, so the only question is the mail.
        for run in session.query(Run).all():
            run.created_at = run.created_at - timedelta(hours=2)
    with db_session() as session:
        assert dispatcher.dispatch_once(session) >= 1
        wake = session.query(RunQueue).filter_by(agent_id=project["ceo_agent_id"]).one()
        assert any(r["kind"] == "messages" and r.get("urgent")
                   for r in wake.trigger["reasons"]), "and it wakes her again for it"


@pytest.mark.parametrize("left", [{"tokens_in": 900}, {"produced": 1}],
                         ids=["a model was billed for it", "it wrote something"])
def test_mail_a_model_read_stays_read_when_the_wake_is_given_up(client, left):
    """Give back only a read nobody made. An attempt a model was billed for
    was shown the mail, and one that wrote something acted on it; putting the
    mail back would have every failure after that replay the chairman's words."""
    project = make_project(client)
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")
    _given_up(project, **left)

    assert _unread_for_the_ceo(project) == []


def test_a_harness_that_never_got_an_answer_read_nothing(client):
    """The night after the first: the jail started Python, the harness wrote
    its trace, and every call to the model's API died at the name lookup. A
    trace is the harness, not a model — that mail was read by nobody."""
    project = make_project(client)
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")
    _given_up(project, trace_path="traces/first-attempt.jsonl")

    assert _unread_for_the_ceo(project) == ["are you there?"]


def test_giving_mail_back_never_marks_anything_read(client):
    """The give-back only moves a cursor backwards. A message that arrived
    after the wake died is unread before the give-back and after it."""
    project = make_project(client)
    ceo_read_the_boardroom(project["id"])
    _say(client, project, "are you there?")
    first, _ = _attempt(project)
    _dies(first)
    second, _ = _attempt(project, prior=first, attempt=2)
    _dies(second)
    third, _ = _attempt(project, prior=second, attempt=daemon.MAX_RUN_ATTEMPTS)
    _say(client, project, "hello again")
    _dies(third)

    assert _unread_for_the_ceo(project) == ["are you there?", "hello again"]
    # Handed a position later than the cursor — a stale record, a second
    # give-back — it leaves the cursor alone rather than skip mail with it.
    from yaaf.core.models import Channel, ChannelKind
    from yaaf.server.contextpack import give_back_reads

    with db_session() as session:
        room = session.query(Channel).filter_by(project_id=project["id"],
                                                kind=ChannelKind.BOARDROOM).one()
        give_back_reads(session, project["ceo_agent_id"], {room.id: utcnow().isoformat()})
    assert _unread_for_the_ceo(project) == ["are you there?", "hello again"]
