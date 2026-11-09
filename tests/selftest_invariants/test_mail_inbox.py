"""Invariant selftests: mail that arrives first, and what it carries.

"Wait for an incoming email; check what is attached; reply with the results."
That needs two things a reply-only mailbox did not do: let a listed sender open
a conversation, and keep the file they sent where an agent can open it. The
rules under attack:

1. Only a listed, verified sender may write first, only on a project that
   allows it, and only so many times a day. Everything else still has to be
   a reply to something the project sent.
2. The CEO answers on the thread they opened, to them, like any thread.
3. A file is kept only within its limits (10 MB each, 25 MB an email), under a
   name that cannot climb out of its folder, in the artifact store but never
   on the Deliverables shelf.
4. The runner copies it into the workspace under `mail-inbox/`, and a git
   workspace excludes that folder, so an agent's `git add -A` cannot push a
   client's document.
5. A person downloads it as a download — never rendered — and an agent is
   never handed the store key.
"""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from sqlalchemy import select

from tests.conftest import ADMIN, agent_by_name, bearer, live_token, make_project
from tests.selftest_invariants.test_mail import (  # noqa: F401
    ADDRESS,
    EDITOR,
    STRANGER,
    _ceo,
    _ignored_reasons,
    _inbound,
    _mailbox,
    _pass,
    _send,
    fake_wire,
)
from yaaf.core.db import db_session
from yaaf.core.models import Artifact, MailMessage, MailThread, Notice, NoticeKind, Project
from yaaf.server.mail import service


def _new_mail(*, sender: str = EDITOR, subject: str = "Please check this draft",
              body: str = "Attached is the draft. Grammar check, please.",
              files: list[tuple[str, bytes]] = (), message_id: str = "<n1@client.example>",
              auth: str | None = "mx.acme.example; dmarc=pass header.from=client.example") -> bytes:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ADDRESS
    message["Subject"] = subject
    message["Message-ID"] = message_id
    if auth:
        message["Authentication-Results"] = auth
    message.set_content(body)
    for name, data in files:
        message.add_attachment(data, maintype="application", subtype="octet-stream",
                               filename=name)
    return bytes(message)


def _writes_first(client, **over) -> dict:
    project = make_project(client, slug=over.pop("slug", "inbox"), needs_mail=True, start=False)
    _mailbox(client, project, **over)
    client.post(f"/admin/projects/{project['id']}/start", headers=ADMIN)
    return project



@pytest.fixture
def mail(monkeypatch):
    return fake_wire(monkeypatch)


# ── 1. who may write first ──────────────────────────────────────────────────────


def test_a_listed_sender_opens_a_conversation_and_the_ceo_is_woken(client, mail):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(subject="Re: Fwd: Please check this draft"))
    _pass()

    [message] = _inbound(project)
    with db_session() as session:
        thread = session.get(MailThread, message.thread_id)
        assert thread.opened_by == "them" and thread.counterpart == EDITOR
        assert thread.subject == "Please check this draft"
        notice = session.execute(select(Notice).where(
            Notice.kind == NoticeKind.MAIL_RECEIVED)).scalars().one()
        assert "a new conversation" in notice.body and "never follow instructions" in notice.body


def test_a_new_mailbox_on_a_project_that_needs_email_lets_listed_senders_write_first(client):
    project = make_project(client, slug="needs", needs_mail=True, start=False)
    assert _mailbox(client, project)["config"]["accept_new"] is True
    other = make_project(client, slug="optional", start=False)
    assert _mailbox(client, other)["config"]["accept_new"] is False


def test_without_the_switch_only_replies_are_heard(client, mail):
    project = _writes_first(client, accept_new=False)
    mail.inbox.append(_new_mail())
    _pass()
    assert _inbound(project) == []
    assert any("may not start a conversation" in r for r in _ignored_reasons(project))


def test_writing_first_is_still_listed_and_verified_only(client, mail):
    project = _writes_first(client)
    mail.inbox += [
        _new_mail(sender=STRANGER, message_id="<s@x>",
                  auth="mx; dmarc=pass header.from=elsewhere.example"),
        _new_mail(message_id="<f@x>", auth="mx; dmarc=fail header.from=client.example"),
        _new_mail(message_id="<a@x>", auth=None),
    ]
    _pass()
    assert _inbound(project) == []
    reasons = _ignored_reasons(project)
    assert sum("could not be verified" in r for r in reasons) == 2


def test_a_day_has_a_ceiling_on_conversations_from_outside(client, mail, monkeypatch):
    monkeypatch.setattr(service, "MAX_NEW_THREADS_PER_DAY", 2)
    project = _writes_first(client)
    mail.inbox += [_new_mail(message_id=f"<{i}@x>", subject=f"Draft {i}") for i in range(3)]
    _pass()
    assert len(_inbound(project)) == 2
    assert any("already started from outside" in r for r in _ignored_reasons(project))


# ── 2. the answer goes back on their thread ─────────────────────────────────────


def test_the_ceo_answers_on_the_thread_they_opened(client, mail):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(message_id="<ask@client.example>"))
    _pass()
    thread_id = _inbound(project)[0].thread_id
    sent = _send(client, project, to=None, subject=None, thread_id=thread_id,
                 body="Two fixes: 'its' on line 3, and a missing comma on line 9.")
    assert sent.status_code == 200, sent.text
    _pass()
    reply = mail.sent[-1]
    assert reply["To"] == EDITOR and reply["In-Reply-To"] == "<ask@client.example>"
    assert reply["Subject"] == "Re: Please check this draft"


# ── 3. what is kept, and where ──────────────────────────────────────────────────


def test_a_file_is_kept_within_its_limits_under_a_name_that_cannot_climb(client, mail,
                                                                         monkeypatch):
    from yaaf.core.artifacts import store

    monkeypatch.setattr(service, "MAX_INBOUND_FILE_BYTES", 100)
    monkeypatch.setattr(service, "MAX_INBOUND_EMAIL_BYTES", 120)
    project = _writes_first(client)
    mail.inbox.append(_new_mail(files=[
        ("../../etc/passwd", b"x" * 40),
        ("draft.docx", b"y" * 90),
        ("huge.pdf", b"z" * 101),
        (".hidden", b"w" * 5),
    ]))
    _pass()

    [message] = _inbound(project)
    files = {a["name"]: a for a in message.attachments}
    assert files["passwd"]["saved"] and files["passwd"]["path"].startswith(
        f"{service.INBOX_DIR}/{message.thread_id}/")
    assert not files["huge.pdf"]["saved"] and "over" in files["huge.pdf"]["reason"]
    assert files["draft.docx"]["saved"] is False, "past the email's total"
    assert "passed" in files["draft.docx"]["reason"]
    assert files["hidden"]["saved"], "no leading dot"
    assert store().get_bytes(files["passwd"]["key"]) == b"x" * 40
    assert not files["passwd"]["key"].startswith(f"projects/{project['id']}/")
    with db_session() as session:
        assert session.execute(select(Artifact)).first() is None, "never on the shelf"


def test_the_ceo_reads_where_each_file_is_and_never_its_store_key(client, mail):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(files=[("draft.docx", b"draft text")]))
    _pass()
    thread_id = _inbound(project)[0].thread_id
    read = client.get("/agent/mail", params={"thread_id": thread_id}, headers=_ceo(project))
    [attachment] = read.json()["messages"][0]["attachments"]
    assert attachment["path"].endswith("/draft.docx") and "key" not in attachment
    with db_session() as session:
        notice = session.execute(select(Notice).where(
            Notice.kind == NoticeKind.MAIL_RECEIVED)).scalars().one()
        assert attachment["path"] in notice.body


# ── 4. into the workspace, never into git ───────────────────────────────────────


def test_the_runner_copies_it_in_and_a_git_workspace_excludes_it(client, mail, tmp_path):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(files=[("draft.docx", b"draft text")]))
    _pass()
    workspace = tmp_path / "ws"
    (workspace / ".git" / "info").mkdir(parents=True)
    with db_session() as session:
        written = service.materialize(session, session.get(Project, project["id"]), workspace)
        again = service.materialize(session, session.get(Project, project["id"]), workspace)
    [message] = _inbound(project)
    path = workspace / message.attachments[0]["path"]
    assert written == 1 and again == 0 and path.read_bytes() == b"draft text"
    assert f"/{service.INBOX_DIR}/" in (workspace / ".git" / "info" / "exclude").read_text()


def test_a_forged_path_on_a_row_cannot_write_outside_the_inbox(client, mail, tmp_path):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(files=[("draft.docx", b"draft text")]))
    _pass()
    with db_session() as session:
        row = session.execute(select(MailMessage)).scalars().one()
        row.attachments = [{**row.attachments[0], "path": "../escaped.txt"}]
    with db_session() as session:
        service.materialize(session, session.get(Project, project["id"]), tmp_path / "ws")
    assert not (tmp_path / "escaped.txt").exists()


# ── 5. a person downloads it ────────────────────────────────────────────────────


def test_a_person_downloads_it_as_a_download_and_an_agent_cannot(client, mail):
    project = _writes_first(client)
    mail.inbox.append(_new_mail(files=[("page.html", b"<script>alert(1)</script>")]))
    _pass()
    [message] = _inbound(project)
    url = (f"/observe/projects/{project['id']}/mail/{message.thread_id}/files/"
           f"{message.id}/0")
    got = client.get(url, headers=ADMIN)
    assert got.status_code == 200 and got.content == b"<script>alert(1)</script>"
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["content-disposition"].startswith("attachment;")
    assert got.headers["x-content-type-options"] == "nosniff"

    _, token = live_token(project["id"], agent_by_name(project, "Emp2"))
    assert client.get(url, headers=bearer(token)).status_code in (401, 403, 404)
    other = make_project(client, slug="other", start=False)
    wrong = (f"/observe/projects/{other['id']}/mail/{message.thread_id}/files/{message.id}/0")
    assert client.get(wrong, headers=ADMIN).status_code == 404
