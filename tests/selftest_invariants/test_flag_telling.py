"""Invariant selftests: a flag reaches the chairman readable without help.

The chairman looked at two cards on his own project screen and had to ask what
they meant. The first was titled "Something owed every cycle did not happen";
the second "A cycle ended with work still open". Under each, one paragraph of
the CEO's working notes ("fix the six failed skills in place", "skills 8 to
10", "the clean scheduled week"), cut off at 200 characters — and the two
cards were one problem: a server schedule nobody had set up yet.

So a flag is told in parts — headline, why, plan, from_you — and the platform
refuses a telling he could not read cold: a headline in the platform's nouns,
an ID where a name belongs, a run-on sentence. Flags with one cause are told
once, and his one answer answers all of them. These attack each refusal over
HTTP, as a CEO would, and check every surface that shows the result.
"""

from __future__ import annotations

from tests.conftest import ADMIN, bearer, live_token, make_project
from tests.selftest_invariants.test_mcp_auth import mint
from tests.selftest_invariants.test_mcp_untrusted import call, payload
from tests.selftest_invariants.test_speaking_up import _channel_id, _drain
from yaaf.core.db import db_session
from yaaf.core.models import Flag, FlagKind, FlagStatus, Project
from yaaf.server import flags

TOLD = {
    "headline": "The weekly results report did not go out at 6am today",
    "why": "Six of the twelve skills that feed it failed their checks.",
    "plan": "Fix those six skills and set the weekly run up on the server by Thursday.",
}


def _open(project_id: str, kind: FlagKind, subject: str) -> str:
    with db_session() as session:
        proj = session.get(Project, project_id)
        flag = flags.open_flag(session, proj, kind, f"{kind} tripped.",
                               arithmetic={"n": 1}, subject_id=subject)
        session.flush()
        return flag.id


def _flagged(client, slug: str = "telling") -> tuple[dict, str, dict, str]:
    """A project with one open flag, a CEO token, and a boardroom message."""
    project = make_project(client, slug=slug)
    _drain()
    flag_id = _open(project["id"], FlagKind.STANDING_MISSED, "rqcr_weekly")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    ceo = bearer(token)
    said = client.post("/agent/messages", headers=ceo, json={
        "channel_id": _channel_id(project["id"], "boardroom"),
        "body": "The weekly results report missed 6am. Six skills failed. Fixed by Thursday.",
    })
    assert said.status_code == 200, said.text
    return project, flag_id, ceo, said.json()["id"]


def _raise(client, ceo, flag_id, message_id, **told):
    return client.post("/agent/flags/raise", headers=ceo, json={
        "flag_id": flag_id, "message_id": message_id, **{**TOLD, **told}})


def _view(client, project) -> list[dict]:
    got = client.get(f"/observe/projects/{project['id']}/flags", headers=ADMIN)
    assert got.status_code == 200, got.text
    return got.json()["flags"]


# ── what is refused ────────────────────────────────────────────────────────────


def test_the_old_one_paragraph_proposal_is_refused(client):
    """The paragraph is what failed. A runner still sending it must not land a
    card with empty parts."""
    project, flag_id, ceo, message_id = _flagged(client)
    refused = client.post("/agent/flags/raise", headers=ceo, json={
        "flag_id": flag_id, "message_id": message_id,
        "proposal": "This week we fix the six failed skills in place and ask for help.",
    })
    assert refused.status_code == 422
    with db_session() as session:
        assert session.get(Flag, flag_id).status == FlagStatus.OPEN


def test_a_headline_that_names_the_category_is_refused(client):
    """The exact titles that sent him to ask. Each reads the same on every
    project, so none of them says what happened to his."""
    project, flag_id, ceo, message_id = _flagged(client)
    for headline in ("Something owed every cycle did not happen",
                     "A cycle ended with work still open",
                     "Scope slipped on the weekly plan",
                     "Raising the standing obligation flag"):
        refused = _raise(client, ceo, flag_id, message_id, headline=headline)
        assert refused.status_code == 422, headline
        assert "platform words" in refused.json()["error"], headline
    with db_session() as session:
        assert session.get(Flag, flag_id).status == FlagStatus.OPEN


def test_an_id_where_a_name_belongs_is_refused_in_any_part(client):
    project, flag_id, ceo, message_id = _flagged(client)
    for part, text in (("headline", "R-07 did not go out at 6am today"),
                       ("why", "R-136 and R-139 failed their checks this morning."),
                       ("plan", f"Re-run {flag_id} once the server schedule is set up."),
                       ("from_you", "Approve the spend on rqcr_018f2a9b3c4d please.")):
        refused = _raise(client, ceo, flag_id, message_id, **{part: text})
        assert refused.status_code == 422, part
        assert f"In `{part}`" in refused.json()["error"], part
        assert "does not know what that is" in refused.json()["error"], part


def test_a_run_on_plan_is_refused(client):
    """The plain measure the boardroom, questions and heads-ups already had.
    A flag's proposal was the one thing he read from the CEO that skipped it."""
    project, flag_id, ceo, message_id = _flagged(client)
    run_on = ("This week we fix the six failed skills in place and ask the data team "
              "whether the server can take the schedule and then once that is done we "
              "run the clean scheduled week and see whether skill ten still waits on "
              "the vendor export.")
    refused = _raise(client, ceo, flag_id, message_id, plan=run_on)
    assert refused.status_code == 422
    assert "In `plan`" in refused.json()["error"]


def test_also_covers_cannot_reach_another_projects_flag(client):
    project, flag_id, ceo, message_id = _flagged(client)
    other = make_project(client, slug="elsewhere")
    theirs = _open(other["id"], FlagKind.CYCLE_MISSED, "cycl_theirs")
    refused = _raise(client, ceo, flag_id, message_id, also_covers=[theirs])
    assert refused.status_code == 404
    with db_session() as session:
        assert session.get(Flag, theirs).status == FlagStatus.OPEN
        assert session.get(Flag, flag_id).status == FlagStatus.OPEN


def test_also_covers_cannot_re_tell_a_flag_already_told(client):
    """One telling per flag. Folding a raised flag under a new card would
    rewrite what he was already told."""
    project, flag_id, ceo, message_id = _flagged(client)
    earlier = _open(project["id"], FlagKind.CYCLE_MISSED, "cycl_earlier")
    assert _raise(client, ceo, earlier, message_id).status_code == 200
    refused = _raise(client, ceo, flag_id, message_id, also_covers=[earlier])
    assert refused.status_code == 409


# ── what lands, and where he reads it ──────────────────────────────────────────


def test_a_plain_telling_reaches_his_screen_in_its_parts(client):
    project, flag_id, ceo, message_id = _flagged(client)
    raised = _raise(client, ceo, flag_id, message_id)
    assert raised.status_code == 200, raised.text

    (row,) = _view(client, project)
    assert row["headline"] == TOLD["headline"]
    assert row["why"] == TOLD["why"]
    assert row["plan"] == TOLD["plan"]
    # Nothing asked of him is a value his screen says out loud, not a gap.
    assert row["from_you"] is None
    assert row["covered_by_flag_id"] is None
    # Older readers still get one string, and it is assembled, not truncated.
    assert TOLD["plan"] in row["proposal"]


def test_one_cause_is_one_card_and_one_answer(client):
    """The two cards that prompted this file were one problem. Told together,
    they are one card, and his Noted on it closes both — he is never asked to
    answer a card he was not shown."""
    project, standing_id, ceo, message_id = _flagged(client)
    cycle_id = _open(project["id"], FlagKind.CYCLE_MISSED, "cycl_lastweek")
    raised = _raise(client, ceo, standing_id, message_id, also_covers=[cycle_id])
    assert raised.status_code == 200, raised.text
    assert raised.json()["covered"] == [cycle_id]

    rows = {r["id"]: r for r in _view(client, project)}
    assert rows[cycle_id]["status"] == "raised"
    assert rows[cycle_id]["covered_by_flag_id"] == standing_id
    assert rows[cycle_id]["headline"] == TOLD["headline"]

    # The letter counts problems, and names them.
    brief = client.get(f"/observe/projects/{project['id']}/briefing", headers=ADMIN).json()
    assert brief["waiting"]["flags_raised"] == 1
    assert brief["waiting"]["flag_headlines"] == [TOLD["headline"]]

    # The MCP desk shows him one row, carrying the CEO's headline.
    reader = mint(client)
    desk = payload(call(client, reader["token"], "desk", {}))
    mine = [r for r in desk["also_yours"] if r["kind"] == "flag"]
    assert [r["id"] for r in mine] == [standing_id]
    assert TOLD["headline"] in mine[0]["what"]

    answered = client.post(f"/admin/flags/{standing_id}/ack", headers=ADMIN,
                           json={"note": ""})
    assert answered.status_code == 200, answered.text
    with db_session() as session:
        for flag_id in (standing_id, cycle_id):
            flag = session.get(Flag, flag_id)
            assert flag.acknowledged_at is not None, flag.kind
            # Both are moments; his answer is the end of each.
            assert flag.status == FlagStatus.RESOLVED, flag.kind
    assert _view(client, project) == []
