"""Invariant selftests: what the platform is allowed to put in a human's inbox.

Every notice used to become an email. That made the inbox the one surface in
this system with no theory of whose news is whose — the briefing has had one
since it shipped (`briefing.LINE_OWNER` files each line against the autonomy
dial), and the mail path ignored it, so a chairman who set `scope_authority:
ceo` was still mailed every decision he had just delegated. A working project
sent about thirty emails a day, most of them announcing something already done
and already batched into that morning's brief.

So a notice now names how much of the chairman it is asking for, and that
decides the inbox. The rules under attack here:

1. An `fyi` never becomes an email, whatever the transport is configured to do.
2. An `alarm` always does, even past the daily limit — rationing the message
   that says the project stopped is how a project sits dead for a week.
3. An `action` spends against `mail_max_per_day` and is held after it.
4. The webhook is exempt from all of it: it is a machine receiver that routes
   on `kind`, and muting it would break integrations silently.
5. Every call site names a class. A default would have let the next person
   decide by not thinking about it, which is how this got here.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tests.conftest import amend, make_project
from yaaf.core.db import db_session
from yaaf.core.models import AuditLog, Project
from yaaf.server import notify
from yaaf.server.notify import Attention, chairman_notify


class Wire:
    """Both transports, in place of the network."""

    def __init__(self) -> None:
        self.emails: list[tuple] = []
        self.hooks: list[tuple] = []


@pytest.fixture
def wire(monkeypatch) -> Wire:
    """A configured deployment: SMTP and a webhook both live, both captured.

    `chairman_notify` delivers on a daemon thread by contract, so the thread is
    run inline here — threading it would test the threading.
    """
    caught = Wire()
    monkeypatch.setattr(notify, "settings", lambda: type("Cfg", (), {
        "smtp_host": "smtp.example.invalid", "smtp_port": 587, "smtp_from": "y@example",
        "smtp_username": "", "smtp_password": "",
        "chairman_webhook_url": "https://hooks.example.invalid/x"})())
    monkeypatch.setattr(notify, "_send_email",
                        lambda *a, **k: caught.emails.append(a))
    monkeypatch.setattr(notify, "_send_webhook",
                        lambda *a, **k: caught.hooks.append(a))
    return caught


class _Inline:
    """Runs the delivery on the calling thread, so the assertion can see it."""

    def __init__(self, target=None, daemon=None):  # noqa: ARG002
        self._target = target

    def start(self) -> None:
        self._target()


@pytest.fixture
def _inline_thread(monkeypatch):
    import threading
    monkeypatch.setattr(threading, "Thread", _Inline)


def _project(client, **cons) -> str:
    return make_project(client, constitution=cons or None)["id"]


def _notify(project_id: str, subject: str, attention: Attention, **kw) -> None:
    with db_session() as session:
        chairman_notify(session, session.get(Project, project_id), subject, "body",
                        attention=attention, **kw)
        session.commit()


# ── 1. an fyi never reaches the inbox ───────────────────────────────────────────


def test_an_fyi_never_becomes_an_email_however_the_transport_is_configured(
        client, wire, _inline_thread):
    """The rule that does the work. SMTP is live and the chairman has an
    address; the notice still does not become mail, because of what it IS."""
    pid = _project(client)
    _notify(pid, "deliverable published: report.md v1", Attention.FYI)
    assert wire.emails == []


def test_an_fyi_still_reaches_the_webhook(client, wire, _inline_thread):
    """The receiver is a machine that routes on `kind`, it is not the party
    drowning, and a silently truncated stream is how an integration breaks in a
    way nobody reports. Muting the human must not mute the API."""
    pid = _project(client)
    _notify(pid, "cycle 3 closed shipped", Attention.FYI, kind="cycle_closed")
    assert len(wire.hooks) == 1


def test_an_fyi_does_not_spend_the_budget_it_never_used(client, wire, _inline_thread):
    """A held ACTION and a silent FYI are different states. If FYI spent, a
    chatty project would starve its own alarms' neighbours for nothing."""
    pid = _project(client, mail_max_per_day=2)
    for i in range(6):
        _notify(pid, f"deliverable published: r{i}.md", Attention.FYI)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    _notify(pid, "R-02 is waiting on your verdict", Attention.ACTION)
    assert len(wire.emails) == 2, "the FYIs ate the chairman's ration"


# ── 2. an alarm is never held ───────────────────────────────────────────────────


def test_an_alarm_still_mails_with_the_budget_already_spent(
        client, wire, _inline_thread):
    """The one that must not regress. A project that has already used its
    ration and THEN pauses itself is exactly the case where silence costs a
    week — so alarms are outside the budget in both directions: they do not
    spend it and it does not stop them."""
    pid = _project(client, mail_max_per_day=1)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    _notify(pid, "R-02 is waiting on your verdict", Attention.ACTION)  # held
    assert len(wire.emails) == 1

    _notify(pid, "project auto-paused: hard stop reached", Attention.ALARM)
    subjects = [e[1] for e in wire.emails]
    assert any("hard stop" in s for s in subjects), subjects


def test_alarms_do_not_spend_the_budget_either(client, wire, _inline_thread):
    """Twenty alarms must not silence the approval that arrives after them."""
    pid = _project(client, mail_max_per_day=1)
    for i in range(20):
        _notify(pid, f"the project has gone quiet {i}", Attention.ALARM)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    assert any("waiting on your verdict" in e[1] for e in wire.emails)


def test_the_daily_brief_is_never_held(client, wire, _inline_thread):
    """It is the letter the held notices are read in. Capping the digest that
    carries the overflow would lose the overflow twice."""
    pid = _project(client, mail_max_per_day=1)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    _notify(pid, "briefing — two requirements settled", Attention.BRIEF)
    assert any("briefing" in e[1] for e in wire.emails)


# ── 3. the budget binds, and says so on the record ──────────────────────────────


def test_action_mail_stops_at_the_chairmans_number(client, wire, _inline_thread):
    pid = _project(client, mail_max_per_day=3)
    for i in range(9):
        _notify(pid, f"R-{i:02d} is waiting on your verdict", Attention.ACTION)
    assert len(wire.emails) == 3


def test_the_chairman_can_turn_the_limit_off(client, wire, _inline_thread):
    """0 is unlimited. A chairman who wants every interrupt is entitled to it —
    the constraint in this system runs on the agents, never on him."""
    pid = _project(client, mail_max_per_day=0)
    for i in range(9):
        _notify(pid, f"R-{i:02d} is waiting on your verdict", Attention.ACTION)
    assert len(wire.emails) == 9


def test_the_limit_binds_from_the_next_notice_when_he_turns_it_on(
        client, wire, _inline_thread):
    """Amendments bind immediately in this system, and this is no exception.

    Mail sent while the limit was off is not metered and so does not count
    against the limit he then sets: he gets a full ration from the moment the
    rule exists, rather than a first day silently shortened by traffic sent
    under a rule that did not. Turning a limit ON should not read as punishment
    for the period before it.
    """
    project = make_project(client, constitution={"mail_max_per_day": 0})
    pid = project["id"]
    _notify(pid, "R-00 is waiting on your verdict", Attention.ACTION)  # unmetered
    amend(client, pid, mail_max_per_day=1)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)  # the ration
    _notify(pid, "R-02 is waiting on your verdict", Attention.ACTION)  # held
    assert [e[1] for e in wire.emails] == [
        "[yaaf/acme] R-00 is waiting on your verdict",
        "[yaaf/acme] R-01 is waiting on your verdict",
    ]


def test_both_the_sent_and_the_held_are_on_the_record(client, wire, _inline_thread):
    """A chairman whose inbox goes quiet must be able to find out why rather
    than infer it, and an auditor has to be able to reconstruct what he was
    actually told."""
    pid = _project(client, mail_max_per_day=1)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    _notify(pid, "R-02 is waiting on your verdict", Attention.ACTION)
    with db_session() as session:
        actions = [r.action for r in session.query(AuditLog)
                   .filter(AuditLog.project_id == pid).all()]
    assert actions.count(notify.MAIL_SPEND) == 1
    assert actions.count(notify.MAIL_HELD) == 1


def test_a_deployment_with_no_mail_host_spends_no_ration(client, monkeypatch):
    """A webhook-only install sends no mail, so it has nothing to ration. If it
    charged anyway, the first notice after someone finally configured a mailbox
    would be held against traffic that never reached anybody."""
    caught: list[tuple] = []
    monkeypatch.setattr(notify, "settings", lambda: type("Cfg", (), {
        "smtp_host": "", "smtp_port": 587, "smtp_from": "", "smtp_username": "",
        "smtp_password": "", "chairman_webhook_url": "https://hooks.example/x"})())
    monkeypatch.setattr(notify, "_send_email", lambda *a, **k: caught.append(a))
    monkeypatch.setattr(notify, "_send_webhook", lambda *a, **k: None)
    import threading
    monkeypatch.setattr(threading, "Thread", _Inline)

    pid = _project(client, mail_max_per_day=1)
    for i in range(5):
        _notify(pid, f"R-{i:02d} is waiting on your verdict", Attention.ACTION)
    assert caught == []
    with db_session() as session:
        actions = [r.action for r in session.query(AuditLog)
                   .filter(AuditLog.project_id == pid).all()]
    assert notify.MAIL_SPEND not in actions and notify.MAIL_HELD not in actions


def test_the_window_rolls_rather_than_resetting_at_midnight(
        client, wire, _inline_thread):
    """A calendar reset is a cliff a busy project walks off: chased five times
    by 23:00, five more before he wakes. The spend older than the window ages
    out; the one inside it still counts."""
    from datetime import timedelta

    from yaaf.core.models import utcnow

    pid = _project(client, mail_max_per_day=1)
    _notify(pid, "R-01 is waiting on your verdict", Attention.ACTION)
    _notify(pid, "R-02 is waiting on your verdict", Attention.ACTION)
    assert len(wire.emails) == 1

    with db_session() as session:
        for row in session.query(AuditLog).filter(
                AuditLog.action == notify.MAIL_SPEND).all():
            row.at = utcnow() - timedelta(hours=25)
        session.commit()

    _notify(pid, "R-03 is waiting on your verdict", Attention.ACTION)
    assert len(wire.emails) == 2


# ── 4. no call site gets to skip the question ───────────────────────────────────


def test_every_chairman_notify_call_names_its_class():
    """The rule as a refusal rather than a comment. `attention` is keyword-only
    with no default so this is already a TypeError at runtime — but a call site
    on a path no test walks would find that out in production, and the point of
    the parameter is that a human had to decide. Checked over the AST, so a
    call spelled across four lines is still seen."""
    offenders = []
    for path in sorted(pathlib.Path("yaaf").rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if name != "chairman_notify":
                continue
            if not any(kw.arg == "attention" for kw in node.keywords):
                offenders.append(f"{path}:{node.lineno}")
    assert not offenders, (
        "these notices do not say how much of the chairman they are asking for: "
        + ", ".join(offenders))


def test_the_classes_are_exactly_the_ones_the_inbox_rule_knows_about():
    """A fifth class added without deciding whether it mails would default to
    silent, which is the failure mode this whole change exists to fix."""
    assert set(Attention) == {Attention.ALARM, Attention.ACTION,
                              Attention.BRIEF, Attention.FYI}
    assert notify.MAILED | {Attention.FYI} == set(Attention)
    assert notify.BUDGETED <= notify.MAILED
