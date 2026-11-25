"""Invariant selftests: when the chairman asks, the CEO talks it through.

25 November 2026, the dplouffe.ca blog. He asked "what is the best way of
collaborating with you on this?" and "how can we pick more current, more
popular projects?" — and got three rewritten briefs in thirty minutes, one
turning a passing remark into a rule for every post. A question became a
brief because no rule said otherwise. These hold the line: a draft waits while
his newest message asks something, one draft per thing he said, and a reply to
his question gets a conversation's room.
"""

from __future__ import annotations

from tests.conftest import ADMIN, make_project
from tests.selftest_invariants.test_brief import propose
from tests.selftest_invariants.test_plain_to_chairman import _post
from yaaf.runner.talk_prompt import BOARDROOM_ADDENDUM, CHAT_ADDENDUM
from yaaf.server import discussion

#: Verbatim, as he wrote them.
COLLABORATE = ("I think we need to change the approach of how we write blog post, what "
               "is the best way of collaborating with you on this?")
REPHRASE = (
    "Maybe let's me rephrase.  How can we change the posts on a daily basis to be more "
    "interesting, less boring, less like an AI wrote it.\n\nYou're picking many abstract "
    "github projects which are getting very little page views.   How can we pick more "
    "current, more popular projects.\n\nSo 2 things... better story and narrative.  get "
    "better at picking projects.  If we need more tokens, I will add some."
)
DECIDED = "Go with the second direction: pick on popularity, and vary how posts open."


def _chairman_says(client, project: dict, body: str) -> None:
    response = client.post(f"/admin/projects/{project['id']}/boardroom",
                           json={"body": body}, headers=ADMIN)
    assert response.status_code in (200, 201), response.text


# ── what counts as a question ───────────────────────────────────────────────────


def test_a_question_mark_anywhere_in_his_words_is_a_question():
    assert discussion.asks(COLLABORATE)
    assert discussion.asks("How can we pick better? If we need tokens I will add some.")
    assert not discussion.asks(DECIDED)


def test_a_sentence_that_opens_like_a_question_is_one_without_the_mark():
    """The message that became a rule for every post had no question mark."""
    assert discussion.asks(REPHRASE)
    assert discussion.asks("So, what do you think about weekly posts.")
    assert discussion.asks("Any ideas on how to get more readers")


def test_an_order_is_not_mistaken_for_a_question():
    for order in ("Do the vLLM post tomorrow.", "Pick on popularity from now on.",
                  "What I want is posts people actually read.",
                  "Stop leading with Hacker News.", DECIDED):
        assert not discussion.asks(order), order


def test_a_polite_request_or_a_question_with_an_order_in_it_is_an_order():
    """Both verbatim from the boardroom. Holding the brief on either would make
    him the bottleneck for a change he already made."""
    assert not discussion.asks("can we remove it from the brief or order?")
    assert not discussion.asks(
        "Why do we post this in the change log.. this is not part of the change log, "
        "don't do this anymore.")
    assert not discussion.asks("Can you write a blog post about LongCat-2.5?")


def test_the_decision_title_he_is_replying_to_is_not_his_question():
    """A note arrives as `Re: “<the CEO's title>” — <his words>`; the title is
    the CEO's ("… cover three, or run over?")."""
    note = ("Re: “November has 22 million tokens left — cover three, or run over?” — "
            "Run about 20 million past the plan and cover all seven days.")
    assert not discussion.asks(note)
    assert discussion.asks("Re: “Paused the project” — why did it stop?")


def test_a_link_with_a_query_string_is_not_a_question():
    assert not discussion.asks("Use this one: https://example.com/search?q=mcp")


# ── the brief waits for his decision ───────────────────────────────────────────


def test_a_brief_is_not_filed_while_his_newest_message_asks_something(client):
    project = make_project(client, slug="talk-question")
    _chairman_says(client, project, COLLABORATE)
    refused = propose(client, project)
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error.startswith("Not filed. His last message asks you something")
    assert "what is the best way of collaborating" not in error  # only the opening
    assert "I think we need to change the approach" in error


def test_the_message_that_produced_the_template_rule_is_a_question(client):
    """It ends on a statement; it still asks twice."""
    project = make_project(client, slug="talk-rephrase")
    _chairman_says(client, project, REPHRASE)
    assert propose(client, project).status_code == 409


def test_once_he_says_which_way_the_brief_is_filed(client):
    project = make_project(client, slug="talk-decided")
    _chairman_says(client, project, COLLABORATE)
    assert propose(client, project).status_code == 409
    _chairman_says(client, project, DECIDED)
    filed = propose(client, project)
    assert filed.status_code == 200, filed.text


def test_one_draft_per_thing_he_said(client):
    project = make_project(client, slug="talk-one-draft")
    _chairman_says(client, project, DECIDED)
    assert propose(client, project).status_code == 200
    again = propose(client, project, standing_orders=["Max 10% of the book in one name"])
    assert again.status_code == 409
    assert "your mind changing, not his" in again.json()["error"]

    _chairman_says(client, project, "Make it 10% in one name, not 15%.")
    assert propose(client, project,
                   standing_orders=["Max 10% of the book in one name"]).status_code == 200


def test_before_he_has_said_anything_the_founding_draft_still_files(client):
    project = make_project(client, slug="talk-founding")
    assert propose(client, project).status_code == 200


# ── a reply to his question has room to think ──────────────────────────────────


def _words(n: int) -> str:
    sentence = "The second post was read four times as often as the first one."
    per = len(sentence.split())
    return " ".join([sentence] * (n // per + 1))


def test_answering_his_question_may_run_past_a_status(client):
    project = make_project(client, slug="talk-room")
    _chairman_says(client, project, COLLABORATE)
    assert _post(client, project, _words(250), kind="chat_session").status_code == 200
    assert _post(client, project, _words(320), kind="chat_session").status_code == 422


def test_without_a_question_a_reply_is_a_status_and_stays_short(client):
    project = make_project(client, slug="talk-status")
    _chairman_says(client, project, DECIDED)
    assert _post(client, project, _words(250), kind="chat_session").status_code == 422


def test_a_work_run_never_gets_the_conversations_room(client):
    project = make_project(client, slug="talk-workrun")
    _chairman_says(client, project, COLLABORATE)
    assert _post(client, project, _words(250)).status_code == 422


# ── both vehicles of the talk lane carry the rule ──────────────────────────────


def test_both_talk_vehicles_are_told_to_think_it_through():
    for text in (CHAT_ADDENDUM, BOARDROOM_ADDENDUM):
        assert "WHEN HE ASKS YOU SOMETHING" in text
        assert "A question is not a decision" in text
        assert str(discussion.DISCUSSION_MAX_WORDS) in text
