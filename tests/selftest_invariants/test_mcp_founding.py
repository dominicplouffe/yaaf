"""Designing a project from a client, and the three things that must not slip.

M4 hands a model the verb that starts spending money. The attacks are the ones
that verb invites:

  * a draft the platform would refuse must not become a project through here
    any more than through the form
  * the chairman is whoever holds the token — never whoever the model typed
  * a credential is named, never carried: no secret value passes through a
    tool call, and nothing here can attach one that is not already stored
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, make_project, seed_role
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from yaaf.core.constitution import AUTONOMY_PRESETS

SLUG = "acme"

GOOD_SPEC = {
    "slug": "ppc-audit",
    "name": "PPC Audit",
    "goal": ("Audit the customer's Amazon PPC account and produce a plain-English deck "
             "a non-technical owner can act on, by August 20th."),
    "mode": "deliver",
    "ceo_role_slug": "ceo",
    "ceo_name": "CEO",
    "teams": [{"role_slug": "worker", "name": "Alpha", "employees": ["Lead"], "lead": "Lead"}],
}


def founder(client, name="drafting", projects=()):
    return mint(client, name=name, scopes=("create",), projects=projects)


def call(client, token, tool, arguments=None):
    response = rpc(client, token, "tools/call",
                   {"name": tool, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    text = result["content"][0]["text"]
    return (result["isError"], text if result["isError"] else json.loads(text))


def confirmed(client, token, tool, arguments):
    failed, preview = call(client, token, tool, arguments)
    assert failed is False, preview
    assert preview.get("confirm_required") is True, preview
    return call(client, token, tool, {**arguments, "confirm": preview["confirm"]})


def test_the_catalog_is_what_you_can_hire(client):
    make_project(client, slug=SLUG)
    seed_role(client, "researcher", category="research")
    token = founder(client)["token"]

    failed, roles = call(client, token, "catalog", {"what": "roles"})
    assert failed is False
    slugs = {r["slug"] for r in roles["roles"]}
    assert {"ceo", "worker", "researcher"} <= slugs

    failed, filtered = call(client, token, "catalog",
                            {"what": "roles", "category": "research"})
    assert {r["slug"] for r in filtered["roles"]} == {"researcher"}


def test_a_draft_the_form_would_refuse_is_refused_here(client):
    """`validate` runs the endpoint's own Pydantic model, so the two cannot drift."""
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, message = call(client, token, "create_project",
                           {"spec": {**GOOD_SPEC, "goal": "do stuff"}})
    assert failed is True
    assert "not something the platform will accept" in message
    assert "goal" in message

    # An invented constitution rule is the specific mistake a real Founder run
    # made: it validates as a bare dict and is then ignored for the project's
    # life, so the chairman believes he set something.
    failed, message = call(client, token, "create_project",
                           {"spec": {**GOOD_SPEC, "constitution": {"reporting_cadence": 4}}})
    assert failed is True
    assert "no such rule" in message


def test_the_chairman_is_the_token_holder_not_the_argument(client):
    """A model must not be able to create a project chaired by somebody else."""
    make_project(client, slug=SLUG)
    client.post("/admin/users", json={"email": "someone@else.example", "display_name": "Else"},
                headers=ADMIN)
    token = founder(client)["token"]

    failed, created = confirmed(client, token, "create_project",
                                {"spec": {**GOOD_SPEC,
                                          "chairman_email": "someone@else.example"}})
    assert failed is False, created

    from yaaf.core.db import db_session
    from yaaf.core.models import Project, User

    with db_session() as session:
        made = session.query(Project).filter(Project.slug == "ppc-audit").one()
        assert session.get(User, made.chairman_user_id).email == "chair@example.com"

    # And the new project is reachable by the client that made it, because its
    # chairman is that client's user.
    failed, projects = call(client, token, "projects")
    assert "ppc-audit" in {p["slug"] for p in projects["projects"]}


def test_creating_is_two_phase_and_the_preview_says_what_it_will_cost(client):
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, preview = call(client, token, "create_project", {"spec": GOOD_SPEC})
    assert failed is False
    assert preview["confirm_required"] is True
    assert preview["about_to"]["about_to_create"]["slug"] == "ppc-audit"
    assert preview["about_to"]["about_to_create"]["headcount"] == 1
    assert preview["about_to"]["chairman"] == "chair@example.com"
    assert "stops being free" in preview["about_to"]["warning"]
    # Nothing exists yet.
    assert "ppc-audit" not in {p["slug"] for p in
                               client.get("/observe/projects", headers=ADMIN).json()}


def test_the_preview_names_the_dial_the_project_will_actually_get(client):
    """The one thing about a spec the platform may have decided rather than the
    drafter. A chairman finding out afterwards that his CEO signs its own dates
    is a bad way to learn what a default is."""
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, preview = call(client, token, "create_project", {"spec": GOOD_SPEC})
    assert failed is False
    authority = preview["about_to"]["authority"]
    assert authority.startswith("autonomous")
    assert "this spec did not say" in authority

    supervised = {**GOOD_SPEC, "slug": "hands-on",
                  "constitution": dict(AUTONOMY_PRESETS["supervised"])}
    failed, preview = call(client, token, "create_project", {"spec": supervised})
    assert failed is False
    authority = preview["about_to"]["authority"]
    assert authority.startswith("supervised")
    assert "as this spec asks" in authority


def test_a_spec_that_says_nothing_creates_an_autonomous_project(client):
    """Same default as the form and the endpoint, and the receipt says which."""
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, created = confirmed(client, token, "create_project", {"spec": GOOD_SPEC})
    assert failed is False, created
    assert created["authority"] == "autonomous"

    from yaaf.core.constitution import effective, preset_name
    from yaaf.core.db import db_session
    from yaaf.core.models import Project

    with db_session() as session:
        made = session.query(Project).filter(Project.slug == "ppc-audit").one()
        assert preset_name(effective(session, made.id).model_dump()) == "autonomous"


def test_a_credential_is_named_never_carried(client):
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, message = call(client, token, "create_project",
                           {"spec": GOOD_SPEC, "billing_secret": "not-a-real-secret"})
    assert failed is True
    assert "does not belong in a tool call" in message

    # A service credential cannot become the model credential by being named
    # in the wrong field — the same refusal the secrets API makes.
    client.post("/admin/secrets", json={"name": "wordpress", "value": "hunter2hunter2",
                                        "kind": "service", "role_scopes": []}, headers=ADMIN)
    failed, message = call(client, token, "create_project",
                           {"spec": GOOD_SPEC, "billing_secret": "wordpress"})
    assert failed is True
    assert "not a billing one" in message

    # And no tool on this scope will hand back a value.
    failed, listed = call(client, token, "catalog", {"what": "roles"})
    assert "hunter2hunter2" not in json.dumps(listed)


def test_export_never_carries_values(client):
    """An export through a model client would put every credential in its context."""
    project = make_project(client, slug=SLUG)
    client.post("/admin/secrets", json={"project_id": project["id"], "name": "wordpress",
                                        "value": "hunter2hunter2", "kind": "service",
                                        "role_scopes": ["worker"]}, headers=ADMIN)
    token = founder(client)["token"]

    failed, exported = call(client, token, "clone_project",
                            {"project": SLUG, "verb": "export"})
    assert failed is False
    assert exported["values_included"] is False
    assert "hunter2hunter2" not in json.dumps(exported)


def test_a_drill_creates_nothing(client):
    """The cheapest way to find out a roster does not work, and it stays free."""
    make_project(client, slug=SLUG)
    token = founder(client)["token"]
    before = {p["slug"] for p in client.get("/observe/projects", headers=ADMIN).json()}

    failed, drill = call(client, token, "rehearse", {"spec": GOOD_SPEC, "days": 7})
    assert failed is False, drill
    assert {p["slug"] for p in client.get("/observe/projects", headers=ADMIN).json()} == before

    failed, message = call(client, token, "rehearse", {"spec": GOOD_SPEC, "days": 900})
    assert failed is True
    assert "between 1 and 90" in message


def test_grading_says_so_when_it_cannot_grade(client):
    """No platform credential is a normal state, and a rubric beats a refusal."""
    make_project(client, slug=SLUG)
    token = founder(client)["token"]

    failed, graded = call(client, token, "grade_goal", {"goal": GOOD_SPEC["goal"]})
    assert failed is False
    assert graded["graded"] is False
    assert "no platform credential" in graded["why"]
    # The eight elements travel with the refusal, and the rubric itself is
    # pointed at rather than inlined — eight kilobytes would only get trimmed.
    assert "finish_line" in graded["elements"]
    assert len(graded["elements"]) == 8
    assert graded["rubric"] == "yaaf://docs/goal-grammar"


def test_the_create_scope_is_its_own(client):
    """A reader cannot design, and a designer cannot steer."""
    make_project(client, slug=SLUG)
    reader = mint(client, scopes=("read",))["token"]
    designer = founder(client)["token"]

    for tool in ("create_project", "rehearse", "catalog", "grade_goal"):
        assert rpc(client, reader, "tools/call",
                   {"name": tool, "arguments": {}}).json()["error"]["code"] == -32601, tool

    # `create` implies read, so a designer can look at what it is cloning.
    failed, _ = call(client, designer, "projects")
    assert failed is False
    assert rpc(client, designer, "tools/call",
               {"name": "lifecycle", "arguments": {"project": SLUG, "verb": "stop"}}
               ).json()["error"]["code"] == -32601


def test_the_design_prompt_tells_a_client_to_stop_before_spending(client):
    """A prompt grants nothing, but it is where the habit lives — and the habit
    is that the chairman says go."""
    make_project(client, slug=SLUG)
    designer = founder(client)["token"]
    reader = mint(client, name="reader", scopes=("read",))["token"]

    listed = rpc(client, designer, "prompts/list").json()["result"]["prompts"]
    assert {p["name"] for p in listed} == {"brief", "why-at-risk", "spot-audit", "design"}
    # A reader is offered the three it can actually carry out.
    reader_prompts = rpc(client, reader, "prompts/list").json()["result"]["prompts"]
    assert {p["name"] for p in reader_prompts} == {"brief", "why-at-risk", "spot-audit"}

    got = rpc(client, designer, "prompts/get",
              {"name": "design", "arguments": {"idea": "audit a PPC account"}}).json()
    text = got["result"]["messages"][0]["content"]["text"]
    assert "audit a PPC account" in text
    assert "Do not call `create_project` until I say so" in text

    refused = rpc(client, reader, "prompts/get", {"name": "design", "arguments": {}}).json()
    assert "no prompt" in refused["error"]["message"]


def test_a_project_prompt_resolves_the_slug_before_the_model_sees_it(client):
    make_project(client, slug=SLUG)
    make_project(client, slug="beta")
    scoped = mint(client, name="one", scopes=("read",), projects=[SLUG])["token"]

    got = rpc(client, scoped, "prompts/get",
              {"name": "why-at-risk", "arguments": {"project": SLUG}}).json()
    assert SLUG in got["result"]["messages"][0]["content"]["text"]

    refused = rpc(client, scoped, "prompts/get",
                  {"name": "why-at-risk", "arguments": {"project": "beta"}}).json()
    assert "is reachable by this client" in refused["error"]["message"]
