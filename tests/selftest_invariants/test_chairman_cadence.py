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

from tests.conftest import ADMIN, bearer, channel_of, live_token, make_project
from yaaf.core import ids
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Message, Project, utcnow
from yaaf.server import contextpack, policy

CADENCE = {"chairman_report_interval_hours": 4,
           "chairman_report_window_local": "09:00-20:00",
           "timezone": "America/New_York"}


def _with_cadence(client, config: dict | None = None) -> dict:
    project = make_project(client, slug="cadence")
    client.patch(f"/admin/projects/{project['id']}",
                 json={"config": CADENCE if config is None else config}, headers=ADMIN)
    return project


def _post(client, project: dict, body: str, kind: str = "agent_run", **extra):
    _, token = live_token(project["id"], project["ceo_agent_id"], kind=kind)
    return client.post(
        "/agent/messages",
        json={"channel_id": channel_of(client, project["id"], "boardroom"),
              "body": body, **extra},
        headers=bearer(token),
    )


def _chairman_says(client, project: dict, body: str = "a word from the chairman") -> None:
    channel_id = channel_of(client, project["id"], "boardroom")
    with db_session() as session:
        session.add(Message(
            id=ids.new_id(ids.MESSAGE), project_id=project["id"], channel_id=channel_id,
            sender_user_id=session.get(Project, project["id"]).chairman_user_id, body=body))


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
    project = _with_cadence(client)
    assert _post(client, project, "**Status 1**").status_code == 200
    assert _post(client, project, "**Status 2**").status_code == 429
    _backdate_ceo_posts(project, hours=5)
    assert _post(client, project, "**Status 2**").status_code == 200


def test_no_cadence_configured_leaves_the_boardroom_wide_open(client):
    """The rule is opt-in: a project whose chairman never asked for one must
    behave exactly as it did before this existed."""
    project = _with_cadence(client, config={})
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
    cadence = policy.ReportCadence(4.0, policy._parse_window("09:00-20:00"), et)

    def slot_after(hour: int, minute: int = 0) -> datetime:
        return policy.next_report_slot(
            cadence, datetime(2026, 8, 16, hour, minute, tzinfo=et)).astimezone(et)

    assert slot_after(9, 30) == datetime(2026, 8, 16, 13, 30, tzinfo=et)   # inside hours
    assert slot_after(18) == datetime(2026, 8, 17, 9, 0, tzinfo=et)        # +4h = 22:00
    assert slot_after(19, 59) == datetime(2026, 8, 17, 9, 0, tzinfo=et)    # +4h = 23:59
    assert slot_after(2) == datetime(2026, 8, 16, 9, 0, tzinfo=et)         # +4h = 06:00
    # A malformed window must not silently gag the CEO — it reads as "no window".
    assert policy._parse_window("20:00-09:00") is None
    assert policy._parse_window("nonsense") is None


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


def test_cadence_appears_in_the_system_prompt_only_when_set(client):
    project = _with_cadence(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    prompt = client.get("/agent/context", headers=bearer(token)).json()
    assert prompt  # pack builds
    with db_session() as session:
        from yaaf.core.models import Agent, Project

        agent = session.get(Agent, project["ceo_agent_id"])
        row = session.get(Project, project["id"])
        text = contextpack.build_system_prompt(session, agent, row)
        assert "at most every 4h" in text
        assert "never about frequency" in text
        row.config = {}
        session.flush()
        assert "at most every" not in contextpack.build_system_prompt(session, agent, row)
