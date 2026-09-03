"""Invariant selftests: the two rules that decide how good the work has to be.

`stakes` was already on the form, labelled "How much this project matters", and
the answer reached exactly two places: the budget's headroom and the tone of one
paragraph. A high-stakes project got more money and better encouragement, and
was still staffed on whatever the shared role library said — which is how the
seats that produce what the chairman reads ended up on the cheapest model in the
building while the seats that critique them ran on Opus.

`deliverable_review` is the other half: a project can now say the work gets read
by somebody who did not write it, and the platform refuses a roster that asks
for that and hires nobody who can do it.

The load-bearing test in here is the last one. Everything above it changes
behaviour on purpose; that one asserts an install which never mentions either
rule is byte-identical to what it was, because that is the whole promise of
shipping this as a dial rather than as an opinion.
"""

from __future__ import annotations

from tests.conftest import ADMIN, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentKind, Project

CHAIR = {"email": "chair@example.com", "display_name": "Chair"}


def _library(client) -> None:
    """A small library with one role in each category the lift cares about."""
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership",
              model="claude-opus-5", effort="high")
    seed_role(client, "scribe", category="content",
              model="claude-sonnet-5", effort="high")
    seed_role(client, "digger", category="research",
              model="claude-sonnet-5", effort="high")
    seed_role(client, "hawker", category="growth",
              model="claude-sonnet-5", effort="high")
    seed_role(client, "reader", category="challenge",
              model="claude-sonnet-5", effort="xhigh")


def _create(client, slug: str, *, constitution: dict, teams: list[dict]):
    return client.post("/admin/projects", json={
        "slug": slug, "name": slug.title(), "goal": "Publish something worth reading.",
        "chairman_email": CHAIR["email"], "constitution": constitution, "teams": teams,
    }, headers=ADMIN)


def _seats(project_id: str) -> dict[str, tuple[str | None, str | None]]:
    with db_session() as session:
        return {
            a.name: (a.model_override, a.effort_override)
            for a in session.query(Agent).filter_by(project_id=project_id)
        }


def test_high_stakes_hires_the_producing_seats_better_and_leaves_the_rest(client):
    """The lift is aimed, not blanket. A seat whose output IS the deliverable,
    or whose job is to check it, is worth the money; the one doing outreach is
    not, and paying Opus for support triage is how a dial like this gets a bad
    name."""
    _library(client)
    created = _create(client, "matters", constitution={"stakes": "high"}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
        {"role_slug": "digger", "name": "Research", "employees": ["Rey"]},
        {"role_slug": "reader", "name": "Review", "employees": ["Juno"]},
        {"role_slug": "hawker", "name": "Growth", "employees": ["Sol"]},
    ])
    assert created.status_code == 200, created.text
    seats = _seats(created.json()["id"])

    assert seats["Ada"] == ("claude-opus-5", "xhigh"), "the seat he actually reads"
    assert seats["Rey"] == ("claude-opus-5", "xhigh")
    # Already at xhigh in the library, so only the model moves — never downward.
    assert seats["Juno"] == ("claude-opus-5", None)
    assert seats["Sol"] == (None, None), "outreach is not the deliverable"


def test_normal_stakes_changes_nothing_at_all(client):
    """The default has to be inert or the rule is not a dial, it is an
    opinion."""
    _library(client)
    created = _create(client, "ordinary", constitution={}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    assert created.status_code == 200, created.text
    assert _seats(created.json()["id"])["Ada"] == (None, None)


def test_what_the_chairman_named_survives_the_lift(client):
    """Somebody who deliberately put one seat on a cheaper model must not have
    it undone by a dial about the project as a whole."""
    _library(client)
    created = _create(client, "mixed", constitution={"stakes": "high"}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada", "Kit"],
         "model_overrides": {"Kit": "claude-haiku-4-5"},
         "effort_overrides": {"Kit": "low"}},
    ])
    assert created.status_code == 200, created.text
    seats = _seats(created.json()["id"])
    assert seats["Kit"] == ("claude-haiku-4-5", "low"), "his answer wins"
    assert seats["Ada"] == ("claude-opus-5", "xhigh"), "the silence is filled"


def test_the_shared_role_library_never_moves(client):
    """The reason this is a per-project stamp rather than an edit to `roles/`:
    the library is global, and raising effort there raises it for every project
    on the install."""
    _library(client)
    assert _create(client, "greedy", constitution={"stakes": "high"}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ]).status_code == 200
    role = client.get("/admin/roles/scribe", headers=ADMIN).json()
    assert (role["model"], role["effort"]) == ("claude-sonnet-5", "high")


def test_a_team_added_later_is_hired_on_the_same_terms(client):
    """`add_team` used to drop the per-employee settings on the floor — the
    spec carried them and the route ignored them — so a team added on Tuesday
    came back without the wakes and models its spec named."""
    _library(client)
    created = _create(client, "grows", constitution={"stakes": "high"}, teams=[
        {"role_slug": "reader", "name": "Review", "employees": ["Juno"]},
    ])
    project_id = created.json()["id"]
    added = client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "scribe", "name": "Editorial", "employees": ["Ada", "Kit"],
        "schedules": {"Ada": "09:30"},
        "model_overrides": {"Kit": "claude-haiku-4-5"},
    }, headers=ADMIN)
    assert added.status_code == 200, added.text

    seats = _seats(project_id)
    assert seats["Ada"] == ("claude-opus-5", "xhigh"), "the lift reaches a later team"
    assert seats["Kit"] == ("claude-haiku-4-5", "xhigh"), "and his model still wins"
    with db_session() as session:
        assert session.query(Agent).filter_by(
            project_id=project_id, name="Ada").one().daily_at == "09:30"

    # And it is checked as tightly as one hired at birth.
    refused = client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "scribe", "name": "Late", "employees": ["Nils"],
        "effort_overrides": {"Nobody": "xhigh"},
    }, headers=ADMIN)
    assert refused.status_code == 422 and "Nobody" in refused.text


def test_asking_for_a_read_with_nobody_to_give_one_is_refused(client):
    """The refusal lives at POST /admin/projects because the form, the Founder
    and mcp__yaaf__create_project all land there — a check written once covers
    three front doors, where the same sentence in three prompts covers whichever
    the author remembered."""
    _library(client)
    refused = _create(client, "unread",
                      constitution={"deliverable_review": "independent_read"},
                      teams=[{"role_slug": "scribe", "name": "Editorial",
                              "employees": ["Ada"]}])
    assert refused.status_code == 422
    # Names the fix, not just the problem — and names the seats that can give it.
    assert "reader" in refused.text, refused.text
    assert "wrote it" in refused.text

    ok = _create(client, "read",
                 constitution={"deliverable_review": "independent_read"},
                 teams=[{"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
                        {"role_slug": "reader", "name": "Review", "employees": ["Juno"]}])
    assert ok.status_code == 200, ok.text

    # Saying plainly that you do not want one is always allowed.
    assert _create(client, "solo-read", constitution={"deliverable_review": "none"},
                   teams=[{"role_slug": "scribe", "name": "Editorial",
                           "employees": ["Ada"]}]).status_code == 200


def test_an_install_that_says_nothing_is_byte_identical(client):
    """The promise this whole change rests on. yaaf is open source and these
    are dials, so a constitution written before they existed, and a project
    created without mentioning them, must behave exactly as they did — the
    field defaults reproduce today, and no seat's model or effort moves."""
    from yaaf.core.constitution import ConstitutionDoc

    defaults = ConstitutionDoc()
    assert defaults.stakes == "normal"
    assert defaults.deliverable_review == "none"

    _library(client)
    # A spec that mentions neither rule, on a roster with no reviewing seat —
    # which would be refused if `independent_read` had become the field default.
    created = _create(client, "legacy", constitution={"queue_ceiling": 4}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
        {"role_slug": "hawker", "name": "Growth", "employees": ["Sol"]},
    ])
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    assert all(seat == (None, None) for seat in _seats(project_id).values()), \
        "no seat may be re-hired by a rule the chairman never set"
    with db_session() as session:
        ceo = session.query(Agent).filter_by(project_id=project_id,
                                             kind=AgentKind.CEO).one()
        assert (ceo.model_override, ceo.effort_override) == (None, None)
        assert session.get(Project, project_id).status == "draft"
