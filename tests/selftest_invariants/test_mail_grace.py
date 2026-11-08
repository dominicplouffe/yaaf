"""Invariant selftests: the chairman is emailed when his CEO is waiting on him
and he has not answered — and, by default, for nothing else.

He asked for exactly that: "email me when the CEO requires something from me
that I have not answered in the first 60 minutes." Before, every decision,
question and need was mailed the second it was filed, so a chairman sitting in
the app answering it got the email anyway, and every alarm and platform
reminder came with it. Two rules now decide the inbox, both his:

* `mail_when` — which classes may become email at all. Default: only his CEO
  waiting on him.
* `mail_grace_minutes` — how long a CEO's request waits before it becomes mail.
  Default 60. Answered or snoozed in the app before then, it never does.

The rules under attack:

1. A CEO's request is not mailed when it is filed; it is mailed after the grace
   if — and only if — it is still open.
2. Answering it, or snoozing it, inside the grace means no email, ever, and the
   record says why.
3. By default an alarm and a platform reminder do not become email; the
   webhook still carries them, because muting the human must not mute the API.
4. Each answer to `mail_when` lets in exactly what it says.
5. A chase inside the grace does not turn one decision into two emails.
6. Every ASK names the row it waits on, so the grace can tell "still open".
"""

from __future__ import annotations

import ast
import pathlib
from datetime import timedelta

import pytest

from tests.conftest import ADMIN, amend, bearer, live_token, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Approval, AuditLog, Project, utcnow
from yaaf.server import janitor, notify
from yaaf.server.notify import Attention, chairman_notify


class Wire:
    def __init__(self) -> None:
        self.emails: list[tuple] = []
        #: Kept apart: the janitor ticks below send the daily letter on their
        #: own schedule, and it is not what these tests are counting.
        self.briefs: list[tuple] = []
        self.hooks: list[tuple] = []

    def mail(self, *args) -> None:
        (self.briefs if "] briefing — " in args[1] else self.emails).append(args)


class _Inline:
    def __init__(self, target=None, daemon=None):  # noqa: ARG002
        self._target = target

    def start(self) -> None:
        self._target()


@pytest.fixture
def wire(monkeypatch) -> Wire:
    import threading

    caught = Wire()
    monkeypatch.setattr(threading, "Thread", _Inline)
    monkeypatch.setattr(notify, "settings", lambda: type("Cfg", (), {
        "smtp_host": "smtp.example.invalid", "smtp_port": 587, "smtp_from": "y@example",
        "smtp_username": "", "smtp_password": "",
        "chairman_webhook_url": "https://hooks.example.invalid/x",
        "public_base_url": "https://yaaf.example.com"})())
    monkeypatch.setattr(notify, "_send_email", lambda *a, **k: caught.mail(*a))
    monkeypatch.setattr(notify, "_send_webhook", lambda *a, **k: caught.hooks.append(a))
    return caught


def _file(client, project: dict, title: str = "Buy the $199 projections feed") -> str:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/approvals", headers=bearer(token), json={
        "kind": "spend", "title": title,
        "body": "The free feed lags injury news by a day.",
        "recommendation": "Approve it; the case says why.",
        "if_declined": "We keep the free feed.", "amount_usd": 199})
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _age(approval_id: str, minutes: float) -> None:
    """The suite does not mock a clock; it writes the column backwards."""
    with db_session() as session:
        row = session.get(Approval, approval_id)
        row.created_at = utcnow() - timedelta(minutes=minutes)


def _tick() -> None:
    with db_session() as session:
        janitor.janitor_once(session)


def _outcomes(project_id: str) -> list[str]:
    with db_session() as session:
        return [r.detail["outcome"] for r in session.query(AuditLog).filter(
            AuditLog.project_id == project_id,
            AuditLog.action == notify.MAIL_GRACE_SETTLED)]


def _notify(project_id: str, subject: str, attention: Attention) -> None:
    with db_session() as session:
        chairman_notify(session, session.get(Project, project_id), subject, "body",
                        attention=attention)


# ── 1. held for the grace, mailed only if still open ───────────────────────────


def test_a_new_decision_is_not_mailed_the_moment_it_is_filed(client, wire):
    """He may be in the app right now. The webhook still hears it at once."""
    project = make_project(client)
    _file(client, project)
    assert wire.emails == []
    assert len(wire.hooks) == 1


def test_still_open_after_the_grace_it_is_mailed_once(client, wire):
    project = make_project(client)
    approval_id = _file(client, project)
    _tick()
    assert wire.emails == [], "mailed before the grace ran out"
    _age(approval_id, 61)
    _tick()
    _tick()
    assert [e[1] for e in wire.emails] == [
        "[yaaf/acme] waiting on your decision: Buy the $199 projections feed"]
    assert _outcomes(project["id"]) == ["sent"]


def test_the_grace_is_the_chairmans_number(client, wire):
    project = make_project(client, constitution={"mail_grace_minutes": 180})
    approval_id = _file(client, project)
    _age(approval_id, 90)
    _tick()
    assert wire.emails == []
    _age(approval_id, 181)
    _tick()
    assert len(wire.emails) == 1


def test_a_grace_of_zero_mails_at_once(client, wire):
    project = make_project(client, constitution={"mail_grace_minutes": 0})
    _file(client, project)
    assert len(wire.emails) == 1


# ── 2. answered or snoozed in the app: never mailed ────────────────────────────


def test_a_decision_he_made_inside_the_grace_is_never_mailed(client, wire):
    """The whole point of the grace."""
    project = make_project(client)
    approval_id = _file(client, project)
    decided = client.post(f"/admin/approvals/{approval_id}/decide",
                          json={"approve": True, "note": ""}, headers=ADMIN)
    assert decided.status_code == 200, decided.text
    _age(approval_id, 120)
    _tick()
    assert [e for e in wire.emails if "projections feed" in e[1]] == []
    assert _outcomes(project["id"]) == ["answered"]


def test_a_snooze_counts_as_seen(client, wire):
    """A snooze is "I saw it, not now" — the one thing the email was going to
    make sure of."""
    project = make_project(client)
    approval_id = _file(client, project)
    snoozed = client.post("/me/snoozes", headers=ADMIN, json={
        "project_id": project["id"], "item_key": approval_id,
        "until": (utcnow() + timedelta(hours=4)).isoformat()})
    assert snoozed.status_code == 200, snoozed.text
    _age(approval_id, 61)
    _tick()
    assert wire.emails == []
    assert _outcomes(project["id"]) == ["snoozed"]


def test_a_question_answered_in_the_boardroom_is_never_mailed(client, wire):
    """An ask closes when he speaks in the boardroom. The grace pass runs after
    the janitor marks that, so the tick that sees his reply settles it."""
    from tests.conftest import channel_of
    from yaaf.core import ids
    from yaaf.core.models import Ask, Message

    project = make_project(client)
    _, token = live_token(project["id"], project["ceo_agent_id"])
    asked = client.post("/agent/asks", headers=bearer(token), json={
        "title": "Which candidate?", "body": "Five ranked.", "why": "The launch post goes out on Friday.",
        "answer_by_hours": 6, "default_action": "Take the top-ranked candidate and say so."})
    assert asked.status_code == 200, asked.text
    with db_session() as session:
        session.add(Message(
            id=ids.new_id(ids.MESSAGE), project_id=project["id"],
            channel_id=channel_of(client, project["id"], "boardroom"),
            sender_user_id=session.get(Project, project["id"]).chairman_user_id,
            body="Number three."))
        for ask in session.query(Ask).filter(Ask.project_id == project["id"]):
            ask.created_at = utcnow() - timedelta(minutes=90)
    _tick()
    assert wire.emails == []
    assert _outcomes(project["id"]) == ["answered"]


# ── 3. by default, nothing but the CEO waiting ─────────────────────────────────


def test_by_default_alarms_and_platform_reminders_are_not_mailed(client, wire):
    project = make_project(client)
    _notify(project["id"], "project auto-paused: hard stop reached", Attention.ALARM)
    _notify(project["id"], "status meeting #3 is open", Attention.ACTION)
    _notify(project["id"], "deliverable published: r.md v1", Attention.FYI)
    assert wire.emails == []
    assert len(wire.hooks) == 3, "the webhook is never the party being spared"


def test_the_daily_brief_is_not_governed_by_mail_when(client, wire):
    """It has its own switch; a time set for it is a yes to that one email."""
    project = make_project(client, constitution={"mail_when": "never"})
    _notify(project["id"], "briefing — two things settled", Attention.BRIEF)
    assert len(wire.briefs) == 1


# ── 4. each answer lets in what it says ────────────────────────────────────────


@pytest.mark.parametrize(("mode", "mailed"), [
    ("your_ceo_is_waiting", {"ask"}),
    ("your_ceo_is_waiting_or_the_project_stops", {"ask", "alarm"}),
    ("anything_needs_you", {"ask", "alarm", "action"}),
    ("never", set()),
])
def test_each_answer_lets_in_exactly_what_it_says(client, wire, mode, mailed):
    project = make_project(client, constitution={"mail_when": mode,
                                                 "mail_max_per_day": 0})
    for attention in (Attention.ASK, Attention.ALARM, Attention.ACTION, Attention.FYI):
        _notify(project["id"], f"a {attention} notice", attention)
    assert {e[1].split()[-2] for e in wire.emails} == mailed


def test_changing_his_mind_binds_the_next_notice(client, wire):
    project = make_project(client)
    _notify(project["id"], "the project has gone quiet", Attention.ALARM)
    amend(client, project["id"], mail_when="your_ceo_is_waiting_or_the_project_stops")
    _notify(project["id"], "the project has gone quiet again", Attention.ALARM)
    assert [e[1] for e in wire.emails] == ["[yaaf/acme] the project has gone quiet again"]


def test_a_hold_he_turned_mail_off_for_is_settled_not_sent(client, wire):
    project = make_project(client)
    approval_id = _file(client, project)
    amend(client, project["id"], mail_when="never")
    _age(approval_id, 61)
    _tick()
    assert wire.emails == []
    assert _outcomes(project["id"]) == ["not wanted"]


def test_an_unknown_answer_is_refused_at_the_door(client):
    from yaaf.core.constitution import ConstitutionDoc

    with pytest.raises(ValueError, match="not a choice"):
        ConstitutionDoc(mail_when="sometimes")


# ── 5. one decision, one email ─────────────────────────────────────────────────


def test_an_expiry_chase_inside_the_grace_does_not_double_the_mail(client, wire):
    """A one-day approval is inside its 48-hour chase horizon the moment it is
    filed, so the chase fires on the first tick — inside the grace."""
    project = make_project(client, constitution={"approval_timeout_days": 1})
    approval_id = _file(client, project)
    _tick()
    _age(approval_id, 61)
    _tick()
    assert len([e for e in wire.emails if "projections feed" in e[1]]) == 1


# ── 6. every ASK can be checked for "still open" ───────────────────────────────


def test_every_ask_filed_at_its_moment_names_what_it_waits_on():
    """Over the AST, as the attention rule is. An ASK without `waits_on` is mailed
    at once, which is only right for a chase already past any grace — the
    janitor's batched proposal nags. Anywhere else it is the bug this file
    exists for: the CEO files, he is mailed while answering it."""
    exempt = {"nag_awaiting_chairman"}
    offenders = []
    for path in sorted(pathlib.Path("yaaf").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef) or fn.name in exempt:
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "id", None) == "chairman_notify"):
                    continue
                kws = {kw.arg: kw.value for kw in node.keywords}
                att = kws.get("attention")
                if (isinstance(att, ast.Attribute) and att.attr == "ASK"
                        and "waits_on" not in kws):
                    offenders.append(f"{path}:{node.lineno} ({fn.name})")
    assert not offenders, "an ASK with nothing to check: " + ", ".join(offenders)
