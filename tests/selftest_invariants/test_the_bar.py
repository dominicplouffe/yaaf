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

from sqlalchemy import select

from tests.conftest import ADMIN, seed_role
from yaaf.core.db import db_session
from yaaf.core.models import Agent, AgentKind, Project

CHAIR = {"email": "chair@example.com", "display_name": "Chair"}


def _library(client) -> None:
    """A small library with one role in each category the lift cares about."""
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership",
              model="claude-opus-5-5", effort="high")
    seed_role(client, "scribe", category="content",
              model="claude-sonnet-5-5", effort="high")
    seed_role(client, "digger", category="research",
              model="claude-sonnet-5-5", effort="high")
    seed_role(client, "hawker", category="growth",
              model="claude-sonnet-5-5", effort="high")
    seed_role(client, "reader", category="challenge",
              model="claude-sonnet-5-5", effort="xhigh")


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

    assert seats["Ada"] == ("claude-opus-5-5", "xhigh"), "the seat he actually reads"
    assert seats["Rey"] == ("claude-opus-5-5", "xhigh")
    # Already at xhigh in the library, so only the model moves — never downward.
    assert seats["Juno"] == ("claude-opus-5-5", None)
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
    assert seats["Ada"] == ("claude-opus-5-5", "xhigh"), "the silence is filled"


def test_the_shared_role_library_never_moves(client):
    """The reason this is a per-project stamp rather than an edit to `roles/`:
    the library is global, and raising effort there raises it for every project
    on the install."""
    _library(client)
    assert _create(client, "greedy", constitution={"stakes": "high"}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ]).status_code == 200
    role = client.get("/admin/roles/scribe", headers=ADMIN).json()
    assert (role["model"], role["effort"]) == ("claude-sonnet-5-5", "high")


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
    assert seats["Ada"] == ("claude-opus-5-5", "xhigh"), "the lift reaches a later team"
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


#: The constitution the "Balanced" card on the new-project form now sends.
#: Duplicated from `web/src/pages/NewProject.tsx` on purpose: this asserts the
#: form's own default is creatable, and a copy that drifts is a form whose
#: default is refused — which is exactly what shipped for ten minutes.
BALANCED_CARD = {
    "queue_ceiling": 8, "drought_hours": 24, "monthly_usd_cap": 1000,
    "approval_threshold_usd": 250, "require_evidence_verification": False,
    "stakes": "normal", "deliverable_review": "independent_read",
    "max_usd_per_run": 2, "run_timeout_s": 1800,
}


def test_the_forms_own_default_card_creates_a_project(client):
    """Every run preset asks for an independent read, so the form now sends a
    constitution its own drafted roster cannot satisfy unless somebody staffs
    the seat. The form warns and offers the team; this asserts the two halves
    agree — the card as sent is refused without a reviewing seat and accepted
    with one."""
    _library(client)
    refused = _create(client, "as-drafted", constitution=BALANCED_CARD, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    assert refused.status_code == 422, "the form must warn before this happens"

    staffed = _create(client, "as-warned", constitution=BALANCED_CARD, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
        {"role_slug": "reader", "name": "Review", "employees": ["Juno"]},
    ])
    assert staffed.status_code == 200, staffed.text


def test_solo_mode_is_never_refused_for_lacking_a_reader_it_cannot_have(client):
    """Solo mode is one brain: the CEO IS the team, plans and executes under
    byte-identical policy. There is no second reader to hire, so a rule
    demanding one would make solo projects uncreatable rather than reviewed.

    Found by running the form's own payload rather than by reading the guard —
    the `not teams` exemption did not cover it, because solo passes one team
    with no employees rather than no teams."""
    _library(client)
    created = client.post("/admin/projects", json={
        "slug": "alone", "name": "Alone", "goal": "One brain, planning and doing.",
        "chairman_email": CHAIR["email"], "constitution": BALANCED_CARD, "solo": True,
        "teams": [{"role_slug": "scribe", "name": "Desk", "employees": []}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text


def test_the_rule_cannot_be_amended_into_a_state_the_roster_cannot_keep(client):
    """Creation was the only guarded door, and it is not the only door.

    Found by rehearsing the upgrade rather than by reading the code: a running
    project was amended to demand an independent read with nobody on its roster
    who could give one, and the amendment was accepted with a 200. A rule that
    holds at one door and not the other is a rule the chairman can set and then
    watch do nothing.
    """
    _library(client)
    created = _create(client, "growing", constitution={}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    project_id = created.json()["id"]

    refused = client.post(f"/admin/projects/{project_id}/amendments", headers=ADMIN, json={
        "reason": "I want the work read before I see it",
        "changes": {"deliverable_review": "independent_read"}})
    assert refused.status_code == 422, refused.text
    assert "wrote it" in refused.text and "reader" in refused.text

    # Unchanged, not half-applied.
    from yaaf.core.constitution import effective
    with db_session() as session:
        assert effective(session, project_id).deliverable_review == "none"

    # Staff the seat and the same amendment lands — the refusal names a fix
    # that works.
    assert client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "reader", "name": "Review", "employees": ["Juno"]},
        headers=ADMIN).status_code == 200
    accepted = client.post(f"/admin/projects/{project_id}/amendments", headers=ADMIN, json={
        "reason": "now there is somebody to do it",
        "changes": {"deliverable_review": "independent_read"}})
    assert accepted.status_code == 200, accepted.text
    with db_session() as session:
        assert effective(session, project_id).deliverable_review == "independent_read"


def test_a_clone_does_not_duplicate_a_promise_its_source_cannot_keep(client):
    """The third door. A clone copies the constitution and the roster verbatim,
    so a source in the broken state hands the twin the same broken promise —
    which is how one install's mistake becomes two. Only reachable from a
    project amended before that door was shut, so the state is forced here
    directly rather than through the API that now refuses it."""
    from yaaf.core.models import Constitution

    _library(client)
    created = _create(client, "legacy-broken", constitution={}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    project_id = created.json()["id"]
    with db_session() as session:
        row = session.query(Constitution).filter_by(project_id=project_id).one()
        row.body = {**row.body, "deliverable_review": "independent_read"}

    refused = client.post(f"/admin/projects/{project_id}/clone",
                          json={"slug": "twin", "name": "Twin"}, headers=ADMIN)
    assert refused.status_code == 422, refused.text
    with db_session() as session:
        assert not session.query(Project).filter_by(slug="twin").first(), \
            "a refused clone must not leave half a project behind"

    # A source that CAN keep its rule clones fine.
    assert client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "reader", "name": "Review", "employees": ["Juno"]},
        headers=ADMIN).status_code == 200
    assert client.post(f"/admin/projects/{project_id}/clone",
                       json={"slug": "twin", "name": "Twin"},
                       headers=ADMIN).status_code == 200


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


# ── the ways the lift and the roster rule could be got around ───────────────────


def test_the_lift_never_lowers_a_seat_the_library_already_placed_higher(client):
    """The model check was `!= CRAFT_MODEL`, so a role the library deliberately
    put on something stronger was stamped back DOWN to the craft model by a
    dial whose entire promise is that it raises. The effort check next to it
    already compared ranks; this one now does too."""
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership")
    seed_role(client, "reader", category="challenge", model="claude-fable-5-1",
              effort="xhigh")
    seed_role(client, "scribe", category="content", model="claude-sonnet-5-5",
              effort="high")
    created = _create(client, "no-demotion", constitution={"stakes": "high"}, teams=[
        {"role_slug": "reader", "name": "Review", "employees": ["Juno"]},
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    assert created.status_code == 200, created.text
    seats = _seats(created.json()["id"])
    assert seats["Juno"] == (None, None), "a stronger seat was demoted by the lift"
    assert seats["Ada"] == ("claude-opus-5-5", "xhigh")


def test_a_seat_on_last_generations_opus_is_lifted_not_left(client):
    """When the craft model moved to Opus 5.5, Opus 5 vanished from the rank
    table, ranked 0 and was lifted by luck. With it ranked equal to Opus it
    would have been left behind, paying more per token for the older model."""
    client.post("/admin/users", json=CHAIR, headers=ADMIN)
    seed_role(client, "ceo", is_ceo=True, category="leadership")
    seed_role(client, "scribe", category="content", model="claude-opus-5",
              effort="xhigh")
    created = _create(client, "old-opus", constitution={"stakes": "high"}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
    ])
    assert created.status_code == 200, created.text
    assert _seats(created.json()["id"])["Ada"] == ("claude-opus-5-5", None)


def test_an_empty_roster_cannot_hold_a_rule_it_never_staffs(client):
    """The check exempted a spec with no teams, on the reasoning that staffing
    had not happened yet. But nothing re-checked afterwards, so the exemption
    was permanent: a project could ask for an independent read, hire nobody who
    could give one, and the rule bound nothing for the rest of its life."""
    _library(client)
    refused = _create(client, "hollow",
                      constitution={"deliverable_review": "independent_read"}, teams=[])
    assert refused.status_code == 422, refused.text
    assert "read" in refused.text


def test_adding_a_team_is_the_door_that_closes_it(client):
    """The other half of the same fix, and the reason the empty case can be
    refused at all: `add_team` is how a project gets its reviewer, so it has to
    be checked AFTER the incoming team is counted — refusing there would make
    the rule impossible to satisfy rather than merely enforced."""
    _library(client)
    created = _create(client, "grows-into-it",
                      constitution={"deliverable_review": "independent_read"}, teams=[
                          {"role_slug": "reader", "name": "Review",
                           "employees": ["Juno"]}])
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    # The reviewer is already there, so an ordinary team lands.
    assert client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
        headers=ADMIN).status_code == 200

    # And the incoming team counts toward the rule: a project that lost its
    # reviewer satisfies it again by adding one, not by being refused forever.
    # Losing it the way a project really does — the team repinned onto a role
    # that no longer challenges anything.
    from yaaf.core.models import Role, RoleVersion, Team

    with db_session() as session:
        scribe = session.execute(
            select(RoleVersion.id).join(Role, Role.id == RoleVersion.role_id)
            .where(Role.slug == "scribe").order_by(RoleVersion.id.desc())
        ).scalars().first()
        session.query(Team).filter_by(project_id=project_id, name="Review").one() \
            .role_version_id = scribe
    blocked = client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "hawker", "name": "Growth", "employees": ["Sol"]}, headers=ADMIN)
    assert blocked.status_code == 422, blocked.text
    assert client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "reader", "name": "Second Read", "employees": ["Nia"]},
        headers=ADMIN).status_code == 200


def test_amending_stakes_actually_re_hires_the_seats(client):
    """The dial reads as live on the Settings screen and bought nothing after
    creation: only `create_project` and `add_team` ran the lift, so amending
    `stakes` to high changed the budget headroom and the tone of a paragraph
    and left every seat on the library's model — which is the complaint the
    rule was written to answer, one door along."""
    _library(client)
    created = _create(client, "grew-into-it", constitution={}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]},
        {"role_slug": "hawker", "name": "Growth", "employees": ["Sol"]},
    ])
    project_id = created.json()["id"]
    assert _seats(project_id)["Ada"] == (None, None)

    amended = client.post(f"/admin/projects/{project_id}/amendments", headers=ADMIN,
                          json={"reason": "this turned out to matter",
                                "changes": {"stakes": "high"}})
    assert amended.status_code == 200, amended.text
    assert "Ada" in amended.json()["note"], "say whose hire changed"

    seats = _seats(project_id)
    assert seats["Ada"] == ("claude-opus-5-5", "xhigh")
    assert seats["Sol"] == (None, None), "the lift is still aimed, not blanket"


def test_amending_stakes_does_not_overwrite_an_answer_somebody_gave(client):
    """A NULL override is the platform's own "nobody has said anything about
    this seat". Anything else was the chairman's answer or an earlier lift's,
    and neither is an amendment's to overwrite — which also makes this one-way:
    lowering stakes again does not demote, because a stamp on the row is no
    longer distinguishable from a choice."""
    _library(client)
    created = _create(client, "his-answer", constitution={}, teams=[
        {"role_slug": "scribe", "name": "Editorial", "employees": ["Ada", "Kit"],
         "model_overrides": {"Kit": "claude-haiku-4-5"}},
    ])
    project_id = created.json()["id"]
    client.post(f"/admin/projects/{project_id}/amendments", headers=ADMIN,
                json={"reason": "raising the bar", "changes": {"stakes": "high"}})
    seats = _seats(project_id)
    assert seats["Kit"] == ("claude-haiku-4-5", None), "his answer survived"
    assert seats["Ada"] == ("claude-opus-5-5", "xhigh")

    client.post(f"/admin/projects/{project_id}/amendments", headers=ADMIN,
                json={"reason": "back down", "changes": {"stakes": "low"}})
    assert _seats(project_id)["Ada"] == ("claude-opus-5-5", "xhigh"), \
        "lowering the dial must not silently demote a seat"


def test_a_solo_project_can_still_grow_a_team(client):
    """Solo is exempt from the review rule at creation — the CEO is the team,
    so there is nobody who did not write the work. The three doors that reopen
    the rule afterwards were not exempt, so a solo project created through the
    wizard, whose presets all set the read, had every later "Add a team"
    refused for lacking a seat it was exempt from hiring."""
    _library(client)
    created = client.post("/admin/projects", json={
        "slug": "alone", "name": "Alone", "goal": "Publish something worth reading.",
        "chairman_email": CHAIR["email"], "solo": True,
        "constitution": {"deliverable_review": "independent_read"},
        "teams": [{"role_slug": "scribe", "name": "Desk", "employees": []}],
    }, headers=ADMIN)
    assert created.status_code == 200, created.text
    project_id = created.json()["id"]

    grown = client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "digger", "name": "Research", "employees": ["Rey"]}, headers=ADMIN)
    assert grown.status_code == 200, grown.text

    # And once it has somewhere to put a reader, the rule binds again.
    blocked = client.post(f"/admin/projects/{project_id}/teams", json={
        "role_slug": "hawker", "name": "Growth", "employees": ["Sol"]}, headers=ADMIN)
    assert blocked.status_code == 422, blocked.text
