"""Invariant selftests: one CEO, one record of what the chairman has said.

The chairman answered a worker's assist in his Inbox with a link. A work run of
the CEO answered his note four minutes later, in a box under it he never looks
at. Two hours on, a restarted chat session told him it was still waiting on
him; when he said he had sent it, it "found" the link and apologised for having
missed it. It had not missed it. Nothing had ever handed it over.

The CEO he talks to is several processes: a resident chat session that
restarts with every brief change and every deploy, and work runs that each
start cold. The record they share is the boardroom. So:

1. Whatever he writes on a decision is his line in the boardroom, where every
   lane reads it — and it does not count as an answer to the CEO's own
   questions, which it was not written on.
2. The chat lane reads without consuming: building its picture changes nothing,
   so the lane that acts still finds what it must act on.
3. The chat lane is shown the whole pack except what is named, with a reason,
   in one place — and every lane sees what the CEO itself last said.
"""

from __future__ import annotations

import pathlib
import re

from tests.conftest import ADMIN, agent_by_name, amend, bearer, channel_of, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import (
    Agent,
    ChairmanNote,
    Message,
    Notice,
    NoticeKind,
    Project,
    Run,
    Suggestion,
    utcnow,
)
from yaaf.server import janitor
from yaaf.server.contextpack import CHAT_OMITS, build_context_pack

LINK = "https://demoriff.com/d/repors-hub-version-1"


def _assist(client, project: dict) -> str:
    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    filed = client.post("/agent/assists", headers=bearer(token), json={
        "what": "Upload one short screen recording to the product and reply done."})
    assert filed.status_code == 200, filed.text
    return filed.json()["assist_id"]


def _boardroom(project: dict) -> list[Message]:
    with db_session() as session:
        rows = session.query(Message).filter_by(project_id=project["id"]).order_by(
            Message.created_at).all()
        session.expunge_all()
    return rows


def _chat(project: dict) -> str:
    return live_token(project["id"], project["ceo_agent_id"], kind="chat_session")[1]


# ── 1. what he writes on a decision is said in the boardroom ────────────────────


def test_a_note_on_an_assist_is_his_line_in_the_boardroom(client):
    project = make_project(client)
    assist = _assist(client, project)
    decided = client.post(f"/admin/approvals/{assist}/decide", headers=ADMIN,
                          json={"approve": True, "note": f"Can you see this? {LINK}"})
    assert decided.status_code == 200, decided.text

    said = [m for m in _boardroom(project) if m.sender_user_id and LINK in m.body]
    assert len(said) == 1, "his note must be in the room every CEO lane reads"
    assert said[0].body.startswith("Re: “Approved: Assist: Upload one short"), said[0].body
    with db_session() as session:
        note = session.query(ChairmanNote).one()
        assert note.message_id == said[0].id

    # The chat session reads the room as the chairman speaking, and answers it.
    room = channel_of(client, project["id"], "boardroom")
    heard = client.get("/agent/messages", headers=bearer(_chat(project)),
                       params={"channel_id": room}).json()
    assert any(m["from_user"] and LINK in m["body"] for m in heard)


def test_every_inbox_door_that_takes_a_note_says_it_in_the_boardroom(client):
    """Approve, decline, fulfil a need, note a heads-up: one rule for all of
    them, because each was a different file and a side door is how this broke."""
    project = make_project(client)
    with db_session() as session:
        session.get(Project, project["id"]).standing_orders = ["Tell me each trade"]
    ceo = live_token(project["id"], project["ceo_agent_id"])[1]

    declined = _assist(client, project)
    client.post(f"/admin/approvals/{declined}/decide", headers=ADMIN,
                json={"approve": False, "note": "No: we talked about it in chat."})
    need = client.post("/agent/needs", headers=bearer(ceo), json={
        "title": "An email checker key", "why": "To check 500 addresses before sending."})
    assert need.status_code == 200, need.text
    fulfilled = client.post(f"/admin/approvals/{need.json()['need_id']}/fulfill", headers=ADMIN,
                            json={"secret_name": "MILLION_API_KEY", "value": "k-123",
                                  "roles": ["ceo"], "note": "done, it is in a variable"})
    assert fulfilled.status_code == 200, fulfilled.text
    heads = client.post("/agent/heads_up", headers=bearer(ceo), json={
        "title": "No trade tomorrow", "body": "Nothing to place on Friday.",
        "standing_order": "Tell me each trade"})
    assert heads.status_code == 200, heads.text
    client.post(f"/admin/heads_up/{heads.json()['heads_up_id']}/ack", headers=ADMIN,
                json={"note": "Send it by 8pm."})

    said = [m.body for m in _boardroom(project) if m.sender_user_id]
    for words in ("we talked about it in chat", "done, it is in a variable", "by 8pm"):
        assert any(words in b for b in said), f"{words!r} never reached the boardroom"
    assert not any("k-123" in b for b in said), "the credential itself is never said"
    with db_session() as session:
        assert session.query(ChairmanNote).filter(ChairmanNote.message_id.is_(None)).count() == 0


def test_a_decision_without_a_word_says_nothing(client):
    project = make_project(client)
    before = len(_boardroom(project))
    client.post(f"/admin/approvals/{_assist(client, project)}/decide", headers=ADMIN,
                json={"approve": True})
    assert not [m for m in _boardroom(project)[before:] if m.sender_user_id]


def test_only_chairman_notes_writes_a_note(client):
    """The boardroom line is made in one place, so a note row made anywhere else
    is a door back to the side table nobody read."""
    root = pathlib.Path(__file__).resolve().parents[2] / "yaaf"
    makers = [str(p.relative_to(root)) for p in root.rglob("*.py")
              if re.search(r"(?<!class )\bChairmanNote\(", p.read_text())]
    assert makers == ["server/chairman_notes.py"], makers


def test_a_note_on_a_decision_does_not_answer_the_ceos_question(client):
    """Any line of his after a question answers it — but a reply to a worker's
    captcha was not written on the CEO's question about the postal address."""
    project = make_project(client)
    ceo = live_token(project["id"], project["ceo_agent_id"])[1]
    asked = client.post("/agent/asks", headers=bearer(ceo), json={
        "title": "What street address should sales emails show?",
        "body": "The footer needs one.", "why": "First sends are due Friday.",
        "answer_by_hours": 6, "default_action": "Send with no address and tell you."})
    assert asked.status_code == 200, asked.text
    ask_id = asked.json()["ask_id"]

    client.post(f"/admin/approvals/{_assist(client, project)}/decide", headers=ADMIN,
                json={"approve": True, "note": f"done: {LINK}"})
    with db_session() as session:
        janitor.answer_asks(session, session.get(Project, project["id"]))
    assert client.get(f"/observe/projects/{project['id']}/asks", headers=ADMIN).json()[0][
        "status"] == "open"

    client.post(f"/admin/projects/{project['id']}/boardroom", headers=ADMIN,
                json={"body": "No address. My lawyer says we do not need one."})
    with db_session() as session:
        janitor.answer_asks(session, session.get(Project, project["id"]))
        from yaaf.core.models import Ask

        assert session.get(Ask, ask_id).status == "answered"


# ── 2. the chat lane reads without consuming ─────────────────────────────────────


def _waiting_for_the_ceo(client, project: dict) -> None:
    """One of everything building a pack used to consume."""
    amend(client, project["id"], reason="more room for the drill", wakes_per_agent_per_hour=7)
    lead = live_token(project["id"], agent_by_name(project, "Lead"))[1]
    client.post("/agent/suggestions", headers=bearer(lead), json={"text": "Try the rival names."})
    client.post("/agent/messages", headers=bearer(lead), json={
        "channel_id": channel_of(client, project["id"], "leadership"),
        "body": "The list is at 527 checked addresses."})
    with db_session() as session:
        session.add(Notice(id=ids.new_id(ids.NOTICE), project_id=project["id"],
                           agent_id=project["ceo_agent_id"], kind=NoticeKind.APPROVAL_DECIDED,
                           body="Approval 'Assist' was APPROVED: here is the link.",
                           created_at=utcnow()))


def test_building_the_chat_lanes_picture_changes_nothing(client):
    """Mechanical, not a list of tables: whatever a future section of the pack
    learns to mark as read, the chat lane must not."""
    project = make_project(client)
    _waiting_for_the_ceo(client, project)
    run_id = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")[0]
    with db_session() as session:
        run, ceo = session.get(Run, run_id), session.get(Agent, project["ceo_agent_id"])
        row = session.get(Project, project["id"])
        # Held, so a write is still pending when we look rather than flushed
        # away by the next query the pack makes.
        with session.no_autoflush:
            pack = build_context_pack(session, run, ceo, row)
            changed = session.new | session.dirty | session.deleted
            assert not changed, f"the chat lane changed {sorted({type(o).__name__ for o in changed})}"
        session.rollback()
    assert "approval_decided" in [n["kind"] for n in pack["notices"]]
    assert pack["suggestions"] and pack["unread"] and pack["constitution_amendments"]


def test_the_work_lane_still_gets_what_the_chat_lane_was_shown(client):
    project = make_project(client)
    _waiting_for_the_ceo(client, project)
    chat = _chat(project)
    for _ in range(2):
        shown = client.get("/agent/context", headers=bearer(chat)).json()
        assert "approval_decided" in [n["kind"] for n in shown["notices"]]

    work = client.get("/agent/context", headers=bearer(
        live_token(project["id"], project["ceo_agent_id"])[1])).json()
    assert "approval_decided" in [n["kind"] for n in work["notices"]]
    assert work["suggestions"] and work["constitution_amendments"]
    assert any(c["channel_kind"] == "leadership" for c in work["unread"])
    with db_session() as session:
        assert session.query(Suggestion).filter(Suggestion.consumed_at.is_(None)).count() == 0


# ── 3. one picture for every lane ────────────────────────────────────────────────


def test_the_chat_lane_sees_everything_a_work_run_does_but_what_is_named(client):
    project = make_project(client)
    _waiting_for_the_ceo(client, project)
    client.post(f"/admin/approvals/{_assist(client, project)}/decide", headers=ADMIN,
                json={"approve": True, "note": f"Here: {LINK}"})
    chat = client.get("/agent/context", headers=bearer(_chat(project))).json()
    work = client.get("/agent/context", headers=bearer(
        live_token(project["id"], project["ceo_agent_id"])[1])).json()

    assert set(work) - set(chat) <= set(CHAT_OMITS), set(work) - set(chat) - set(CHAT_OMITS)
    assert not set(chat) & set(CHAT_OMITS)
    assert all(len(why) > 20 for why in CHAT_OMITS.values()), "every omission says why"
    # The very sections the hand-picked list left out.
    assert LINK in chat["chairman_notes"]["notes"][0]["he_wrote"]
    assert chat["project"]["goal"] and "memory_hint" in chat


def test_the_chat_session_shows_a_section_nobody_has_written_yet(monkeypatch):
    """The old list lived in the chat entrypoint, so the server could add any
    section it liked and the CEO in the boardroom never saw it."""
    from tests.selftest_invariants.test_chat_lane import _chat_entry

    chat_entry = _chat_entry(monkeypatch)
    monkeypatch.setattr(chat_entry.base, "_call", lambda *a, **k: {
        "status": 200, "body": {"situation": {"why": "running"},
                                "a_section_added_next_year": {"he_wrote": LINK}}})
    block = chat_entry._situation_block()
    assert "a_section_added_next_year" in block and LINK in block


def test_every_lane_sees_what_the_ceo_last_said(client):
    """Each run starts cold and unread mail skips its own posts, so one run told
    the outbound lead to switch the postal address on and the next, off."""
    project = make_project(client)
    work = live_token(project["id"], project["ceo_agent_id"])[1]
    client.post("/agent/messages", headers=bearer(work), json={
        "channel_id": channel_of(client, project["id"], "leadership"),
        "body": "Set require_postal_address to false and re-run the self-test."})
    for token in (_chat(project), live_token(project["id"], project["ceo_agent_id"])[1]):
        said = client.get("/agent/context", headers=bearer(token)).json()["you_said_lately"]
        assert [m["room"] for m in said["messages"]] == ["leadership"]
        assert "require_postal_address to false" in said["messages"][0]["body"]


def test_a_fresh_chat_session_does_not_mistake_the_platform_for_itself(monkeypatch):
    """The bootstrap transcript called every sender-less line "You (CEO)", so a
    restarted session read "Answer it in your Inbox" as its own words."""
    from tests.selftest_invariants.test_chat_lane import _chat_entry

    chat_entry = _chat_entry(monkeypatch)
    text = chat_entry._transcript_block([
        {"from_user": None, "from_agent": None, "body": "🤝 ASSIST NEEDED (Theo)"},
        {"from_user": "user_1", "from_agent": None, "body": "Here is the link"},
        {"from_user": None, "from_agent": "agnt_ceo", "body": "Thanks"},
    ])
    assert text.splitlines() == ["Platform: 🤝 ASSIST NEEDED (Theo)",
                                 "Chairman: Here is the link", "You (CEO): Thanks"]

