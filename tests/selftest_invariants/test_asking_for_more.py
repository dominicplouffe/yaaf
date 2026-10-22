"""Invariant selftests: a project can argue for more room, and probing is not failing.

Three of PLAN-04's six one-way ratchets, the two the boardroom actually
evidenced first.

**There was no verb for "we need more".** Nine existed for saying something —
message_post, ask_chairman, suggest, decision_add, approval_request,
need_request, assist_request and the rest — and none for asking for more of
anything. `directive_file` is restricted to the rules that *limit what the CEO
asks of the chairman*, on the stated grounds that "an agent filing its own
constitution is how a project votes itself a bigger budget". That reasoning is
right, and it left a hole: it made asking impossible as well as taking. A live
CEO had the diagnosis exactly right — *"the platform also rations runs... that's
what actually paces us now... if we ever want genuinely more runs in a day,
that's a separate dial and I'd come to you with what it buys before asking"* —
and no route by which to come.

**A refused call was arithmetically a failed one.** TERMINATED_REFUSALS sat in
both the capacity and velocity dead-run sets, so probing a wall raised the
measured failure rate, which raised the retry reserve, which cut forecast slack,
which trips DEADLINE_AT_RISK. Testing where the edges are made a project look
like it was falling apart.

**The estimate multiplier could only ever rise.** Scored over all history and
floored at 1, so a project that overran early paid for it in every plan it would
ever make.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_requirements import ceo_token, deliver_project
from yaaf.core.db import db_session
from yaaf.core.models import ApprovalKind, RunStatus
from yaaf.server import velocity

CASE = {
    "buys": "Four analyses a gameweek instead of one, which is the difference between "
            "spotting a fixture swing and reading about it.",
    "costs": "About 6m more tokens a week, roughly $40 a month at current rates.",
    "without": "We stay at one analysis a week and keep losing ground to the field "
               "average, which is the position we are already in.",
}


def ask(client, token: str, rule: str = "runs_per_project_per_day", to: float = 60,
        **over):
    return client.post("/agent/capacity",
                       json={"rule": rule, "to": to, **CASE, **over},
                       headers=bearer(token))


# ── the verb that asks for more ─────────────────────────────────────────────────


def test_the_ceo_can_put_a_case_for_more_runs(client):
    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    token = ceo_token(project)

    response = ask(client, token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["from"] == 18 and body["to"] == 60
    # It must not move the dial. The chairman signs, or it expires to no.
    assert "has not moved" in body["note"]

    approvals = client.get("/agent/approvals", headers=bearer(token)).json()
    filed = [a for a in approvals if a["kind"] == ApprovalKind.CAPACITY]
    assert filed, f"nothing landed on the chairman's desk: {approvals}"


def test_the_dial_does_not_move_until_the_chairman_signs(client):
    from yaaf.core.constitution import effective

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    token = ceo_token(project)
    ask(client, token)
    with db_session() as session:
        assert effective(session, project["id"]).runs_per_project_per_day == 18, (
            "filing a capacity request raised the cap; a project that can grant its "
            "own request does not have a cap"
        )


def test_and_moves_when_he_does(client):
    """Sixteen were approved before this and none moved anything. One desk filed
    the same token-cap raise three times, because each yes left it where it was."""
    from yaaf.core.constitution import effective
    from yaaf.core.models import Approval

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    approval_id = ask(client, ceo_token(project)).json()["approval_id"]
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    with db_session() as session:
        assert effective(session, project["id"]).runs_per_project_per_day == 60
        assert session.get(Approval, approval_id).status == "approved"


def test_he_can_grant_a_different_number(client):
    from yaaf.core.constitution import effective

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    approval_id = ask(client, ceo_token(project)).json()["approval_id"]
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": True, "to": 30}, headers=ADMIN)
    with db_session() as session:
        assert effective(session, project["id"]).runs_per_project_per_day == 30


def test_a_yes_never_lowers_a_dial_he_already_raised(client):
    from yaaf.core.constitution import effective

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    approval_id = ask(client, ceo_token(project)).json()["approval_id"]
    client.post(f"/admin/projects/{project['id']}/amendments",
                json={"reason": "raised it myself meanwhile",
                      "changes": {"runs_per_project_per_day": 80}}, headers=ADMIN)
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": True}, headers=ADMIN)
    with db_session() as session:
        assert effective(session, project["id"]).runs_per_project_per_day == 80


def test_declining_keeps_the_dial(client):
    from yaaf.core.constitution import effective

    project = deliver_project(client, constitution={"runs_per_project_per_day": 18})
    approval_id = ask(client, ceo_token(project)).json()["approval_id"]
    client.post(f"/admin/approvals/{approval_id}/decide",
                json={"approve": False, "note": "Not this month."}, headers=ADMIN)
    with db_session() as session:
        assert effective(session, project["id"]).runs_per_project_per_day == 18


def test_the_arithmetic_is_required(client):
    """The difference between this verb and a remark in the boardroom is that
    this one carries the numbers to decide on."""
    project = deliver_project(client)
    token = ceo_token(project)
    for missing in ("buys", "costs", "without"):
        response = ask(client, token, **{missing: "x"})
        assert response.status_code >= 400, (
            f"a capacity request with no {missing} was accepted; that is a wish"
        )


def test_authority_is_not_capacity(client):
    """A project arguing for a lower bar on itself is not a capacity request.
    The whole point of the allowlist is that it governs how much work the
    project may do, never who decides or what good means."""
    project = deliver_project(client)
    token = ceo_token(project)
    for rule in ("verdict_policy", "audit_sample_rate", "min_success_test_len",
                 "stakes", "approval_timeout_days"):
        response = ask(client, token, rule=rule, to=99)
        assert response.status_code >= 400, (
            f"{rule} was accepted as a capacity dial: {response.text}"
        )


def test_asking_for_less_is_refused_and_names_the_right_verb(client):
    """Holding yourself to less is `directive_file`, which the CEO may already
    set itself. This verb is only for arguing you need more room."""
    project = deliver_project(client, constitution={"runs_per_project_per_day": 40})
    token = ceo_token(project)
    response = ask(client, token, to=10)
    assert response.status_code == 422
    assert "directive" in response.json()["error"]


def test_the_shared_dial_says_it_is_shared(client):
    """max_concurrent_runs protects the provider rate limit every project on the
    box shares, so granting it takes headroom from the others. That is not true
    of the rest of the list and the chairman should not have to know which."""
    project = deliver_project(client)
    token = ceo_token(project)
    assert ask(client, token, rule="max_concurrent_runs", to=8).status_code == 200

    from yaaf.core.models import Approval

    with db_session() as session:
        filed = session.query(Approval).filter(
            Approval.project_id == project["id"],
            Approval.kind == ApprovalKind.CAPACITY,
        ).one()
        assert "every project on this box shares" in filed.body


def test_only_the_ceo_argues_for_capacity(client):
    from tests.conftest import agent_by_name

    project = make_project(client, slug="cap2")
    employee = agent_by_name(project, "Emp2")
    _, token = live_token(project["id"], employee)
    response = ask(client, token)
    assert response.status_code == 403
    assert "suggestion" in response.json()["error"]


# ── probing a wall is not failing ───────────────────────────────────────────────


def test_a_refused_run_is_not_counted_as_a_failure(client):
    """The chain this breaks: refusal -> dead run -> failure rate up -> retry
    reserve up -> forecast slack down -> DEADLINE_AT_RISK. A CEO that tested
    where the edges were made its project look like it was falling apart."""
    assert RunStatus.TERMINATED_REFUSALS not in velocity.DEAD_STATUSES, (
        "a run that ended by being told no is counted as a run that broke"
    )
    assert RunStatus.TERMINATED_REFUSALS in velocity.PROBE_STATUSES


def test_but_it_is_still_reported(client):
    """Not swept under the rug: "how often is this project told no" is a real
    signal — an agent misreading the rules, or a rule in the way of work
    somebody wants done. It is simply not the same signal as "how often does
    this project break"."""
    from yaaf.core import ids
    from yaaf.core.models import Run, RunKind

    project = make_project(client, slug="probe")
    with db_session() as session:
        for status in (RunStatus.TERMINATED_REFUSALS, RunStatus.TERMINATED_REFUSALS,
                       RunStatus.FAILED, RunStatus.DONE):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                            trigger={"type": "test"}, metered=True, status=status))
    from datetime import timedelta

    from yaaf.core.models import utcnow

    with db_session() as session:
        pace = velocity._runs_in_window(session, project["id"],
                                        utcnow() - timedelta(days=7))

    assert pace["probed"] == 2, pace
    assert pace["dead"] == 1, pace
    assert pace["refused_share"] > 0 and pace["failure_share"] > 0
    assert pace["refused_share"] > pace["failure_share"], (
        "the two numbers are still conflated"
    )


# ── the multiplier can come down again ──────────────────────────────────────────


def test_estimate_bias_is_measured_over_a_window(client):
    """A project that overran early used to pay for it in every plan it would
    ever make. The anti-gaming floor is untouched — within the window the ratio
    still cannot go below 1, so over-estimating once still buys nothing."""
    from yaaf.server.policy import BIAS_WINDOW_SETTLED

    assert BIAS_WINDOW_SETTLED > 0
    # Long relative to a cycle: earnable, but far too long to be worked with one
    # deliberate over-estimate.
    assert BIAS_WINDOW_SETTLED >= 8, (
        "the window is short enough to game by over-estimating a couple of times"
    )


# ── a cycle verdict carries what it did ─────────────────────────────────────────


def test_a_missed_cycle_flag_says_how_much_landed(client):
    """MISSED with 9 of 10 met is not the same event as MISSED with 0 of 10, and
    the status alone cannot tell them apart.

    Every FlagKind is supposed to be arithmetic. This one carried the ordinal,
    the timestamp and the goal — and no counts — so the CEO on the desk that
    prompted this re-explained the distinction to its chairman six times in five
    days, by hand, out of its own run allowance. The numbers were on the row all
    along; they were never in the sentence.
    """
    from yaaf.core import ids
    from yaaf.core.constitution import effective
    from yaaf.core.models import Cycle, CycleStatus, FlagKind, Project
    from yaaf.server import flags

    project = make_project(client, slug="cycle1")
    with db_session() as session:
        session.add(Cycle(
            id=ids.new_id(ids.CYCLE), project_id=project["id"], ordinal=1,
            status=CycleStatus.MISSED, goal="Ship the GW3 memo",
            scorecard={"accepted": 10, "met": 9, "carried": 1,
                       "met_keys": [f"R-{n:02d}" for n in range(1, 10)],
                       "carried_keys": ["R-10"]},
        ))
    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))

    with db_session() as session:
        flag = next(f for f in flags.open_flags(session, project["id"])
                    if f.kind == FlagKind.CYCLE_MISSED)
        assert "9 of 10 met" in flag.summary, flag.summary
        assert "R-10" in flag.summary, "the flag does not say what was carried"
        assert flag.arithmetic["met"] == 9 and flag.arithmetic["carried"] == 1


# ── the run cap is a plan, not a cliff ──────────────────────────────────────────


def test_the_run_plan_and_the_run_wall_are_two_numbers(client):
    """Money learned this first: a number that is fatal to cross is not a plan
    anybody works to, it is a cliff everybody stands back from — and a safe
    number is indistinguishable from an honest one from outside. Runs were
    never given the same treatment, so crossing the daily cap was silently
    fatal for the rest of the day, with no account, no flag and no way to ask.
    """
    from yaaf.core.constitution import effective

    # The rule exists and is settable. Its DEFAULT is 1.0 on purpose — see
    # test_governance_reserve.py::test_the_run_wall_defaults_to_the_plan — so
    # what is asserted here is that a chairman who raises it gets a wall above
    # the plan, not that every project starts with one.
    project = make_project(client, slug="wall",
                           constitution={"runs_per_project_per_day": 20,
                                         "runs_hard_stop_multiple": 2.0})
    with db_session() as session:
        cons = effective(session, project["id"])
    assert cons.runs_hard_stop_multiple == 2.0
    wall = max(cons.runs_per_project_per_day,
               int(cons.runs_per_project_per_day * cons.runs_hard_stop_multiple))
    assert wall == 40 and wall > cons.runs_per_project_per_day, (
        "the run plan and the run wall are still the same number when raised"
    )


def test_the_concurrency_dial_is_untouched_by_the_overrun(client):
    """The one dial whose help text says it protects the provider rate limit
    every project on the box shares. It caps simultaneity, not volume, so it
    goes on binding absolutely however far past its daily plan a project runs —
    otherwise one project's overrun is every other project's outage."""
    import inspect

    from yaaf.server import dispatcher

    source = inspect.getsource(dispatcher._dispatch_project)
    assert "runs_hard_stop_multiple" in source
    assert "max_concurrent_runs" not in source.split("wall =")[1].split("\n\n")[0], (
        "the run overrun was applied to the shared concurrency limit"
    )


def test_going_past_the_plan_opens_an_account(client):
    """Not a failure and not a stop — an obligation to say what the extra runs
    bought, which is the whole difference between a plan and a cliff."""
    from yaaf.core import ids
    from yaaf.core.constitution import effective
    from yaaf.core.models import FlagKind, Project, Run, RunKind
    from yaaf.server import flags

    project = make_project(client, slug="overrun",
                           constitution={"runs_per_project_per_day": 4,
                                         "runs_hard_stop_multiple": 2.0})
    with db_session() as session:
        for _ in range(6):
            session.add(Run(id=ids.new_id(ids.RUN), project_id=project["id"],
                            agent_id=project["ceo_agent_id"], kind=RunKind.AGENT_RUN,
                            trigger={"type": "test"}, status=RunStatus.DONE))
    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))

    with db_session() as session:
        opened = {f.kind: f for f in flags.open_flags(session, project["id"])}
    assert FlagKind.RUN_OVERRUN in opened, f"no account was opened: {list(opened)}"
    flag = opened[FlagKind.RUN_OVERRUN]
    assert flag.arithmetic["plan"] == 4 and flag.arithmetic["wall"] == 8
    assert "capacity_request" in flag.summary, (
        "the account does not name the verb for asking the plan to be raised"
    )
