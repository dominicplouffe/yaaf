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

from tests.conftest import ADMIN, agent_by_name, bearer, channel_of, live_token, make_project
from tests.selftest_invariants.test_mcp_auth import foreign_project, mint, rpc

SLUG = "acme"

OPEN_TAG = "<untrusted_agent_content"
CLOSE_TAG = "</untrusted_agent_content>"
TRUNCATION_MARK = "… [truncated]"

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
        ("inbox", {"project": SLUG}),
        ("files", {"project": SLUG}),
        ("record", {"project": SLUG}),
        ("record", {"project": SLUG, "view": "everything"}),
        ("record", {"project": SLUG, "view": "results"}),
    ]
    oversized = {
        f"{name} {arguments.get('view', '')}".strip(): len(text_of(client, reader["token"],
                                                                   name, arguments))
        for name, arguments in calls
    }
    assert not {k: v for k, v in oversized.items() if v > DEFAULT_CEILING}, oversized


def test_a_full_inbox_fits_and_says_what_it_left_out(client):
    """Every message lands in each reader's inbox, so a chatty room multiplies:
    sixty long messages are sixty per teammate. The default call still has to
    fit, and still has to say it was cut."""
    project = make_project(client, slug=SLUG)
    team_room = channel_of(client, project["id"], "team")
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    for n in range(60):
        posted = client.post("/agent/messages", headers=bearer(token), json={
            "channel_id": team_room, "body": f"Update {n}: " + ("detail. " * 120)})
        assert posted.status_code == 200, posted.text
    reader = mint(client)

    text = text_of(client, reader["token"], "inbox", {"project": SLUG})
    assert len(text) <= DEFAULT_CEILING, len(text)
    body = json.loads(text)
    assert body["unread"] >= 60 and body["truncated"] is True


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
    """The budget work must not have loosened the filter on the way through.

    The desk is somebody else's cross-project rollup, so it is the one payload
    where filtering is applied to a page we did not write.
    """
    make_project(client, slug=SLUG)
    foreign_project(client, slug="beta")
    scoped = mint(client)

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
        "yaaf://desk", "yaaf://docs/goal-grammar", "yaaf://docs/design-doctrine",
        f"yaaf://project/{SLUG}/briefing", f"yaaf://project/{SLUG}/plan",
        f"yaaf://project/{SLUG}/goal", f"yaaf://project/{SLUG}/constitution",
    }
    # Every one of them has a tool that answers the same question.
    assert text_of(client, reader["token"], "desk")
    assert text_of(client, reader["token"], "plan", {"project": SLUG})
    assert text_of(client, reader["token"], "project", {"project": SLUG, "view": "briefing"})
    assert SLUG in text_of(client, reader["token"], "projects")


def test_a_resource_for_another_chairmans_project_is_refused(client):
    make_project(client, slug=SLUG)
    foreign_project(client, slug="beta")
    scoped = mint(client)

    answer = rpc(client, scoped["token"], "resources/read",
                 {"uri": "yaaf://project/beta/plan"}).json()
    assert "error" in answer
    assert "not reachable" in answer["error"]["message"] or \
           "is reachable by this client" in answer["error"]["message"]


def test_a_personal_token_still_opens_nothing_here(client):
    """The read tools are new surface; the principal rule is not weakened by them."""
    make_project(client, slug=SLUG)
    assert rpc(client, ADMIN["Authorization"].split()[1], "resources/list").status_code == 401


def _long_file(client, chars: int = 46_000, name: str = "recommendation.md") -> str:
    """A deliverable the size the chairman actually hit: 46KB, cut at 20K."""
    from pathlib import Path

    from yaaf.core.config import settings

    make_project(client, slug=SLUG)
    root = Path(settings().workspaces_root) / SLUG
    root.mkdir(parents=True, exist_ok=True)
    # Numbered so a window landing anywhere can be located exactly.
    body = "".join(f"line {n:05d} of the recommendation, verbatim.\n" for n in range(1200))
    body = (body * 2)[:chars]
    (root / name).write_text(body)
    return body


def test_a_long_file_can_be_walked_to_its_end(client):
    """The tail of a long file used to be unreachable, not merely slow to reach.

    `fit` cut a string at the budget, appended "… [truncated]", and offered no
    second call that started where the first stopped — so a 46KB document was
    permanently three-fifths readable and a client that said so was right.
    """
    body = _long_file(client)
    reader = mint(client)

    seen, offset, calls = "", 0, 0
    while True:
        page = json.loads(text_of(client, reader["token"], "files",
                                  {"project": SLUG, "path": "recommendation.md",
                                   "detail": "full", "offset": offset}))
        assert page["chars"] == len(body), "the true length travels with every window"
        seen += page["content"].split(">\n", 1)[1].rsplit("\n<", 1)[0]
        calls += 1
        assert calls < 10, "a 46KB file should not take ten calls"
        if "next_offset" not in page:
            break
        offset = page["next_offset"]

    assert seen == body, "walking the offsets returns the file, whole and in order"
    assert calls == 3


def test_every_window_closes_its_own_envelope(client):
    """The cut used to land inside the wrapper it was cutting.

    `fit` trimmed the *wrapped* string, so a truncated file lost its closing
    tag — the one guarantee the untrusted envelope exists to make, broken by
    our own budget rather than by an agent.
    """
    _long_file(client)
    reader = mint(client)

    for offset in (0, 20_000, 40_000):
        page = json.loads(text_of(client, reader["token"], "files",
                                  {"project": SLUG, "path": "recommendation.md",
                                   "detail": "full", "offset": offset}))
        assert page["content"].startswith(OPEN_TAG)
        assert page["content"].endswith(CLOSE_TAG)
        assert TRUNCATION_MARK not in page["content"]


def test_an_offset_past_the_end_is_empty_rather_than_a_wrap_around(client):
    """The boundary a walker hits when it miscounts: empty and final, never the
    head of the file served a second time as though it were the tail."""
    body = _long_file(client)
    reader = mint(client)

    page = json.loads(text_of(client, reader["token"], "files",
                              {"project": SLUG, "path": "recommendation.md",
                               "detail": "full", "offset": len(body) + 5_000}))
    assert page["offset"] == len(body)
    assert "next_offset" not in page
    assert page["content"] == f'{OPEN_TAG} author="workspace" kind="file">\n\n{CLOSE_TAG}'


def test_an_offset_is_not_a_second_way_into_another_project(client):
    """A new argument on a project-addressed tool is a new chance to skip the
    gate. It resolves the project first, exactly as it did before."""
    _long_file(client)
    beta = foreign_project(client, slug="beta")
    reader = mint(client)

    for ref in ("beta", beta["id"]):
        answer = rpc(client, reader["token"], "tools/call",
                     {"name": "files", "arguments": {"project": ref, "path": "x.md",
                                                     "offset": 0}}).json()
        assert answer["result"]["isError"] is True
        assert "is reachable by this client" in answer["result"]["content"][0]["text"]


def test_a_nonsense_offset_is_refused_rather_than_read_as_zero(client):
    """Silently coercing to zero would re-serve the first window for ever —
    the exact confusion the argument exists to end."""
    _long_file(client)
    reader = mint(client)

    for bad in ("soon", -1, {"n": 1}):
        answer = rpc(client, reader["token"], "tools/call",
                     {"name": "files", "arguments": {"project": SLUG,
                                                     "path": "recommendation.md",
                                                     "offset": bad}}).json()
        assert answer["result"]["isError"] is True, bad
        assert "offset" in answer["result"]["content"][0]["text"]
