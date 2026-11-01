"""Invariant selftests: what the CEO says in the boardroom can be read cold.

"Plain English, short sentences" and "three sentences is the size of a
message" were prompt text for months, and the CEO honoured the letter of the
second by gluing every clause it had into three sentences. The message that
made this a refusal is pinned below verbatim: a 112-word sentence, a 70-word
one, 206 words in all. The line these tests hold: that message is refused with
what to fix, a plain rewrite of the same news lands, agents talking to agents
are untouched, and a live conversation's second draft always reaches him.
"""

from __future__ import annotations

import asyncio

from tests.conftest import bearer, channel_of, live_token, make_project
from tests.selftest_invariants.test_chat_lane import _chat_entry
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog
from yaaf.server import plain

#: Verbatim, as the chairman received it.
COLD = (
    "One flag on your screen and I am raising it rather than folding it into tonight's "
    "slot, because it is about the desk sitting empty: at 9:21am your time there was 1 "
    "task on the board and 0 claimable, because I hold Tuesday's MCP-server post until "
    "6:35pm Monday so it does not go into WordPress three days early — that gate is "
    "deliberate, but it left my writer with nothing while the two fixes on that same "
    "post were still undone, so the fixes are now their own claimable job (the Shopify "
    "4,096-versus-16,384 line and a missing link, word-neutral at 1,494 of a 1,500 "
    "cap) and the desk is not empty any more.\n\n"
    "Separate from the flag, and the reason you should open your site: your long piece "
    "is in your WordPress now as draft 2123, \"AI Coding Agents: From Assistants to "
    "Engineering Teams\", created 9:20am your time, 2,797 words, your ten sections in "
    "your order, 21 outside links, the motioneye pull-request walk-through, the "
    "architecture diagram and the \"what still does not work\" section — read it, and "
    "Kit's accuracy read runs alongside mine.\n\n"
    "Nothing needs your decision on either point. If you would rather I move the "
    "Monday gate than keep it, say so and it moves."
)

#: The same news, written for him.
PLAIN = (
    "Your long article is ready to read in WordPress. It is draft 2123, \"AI Coding "
    "Agents: From Assistants to Engineering Teams\", 2,797 words. Kit is checking it "
    "for accuracy too.\n\n"
    "Separately: Tuesday's post is held until Monday evening so it does not publish "
    "early. That left the writer with nothing to do, so I made the post's two small "
    "fixes their own job. Nothing needs your decision."
)


def _post(client, project: dict, body: str, channel: str = "boardroom",
          kind: str = "agent_run", agent: str = "ceo_agent_id", **extra):
    _, token = live_token(project["id"], project[agent], kind=kind)
    return client.post(
        "/agent/messages",
        json={"channel_id": channel_of(client, project["id"], channel), "body": body,
              **extra},
        headers=bearer(token))


# ── the measure ─────────────────────────────────────────────────────────────────


def test_the_message_that_started_this_fails_on_every_count():
    found = plain.problems(COLD)
    assert any("112-word sentence" in f for f in found)
    assert any("70-word sentence" in f for f in found)
    assert any("206 words in all" in f for f in found)


def test_a_plain_rewrite_of_the_same_news_passes():
    assert plain.problems(PLAIN) == []


def test_links_code_and_decimals_do_not_break_or_pad_sentences():
    text = ("Spend is $1.50 of $100 (see [the report](https://example.com/a/b.c.d)). "
            "Run `pytest -q -n auto` e.g. tonight.")
    assert plain.problems(text) == []
    assert plain.words("Spend is $1.50 today.") == 4


def test_list_lines_are_their_own_sentences():
    """The decision shape is a short list; each line is read on its own."""
    options = "\n".join(f"- Option {i}: keep the post where it is for now" for i in range(6))
    assert plain.problems("Which do you want?\n" + options) == []


# ── the refusal ─────────────────────────────────────────────────────────────────


def test_a_cold_boardroom_message_is_refused_with_what_to_fix(client):
    project = make_project(client, slug="plain-cold")
    response = _post(client, project, COLD)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error.startswith("Not sent — the chairman would have to read this twice.")
    assert "112-word sentence" in error and "One flag on your screen" in error


def test_the_plain_rewrite_lands(client):
    project = make_project(client, slug="plain-ok")
    assert _post(client, project, PLAIN).status_code == 200


def test_agent_to_agent_rooms_are_not_measured(client):
    """The chairman said the abstraction between agents is not what he minds."""
    project = make_project(client, slug="plain-leadership")
    assert _post(client, project, COLD, channel="leadership").status_code == 200


def test_a_work_run_cannot_claim_a_redraft(client):
    project = make_project(client, slug="plain-redraft-run")
    assert _post(client, project, COLD, redrafted=True).status_code == 422


def test_the_chat_lanes_second_draft_lands_and_is_recorded(client):
    project = make_project(client, slug="plain-redraft-chat")
    assert _post(client, project, COLD, kind="chat_session", redrafted=True).status_code == 200
    with db_session() as session:
        assert session.query(AuditLog).filter(
            AuditLog.project_id == project["id"],
            AuditLog.action == "boardroom.plain_override").count() == 1


# ── the chat lane rewrites once, then delivers ──────────────────────────────────


def test_the_runner_knows_the_refusal_when_it_sees_one(monkeypatch):
    """The chat harness cannot import the server, so it matches on the opening."""
    chat_entry = _chat_entry(monkeypatch)
    assert plain.refusal_text(["x"]).startswith(chat_entry.PLAIN_REFUSAL_PREFIX)


def test_a_refused_chat_reply_is_rewritten_once_then_sent(monkeypatch):
    chat_entry = _chat_entry(monkeypatch)
    posted: list[tuple[str, bool]] = []
    prompts: list[str] = []

    def post(channel_id, text, answers=None, *, redrafted=False):
        posted.append((text, redrafted))
        if not redrafted and plain.problems(text):
            return {"status": 422, "body": {"error": plain.refusal_text(plain.problems(text))}}
        return {"status": 200, "body": {}}

    class Conversation:
        async def turn(self, channel_id, prompt):
            prompts.append(prompt)
            return PLAIN

    monkeypatch.setattr(chat_entry, "_post_reply", post)
    monkeypatch.setattr(chat_entry, "_set_typing", lambda *a: None)
    delivered = asyncio.run(chat_entry._deliver(Conversation(), "chan", COLD, ["m1"]))
    assert delivered is True
    assert posted == [(COLD, False), (PLAIN, True)]
    assert len(prompts) == 1 and "112-word sentence" in prompts[0]


# ── the run-on cannot move one tool over ────────────────────────────────────────


def test_a_question_to_him_is_measured_too(client):
    project = make_project(client, slug="plain-ask")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    ask = {"title": "Move the Monday hold?", "body": COLD.split("\n\n")[0],
           "why": "The writer has nothing to do until it lifts.", "answer_by_hours": 24,
           "default_action": "I keep the hold and the post goes out Tuesday."}
    response = client.post("/agent/asks", json=ask, headers=bearer(token))
    assert response.status_code == 422
    assert "In `body`: A 112-word sentence" in response.json()["error"]
    ask["body"] = "Should I publish Tuesday's post on Monday instead?"
    assert client.post("/agent/asks", json=ask, headers=bearer(token)).status_code == 200


# ── the review he takes ─────────────────────────────────────────────────────────

#: Verbatim from review #1's "What your CEO says it will do": 119 words, under
#: the total, but with a 40-word and a 39-word sentence.
REVIEW_RECOMMENDED = (
    "Cut no scope: every open item is a channel test, and we have no proof from any "
    "channel yet. Move the finish-line promise from today to the platform's own "
    "projection of 2 November, and treat 7 November — the first day the cold send may "
    "legally and sensibly go out — as the date that actually matters for a first "
    "customer. If the Google Ads account turns out to be blocked this morning, I would "
    "leave its remaining budget unspent rather than chase another workaround, and put "
    "the effort into the 7 November send and the 9 November newsletter slot. I would "
    "also stop adding seats: the work left is execution, and eight of our twelve seats "
    "are stood down already."
)

REVIEW_PLAIN = (
    "Keep all the work. None of our channels has shown results yet, so there is nothing "
    "to cut. Move the finish date from today to 2 November. The date that matters is 7 "
    "November, the first day the cold emails can go out. If Google Ads is blocked this "
    "morning, I will leave its budget unspent. I will hire no one else."
)


def test_a_review_recommendation_is_held_to_a_boardroom_message(client):
    from tests.selftest_invariants.test_reviews import ceo_token, deliver_project, open_review

    project = deliver_project(client, slug="plain-review")
    open_review(project)
    token = ceo_token(project)
    narrative = "Pace fell because the ad account was blocked for three days. Nothing else moved."
    refused = client.post("/agent/review_narrative", headers=bearer(token), json={
        "narrative": narrative, "recommended": REVIEW_RECOMMENDED})
    assert refused.status_code == 422
    assert "In `recommended`: A 40-word sentence" in refused.json()["error"]

    long_why = client.post("/agent/review_narrative", headers=bearer(token), json={
        "narrative": COLD.split("\n\n")[0], "recommended": REVIEW_PLAIN})
    assert long_why.status_code == 422
    assert "In `narrative`: A 112-word sentence" in long_why.json()["error"]

    filed = client.post("/agent/review_narrative", headers=bearer(token), json={
        "narrative": narrative, "recommended": REVIEW_PLAIN})
    assert filed.status_code == 200, filed.text
