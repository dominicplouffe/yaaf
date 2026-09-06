"""Invariant selftests: the platform can say the project is losing, and idle.

`FlagKind` had six members and every one was process or money — a missed
obligation, a slipped date, a spent allowance, an overrun. None of them could
say a project was *losing*, so a desk fifteen points behind the field average
over two gameweeks tripped nothing at all, while re-explaining a cycle-boundary
artifact to its chairman six times in five days.

The enum's own docstring already stated the right principle: every flag is
arithmetic or a failed test, never a judgement the CEO makes about whether the
news is worth the chairman's time. The taxonomy simply had no outcome in it,
and no way to see a desk that had gated itself shut.

Three rules are attacked here:

  * LOSING_GROUND trips on the measured objective moving away from target, and
    resolves when it turns — arithmetic, not mood, and not on one bad reading
  * SELF_GATED trips when nothing is claimable and no cap is responsible, which
    is the case WORK_STARVED structurally cannot see
  * **every flag kind the platform can open has a verb the agent can reach.**
    That one is not a style rule: `flag_raise` shipped as an HTTP route with no
    tool exposing it, so a live CEO spent five days being told it had one
    unraised flag and an obligation it could not discharge. Its own words, on
    the record: "the platform has no tool to close it".
"""

from __future__ import annotations

from pathlib import Path

from tests.conftest import bearer, live_token
from tests.selftest_invariants.test_metric_requirements import (
    RANK_TEST,
    fetch_returning,
)
from tests.selftest_invariants.test_requirements import (
    accept,
    ceo_token,
    deliver_project,
    propose,
)
from yaaf.core.constitution import effective
from yaaf.core.db import db_session
from yaaf.core.models import FlagKind, FlagStatus, Project, Requirement, RequirementStatus
from yaaf.server import flags, metrics


def flag_kinds(project_id: str) -> dict[str, str]:
    with db_session() as session:
        return {f.kind: f.status for f in flags.open_flags(session, project_id)}


def evaluate(project: dict) -> None:
    with db_session() as session:
        row = session.get(Project, project["id"])
        flags.evaluate(session, row, effective(session, row.id))


# ── the platform can say we are losing ──────────────────────────────────────────


def read(project: dict, requirement_id: str, *values) -> None:
    for value in values:
        with db_session() as session:
            metrics.record(session, session.get(Requirement, requirement_id),
                           fetch=fetch_returning(value))


def losing_project(client):
    project = deliver_project(client)
    token = ceo_token(project)
    metric = propose(client, token, test=RANK_TEST,
                     statement="Overall rank is inside the top 500,000")
    accept(client, metric["requirement_id"], project)
    with db_session() as session:
        session.get(Requirement, metric["requirement_id"]).status = (
            RequirementStatus.IN_PROGRESS
        )
    return project, metric["requirement_id"]


def test_a_metric_moving_away_from_target_raises_a_flag(client):
    project, requirement_id = losing_project(client)
    read(project, requirement_id, 4_087_187, 2_627_278, 6_698_328)
    evaluate(project)

    assert FlagKind.LOSING_GROUND in flag_kinds(project["id"]), (
        "the desk is further from its target than when it started and nothing tripped"
    )
    with db_session() as session:
        flag = next(f for f in flags.open_flags(session, project["id"])
                    if f.kind == FlagKind.LOSING_GROUND)
        # Arithmetic, per the enum's own doctrine — the numbers travel with it,
        # so the CEO cannot report the news without the figures attached.
        assert flag.arithmetic["value"] == 6_698_328
        assert flag.arithmetic["target"] == 500000
        assert flag.arithmetic["trend"] == [4_087_187, 2_627_278, 6_698_328]


def test_one_bad_reading_does_not_raise_it(client):
    """A flag that cried wolf on noise would be muted inside a week, which is
    worse than not having it."""
    project, requirement_id = losing_project(client)
    read(project, requirement_id, 6_698_328, 3_000_000, 3_100_000)
    evaluate(project)
    assert FlagKind.LOSING_GROUND not in flag_kinds(project["id"])


def test_it_resolves_when_the_number_turns(client):
    """Unlike a missed cycle, this is not history — it is a live position, so a
    good week really does clear it."""
    project, requirement_id = losing_project(client)
    read(project, requirement_id, 4_087_187, 2_627_278, 6_698_328)
    evaluate(project)
    assert FlagKind.LOSING_GROUND in flag_kinds(project["id"])

    read(project, requirement_id, 900_000, 700_000)
    evaluate(project)
    assert FlagKind.LOSING_GROUND not in flag_kinds(project["id"])


# ── the platform can see a desk that gated itself shut ──────────────────────────


def test_a_board_gated_shut_by_its_own_ceo_trips_a_flag(client):
    """2026-09-04, reproduced: eight tasks, all with start times the CEO set,
    claimable 0, no cap involved, and nothing anywhere noticed. WORK_STARVED
    cannot fire — it needs a wall to have stopped the work, and nothing did."""
    from datetime import timedelta

    from yaaf.core.models import utcnow

    # `max_task_gate_hours` defaults to 6, which already refuses this. The FPL
    # desk's Founder set it to >=96 — a sixteenfold loosening of the one rule
    # whose purpose is to prevent an idle desk, on the same project whose run
    # cap it had tightened eightfold. That combination is the pathology, and
    # this is what it produces.
    project = deliver_project(client, constitution={"max_task_gate_hours": 96})
    token = ceo_token(project)
    requirement = propose(client, token)
    accept(client, requirement["requirement_id"], project)

    later = (utcnow() + timedelta(hours=90)).isoformat()
    created = client.post("/agent/tasks", json={
        "team_id": project["teams"][0]["id"],
        "title": "post-GW3 result snapshot",
        "goal": "read the result once bonus settles",
        "success_test": {"type": "command", "command": "test -s snapshot.md"},
        "requirement_id": requirement["requirement_id"],
        "not_before": later,
    }, headers=bearer(token))
    assert created.status_code == 200, created.text

    evaluate(project)
    kinds = flag_kinds(project["id"])
    assert FlagKind.SELF_GATED in kinds, (
        f"a board gated 90h out with nothing claimable tripped nothing: {kinds}"
    )
    assert FlagKind.WORK_STARVED not in kinds, (
        "WORK_STARVED fired for self-gating; it means a CAP stopped the work, and "
        "conflating the two loses the only thing that says whose fault it is"
    )


def test_it_clears_when_work_becomes_claimable(client):
    project = deliver_project(client)
    token = ceo_token(project)
    requirement = propose(client, token)
    accept(client, requirement["requirement_id"], project)
    client.post("/agent/tasks", json={
        "team_id": project["teams"][0]["id"],
        "title": "do it now",
        "goal": "no gate on this one",
        "success_test": {"type": "command", "command": "test -s out.md"},
        "requirement_id": requirement["requirement_id"],
    }, headers=bearer(token))
    evaluate(project)
    assert FlagKind.SELF_GATED not in flag_kinds(project["id"])


# ── no obligation without a verb ────────────────────────────────────────────────


def test_every_flag_kind_can_actually_be_discharged():
    """The bug this test exists to stop recurring, stated as a rule.

    Raising a flag is compulsory and overrides the reporting cadence: the pack
    tells the CEO, every wake, that deciding the news is not worth the
    chairman's time is a judgement it does not have. `POST /agent/flags/raise`
    is how that obligation is discharged — and for months no tool exposed it.

    The live consequence, from the 2026-09-05 transcript, in the CEO's own
    words: *"The flag the context flags as unraised was in fact raised in full
    in yesterday's boardroom post — the platform has no tool to close it, only
    a post at a real cadence slot, which happened."* It was right. It had told
    him. It had no way to say so, and was nagged for five days.

    A platform that can open an obligation an agent cannot close is not strict,
    it is broken, and it teaches the agent that the rules are noise.
    """
    import re

    entry = Path("yaaf/runner/sandbox_entry.py").read_text()
    pack = Path("yaaf/server/contextpack.py").read_text()

    tools = set(re.findall(r'@tool\(\s*"([a-z_]+)"', entry))
    handed_out = entry.split("ALL_TOOLS")[1]

    # Every verb the pack NAMES, an agent must be able to call. The list is
    # derived rather than declared: any snake_case token in the pack's prose
    # that is also a tool name. That deliberately cannot catch a verb the pack
    # names which was never a tool at all — `flag_raise` was exactly that, and
    # sat indistinguishable among a hundred JSON field names — so the second
    # assertion below pins the specific instruction that went unbacked.
    named = set()
    for literal in re.findall(r'"([^"\n]{4,400})"', pack) + re.findall(r"'([^'\n]{4,400})'", pack):
        named |= set(re.findall(r"\b[a-z]+_[a-z_]+\b", literal)) & tools

    assert named, "the pack names no verbs at all, which means this test stopped working"
    missing = sorted(v for v in named if f"t_{v}" not in handed_out)
    assert not missing, (
        f"the context pack instructs an agent to use {missing}, and no agent is handed "
        "them. An obligation the platform can open and an agent cannot close is not "
        "strict, it is broken."
    )

    # The specific one, pinned by name because it is the one that happened.
    assert "flag_raise" in tools and "t_flag_raise" in handed_out, (
        "no flag_raise tool: the platform can open a flag no agent can close, while "
        "telling it every wake to close it with flag_raise"
    )
    assert "flag_raise citing the message" in pack, (
        "the pack stopped naming the verb; if the instruction moved, move this with it"
    )


def test_the_ceo_can_close_a_flag_end_to_end(client):
    """The whole loop over HTTP: the platform opens it, the CEO says it in the
    boardroom, and the message is the proof that closes it."""
    project, requirement_id = losing_project(client)
    read(project, requirement_id, 4_087_187, 2_627_278, 6_698_328)
    evaluate(project)
    token = ceo_token(project)

    with db_session() as session:
        flag = next(f for f in flags.open_flags(session, project["id"])
                    if f.kind == FlagKind.LOSING_GROUND)
        flag_id = flag.id

    channels = client.get("/agent/channels", headers=bearer(token)).json()
    boardroom = next(c["id"] for c in channels if c["kind"] == "boardroom")
    posted = client.post("/agent/messages", json={
        "channel_id": boardroom,
        "body": "We are further from the rank target than when we started: 6,698,328 "
                "against 500,000. I am moving the analysis budget to captaincy.",
    }, headers=bearer(token))
    assert posted.status_code == 200, posted.text

    raised = client.post("/agent/flags/raise", json={
        "flag_id": flag_id,
        "message_id": posted.json()["id"],
        "proposal": "Shift two runs a week from memo polish to captaincy modelling, "
                    "and review in two gameweeks.",
    }, headers=bearer(token))
    assert raised.status_code == 200, raised.text

    with db_session() as session:
        flag = next(f for f in flags.open_flags(session, project["id"])
                    if f.kind == FlagKind.LOSING_GROUND)
        assert flag.status == FlagStatus.RAISED
    assert flags_unraised(project["id"]) == 0


def flags_unraised(project_id: str) -> int:
    with db_session() as session:
        return flags.unraised_count(session, project_id)


# ── the CEO can see its own scoreboard ──────────────────────────────────────────


def test_ceo_judgment_reaches_the_ceo(client):
    """It was computed, shown to the chairman on two screens, and withheld from
    the only party who could act on it."""
    from yaaf.core.models import Agent, Run
    from yaaf.server.contextpack import build_context_pack

    project = deliver_project(client)
    run_id, _ = live_token(project["id"], project["ceo_agent_id"])
    with db_session() as session:
        pack = build_context_pack(
            session,
            session.get(Run, run_id),
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
    assert "ceo_judgment" in pack, "the CEO still cannot see its own scoreboard"
    assert "note" in pack["ceo_judgment"], (
        "the number arrived without the sentence saying what gaming it would look like"
    )
