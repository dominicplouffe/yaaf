"""Invariant selftests: the Founder may write a posture, not a ceiling.

The Founder runs once and what it writes is read by every agent on the project
for the project's whole life. On the desk that prompted this it wrote, in
capitals:

    COST DISCIPLINE, AND IT IS A REAL CONSTRAINT, NOT A PREFERENCE... the
    platform's brakes will stop a runaway, but they will not make you efficient
    — that is your job, and it is a job you do by NOT commissioning work.
    ... A cheap week is a correct week, not a lazy one.

The chairman had said he was experimenting. He never said "run light". The
grammar's own element 6 predicted the outcome: *"a preference stated as a hard
rule gets obeyed past the point of sense."*

Checked against the live install on 2026-09-06, which settles this plan's
second open question — was the tight run cap the Founder's or the chairman's?

    constitution v1, 2026-08-26, at founding:  runs_per_project_per_day = 12
    amendment,       2026-08-27, the chairman: runs_per_project_per_day = 18
        reason: "increase runs per day to try and get this finished today"

The Founder's number, against a platform default of 150 — twelve and a half
times tighter — and the chairman's only run-cap intervention was to raise it,
the day after founding, because he wanted the work to go faster. Along with it,
at v1: `external_ratio_min` 0.5 -> 0, `drought_hours` 24 -> 720,
`max_task_gate_hours` 6 -> 120. Both floors off, the anti-idle brake twenty
times looser, the activity brake twelve times tighter.
"""

from __future__ import annotations

from pathlib import Path

DOCTRINE = Path("yaaf/founder/DESIGN_DOCTRINE.md").read_text()


# ── the doctrine teaches both directions ────────────────────────────────────────


def test_the_doctrine_says_a_posture_is_not_a_rule():
    assert "Resource posture is a bias, never a constraint" in DOCTRINE
    assert "release condition" in DOCTRINE, (
        "a bare number is a ceiling; the doctrine has to ask for the condition "
        "that releases it, which is what lets an agent earn the raise"
    )


def test_the_doctrine_requires_an_escalation_trigger_for_losing():
    """A project that escalates on events but not on outcomes will always be
    busy and never be worried."""
    assert "Escalation triggers must include losing" in DOCTRINE


def test_the_roster_section_argues_both_ways():
    """It taught 'cut' with a vivid reason and taught nothing about the cost of
    cutting too far, so it produced rosters that could not do the job."""
    cut = DOCTRINE.index("Argue for the smallest team")
    counterweight = DOCTRINE.index("Cut for redundancy, never for timidity")
    assert counterweight > cut, "the counterweight is not with the paragraph it balances"
    assert "under-resourced" in DOCTRINE


def test_the_doctrine_is_not_carrying_stray_code():
    """It is read verbatim into every Founder call and served to MCP clients as
    `yaaf://docs/design-doctrine`, so anything in it is being paid for on every
    founding and read as instruction."""
    assert "def system_prompt" not in DOCTRINE
    assert not DOCTRINE.rstrip().endswith('"""')


# ── precedence, where two texts disagree ────────────────────────────────────────


def test_the_prompt_says_which_text_wins():
    """The charter and the constitution contradicted each other on that desk —
    "it is a job you do by NOT commissioning work" against "spend what the work
    needs, and do not manufacture frugality to look careful" — and nothing said
    which governed, so the louder and earlier one did.
    """
    from yaaf.server.contextpack import PRECEDENCE

    assert "constitution wins" in PRECEDENCE
    assert "more cautious" in PRECEDENCE, (
        "the rule has to name the direction that actually happened: a charter "
        "TIGHTER than the rules is the case that went unnoticed for months"
    )


def test_the_precedence_rule_reaches_a_real_agent(client):
    from tests.conftest import make_project
    from yaaf.core.db import db_session
    from yaaf.core.models import Agent, Project
    from yaaf.server.contextpack import build_system_prompt

    project = make_project(client, slug="prec")
    with db_session() as session:
        prompt = build_system_prompt(
            session,
            session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]),
        )
    assert "PRECEDENCE:" in prompt
    # After the charter, before the rules it is adjudicating.
    assert prompt.index("PRECEDENCE:") < prompt.index("STAKES")


# ── somebody whose job is to disagree ───────────────────────────────────────────


def _bundle(mode: str, role_slugs: list[str]) -> dict:
    return {"project": {
        "slug": "probe-x", "name": "X", "goal": "g" * 40, "mode": mode,
        "deadline": "2027-01-01T00:00:00+00:00",
        "deadline_source": {"kind": "chairman", "note": "he said so"},
        "teams": [{"role_slug": s, "name": s.title(), "employees": ["A"]}
                  for s in role_slugs],
    }}


def test_a_deliver_roster_with_no_challenge_seat_is_refused(client):
    """`deliverable_review: independent_read` already refuses a roster that asks
    for the read and hires nobody who can do it. This is the same shape one
    level up, and the reason is stronger: the CEO wrote the plan and on an
    autonomous project accepted it too."""
    from tests.conftest import make_project
    from yaaf.founder.tools import doctrine_problems

    make_project(client, slug="seed-roles")   # so `worker` exists to be judged
    problems = doctrine_problems(_bundle("deliver", ["worker"])["project"])
    assert any("challenge" in p for p in problems), problems


def test_any_challenge_role_satisfies_it(client):
    """The requirement is that somebody's job is to attack the plan, not that a
    particular slug appears."""
    from tests.conftest import make_project
    from yaaf.core.db import db_session
    from yaaf.core.models import Role
    from yaaf.founder.tools import doctrine_problems

    make_project(client, slug="seed-roles2")
    with db_session() as session:
        for role in session.query(Role).filter(Role.slug == "worker"):
            role.category = "challenge"
    assert not [p for p in doctrine_problems(_bundle("deliver", ["worker"])["project"]) if "challenge" in p]


def test_explore_is_not_asked_for_one(client):
    """Explore has no plan to attack — no requirements, by design — so asking
    would be noise, and noise is how a check stops being read."""
    from tests.conftest import make_project
    from yaaf.founder.tools import doctrine_problems

    make_project(client, slug="seed-roles3")

    assert not [p for p in doctrine_problems(_bundle("explore", ["worker"])["project"]) if "challenge" in p]


# ── stakes is chosen, not defaulted into ────────────────────────────────────────


def test_stakes_must_be_named_explicitly(client):
    """The live founding constitution for the desk that prompted this did not
    set `stakes` at all — not "set it low", never mentioned it. So the one rule
    that tells a project not to thin the work to protect a number was never
    switched on, and nobody chose that. An unset dial is a decision nobody made
    and nobody can find afterwards."""
    from tests.conftest import make_project
    from yaaf.founder.tools import doctrine_problems

    make_project(client, slug="seed-roles4")
    silent = _bundle("deliver", ["worker"])
    assert any("stakes" in p for p in doctrine_problems(silent["project"]))


def test_any_explicit_stakes_passes(client):
    """`normal` is often right. What is refused is silence, not a value."""
    from tests.conftest import make_project
    from yaaf.founder.tools import doctrine_problems

    make_project(client, slug="seed-roles5")
    for level in ("low", "normal", "high"):
        bundle = _bundle("deliver", ["worker"])
        bundle["project"]["constitution"] = {"stakes": level}
        assert not [p for p in doctrine_problems(bundle["project"]) if "stakes" in p], level


def test_the_doctrine_is_held_against_the_founder_not_the_chairman(client):
    """The two lists must not merge.

    `validate` mirrors `POST /admin/projects` and cannot drift from it, because
    the chairman founds projects through that endpoint himself — the form, and
    MCP. He is entitled to build a two-person Deliver project with no adversary
    if that is what he wants; he is the person these rules exist to serve. The
    Founder runs once, unsupervised, from a paragraph of brief, and what it
    writes is read by every agent for the project's life. It is held to the
    doctrine and he is not, and that is the whole difference between a contract
    and a standard.

    Written after merging them by mistake, which refused seven of the
    chairman's own founding paths in one afternoon.
    """
    from tests.conftest import make_project
    from yaaf.founder.tools import doctrine_problems, validate

    make_project(client, slug="seed-roles6")
    bare = _bundle("deliver", ["worker"])

    assert not validate(bare), (
        "the endpoint-contract check grew a doctrine rule; the chairman's own "
        f"founding paths run through this: {validate(bare)}"
    )
    assert doctrine_problems(bare["project"]), (
        "the doctrine check stopped catching a roster the doctrine forbids"
    )
