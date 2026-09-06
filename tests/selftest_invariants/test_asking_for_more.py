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

from tests.conftest import bearer, live_token, make_project
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
    from yaaf.core.models import utcnow
    from datetime import timedelta

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
