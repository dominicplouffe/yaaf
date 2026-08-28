"""What a read tool costs a context window.

`observe.py` builds payloads for a browser, where 200KB is free. In a client it
is not, and the failure is quiet: the tool "works", the window fills, and the
chairman's question goes unanswered because there was no room left to answer
it. So the budget is measured here rather than asserted in the doc, and the two
things that must never happen — a silent cut, and a claimed cut that did not
happen — are attacked directly.
"""

from __future__ import annotations

import json

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from tests.selftest_invariants.test_mcp_auth import mint, rpc

SLUG = "acme"

#: The wire ceiling for a default call, in characters of JSON. Not a guess: the
#: fattest read tool on a seeded project is the cockpit view, and this is the
#: room it needs plus headroom. Tighten it if a tool starts sprawling — that is
#: what this number is for.
DEFAULT_CEILING = 8_000


def text_of(client, token, name, arguments=None) -> str:
    response = rpc(client, token, "tools/call",
                   {"name": name, "arguments": arguments or {}})
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result.get("isError") is False, result
    return result["content"][0]["text"]


def noisy_project(client, messages: int = 60) -> dict:
    """A project with enough said in it that trimming has to happen."""
    project = make_project(client, slug=SLUG)
    channel = channel_of(client, project["id"], "boardroom")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    for n in range(messages):
        posted = client.post("/agent/messages", headers=bearer(token), json={
            "channel_id": channel,
            "body": f"Update {n}: " + ("progress in some detail. " * 40),
        })
        assert posted.status_code == 200, posted.text
    return {**project, "channel": channel}


def test_every_read_tool_stays_inside_the_default_budget(client):
    project = noisy_project(client)
    reader = mint(client)

    calls = [
        ("desk", {}),
        ("projects", {}),
        ("project", {"project": SLUG}),
        ("project", {"project": SLUG, "view": "health"}),
        ("project", {"project": SLUG, "view": "agenda"}),
        ("plan", {"project": SLUG}),
        ("spend", {"project": SLUG}),
        ("work", {"project": SLUG}),
        ("proof", {"project": SLUG}),
        ("org", {"project": SLUG}),
        ("channels", {"project": SLUG}),
        ("channels", {"project": SLUG, "channel": project["channel"]}),
        ("files", {"project": SLUG}),
        ("record", {"project": SLUG}),
    ]
    oversized = {
        f"{name} {arguments.get('view', '')}".strip(): len(text_of(client, reader["token"],
                                                                   name, arguments))
        for name, arguments in calls
    }
    assert not {k: v for k, v in oversized.items() if v > DEFAULT_CEILING}, oversized


def test_a_cut_is_always_declared(client):
    """A silent truncation is a lie the model has no way to detect."""
    project = noisy_project(client)
    reader = mint(client)

    body = json.loads(text_of(client, reader["token"], "channels",
                              {"project": SLUG, "channel": project["channel"]}))
    assert body["truncated"] is True
    assert "detail" in body["note"]
    # Told what is missing, not merely that something is: the list ends in a
    # marker counting what it dropped, so a model can decide whether to ask
    # again rather than guess whether it has the whole room.
    kept = [m for m in body["messages"] if isinstance(m, dict)]
    marker = [m for m in body["messages"] if isinstance(m, str)]
    assert 0 < len(kept) < 60
    assert marker and "more, not shown" in marker[0]


def test_full_detail_costs_more_and_says_so(client):
    project = noisy_project(client)
    reader = mint(client)

    summary = text_of(client, reader["token"], "channels",
                      {"project": SLUG, "channel": project["channel"]})
    full = text_of(client, reader["token"], "channels",
                   {"project": SLUG, "channel": project["channel"],
                    "limit": 60, "detail": "full"})
    assert len(full) > len(summary)
    # Full detail buys more of the room, and stays honest when even that is not
    # all of it — sixty thousand characters of chatter does not fit either.
    full_body = json.loads(full)
    kept_full = [m for m in full_body["messages"] if isinstance(m, dict)]
    kept_summary = [m for m in json.loads(summary)["messages"] if isinstance(m, dict)]
    assert len(kept_full) > len(kept_summary)
    assert len(kept_full) == 60 or full_body["truncated"] is True


def test_an_oversize_payload_is_reported_not_pretended(client):
    """The other half of honesty: never claim a trim that did not happen.

    A wide payload — many small fields, none of them long — cannot be cut
    without deciding which of the chairman's fields do not matter. When that
    happens the tool says how big it is instead of inventing a truncation.
    """
    from yaaf.server.mcp import envelope

    wide = {f"field_{n}": f"value {n}" for n in range(400)}
    fitted = envelope.fit(wide)
    assert "truncated" not in fitted
    assert fitted["oversize_bytes"] > envelope.SUMMARY["budget"]

    # And a payload that genuinely was cut says so instead.
    trimmed = envelope.fit({"rows": [{"body": "x" * 900} for _ in range(50)]})
    assert trimmed["truncated"] is True


def test_the_desk_shows_only_the_projects_the_client_may_see(client):
    """The budget work must not have loosened the allowlist on the way through.

    The desk is somebody else's cross-project rollup, so it is the one payload
    where filtering is applied to a page we did not write.
    """
    make_project(client, slug=SLUG)
    make_project(client, slug="beta")
    scoped = mint(client, projects=[SLUG])

    desk = text_of(client, scoped["token"], "desk")
    assert SLUG in desk
    assert "beta" not in desk


def test_resources_are_a_convenience_not_the_only_path(client):
    """claude.ai's connector may ignore resources entirely.

    So every resource has to be reachable as a tool too, or the browser client
    silently loses a fact the terminal one has.
    """
    make_project(client, slug=SLUG)
    reader = mint(client)

    uris = {r["uri"] for r in
            rpc(client, reader["token"], "resources/list").json()["result"]["resources"]}
    assert uris == {
        "yaaf://desk", "yaaf://docs/goal-grammar",
        f"yaaf://project/{SLUG}/briefing", f"yaaf://project/{SLUG}/plan",
        f"yaaf://project/{SLUG}/goal", f"yaaf://project/{SLUG}/constitution",
    }
    # Every one of them has a tool that answers the same question.
    assert text_of(client, reader["token"], "desk")
    assert text_of(client, reader["token"], "plan", {"project": SLUG})
    assert text_of(client, reader["token"], "project", {"project": SLUG, "view": "briefing"})
    assert SLUG in text_of(client, reader["token"], "projects")


def test_a_resource_outside_the_allowlist_is_refused(client):
    make_project(client, slug=SLUG)
    make_project(client, slug="beta")
    scoped = mint(client, projects=[SLUG])

    answer = rpc(client, scoped["token"], "resources/read",
                 {"uri": "yaaf://project/beta/plan"}).json()
    assert "error" in answer
    assert "not reachable" in answer["error"]["message"] or \
           "is reachable by this client" in answer["error"]["message"]


def test_admin_token_still_opens_nothing_here(client):
    """The read tools are new surface; the principal rule is not weakened by them."""
    make_project(client, slug=SLUG)
    assert rpc(client, ADMIN["Authorization"].split()[1], "resources/list").status_code == 401
