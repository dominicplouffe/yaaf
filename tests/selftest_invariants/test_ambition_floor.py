"""The attack on house rule 1's second half: a floor must be able to fail.

`CONTRIBUTING.md` rule 1 says an automatic state change may point either way —
a rule that fires when a project is *under*-reaching is as legitimate as one
that refuses when it over-reaches. Rule 2 says every rule ships with an attack.
This is that attack, and the thing it attacks is the eval gate itself.

The claim under test is narrow and mechanical: **there exists a scenario in
which a CEO that does nothing fails the build.** Before the `escalation` and
`activity` expectations existed, no such scenario could be written. Every
assertion the harness could make — refusal counts, task counts, ceilings,
success-test shape — is tripped by doing too *much*. A CEO that woke, read that
its project was losing, and went back to sleep satisfied all of them, and
scored identically to `disciplined_ceo`.

These tests are deliberately slow: each runs its own full scenario (fresh
SQLite, real app, three dispatch ticks) because the floor is only meaningful
end to end. Roughly 1.5s each, three of them. They do not share a run, because
`run_scenario` resets the database — a result checked after the next run has
started is checked against a project that no longer exists.
"""

from __future__ import annotations

from evals import harness

SCENARIO = harness.SCENARIOS_DIR / "losing_ceo.yaml"

#: The blocks that existed before floors did.
CEILING_ONLY_BLOCKS = ("refusals", "reaches", "tasks", "hypotheses")


def _run(behavior: str, expectations: dict | None = None) -> harness.ScenarioResult:
    """One scenario run with the CEO swapped, and optionally the expectations."""
    scenario = harness.load_scenario(SCENARIO)
    scenario.behaviors = {**scenario.behaviors, "CEO": behavior}
    if expectations is not None:
        scenario.expectations = expectations
    return harness.run_scenario(scenario)


def test_a_ceo_that_goes_quiet_while_losing_fails_the_build():
    """The floor holds: silence in a losing project is a failing scenario."""
    timid = _run("evals.behaviors.timid_ceo")

    assert timid.failures, (
        "timid_ceo passed losing_ceo's expectations. The floor is decorative: "
        "a CEO that read the deficit and did nothing just scored a green build."
    )
    tripped = " ".join(timid.failures)
    assert "escalation.chairman_messages" in tripped, (
        f"the chairman-messages floor did not trip; failures were: {timid.failures}"
    )
    assert "activity.tasks_created" in tripped, (
        f"the tasks-created floor did not trip; failures were: {timid.failures}"
    )


def test_the_floors_are_what_caught_it_not_the_bookkeeping():
    """Which assertion did the work, pinned so it cannot rot quietly.

    The timid run fails several ways, because an exact `tasks.total: 2` catches
    zero tasks too. That is bookkeeping, not a floor — it would have caught a
    CEO that made *three* just as fast. What makes this scenario about
    under-reach is that the escalation and activity blocks fail **on their
    own**, with every exact count removed.
    """
    scenario = harness.load_scenario(SCENARIO)
    floors_only = {
        block: value
        for block, value in scenario.expectations.items()
        if block not in CEILING_ONLY_BLOCKS
    }
    assert floors_only, "losing_ceo.yaml has no floor blocks left to test"

    timid = _run("evals.behaviors.timid_ceo", expectations=floors_only)

    assert timid.failures, (
        "with the exact counts stripped out, nothing caught the timid CEO — "
        "the floors are not carrying this scenario, the bookkeeping is."
    )
    assert all(f.startswith(("escalation.", "activity.")) for f in timid.failures), (
        f"a non-floor assertion leaked into the floors-only check: {timid.failures}"
    )


def test_an_exact_count_is_not_a_floor():
    """Why the pre-floor vocabulary could not express ambition.

    Every count it offered was *exact*. An exact count does not say "at least
    this much" — it pins one scripted trajectory and fails departures in
    **either** direction, so a CEO that did more failed the build exactly as
    loudly as one that did less. That symmetry is the mechanical form of
    PLAN-01's finding: a paved road for caution and no road at all for
    reaching, because reaching and slacking produced the same red build.

    Concretely: had this scenario been recorded against a lighter CEO — one
    analysis — the driven CEO would fail it. The floor form of the same
    intent passes, because a floor is satisfied by exceeding it.
    """
    driven = _run("evals.behaviors.losing_ceo")
    assert not driven.failures, (
        f"the driven CEO should pass its own scenario: {driven.failures}"
    )

    scenario = harness.load_scenario(SCENARIO)

    # What a more timid author would have recorded. A legal scenario today.
    scenario.expectations = {"tasks": {"total": 1}}
    exact = harness.check_expectations(scenario, driven.transcript, driven.project_id)
    assert any("tasks.total" in f for f in exact), (
        "an exact task count no longer fails a CEO that exceeded it, so this "
        f"test no longer demonstrates the asymmetry it was written for: {exact}"
    )

    # The same intent written as a floor. Doing more is now correct.
    scenario.expectations = {"activity": {"tasks_created": {"min": 1}}}
    floored = harness.check_expectations(scenario, driven.transcript, driven.project_id)
    assert floored == [], f"a floor of 1 rejected a CEO that created more than 1: {floored}"
