"""Invariant selftests: an approval reaches the chairman when it is filed.

Spend, legal and tool approvals are the decisions no autonomy setting takes
from him, and each carries a default-deny clock. The filing route used to send
nothing: the first word he heard was the expiry chase, 48 hours before a
seven-day clock ran out — five days in, on a request whose silence is a "no".
A veto he never got to cast is not a decision of his.

The rules under attack here:

1. Every kind an agent can file on `/agent/approvals` sends an ACTION notice
   at filing, saying what silence decides and when.
2. The notice says where to decide it — the Inbox — and links to that one row
   when the install knows its browser-facing origin, and never to the API's
   own origin, which only the containers can resolve.
3. No string the platform sends names a screen that no longer exists.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from tests.conftest import ADMIN, bearer, live_token, make_project
from yaaf.server import notify


class Wire:
    def __init__(self) -> None:
        self.emails: list[tuple] = []
        self.hooks: list[tuple] = []


class _Inline:
    """Runs the delivery on the calling thread, so the assertion can see it."""

    def __init__(self, target=None, daemon=None):  # noqa: ARG002
        self._target = target

    def start(self) -> None:
        self._target()


def _wire(monkeypatch, public_base_url: str, api_base_url: str = "http://api:8080") -> Wire:
    import threading

    caught = Wire()
    monkeypatch.setattr(threading, "Thread", _Inline)
    monkeypatch.setattr(notify, "settings", lambda: type("Cfg", (), {
        "smtp_host": "smtp.example.invalid", "smtp_port": 587, "smtp_from": "y@example",
        "smtp_username": "", "smtp_password": "",
        "chairman_webhook_url": "https://hooks.example.invalid/x",
        "public_base_url": public_base_url, "api_base_url": api_base_url})())
    monkeypatch.setattr(notify, "_send_email", lambda *a, **k: caught.emails.append(a))
    monkeypatch.setattr(notify, "_send_webhook", lambda *a, **k: caught.hooks.append((a, k)))
    return caught


@pytest.fixture
def wire(monkeypatch) -> Wire:
    return _wire(monkeypatch, public_base_url="https://yaaf.example.com")


def _file(client, project: dict, kind: str = "spend") -> dict:
    _, token = live_token(project["id"], project["ceo_agent_id"])
    response = client.post("/agent/approvals", headers=bearer(token), json={
        "kind": kind, "title": "Buy the $199 projections feed",
        "body": "The free feed lags injury news by a day. I recommend one month.",
        "if_declined": "We keep the free feed and miss late team news.",
        "amount_usd": 199 if kind == "spend" else None})
    assert response.status_code == 200, response.text
    return response.json()


def _filed_notices(wire: Wire) -> list[tuple]:
    return [(args, kw) for args, kw in wire.hooks if kw.get("kind") == "approval_requested"]


@pytest.mark.parametrize("kind", ["spend", "legal", "tool_request"])
def test_every_undelegable_kind_is_sent_the_moment_it_is_filed(client, wire, kind):
    project = make_project(client)
    filed = _file(client, project, kind)
    sent = _filed_notices(wire)
    assert len(sent) == 1, wire.hooks
    (url, slug, subject, body), kw = sent[0]
    assert "Buy the $199 projections feed" in subject
    assert kw["data"]["approval"]["id"] == filed["id"]
    # An ACTION, so it is mailed too — under the ration, never instead of it.
    assert any("Buy the $199 projections feed" in e[1] for e in wire.emails)


def test_the_notice_says_what_silence_decides(client, wire):
    project = make_project(client)
    _file(client, project)
    (_, _, _, body), _ = _filed_notices(wire)[0]
    assert "What happens without it: We keep the free feed" in body
    assert "the answer is no" in body


def test_the_notice_links_to_the_one_inbox_row(client, wire):
    project = make_project(client)
    filed = _file(client, project)
    (_, _, _, body), _ = _filed_notices(wire)[0]
    assert f"https://yaaf.example.com/app/#/inbox?item={filed['id']}" in body


def test_without_a_public_origin_it_names_the_inbox_and_links_nowhere(client, monkeypatch):
    wire = _wire(monkeypatch, public_base_url="")
    project = make_project(client)
    _file(client, project)
    (_, _, _, body), _ = _filed_notices(wire)[0]
    assert "Decide it in your Inbox." in body
    # The API's own origin is a container name; a link to it opens nothing.
    assert "api:8080" not in body
    assert "http" not in body.split("Decide it in your Inbox")[-1]


RETIRED_SCREENS = ("Decisions screen", "Plan tab", "Work tab")


def _sent_strings(path: pathlib.Path):
    """Every string literal in a module, docstrings and comments excluded —
    what the module can put in front of a person or an agent."""
    tree = ast.parse(path.read_text())
    docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                  if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef))
                  and node.body and isinstance(node.body[0], ast.Expr)
                  and isinstance(node.body[0].value, ast.Constant)}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in docstrings):
            yield node.lineno, node.value


def test_no_notice_names_a_screen_that_no_longer_exists():
    root = pathlib.Path(__file__).resolve().parents[2] / "yaaf" / "server"
    stale = [f"{path.relative_to(root.parent.parent)}:{line}: {text[:80]!r}"
             for path in root.rglob("*.py")
             for line, text in _sent_strings(path)
             if any(screen in text for screen in RETIRED_SCREENS)]
    assert not stale, "\n".join(stale)


def test_when_silence_decides_the_notice_says_which_way_and_what_now(client, wire):
    """"DENIED per this constitution" told him a rule had fired, not what was
    now true or how to undo it."""
    from datetime import timedelta

    from yaaf.core.constitution import effective
    from yaaf.core.db import db_session
    from yaaf.core.models import Approval, Project, utcnow
    from yaaf.server import janitor

    project = make_project(client)
    filed = _file(client, project)
    with db_session() as session:
        session.get(Approval, filed["id"]).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.expire_approvals(session, row, effective(session, row.id))

    sent = [(args, kw) for args, kw in wire.hooks if kw.get("kind") == "approval_expired"]
    assert len(sent) == 1, wire.hooks
    (_, _, subject, body), _ = sent[0]
    assert subject.endswith("no decision, so no: Buy the $199 projections feed")
    assert "it is declined and the CEO will plan without it" in body
    assert "tell the CEO in chat" in body
    assert "https://yaaf.example.com/app/#/projects/" in body
    assert "UTC" not in body


def test_a_spend_says_a_yes_cannot_be_taken_back(client, wire):
    """The one answer the platform knows from the kind alone, said where he
    decides: in the notice and on the row."""
    project = make_project(client)
    filed = _file(client, project)
    (_, _, _, body), _ = _filed_notices(wire)[0]
    assert "Can you undo a yes? No — a purchase cannot be taken back" in body
    rows = client.get(f"/observe/projects/{project['id']}/approvals",
                      headers=ADMIN).json()
    assert next(r for r in rows if r["id"] == filed["id"])["undo"].startswith("No")


def test_a_kind_the_platform_cannot_vouch_for_says_nothing(client, wire):
    project = make_project(client)
    _file(client, project, "legal")
    (_, _, _, body), _ = _filed_notices(wire)[0]
    assert "Can you undo" not in body


def test_the_row_says_what_silence_decides_on_this_project(client, wire):
    project = make_project(client, constitution={"approval_timeout_decision": "approve"})
    filed = _file(client, project)
    desk = client.get("/observe/desk", headers=ADMIN).json()
    row = next(p for p in desk["projects"] if p["id"] == project["id"])
    pending = next(a for a in row["pending_approvals"] if a["id"] == filed["id"])
    assert pending["if_silent"] == "approved"
