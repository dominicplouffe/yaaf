"""Invariant selftests: the finish line is a number the platform reads.

`Project.goal` was prose interpolated into a system prompt, read by no gate and
no forecast. The only thing the platform could verify was a shell command run
against the project's own workspace, so that is what every requirement on every
desk became — and a desk whose goal was "finish inside the top 500,000 by GW38"
had eleven greps, no requirement for the rank at all, and a platform that
believed the 38-gameweek season was 1.1 weeks from done.

`GOAL_GRAMMAR.md` already stated the rule: *a finish line the project can write
into its own status report is not a finish line.* COMMAND and OBSERVABLE are
both structurally exactly that. METRIC is the kind that is not, and each of its
rules is attacked here the way an agent would, over HTTP with a live run token:

  * a metric must name a source outside the project — a vague one is refused,
    because it is the obvious way to launder a self-reported number
  * no agent writes a reading, because the verb does not exist
  * it settles on the READING, never on the work closing
  * a metric moving the wrong way raises no block: the work may be right and
    the world hostile
  * it is excluded from the burn-down, so a season stops reading as nearly done
"""

from __future__ import annotations

from tests.conftest import bearer
from tests.selftest_invariants.test_requirements import (
    CMD_TEST,
    accept,
    ceo_token,
    deliver_project,
    make_task,
    propose,
    tick,
)
from yaaf.core.db import db_session
from yaaf.core.models import (
    Measurement,
    Requirement,
    RequirementStatus,
)
from yaaf.server import metrics

RANK_TEST = {
    "type": "metric",
    "source": {
        "kind": "http_json",
        "url": "https://fantasy.premierleague.com/api/entry/8171155/history/",
        "path": "$.current[-1].overall_rank",
    },
    "direction": "decreasing",
    "target": 500000,
    "measure_every_hours": 24,
}


def fetch_returning(*values):
    """A fetch that hands back successive readings, then repeats the last."""
    seq = list(values)

    def _fetch(url: str):
        import json as _json

        value = seq.pop(0) if len(seq) > 1 else seq[0]
        return 200, _json.dumps({"current": [{"overall_rank": value}]}).encode()

    return _fetch


def requirement_row(requirement_id: str) -> Requirement:
    with db_session() as session:
        row = session.get(Requirement, requirement_id)
        session.expunge(row)
        return row


# ── the source must be outside the project ──────────────────────────────────────


def test_a_metric_without_a_real_source_is_refused(client):
    """The attack: launder a self-reported number through the rigorous-looking
    kind. Every one of these is a metric an agent could satisfy by writing a
    file, or by nothing at all."""
    project = deliver_project(client)
    token = ceo_token(project)

    for bad, why in [
        ({"type": "metric", "direction": "decreasing", "target": 1}, "no source"),
        ({"type": "metric", "source": "the dashboard",
          "direction": "decreasing", "target": 1}, "source is prose"),
        ({"type": "metric", "source": {"kind": "workspace", "path": "rank.txt"},
          "direction": "decreasing", "target": 1}, "a source the project writes"),
        ({"type": "metric", "source": {"kind": "http_json", "url": "not-a-url",
                                       "path": "$.a"},
          "direction": "decreasing", "target": 1}, "not fetchable"),
        ({"type": "metric", "source": {"kind": "http_json",
                                       "url": "https://x.test/a.json"},
          "direction": "decreasing", "target": 1}, "no path to a number"),
        ({"type": "metric", "source": {"kind": "http_json",
                                       "url": "https://x.test/a.json", "path": "$.a"},
          "target": 1}, "no direction, so no opinion"),
        ({"type": "metric", "source": {"kind": "http_json",
                                       "url": "https://x.test/a.json", "path": "$.a"},
          "direction": "decreasing"}, "no target, so it can never arrive"),
        ({"type": "metric", "source": {"kind": "http_json",
                                       "url": "https://x.test/a.json", "path": "$.a"},
          "direction": "decreasing", "target": 1, "measure_every_hours": 0.001},
         "read so often it is spend with no information in it"),
    ]:
        response = client.post(
            "/agent/requirements",
            json={"statement": f"A metric that is really {why}",
                  "acceptance_test": bad, "est_runs": 3},
            headers=bearer(token),
        )
        assert response.status_code >= 400, f"accepted a metric with {why}: {bad}"


def test_a_real_metric_is_accepted(client):
    project = deliver_project(client)
    token = ceo_token(project)
    proposed = propose(client, token, statement="Overall rank is inside the top 500,000",
                       test=RANK_TEST)
    assert proposed["status"] == RequirementStatus.PROPOSED


# ── only the platform writes a reading ──────────────────────────────────────────


def test_no_agent_route_can_write_a_measurement(client):
    """The strongest form of "the project does not author its own scoreboard"
    is that the verb does not exist. Attacked by trying the shapes an agent
    would guess, with a live CEO token."""
    project = deliver_project(client)
    token = ceo_token(project)
    body = {"requirement_id": "reqt_x", "value": 1.0}
    for path in ("/agent/measurements", "/agent/metrics", "/agent/measurements/record",
                 "/agent/requirements/measure"):
        response = client.post(path, json=body, headers=bearer(token))
        assert response.status_code in (403, 404, 405), (
            f"{path} answered {response.status_code}; an agent must not be able to "
            "write its own reading"
        )


# ── it settles on the reading, not on the work ──────────────────────────────────


def test_closing_every_task_does_not_settle_a_metric(client):
    """The attack that matters most: do all the work, then claim the outcome.

    A command requirement settles when its tasks close and the check passes.
    If a metric did the same, a project could reach its finish line by finishing
    its own to-do list — which is the exact substitution this test kind exists
    to stop.
    """
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, statement="Overall rank is inside the top 500,000",
                     test=RANK_TEST)
    accept(client, metric["requirement_id"], project)

    # Give it work, and close all of it.
    created = make_task(client, token, project, metric["requirement_id"],
                        success_test=CMD_TEST)
    assert created.status_code == 200, created.text
    with db_session() as session:
        from yaaf.core.models import Task, TaskStatus

        for task in session.query(Task).filter(
            Task.requirement_id == metric["requirement_id"]
        ):
            task.status = TaskStatus.DONE
    tick(project)
    with db_session() as session:
        janitor_sweep(session)

    assert requirement_row(metric["requirement_id"]).status != RequirementStatus.MET, (
        "a metric requirement settled because the project finished its own tasks"
    )


def test_a_reading_at_target_settles_it(client):
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, statement="Overall rank is inside the top 500,000",
                     test=RANK_TEST)
    accept(client, metric["requirement_id"], project)
    with db_session() as session:
        row = session.get(Requirement, metric["requirement_id"])
        row.status = RequirementStatus.IN_PROGRESS

    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        metrics.record(session, requirement, fetch=fetch_returning(412_000))
    with db_session() as session:
        metrics.settle_metrics(session, project["id"])

    assert requirement_row(metric["requirement_id"]).status == RequirementStatus.MET


def test_a_reading_short_of_target_does_not(client):
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)
    with db_session() as session:
        session.get(Requirement, metric["requirement_id"]).status = (
            RequirementStatus.IN_PROGRESS
        )
    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        metrics.record(session, requirement, fetch=fetch_returning(6_698_328))
    with db_session() as session:
        metrics.settle_metrics(session, project["id"])
    assert requirement_row(metric["requirement_id"]).status != RequirementStatus.MET


# ── losing is reported, never enforced ──────────────────────────────────────────


def test_a_metric_going_the_wrong_way_reads_as_losing_and_blocks_nothing(client):
    """A failing command test blocks its task: a check that keeps failing means
    the work is wrong. A metric moving away from target may mean the work is
    right and the world is hostile, so it says so and stops nobody."""
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)

    for value in (4_087_187, 2_627_278, 6_698_328):
        with db_session() as session:
            requirement = session.get(Requirement, metric["requirement_id"])
            metrics.record(session, requirement, fetch=fetch_returning(value))

    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        board = metrics.scoreboard(session, requirement)
    assert board["verdict"] == "losing", board
    assert board["value"] == 6_698_328
    assert board["trend"] == [4_087_187, 2_627_278, 6_698_328]

    # ...and the board is still open for business.
    allowed = make_task(client, token, project, metric["requirement_id"],
                        success_test=CMD_TEST)
    assert allowed.status_code == 200, (
        "a losing metric blocked work; it is a report, not a wall: " + allowed.text
    )


def test_one_bad_reading_is_not_losing(client):
    """A metric that cried wolf on noise would be muted inside a week."""
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)
    for value in (6_698_328, 3_000_000, 3_100_000):   # net closer than it started
        with db_session() as session:
            requirement = session.get(Requirement, metric["requirement_id"])
            metrics.record(session, requirement, fetch=fetch_returning(value))
    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        assert metrics.scoreboard(session, requirement)["verdict"] == "gaining"


# ── a failed read is a row, not a silence ───────────────────────────────────────


def test_a_reading_that_fails_is_recorded(client):
    """A scoreboard that stops reporting looks exactly like one reporting no
    change, and the difference is the whole point of having one."""
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)

    def boom(url):
        raise TimeoutError("the source did not answer")

    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        metrics.record(session, requirement, fetch=boom)
    with db_session() as session:
        row = session.query(Measurement).filter(
            Measurement.requirement_id == metric["requirement_id"]
        ).one()
        assert row.error and "did not answer" in row.error
        requirement = session.get(Requirement, metric["requirement_id"])
        assert metrics.scoreboard(session, requirement)["verdict"] == "unknown"


# ── the burn-down stops lying ───────────────────────────────────────────────────


def test_a_season_long_metric_is_not_burned_down_at_the_memo_rate(client):
    """The FPL regression, in miniature.

    Eleven grep-over-workspace requirements plus one season-long rank target,
    divided by the rate memos were landing, produced `weeks_left_at_pace: 1.1`
    on a 38-gameweek season. The memos really were nearly done; the season was
    not, and the arithmetic could not tell the difference.
    """
    from yaaf.server import velocity

    project = deliver_project(client)
    token = ceo_token(project)
    doc = propose(client, token, statement="The GW4 memo is posted", test=CMD_TEST)
    accept(client, doc["requirement_id"], project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)

    with db_session() as session:
        pace = velocity.project_velocity(session, project["id"])

    assert pace["metrics_open"] == 1
    assert pace["remaining"] == 1, (
        f"the metric was counted as a unit of work: remaining={pace['remaining']}"
    )
    assert "measured separately" in pace["weeks_left_covers"]
    assert pace["metrics"][0]["target"] == 500000


def test_the_ledger_source_reads_revenue_nobody_here_can_write(client):
    """The reference goal's finish line, as a row.

    `docs/projects/idea-to-customer.md` says it plainly: revenue booked through
    the payment processor's signed webhook, "the platform's own record, not a
    claim in a status report". Those rows were already unforgeable — only the
    connector's signature books one — and were then excluded from every sum in
    the system and read by no requirement, flag or score. This is that channel
    finally wired to something.
    """
    from yaaf.core import ids
    from yaaf.core.models import LedgerEntry, LedgerKind

    project = deliver_project(client)
    token = ceo_token(project)
    revenue = {
        "type": "metric",
        "source": {"kind": "ledger", "ledger_kind": LedgerKind.REVENUE.value},
        "direction": "increasing",
        "target": 1,
        "measure_every_hours": 24,
    }
    metric = propose(client, token, test=revenue,
                     statement="A stranger has paid us at least one dollar")
    accept(client, metric["requirement_id"], project)
    with db_session() as session:
        session.get(Requirement, metric["requirement_id"]).status = (
            RequirementStatus.IN_PROGRESS
        )

    # Nothing booked yet: measured, and short.
    with db_session() as session:
        metrics.record(session, session.get(Requirement, metric["requirement_id"]))
    with db_session() as session:
        metrics.settle_metrics(session, project["id"])
    assert requirement_row(metric["requirement_id"]).status != RequirementStatus.MET

    # The webhook books a dollar.
    with db_session() as session:
        session.add(LedgerEntry(id=ids.new_id(ids.LEDGER), project_id=project["id"],
                                kind=LedgerKind.REVENUE, usd=1.0))
    with db_session() as session:
        metrics.record(session, session.get(Requirement, metric["requirement_id"]))
    with db_session() as session:
        metrics.settle_metrics(session, project["id"])
    assert requirement_row(metric["requirement_id"]).status == RequirementStatus.MET


def test_a_task_cannot_carry_a_metric(client):
    """A task's check runs when the work closes; a metric may not move for
    weeks. A task carrying one could never be completed, so it is refused at
    creation rather than left to rot in `claimed`."""
    project = deliver_project(client)
    token = ceo_token(project)
    requirement = propose(client, token, test=CMD_TEST)
    accept(client, requirement["requirement_id"], project)

    refused = make_task(client, token, project, requirement["requirement_id"],
                        success_test=RANK_TEST)
    assert refused.status_code >= 400, refused.text
    assert "belongs on a requirement" in refused.json()["error"]


def test_a_deliver_project_can_hold_an_outcome_it_cannot_yet_decompose(client):
    """The plan gate and ambition are no longer a choice between two.

    PLAN-05's whole finding was that the only mode in which a project could be
    genuinely ambitious was Explore — the one mode with none of the verification
    machinery. `docs/projects/idea-to-customer.md` says why in as many words:
    take Deliver "and the project owes a plan before it may create any work at
    all, and in week one it would be writing acceptance tests for a product it
    has not chosen yet. Those requirements would be fiction, and the plan gate
    would be checking the arithmetic of fiction."

    That was true of the two test kinds that existed. A `command` test describes
    the WORK, so committing to one in week one means inventing work you have not
    designed. A `metric` test describes the OUTCOME, so it can be accepted
    before anybody knows how — the test does not get truer or falser depending
    on what you decide to do about it.

    So: a Deliver project whose entire scope is a number it does not yet know
    how to move, and whose plan gate closes anyway. This is PLAN-05's stated
    acceptance test, and it passing is why that plan dissolved instead of being
    built.
    """
    from yaaf.server import policy

    project = deliver_project(client, slug="outcome")
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST, est_runs=40,
                     statement="Overall rank is inside the top 500,000 at GW38")
    accept(client, metric["requirement_id"], project)
    tick(project)

    with db_session() as session:
        plan = policy.plan_requirement(session, project["id"])
        assert plan.status == RequirementStatus.MET, (
            "the plan gate did not close on a project whose only scope is a "
            "measured outcome, so Deliver still forces fiction"
        )

    # And work may be created for it without the requirement having described
    # that work — which is the half a command test cannot do.
    made = make_task(client, token, project, metric["requirement_id"],
                     success_test=CMD_TEST)
    assert made.status_code == 200, made.text


def janitor_sweep(session) -> None:
    from yaaf.server.janitor import janitor_once

    janitor_once(session)


def test_a_settled_metric_does_not_speed_up_the_pace(client):
    """It is out of `remaining`, so it must be out of the pace numerator too.

    Otherwise a season-long target landing raises requirements-met-per-week on a
    denominator that deliberately excludes it, and `commitment_projection`
    divides one by the other to promise the chairman an earlier date — the
    memos look faster because the season finished.
    """
    from yaaf.server import velocity

    project = deliver_project(client, slug="pace")
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)
    with db_session() as session:
        session.get(Requirement, metric["requirement_id"]).status = (
            RequirementStatus.IN_PROGRESS
        )
    with db_session() as session:
        metrics.record(session, session.get(Requirement, metric["requirement_id"]),
                       fetch=fetch_returning(412_000))
    with db_session() as session:
        metrics.settle_metrics(session, project["id"])

    with db_session() as session:
        pace = velocity.project_velocity(session, project["id"])
    assert pace["met_3w"] == 0, (
        f"the settled metric counted as scope landing: met_3w={pace['met_3w']}"
    )


def test_a_metric_cannot_switch_off_the_drought_floor(client):
    """`measure_every_hours` is agent-authored and floored at one hour, so if a
    reading counted as countable external evidence a CEO could propose one
    metric on an hourly schedule and guarantee the drought never fired again.

    A floor an agent's own request can disable is what house rule 1 forbids, and
    the two were never the same question anyway: a drought asks whether the
    PROJECT produced proof from outside itself; a reading is the platform going
    and looking at a number on a timer. A ledger-source metric never leaves the
    box at all.
    """
    from yaaf.server import policy

    project = deliver_project(client, slug="drought")
    token = ceo_token(project)
    metric = propose(client, token, test={**RANK_TEST, "measure_every_hours": 1},
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)

    with db_session() as session:
        requirement = session.get(Requirement, metric["requirement_id"])
        metrics.record(session, requirement, fetch=fetch_returning(400_000))

    with db_session() as session:
        from datetime import timedelta

        from yaaf.core.models import utcnow

        counted = policy.countable_external_evidence(
            session, project["id"], utcnow() - timedelta(hours=24))
    assert counted == 0, (
        "a metric reading counts as the project's own external evidence, so an "
        "hourly metric disables the drought floor for good"
    )


# ── the platform's own network is not the agent's to point ──────────────────────


def test_a_metric_cannot_point_the_platform_at_its_own_network(client):
    """These fetches do not happen in the sandbox.

    An agent run is a container behind a per-role egress allowlist. The metric
    measurer runs in the janitor, on the platform's own network, with the
    platform's credentials ambient — so a URL an agent supplies is a request for
    the CONTROL PLANE to connect somewhere of the agent's choosing. Left open
    that reaches cloud metadata, the database on localhost, and any admin port
    on the loopback interface; and since the failure string is read back through
    `Measurement.error`, a refused connection and a parse failure are
    distinguishable, which maps a network one port at a time.
    """
    from yaaf.core.safefetch import UnsafeURL, fetch

    for url in ("http://169.254.169.254/latest/meta-data/iam/security-credentials/",
                "http://127.0.0.1:5432/", "http://localhost:8080/admin",
                "http://10.0.0.5/internal", "file:///etc/passwd"):
        try:
            fetch(url)
        except UnsafeURL:
            continue
        except Exception as exc:   # noqa: BLE001 — any connection attempt is the failure
            raise AssertionError(f"{url} was attempted, not refused: {exc}") from exc
        raise AssertionError(f"{url} was fetched")


def test_the_evidence_verifier_is_behind_the_same_guard():
    """It fetches agent-supplied URLs from the same process and predates the
    metric measurer, so it had the same hole and now shares the fix."""
    import inspect

    from yaaf.server import verifier

    assert "safefetch" in inspect.getsource(verifier._default_fetch)
