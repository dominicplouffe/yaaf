"""Invariant selftest: the CEO can see the Deliverables shelf it is judged on.

Posting a memo to the boardroom and saving it to /workspace feels like
shipping. It is not: the shelf is a third, separate act, and a CEO doing the
first two had no way to observe that it skipped the third.

One desk wrote its gameweek memo, posted it, filed it to disk — and left the
chairman's Deliverables screen reading "none yet" for two days. He found it by
going to look, and pasted the empty state back into chat:

    CHAIRMAN: deliverables say this at the moment / none yet
    CEO:      That's my mistake, and it's fixed — I'd been posting the memo
              into the boardroom and saving it to the workspace, but never
              publishing it to the Deliverables shelf.

Another project finished every requirement it had with nothing on the shelf at
all. In both cases the platform knew and never said.
"""

from __future__ import annotations

import base64

from tests.conftest import ADMIN, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Agent, Project, Run
from yaaf.server.contextpack import build_context_pack


def _pack(project: dict, agent_id: str | None = None) -> dict:
    agent_id = agent_id or project["ceo_agent_id"]
    run_id, _ = live_token(project["id"], agent_id)
    with db_session() as session:
        return build_context_pack(
            session, session.get(Run, run_id), session.get(Agent, agent_id),
            session.get(Project, project["id"]),
        )




def _publish(client, token: str, filename: str, body: bytes):
    """Publish exactly as an agent does — over HTTP, on a real run token."""
    return client.post(
        "/agent/artifacts",
        json={"filename": filename,
              "content_base64": base64.b64encode(body).decode()},
        headers={"Authorization": f"Bearer {token}"},
    )


def test_an_empty_shelf_says_so_in_the_words_the_chairman_will_use(client):
    project = make_project(client)

    shelf = _pack(project)["deliverables"]

    assert shelf["published"] == 0
    assert shelf["recent"] == []
    assert "none yet" in shelf["note"], (
        "the pack must quote the empty state the chairman is actually looking at"
    )


def test_a_published_deliverable_appears_with_what_it_claims_to_satisfy(client):
    project = make_project(client)
    agent_id = project["ceo_agent_id"]
    _run_id, token = live_token(project["id"], agent_id)

    posted = _publish(client, token, "gw2_memo.md", b"# GW2 MEMO\nRoll the transfer.")
    assert posted.status_code == 200, posted.text

    shelf = _pack(project)["deliverables"]

    assert shelf["published"] == 1
    assert shelf["recent"][0]["name"] == "gw2_memo.md"
    assert shelf["recent"][0]["version"] == 1
    assert "none yet" not in shelf["note"], "a stocked shelf must not report itself empty"
    assert "1 on the shelf" in shelf["note"]


def test_the_pack_and_the_chairmans_screen_cannot_disagree_about_the_count(client):
    """Two answers to "what has shipped" is worse than one. The count in the
    pack must be the count the Deliverables screen renders."""
    project = make_project(client)
    _run_id, token = live_token(project["id"], project["ceo_agent_id"])
    for name in ("a.md", "b.md"):
        assert _publish(client, token, name, b"x").status_code == 200

    screen = client.get(f"/observe/projects/{project['id']}/files", headers=ADMIN).json()
    shelf = _pack(project)["deliverables"]

    assert shelf["published"] == 2, "guard: the comparison below is vacuous at zero"
    assert shelf["published"] == len(screen["published"]), (
        "the CEO and the chairman must be counting the same shelf"
    )


def test_the_shelf_is_the_ceos_business_and_not_an_employees(client):
    """Employees cannot publish and are not judged on the shelf; a block they
    cannot act on is pure token cost on every wake."""
    from tests.conftest import agent_by_name

    project = make_project(client)

    pack = _pack(project, agent_by_name(project, "Emp2"))

    assert "deliverables" not in pack
