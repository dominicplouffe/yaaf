"""Invariant selftests: the drill's spec, as the API prepares it.

The drill builds its own throwaway database, so the install's role library is
not there to hire from — a role resolves only if the spec carries a definition
or the process happens to sit in a checkout with a `roles/` directory. The
server never does: the image ships `yaaf/`, not `roles/`. So the dashboard's
fire drill resolved nothing, and every spec that hired from the library came
back "this project would not start" — the one verdict a preflight must never
get wrong, because it is indistinguishable from a real misconfiguration.

These tests are about the preparation only and run no drill, which is why they
may hold the `client` its sibling module deliberately avoids.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project, seed_role
from yaaf.core.db import db_session
from yaaf.server.api.admin import _carry_library_roles


def carry(spec: dict) -> dict:
    with db_session() as session:
        return _carry_library_roles(session, spec)


def by_slug(spec: dict) -> dict[str, dict]:
    return {role["slug"]: role for role in spec["roles"]}


def test_a_spec_that_hires_from_the_library_leaves_with_those_definitions(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "researcher", description="finds things out")

    out = carry({"project": {"ceo_role_slug": "ceo",
                             "teams": [{"role_slug": "researcher", "name": "Research",
                                        "employees": ["Ida"]}]},
                 "roles": []})

    roles = by_slug(out)
    assert set(roles) == {"ceo", "researcher"}
    # Enough to seed the drill's own library: /admin/roles refuses less.
    assert roles["researcher"]["system_prompt"] and roles["researcher"]["model"]
    assert roles["ceo"]["is_ceo_role"] is True


def test_a_bare_project_spec_is_served_too_not_only_a_setup_file(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")

    out = carry({"ceo_role_slug": "ceo",
                 "teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]}]})

    assert set(by_slug(out)) == {"ceo", "worker"}
    # The rest of the spec travels untouched — the drill runs the project the
    # chairman described, not a version of it this function assembled.
    assert out["teams"][0]["employees"] == ["Ada"]


def test_a_ceo_role_nobody_named_defaults_to_ceo_the_way_the_drill_reads_it(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")

    out = carry({"teams": [{"role_slug": "worker", "name": "Desk", "employees": ["Ada"]}]})

    assert "ceo" in by_slug(out)


def test_the_definition_the_file_carries_wins_over_the_library_s(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "researcher", system_prompt="The library's researcher.")

    carried = {"slug": "researcher", "name": "Researcher", "is_ceo_role": False,
               "description": "the file's own", "system_prompt": "The file's researcher.",
               "model": "claude-sonnet-5-5"}
    out = carry({"project": {"ceo_role_slug": "ceo",
                             "teams": [{"role_slug": "researcher", "name": "Research",
                                        "employees": ["Ida"]}]},
                 "roles": [carried]})

    # A setup file describing a role this install has not got is the whole
    # reason a carried copy exists; the library must not overwrite it.
    assert by_slug(out)["researcher"]["system_prompt"] == "The file's researcher."


def test_a_role_this_install_has_never_heard_of_is_still_left_unresolved(client):
    seed_role(client, "ceo", is_ceo=True)

    out = carry({"project": {"ceo_role_slug": "ceo",
                             "teams": [{"role_slug": "nobody", "name": "Ghost",
                                        "employees": ["Ada"]}]},
                 "roles": []})

    # Nothing is invented to paper over a real gap: the drill's job is to say
    # this project cannot be staffed, and it can only say it if it stays true.
    assert "nobody" not in by_slug(out)


def test_the_endpoint_carries_them_for_real(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "worker")
    client.post("/admin/users", json={"email": "chair@example.com", "display_name": "Chair"},
                headers=ADMIN)

    response = client.post("/admin/rehearse", headers=ADMIN, json={"spec": {"project": {
        "slug": "drill", "name": "Drill", "chairman_email": "chair@example.com",
        "goal": "Ship one briefing note for the committee, sourced and dated, this month.",
        "mode": "deliver", "ceo_role_slug": "ceo",
        "teams": [{"role_slug": "worker", "name": "Desk",
                   "employees": ["Ada", "Grace"], "lead": "Ada"}],
    }}, "days": 30})

    assert response.status_code == 200, response.text
    walk = {step["key"]: step for step in response.json()["walk"]}
    assert walk["roles"]["ok"], walk["roles"]["detail"]
    assert walk["created"]["ok"], walk["created"]["detail"]


def test_the_drill_says_what_the_bundle_asks_of_the_work(client):
    """The drill used to disclaim all of this — "it says nothing about whether
    the work would be any good" — and that sentence is still true about the
    WORK. It was never true about the staffing, which is a fact about the
    bundle and exactly what a drill is entitled to state flatly.

    Two things nobody could see before launching: a project can ask for its
    work to be read and hire nobody who reads, and a project can be told it
    matters and still staff its writing seats at the library's cheapest
    setting. Both are decided at creation and neither shows up in a wall."""
    from yaaf.core.db import db_session
    from yaaf.core.models import Constitution
    from yaaf.rehearsal.report import _craft_findings

    project = make_project(client, start=False)
    project_id = project["id"]

    def craft(**rules) -> list[str]:
        with db_session() as session:
            row = session.query(Constitution).filter_by(project_id=project_id).one()
            row.body = {**row.body, **rules}
        from yaaf.rehearsal.report import _real_constitution
        return _craft_findings(project_id, _real_constitution(project_id))

    # Silent by default: a bundle with no opinion gets no paragraph.
    assert craft(stakes="normal", deliverable_review="none") == []

    # `worker` is uncategorised in the fixture, so it is neither a reviewing
    # seat nor one the stakes lift reaches — which is the refusal's case.
    asked = craft(deliverable_review="independent_read")
    assert len(asked) == 1 and "will be refused" in asked[0], asked

    # And the drill names the seats a high-stakes project actually paid more
    # for. It reads the stamps on the hired rows rather than recomputing the
    # lift, so this needs a project the lift really ran on: an edited category
    # after the fact hires nobody, and a report that announced a lift there
    # would be describing arithmetic instead of the roster.
    seed_role(client, "scribe", category="content", model="claude-sonnet-5-5",
              effort="high")
    high = client.post("/admin/projects", json={
        "slug": "matters", "name": "Matters",
        "goal": "Ship one briefing note the committee can act on.",
        "chairman_email": "chair@example.com",
        "constitution": {"stakes": "high"},
        "teams": [{"role_slug": "scribe", "name": "Editorial", "employees": ["Ada"]}],
    }, headers=ADMIN)
    assert high.status_code == 200, high.text
    from yaaf.rehearsal.report import _real_constitution

    lifted = _craft_findings(high.json()["id"], _real_constitution(high.json()["id"]))
    assert len(lifted) == 1 and "Stakes are high" in lifted[0], lifted
    assert "Ada" in lifted[0] and "Editorial" in lifted[0], \
        "name the seat and its team, not just the rule"

    # A seat the library already placed at or above the craft model is NOT
    # announced: the lift leaves it alone, and a finding that says otherwise is
    # the drill contradicting the thing it is describing.
    seed_role(client, "sage", category="research", model="claude-fable-5-1",
              effort="xhigh")
    already = client.post("/admin/projects", json={
        "slug": "already", "name": "Already",
        "goal": "Ship one briefing note the committee can act on.",
        "chairman_email": "chair@example.com",
        "constitution": {"stakes": "high"},
        "teams": [{"role_slug": "sage", "name": "Research", "employees": ["Rey"]}],
    }, headers=ADMIN)
    assert already.status_code == 200, already.text
    assert _craft_findings(already.json()["id"],
                           _real_constitution(already.json()["id"])) == []


def test_a_spec_that_asks_for_a_read_carries_the_reviewers_it_could_hire(client):
    # The drill refuses a read with no reader and lists the reviewing roles to
    # hire — from its own library. Holding only the roles the spec named, it
    # said none were installed and told the chairman to seed a full library.
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "writer", category="content")
    seed_role(client, "editor", category="challenge")

    out = carry({"project": {"ceo_role_slug": "ceo",
                             "constitution": {"deliverable_review": "independent_read"},
                             "teams": [{"role_slug": "writer", "name": "Desk",
                                        "employees": ["Ada"]}]}})

    assert "editor" in by_slug(out)
    # Carried so the refusal can name it; hiring it is still the chairman's call.
    assert [t["role_slug"] for t in out["project"]["teams"]] == ["writer"]


def test_a_spec_that_does_not_ask_carries_no_reviewers(client):
    seed_role(client, "ceo", is_ceo=True)
    seed_role(client, "writer", category="content")
    seed_role(client, "editor", category="challenge")

    out = carry({"project": {"ceo_role_slug": "ceo",
                             "teams": [{"role_slug": "writer", "name": "Desk",
                                        "employees": ["Ada"]}]}})

    assert "editor" not in by_slug(out)
