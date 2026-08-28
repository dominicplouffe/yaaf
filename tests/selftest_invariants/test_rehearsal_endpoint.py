"""Invariant selftests: the drill's spec, as the API prepares it.

The drill builds its own throwaway database, so the install's role library is
not there to hire from — a role resolves only if the spec carries a definition
or the process happens to sit in a checkout with a `roles/` directory. The
server never does: the image ships `yaaf/`, not `roles/`. So the dashboard's
fire drill resolved nothing, and every spec that hired from the library came
back "this company would not start" — the one verdict a preflight must never
get wrong, because it is indistinguishable from a real misconfiguration.

These tests are about the preparation only and run no drill, which is why they
may hold the `client` its sibling module deliberately avoids.
"""

from __future__ import annotations

from tests.conftest import ADMIN, seed_role
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
    # The rest of the spec travels untouched — the drill runs the company the
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
               "model": "claude-sonnet-5"}
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
    # this company cannot be staffed, and it can only say it if it stays true.
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
