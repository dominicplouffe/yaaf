"""A machine's signature is never mistaken for the chairman's.

Rule 3 of docs/MCP_SERVER.md. The design lets a model hold the chairman's eyes
and, at `steer`, some of the chairman's verbs — and the whole bargain depends on
the record keeping the difference. Six weeks later, in a review, "the chairman
accepted this" and "the chairman's model client accepted this" must not read
the same.

So the attack here is the quiet one: not a forged token, but a write that lands
in the audit trail wearing the chairman's bare name.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, live_token, make_project
from tests.selftest_invariants.test_mcp_auth import mint, rpc
from tests.selftest_invariants.test_mcp_confirm import call, steerer
from tests.selftest_invariants.test_requirements import CMD_TEST, deliver_project, propose
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog

SLUG = "acme"


def audit_rows(action: str | None = None) -> list[AuditLog]:
    with db_session() as session:
        rows = session.query(AuditLog).order_by(AuditLog.at).all()
        return [r for r in rows if action is None or r.action == action]


def confirmed(client, token, tool, arguments):
    """Preview, then perform — the two calls every gated verb needs."""
    failed, preview = call(client, token, tool, arguments)
    assert failed is False, preview
    assert preview.get("confirm_required") is True, preview
    return call(client, token, tool, {**arguments, "confirm": preview["confirm"]})


def test_every_steer_call_leaves_a_row_naming_the_machine(client):
    project = make_project(client, slug=SLUG)
    token = steerer(client, name="dom-laptop")["token"]

    call(client, token, "boardroom_say", {"project": SLUG, "body": "status please"})
    confirmed(client, token, "lifecycle",
              {"project": SLUG, "verb": "pause", "reason": "watching the burn"})

    actors = {r.action: r.actor for r in audit_rows() if r.action.startswith("mcp.")}
    assert set(actors) == {"mcp.boardroom_say", "mcp.lifecycle"}
    for action, actor in actors.items():
        assert actor.startswith("user:user_"), action
        assert actor.endswith(" via mcp:dom-laptop"), action

    # And the detail carries what was asked for, so the row is readable without
    # reconstructing the call from context.
    row = next(r for r in audit_rows("mcp.lifecycle"))
    assert row.detail["arguments"]["verb"] == "pause"
    assert row.detail["arguments"]["reason"] == "watching the burn"
    # Never the confirmation token: it is a credential, and this is a log.
    assert "confirm" not in json.dumps(row.detail)
    assert row.project_id == project["id"]


def test_the_handlers_own_rows_are_attributed_too(client):
    """The extra row is belt; this is braces.

    Every admin handler writes its own audit entry, and those are what a
    reviewer actually reads — `project.pause`, not `mcp.lifecycle`. If only the
    wrapper carried the attribution, the rows people look at would still say
    the chairman did it personally.
    """
    make_project(client, slug=SLUG)
    token = steerer(client, name="phone")["token"]
    confirmed(client, token, "lifecycle",
              {"project": SLUG, "verb": "stop", "reason": "done for now"})

    row = next(r for r in audit_rows("project.stop"))
    assert row.actor.endswith(" via mcp:phone")


def test_no_write_through_mcp_is_recorded_as_the_bare_chairman(client):
    """The claim in one assertion: nothing this surface does looks hand-made."""
    make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    before = {r.id for r in audit_rows()}

    call(client, token, "boardroom_say", {"project": SLUG, "body": "hello"})
    confirmed(client, token, "lifecycle",
              {"project": SLUG, "verb": "pause", "reason": "quiet week"})

    written = [r for r in audit_rows() if r.id not in before]
    assert written
    assert all("via mcp:" in r.actor for r in written), [r.actor for r in written]
    assert not any(r.actor == "user:admin" for r in written)


def test_the_browser_path_is_untouched(client):
    """The actor is ambient during an MCP call and must not leak past it.

    A contextvar that stayed set would relabel the chairman's own clicks — the
    same confusion, pointing the other way.
    """
    project = make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    confirmed(client, token, "lifecycle",
              {"project": SLUG, "verb": "pause", "reason": "briefly"})

    assert client.post(f"/admin/projects/{project['id']}/start",
                       headers=ADMIN).status_code == 200
    assert next(r for r in audit_rows("project.start")[-1:]).actor == "user:admin"


def test_a_scope_verdict_through_mcp_is_marked_as_machine_carried(client):
    """The sharpest verb on the surface, and the one whose record matters most.

    Accepting scope is the chairman's signature on what the project owes. If a
    model carried it, the plan has to say so where the plan is read.
    """
    project = deliver_project(client, slug=SLUG)
    _, ceo = live_token(project["id"], project["ceo_agent_id"])
    proposed = propose(client, ceo, statement="A dated report exists at out/report.pdf",
                       test=CMD_TEST)
    token = steerer(client, name="dom-laptop")["token"]

    failed, outcome = confirmed(client, token, "requirement_decide", {
        "project": SLUG, "verb": "accept", "requirement": proposed["requirement_id"],
        "reason": "in scope and estimated sanely"})
    assert failed is False, outcome

    row = next(r for r in audit_rows("mcp.requirement_decide"))
    assert " via mcp:dom-laptop" in row.actor
    assert row.detail["arguments"]["verb"] == "accept"
    # The handler's own record agrees, so the plan page and the audit page
    # cannot tell different stories about who signed.
    handler_rows = [r for r in audit_rows() if r.entity == "requirement"]
    assert handler_rows and all(" via mcp:" in r.actor for r in handler_rows)


def test_the_preview_itself_writes_nothing(client):
    """A preview is a question. Only the answer belongs in the record."""
    make_project(client, slug=SLUG)
    token = steerer(client)["token"]
    before = {r.id for r in audit_rows()}

    call(client, token, "lifecycle", {"project": SLUG, "verb": "stop", "reason": "maybe"})
    assert {r.id for r in audit_rows()} == before


def test_an_agent_cannot_reach_the_steer_verbs_through_this_door(client):
    """The surface exists so a model can act *for* the chairman. It must never
    become a way for a project's own agents to act *as* one."""
    project = make_project(client, slug=SLUG)
    _, run_token = live_token(project["id"], project["ceo_agent_id"])
    answer = rpc(client, run_token, "tools/call",
                 {"name": "lifecycle", "arguments": {"project": SLUG, "verb": "stop"}})
    assert answer.status_code == 401

    reader = mint(client, scopes=("read",))["token"]
    assert rpc(client, reader, "tools/call",
               {"name": "lifecycle", "arguments": {"project": SLUG}}
               ).json()["error"]["code"] == -32601


def test_actor_columns_hold_the_longest_stamp():
    """The stamp has to fit where it is written, on the database that enforces it.

    SQLite treats a VARCHAR length as advice, so a stamp too long for its column
    passes the default suite and is a 500 on Postgres. Derived from the columns
    that feed the stamp, so widening a client name or a user id without
    widening these fails here rather than in production.
    """
    from yaaf.core.models import McpClient, RequirementEvent, TaskEvent, User

    user_id = User.__table__.c.id.type.length
    client_name = McpClient.__table__.c.name.type.length
    longest = len("user:") + user_id + len(" via mcp:") + client_name
    for model in (AuditLog, RequirementEvent, TaskEvent):
        assert model.__table__.c.actor.type.length >= longest, model.__tablename__


def test_a_long_client_name_still_signs_its_writes(client):
    """The behavioural half: on Postgres this was the 500 itself."""
    make_project(client, slug=SLUG)
    name = "n" * 80
    token = steerer(client, name=name)["token"]

    failed, outcome = call(client, token, "boardroom_say",
                           {"project": SLUG, "body": "status please"})
    assert failed is False, outcome
    row = next(r for r in audit_rows("mcp.boardroom_say"))
    assert row.actor.endswith(f" via mcp:{name}")
