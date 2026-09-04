"""Invariant selftests: outbound chairman notification.

The inbound connectors prove a delivery TO yaaf is genuine. This is the other
direction: a receiver holding several projects' notices has to know whose a
notice is, and has to be able to tell a real one from anything else that can
reach the address. Both answers live in the delivery, so they are asserted on
the wire rather than in the log line.

`chairman_notify` delivers on a daemon thread by contract, so every test here
calls `_send_webhook` directly. Threading it would test the threading.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from tests.conftest import ADMIN, make_project
from yaaf.core.db import db_session
from yaaf.core.models import Project
from yaaf.server import notify


class Sent:
    """Captures one delivery, in place of the network."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, dict]] = []

    def __call__(self, req, timeout=None):  # noqa: ARG002 - urlopen's signature
        self.calls.append((req.full_url, req.data, dict(req.headers)))

        class _Response:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return None

        return _Response()

    @property
    def one(self) -> tuple[str, bytes, dict]:
        assert len(self.calls) == 1, f"expected one delivery, got {len(self.calls)}"
        return self.calls[0]

    def header(self, name: str) -> str | None:
        # urllib normalises header names with str.capitalize(), so "X-Yaaf-Signature"
        # arrives as "X-yaaf-signature". Matched case-insensitively rather than
        # against whichever spelling urllib happens to use.
        wanted = name.lower()
        return next((v for k, v in self.one[2].items() if k.lower() == wanted), None)

    def payload(self) -> dict:
        return json.loads(self.one[1])


def _capture(monkeypatch) -> Sent:
    sent = Sent()
    monkeypatch.setattr(notify.urllib.request, "urlopen", sent)
    return sent


def _verifies(secret: str, raw: bytes, provided: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def test_a_signed_delivery_verifies_under_the_projects_secret(monkeypatch):
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Budget at 80%", "Spent $16 of $20.",
        secret="project-secret", project_id="proj_1",
    )
    url, raw, _ = sent.one
    assert url == "https://clio.example/hook"
    assert _verifies("project-secret", raw, sent.header("X-Yaaf-Signature"))


def test_another_projects_secret_does_not_verify(monkeypatch):
    """The point of signing: one project's secret must not vouch for another's notice."""
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Budget at 80%", "Spent $16 of $20.",
        secret="project-secret", project_id="proj_1",
    )
    assert not _verifies("another-projects-secret", sent.one[1], sent.header("X-Yaaf-Signature"))


def test_a_tampered_body_does_not_verify(monkeypatch):
    """Signed over the raw body, so editing the notice in flight breaks the signature."""
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Budget at 80%", "Spent $16 of $20.",
        secret="project-secret", project_id="proj_1",
    )
    tampered = sent.one[1].replace(b"80%", b"10%")
    assert not _verifies("project-secret", tampered, sent.header("X-Yaaf-Signature"))


def test_the_secret_never_travels_with_the_notice(monkeypatch):
    """A signature the receiver can check, never the key it checks against."""
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Budget at 80%", "Spent $16 of $20.",
        secret="project-secret", project_id="proj_1",
    )
    url, raw, headers = sent.one
    assert b"project-secret" not in raw
    assert "project-secret" not in url
    assert not any("project-secret" in str(value) for value in headers.values())


def test_the_delivery_names_the_project_by_id(monkeypatch):
    """A slug is the chairman's name for a project and can be edited; an id cannot."""
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Subject", "Body",
        secret="s", project_id="proj_1",
    )
    assert sent.payload()["project_id"] == "proj_1"
    assert sent.payload()["project"] == "acme"


def test_an_unsigned_delivery_keeps_the_shape_it_always_had(monkeypatch):
    """The app-wide hook has no per-project secret, so it sends no signature — and
    a receiver written against the old shape must not break."""
    sent = _capture(monkeypatch)
    notify._send_webhook("https://ops.example/hook", "acme", "Subject", "Body")
    assert sent.header("X-Yaaf-Signature") is None
    assert set(sent.payload()) == {"project", "subject", "text"}


def test_a_dead_receiver_does_not_raise(monkeypatch):
    """Best-effort by contract: a Clio outage must never break an enforcement tick."""

    def explode(req, timeout=None):  # noqa: ARG001
        raise OSError("connection refused")

    monkeypatch.setattr(notify.urllib.request, "urlopen", explode)
    notify._send_webhook("https://clio.example/hook", "acme", "Subject", "Body", secret="s")


def test_a_projects_own_hook_wins_over_the_app_wide_one(client, monkeypatch):
    """With several projects on one deployment, the app-wide URL cannot say whose
    notice it is carrying. A project that names its own is routed there."""
    project_id = make_project(client)["id"]
    response = client.patch(
        f"/admin/projects/{project_id}",
        json={
            "integrations": {
                "chairman_webhook_url": "https://clio.example/projects/acme",
                "chairman_webhook_secret": "per-project",
            }
        },
        headers=ADMIN,
    )
    assert response.status_code == 200, response.text

    sent = _capture(monkeypatch)
    monkeypatch.setattr(
        notify, "settings", lambda: type("Cfg", (), {
            "smtp_host": "", "chairman_webhook_url": "https://ops.example/all",
        })()
    )
    with db_session() as session:
        project = session.get(Project, project_id)
        integrations = project.integrations or {}
        # Delivered inline rather than through chairman_notify's daemon thread.
        notify._send_webhook(
            integrations["chairman_webhook_url"], project.slug, "Subject", "Body",
            secret=integrations.get("chairman_webhook_secret"), project_id=project.id,
        )

    url, raw, _ = sent.one
    assert url == "https://clio.example/projects/acme"
    assert _verifies("per-project", raw, sent.header("X-Yaaf-Signature"))
