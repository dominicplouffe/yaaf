"""Invariant selftests: the chairman's reporting cadence.

A CEO run is stateless, and the context pack filters out the agent's own
messages — so "report every N hours" used to live only in the model's memory,
and a rule an agent has to remember is a suggestion. On 16 Aug 2026 that cost
thirteen unprompted statuses in 2h20m at Opus prices, three of them after the
chairman had said stop.

The line these tests hold: an unprompted status between slots is refused, a
reply to something he actually said never is, break-glass must name a reason
the platform can check, and the CEO learns its next slot *before* it spends a
run writing a status nobody will receive.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from tests.conftest import (
    ADMIN,
    agent_by_name,
    bearer,
    channel_of,
    live_token,
    make_project,
)
from yaaf.core import ids
from yaaf.core.constitution import ConstitutionDoc, parse_report_window
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Message, Project, utcnow
from yaaf.server import contextpack, policy

CADENCE = {"chairman_report_interval_hours": 4,
           "chairman_report_window_local": "09:00-20:00",
           "timezone": "America/New_York"}


def _with_cadence(client, constitution: dict | None = None) -> dict:
    return make_project(client, slug="cadence",
                        constitution=CADENCE if constitution is None else constitution)


def _post(client, project: dict, body: str, kind: str = "agent_run", **extra):
    _, token = live_token(project["id"], project["ceo_agent_id"], kind=kind)
    return client.post(
        "/agent/messages",
        json={"channel_id": channel_of(client, project["id"], "boardroom"),
              "body": body, **extra},
        headers=bearer(token),
    )


def _chairman_says(client, project: dict, body: str = "a word from the chairman",
                   kind: str = "boardroom", age_hours: float = 0.0) -> str:
    channel_id = channel_of(client, project["id"], kind)
    message_id = ids.new_id(ids.MESSAGE)
    with db_session() as session:
        session.add(Message(
            id=message_id, project_id=project["id"], channel_id=channel_id,
            sender_user_id=session.get(Project, project["id"]).chairman_user_id, body=body,
            created_at=utcnow() - timedelta(hours=age_hours)))
    return message_id


def _file_directive(client, project: dict, message_id: str, changes: dict,
                    reason: str = "The chairman set a reporting cadence in the boardroom."):
    _, token = live_token(project["id"], project["ceo_agent_id"])
    return client.post("/agent/directives", headers=bearer(token),
                       json={"from_message_id": message_id, "changes": changes,
                             "reason": reason})


def _backdate_ceo_posts(project: dict, hours: float) -> None:
    with db_session() as session:
        for message in session.query(Message).filter(
                Message.project_id == project["id"],
                Message.sender_agent_id == project["ceo_agent_id"]):
            message.created_at = utcnow() - timedelta(hours=hours)


# ── the refusal itself ──────────────────────────────────────────────────────────


def test_first_status_lands_then_the_next_one_is_refused(client):
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1** — plan set.").status_code == 200
    second = _post(client, project, "**Status 2** — still going.")
    assert second.status_code == 429, second.text
    assert "next slot" in second.text.lower()


def test_reply_to_the_chairman_is_always_allowed(client):
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    # He speaks: the turn is his, and answering it is never a status.
    _chairman_says(client, project)
    assert _post(client, project, "Understood.").status_code == 200
    # ...but exactly once. The reply is now the newest message, so anything
    # further is unprompted again.
    assert _post(client, project, "**Status 2**").status_code == 429


def test_the_slot_opens_once_the_interval_has_passed(client):
    # No window: this is the INTERVAL's test, and the shared CADENCE's
    # 09:00-20:00 makes it a test of what time it is where the suite runs. With
    # the window on, a slot backdated past the interval still lands outside the
    # chairman's waking hours after 20:00 New York, _into_window correctly
    # pushes it to 09:00 tomorrow, and this fails every evening. The window has
    # its own tests below, against explicit datetimes rather than the clock.
    project = _with_cadence(client, {**CADENCE, "chairman_report_window_local": None})
    assert _post(client, project, "**Status 1**").status_code == 200
    assert _post(client, project, "**Status 2**").status_code == 429
    _backdate_ceo_posts(project, hours=5)
    assert _post(client, project, "**Status 2**").status_code == 200


def test_no_cadence_configured_leaves_the_boardroom_wide_open(client):
    """The rule is opt-in: a project whose chairman never asked for one must
    behave exactly as it did before this existed."""
    project = _with_cadence(client, constitution={})
    for _ in range(3):
        assert _post(client, project, "chatter").status_code == 200


def test_chat_lane_is_exempt(client):
    """The chat session is the conversation, not the report — it speaks only
    when the chairman does, and gagging it would break the boardroom."""
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    assert _post(client, project, "still talking", kind="chat_session").status_code == 200


def test_a_slot_that_lands_out_of_hours_waits_for_the_morning(client):
    """"Nothing overnight — batch it into the 09:00 post" in wall-clock terms.
    The interval alone is not enough: 18:00 + 4h is 22:00, and the chairman is
    asleep."""
    et = ZoneInfo("America/New_York")
    cadence = policy.ReportCadence(4.0, parse_report_window("09:00-20:00"), et)

    def slot_after(hour: int, minute: int = 0) -> datetime:
        return policy.next_report_slot(
            cadence, datetime(2026, 8, 16, hour, minute, tzinfo=et)).astimezone(et)

    assert slot_after(9, 30) == datetime(2026, 8, 16, 13, 30, tzinfo=et)   # inside hours
    assert slot_after(18) == datetime(2026, 8, 17, 9, 0, tzinfo=et)        # +4h = 22:00
    assert slot_after(19, 59) == datetime(2026, 8, 17, 9, 0, tzinfo=et)    # +4h = 23:59
    assert slot_after(2) == datetime(2026, 8, 16, 9, 0, tzinfo=et)         # +4h = 06:00
    # A malformed window is refused at the door rather than silently gagging
    # (or ungagging) the CEO — the constitution validates it on the way in.
    assert parse_report_window("20:00-09:00") is None
    assert parse_report_window("nonsense") is None
    for bad in ({"chairman_report_window_local": "20:00-09:00"},
                {"chairman_report_window_local": "nonsense"},
                {"timezone": "Mars/Olympus"}):
        with pytest.raises(ValidationError):
            ConstitutionDoc.model_validate(bad)


# ── break-glass ─────────────────────────────────────────────────────────────────


def test_override_must_name_a_reason_the_platform_knows(client):
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    refused = _post(client, project, "urgent!", override="because_i_said_so")
    assert refused.status_code == 422
    assert "break-glass" in refused.text


def test_approval_override_requires_an_actual_pending_approval(client):
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    refused = _post(client, project, "please approve", override="approval")
    assert refused.status_code == 422
    assert "no pending approval" in refused.text


def test_blocker_override_passes_and_is_recorded(client):
    """The platform cannot check a blocker claim, so it does the next best
    thing: lets it through and writes it down where the chairman can see a CEO
    that cries blocker four times a day."""
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    assert _post(client, project, "hard blocker", override="blocker").status_code == 200
    with db_session() as session:
        recorded = session.query(AuditLog).filter_by(
            project_id=project["id"], action="boardroom.cadence_override").all()
        assert [row.detail["reason"] for row in recorded] == ["blocker"]


# ── telling the CEO before it spends the run ────────────────────────────────────


def test_context_pack_tells_the_ceo_when_it_may_next_report(client):
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    _, token = live_token(project["id"], project["ceo_agent_id"])
    pack = client.get("/agent/context", headers=bearer(token)).json()
    chairman = pack["chairman"]
    assert chairman["may_post_status_now"] is False
    assert chairman["your_last_report_at"] is not None
    assert chairman["next_slot_at"] is not None
    assert chairman["cadence_hours"] == 4
    assert "REFUSED" in chairman["note"]


def test_context_pack_flags_an_unanswered_chairman_message(client):
    """The 11:52 failure mode: two runs both saw an open turn, the first spent
    it on a queued status, and the actual acknowledgement was refused. The pack
    has to say plainly that he is waiting."""
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    _chairman_says(client, project, "why did I get a status?")
    _, token = live_token(project["id"], project["ceo_agent_id"])
    chairman = client.get("/agent/context", headers=bearer(token)).json()["chairman"]
    assert chairman["unanswered_chairman_message"] is True
    assert chairman["may_post_status_now"] is True


def _system_prompt(project: dict) -> str:
    with db_session() as session:
        from yaaf.core.models import Agent

        return contextpack.build_system_prompt(
            session, session.get(Agent, project["ceo_agent_id"]),
            session.get(Project, project["id"]))


def test_cadence_is_stated_in_the_system_prompt_only_when_set(client):
    text = _system_prompt(_with_cadence(client))
    assert "at most every 4h" in text
    assert "09:00 and 20:00 America/New_York" in text
    # ...and the asking line grants the verb without granting frequency:
    # allowed always, required never, recorded when it breaks the cadence.
    assert "allowed, never required" in text


def test_a_project_with_no_cadence_says_nothing_about_one(client):
    assert "at most every" not in _system_prompt(_with_cadence(client, constitution={}))


# ── amending it ─────────────────────────────────────────────────────────────────


def test_changing_the_cadence_skips_the_cooling_off_and_is_disclosed(client):
    """A chairman who says "stop, this is costing me money" means the next run.
    Three days of the old cadence is the hazard he is trying to end — the same
    argument the budget fields already won."""
    project = _with_cadence(client, constitution={})
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "Chairman asked for four-hourly reporting inside his working day.",
              "changes": CADENCE},
        headers=ADMIN)
    assert response.status_code == 200, response.text
    assert response.json()["amendments"][0]["applied_now"] is True
    # In force immediately: a second status is now refused...
    assert _post(client, project, "**Status 1**").status_code == 200
    assert _post(client, project, "**Status 2**").status_code == 429
    # ...and the CEO is *told*, which is the half that was missing on 16 Aug:
    # the rule changed and no run ever learned it had.
    _, token = live_token(project["id"], project["ceo_agent_id"])
    pack = client.get("/agent/context", headers=bearer(token)).json()
    disclosed = pack["constitution_amendments"][0]["changes"]
    assert disclosed["chairman_report_interval_hours"] == 4


def test_a_cadence_that_does_not_parse_is_refused_at_the_door(client):
    project = _with_cadence(client, constitution={})
    response = client.post(
        f"/admin/projects/{project['id']}/amendments",
        json={"reason": "Trying to set a nonsense window.",
              "changes": {"chairman_report_window_local": "whenever"}},
        headers=ADMIN)
    assert response.status_code == 422
    assert "HH:MM-HH:MM" in response.text


# ── directives: what he says becomes what the platform enforces ─────────────────


def test_a_directive_turns_his_words_into_an_enforced_rule(client):
    """The 12:40 failure: he wrote "change your updates to every 4 hours" and
    nothing structural happened. Now it does."""
    project = _with_cadence(client, constitution={})
    for _ in range(3):  # no cadence yet: the old, expensive behaviour
        assert _post(client, project, "chatter").status_code == 200
    said = _chairman_says(client, project,
                          "change your updates to every 4 hours between 9am to 8pm Eastern")
    filed = _file_directive(client, project, said, CADENCE)
    assert filed.status_code == 200, filed.text
    assert filed.json()["in_force"] is True
    # Enforced from the very next post, with no cooling-off and no run needing
    # to have remembered anything.
    assert _post(client, project, "**Status**").status_code == 429


def test_a_directive_is_said_back_to_him_in_his_own_channel(client):
    """The platform can verify that he spoke, never what he meant — so the
    safeguard against a misread directive is that he watches it land."""
    project = _with_cadence(client, constitution={})
    said = _chairman_says(client, project, "every 4 hours please")
    assert _file_directive(client, project, said, CADENCE).status_code == 200
    with db_session() as session:
        confirmation = session.query(Message).filter(
            Message.project_id == project["id"],
            Message.sender_agent_id == project["ceo_agent_id"]).one()
        assert "Recorded as policy" in confirmation.body
        assert "interval_hours = 4" in confirmation.body
        assert confirmation.thread_id == said  # threaded to the words it came from


def test_a_directive_may_not_touch_anything_but_his_own_cadence(client):
    """An agent filing its own constitution is how a project votes itself a
    bigger budget. The only rules the CEO may file are the ones that limit what
    it asks of the chairman."""
    project = _with_cadence(client, constitution={})
    said = _chairman_says(client, project, "spend whatever you need")
    for grab in ({"monthly_usd_cap": 10_000.0},
                 {"queue_ceiling": 99},
                 {"chairman_report_interval_hours": 4, "approval_threshold_usd": 0.0}):
        refused = _file_directive(client, project, said, grab)
        assert refused.status_code == 403, refused.text
        assert "may not set" in refused.text


def test_a_directive_must_cite_the_chairman_himself_in_the_boardroom(client):
    project = _with_cadence(client, constitution={})
    # An agent's own message is not a directive, however confident it sounds.
    _post(client, project, "I have decided to report less often")
    with db_session() as session:
        mine = session.query(Message).filter(
            Message.sender_agent_id == project["ceo_agent_id"]).one().id
    assert _file_directive(client, project, mine, CADENCE).status_code == 422
    # Nor is something relayed through leadership.
    relayed = _chairman_says(client, project, "he told me four hours", kind="leadership")
    assert _file_directive(client, project, relayed, CADENCE).status_code == 422
    # Nor a message that does not exist.
    assert _file_directive(client, project, "msg_nope", CADENCE).status_code == 404


def test_a_stale_message_cannot_undo_a_fresher_instruction(client):
    """Re-citing yesterday's "hourly is fine" to escape today's "every four
    hours" is the obvious way to game this."""
    project = _with_cadence(client, constitution={})
    old = _chairman_says(client, project, "hourly is fine", age_hours=2)
    new = _chairman_says(client, project, "no — every 4 hours", age_hours=0)
    assert _file_directive(client, project, new, CADENCE).status_code == 200
    back = _file_directive(client, project, old, {"chairman_report_interval_hours": 1})
    assert back.status_code == 422
    assert "predates the rule currently in force" in back.text


def test_a_directive_from_history_is_refused(client):
    project = _with_cadence(client, constitution={})
    ancient = _chairman_says(client, project, "every 4 hours", age_hours=72)
    refused = _file_directive(client, project, ancient, CADENCE)
    assert refused.status_code == 422
    assert "his to restate" in refused.text


def test_only_the_ceo_files_directives(client):
    project = _with_cadence(client, constitution={})
    said = _chairman_says(client, project, "every 4 hours")
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    refused = client.post("/agent/directives", headers=bearer(token),
                          json={"from_message_id": said, "changes": CADENCE,
                                "reason": "I heard this second-hand and acted on it."})
    assert refused.status_code == 403


# ── the pack and the gate are one fact ──────────────────────────────────────────


def _may_post(client, project: dict) -> bool:
    _, token = live_token(project["id"], project["ceo_agent_id"], kind="chat_session")
    return client.get("/agent/context",
                      headers=bearer(token)).json()["chairman"]["may_post_status_now"]


def test_may_post_status_now_never_disagrees_with_the_endpoint(client):
    """`may_post_status_now` is a promise about what POST /agent/messages will
    do, and for the flag override it was a promise nothing kept: the permission
    was computed in the context pack and enforced nowhere, so a CEO that read
    "the cadence does not hold you here", believed it, and posted got a 429.
    A permission asserted in a prompt and denied at the gate is worse than no
    permission at all, so the two now share one predicate — and this walks the
    states that used to diverge.
    """
    project = _with_cadence(client, constitution={"chairman_report_interval_hours": 4,
                                                  "timezone": "UTC"})

    def agree(where: str) -> None:
        promised = _may_post(client, project)
        landed = _post(client, project, f"status at {where}").status_code == 200
        assert promised == landed, (
            f"{where}: the pack said may_post_status_now={promised} and the endpoint "
            f"{'allowed' if landed else 'refused'} the post")

    agree("the first post ever")          # nothing reported yet — always lands
    agree("inside a closed slot")         # spent it a moment ago — refused
    _chairman_says(client, project)
    agree("an open turn from him")        # his message opens exactly one reply
    agree("that turn already spent")      # and the next post is a status again

    # An ask that ran out its window: owed, so one post goes through — and the
    # post after it faces the cadence again.
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": "Which candidate?", "body": "Five ranked; my pick is first.",
        "answer_by_hours": 6,
        "default_action": "Take the top-ranked one and tell you I did."})
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        from yaaf.core.models import Ask, AskStatus

        ask = session.query(Ask).filter(Ask.project_id == project["id"]).one()
        ask.status, ask.lapsed_at = AskStatus.LAPSED, utcnow()

    agree("an ask that lapsed")
    agree("the same ask, already spoken for")
