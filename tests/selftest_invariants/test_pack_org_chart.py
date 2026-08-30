"""An agent should not have to guess the org chart at model prices.

655 refusals landed across 28% of runs and almost none were safety refusals —
they were agents not knowing which channels exist or who owns which memory
scope. "Unknown channel" 33 times; "Project-scope memory is the CEO's" 20
times. Each one is a full model turn spent discovering something the pack
could have stated for a handful of tokens.
"""

from __future__ import annotations

from tests.conftest import agent_by_name, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Run
from yaaf.server.contextpack import build_context_pack


def _pack(project: dict, agent_id: str) -> dict:
    run_id, _ = live_token(project["id"], agent_id)
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id), session.get(Agent, agent_id),
            session.get(Project, project["id"]),
        )


def test_the_ceo_is_told_its_channels_and_that_memory_is_its_own(client):
    project = make_project(client)
    you = _pack(project, project["ceo_agent_id"])["you"]

    kinds = {c["kind"] for c in you["channels"]}
    assert "boardroom" in kinds, "the CEO's only speaking channel, and it guessed at it"
    assert "leadership" in kinds
    assert you["memory_scope"] == "project"
    assert all(c["channel_id"] for c in you["channels"])


def test_an_employee_is_told_team_scope_not_project(client):
    """The refusal it kept hitting, answered before it is hit."""
    project = make_project(client)
    you = _pack(project, agent_by_name(project, "Emp2"))["you"]

    assert you["memory_scope"] == "team"
    assert {c["kind"] for c in you["channels"]} == {"team"}, "employees see one room"


def test_a_solo_team_room_says_it_is_a_diary(client):
    """A one-member team room is indistinguishable from a real one, and 36
    messages were posted into five of them. The pack says so up front rather
    than leaving it to a refusal after the words are written."""
    project = make_project(
        client, slug="solo-room",
        teams=[{"role_slug": "worker", "name": "Alpha", "employees": ["Only"]}],
    )
    you = _pack(project, agent_by_name(project, "Only"))["you"]

    room = next(c for c in you["channels"] if c["kind"] == "team")
    assert "only member" in room.get("note", "")


def test_a_populated_team_room_carries_no_such_note(client):
    project = make_project(client)
    you = _pack(project, agent_by_name(project, "Emp2"))["you"]
    room = next(c for c in you["channels"] if c["kind"] == "team")
    assert "note" not in room


def test_channels_named_in_the_pack_are_the_ones_that_accept_a_post(client):
    """The pack and the refusal must agree, or this trades one wrong guess for
    another."""
    from yaaf.server import policy

    project = make_project(client)
    ceo = project["ceo_agent_id"]
    you = _pack(project, ceo)["you"]
    with db_session() as session:
        allowed = set(policy.agent_channel_ids(session, session.get(Agent, ceo)))
    assert {c["channel_id"] for c in you["channels"]} == allowed


def test_the_roster_carries_the_slug_a_credential_grant_names(client):
    """Team names and people's names are both refused by `credential_scope`.

    Asked which roles should hold a GitHub token, a CEO answered with its team
    names, then its employees' names, then raw agent ids — three refusals over
    eleven minutes, each relayed back by the chairman by hand. The slug that
    would have worked was in the database throughout. This asserts the pack
    now carries it, and that what it carries is the string the grant accepts.
    """
    from yaaf.server import policy

    project = make_project(client)
    pack = _pack(project, project["ceo_agent_id"])

    slugs = {team["role_slug"] for team in pack["roster"]}
    assert slugs and None not in slugs, "every team names the role it pins"
    assert pack["you"]["role_slug"], "the CEO's own slug is one of the answers"

    with db_session() as session:
        employed = policy.roles_on_project(session, project["id"])
    named = slugs | {pack["you"]["role_slug"]}
    assert named <= employed, (
        "the pack must not name a slug the grant would refuse — "
        f"{named - employed} is exactly the wrong guess this block exists to stop"
    )


def test_a_grant_to_every_slug_the_pack_names_is_accepted(client):
    """The pack and the refusal have to agree, or this trades one wrong guess
    for another — the same standard the channel list is held to above."""
    from yaaf.server import policy

    project = make_project(client)
    pack = _pack(project, project["ceo_agent_id"])
    named = sorted(
        {t["role_slug"] for t in pack["roster"] if t["role_slug"]}
        | {pack["you"]["role_slug"]}
    )

    with db_session() as session:
        # Raises Refusal if any named slug is not employed here.
        policy.check_roles_on_project(session, project["id"], named)
