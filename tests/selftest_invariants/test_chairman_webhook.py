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


# ── the envelope names the event ───────────────────────────────────────────────


def test_a_kind_and_its_row_ride_the_envelope(monkeypatch):
    """A receiver routes on `kind` and reads the row it is about, in the same shape
    the observe listing returns, rather than parsing the subject's wording."""
    sent = _capture(monkeypatch)
    notify._send_webhook(
        "https://clio.example/hook", "acme", "Approve a change?", "body",
        secret="s", project_id="proj_1",
        kind="approval_requested", data={"approval": {"id": "apr_1", "amount_usd": 40}},
    )
    payload = sent.payload()
    assert payload["kind"] == "approval_requested"
    assert payload["approval"] == {"id": "apr_1", "amount_usd": 40}
    assert payload["text"].startswith("*Approve a change?*")


def test_a_notice_with_no_kind_keeps_the_old_envelope(monkeypatch):
    sent = _capture(monkeypatch)
    notify._send_webhook("https://clio.example/hook", "acme", "briefing — x", "y",
                         secret="s", project_id="proj_1")
    assert set(sent.payload()) == {"project", "subject", "text", "project_id"}


class Recorded:
    """Stands in for chairman_notify inside a module: records, never delivers."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, session, project, subject, body, *, attention,
                 kind=None, data=None):  # noqa: ARG002
        self.calls.append({"subject": subject, "body": body, "kind": kind, "data": data or {}})

    def of(self, kind: str) -> list[dict]:
        return [c for c in self.calls if c["kind"] == kind]


def test_a_data_write_request_notifies_with_the_approval_row(client, monkeypatch):
    from tests.conftest import agent_by_name, bearer, live_token
    from yaaf.server.api import agent as agent_api

    recorded = Recorded()
    monkeypatch.setattr(agent_api, "chairman_notify", recorded)
    project = make_project(client)
    attach = client.post(
        f"/admin/projects/{project['id']}/mcp_servers",
        json={"name": "ads", "url": "https://vendor.example.com/mcp", "bearer": "t",
              "roles": ["worker"], "access": "bulk", "tools": ["get_stats"]},
        headers=ADMIN)
    assert attach.status_code == 200, attach.text
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    filed = client.post("/agent/data_writes", headers=bearer(token),
                        json={"source": "ads", "tool": "create_campaigns", "arguments": {"b": 1}})
    assert filed.status_code == 200, filed.text

    notice = recorded.of("approval_requested")[0]
    row = notice["data"]["approval"]
    assert row["id"] == filed.json()["id"]
    assert row["status"] == "pending" and row["created_at"] and row["expires_at"]
    assert row["grant"]["tool"] == "create_campaigns"


def test_a_published_deliverable_notifies_with_its_row(client, monkeypatch):
    import base64

    from tests.conftest import agent_by_name, bearer, live_token
    from yaaf.server.api import agent as agent_api

    recorded = Recorded()
    monkeypatch.setattr(agent_api, "chairman_notify", recorded)
    project = make_project(client)
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    put = client.post("/agent/artifacts", headers=bearer(token),
                      json={"filename": "report.md",
                            "content_base64": base64.b64encode(b"# The audit\n").decode()})
    assert put.status_code == 200, put.text

    notice = recorded.of("deliverable_published")[0]
    deliverable = notice["data"]["deliverable"]
    assert deliverable["id"] == put.json()["artifact_id"]
    assert deliverable["name"] == "report.md" and deliverable["version"] == 1
    assert deliverable["published_at"]


def test_an_expired_approval_notifies_with_its_row(client, monkeypatch):
    from datetime import timedelta

    from tests.conftest import agent_by_name, bearer, live_token
    from yaaf.core.constitution import effective
    from yaaf.core.models import Approval, utcnow
    from yaaf.server import janitor

    recorded = Recorded()
    monkeypatch.setattr(janitor, "chairman_notify", recorded)
    project = make_project(client)
    _, token = live_token(project["id"], agent_by_name(project, "Lead"))
    filed = client.post("/agent/approvals", headers=bearer(token),
                        json={"kind": "spend", "title": "Buy a data pull",
                              "body": "Two months.",
                              "if_declined": "We forecast from last year's data.",
                              "amount_usd": 40})
    assert filed.status_code == 200, filed.text
    with db_session() as session:
        session.get(Approval, filed.json()["id"]).expires_at = utcnow() - timedelta(minutes=1)
    with db_session() as session:
        row = session.get(Project, project["id"])
        janitor.expire_approvals(session, row, effective(session, row.id))

    notice = recorded.of("approval_expired")[0]
    assert notice["data"]["approval"]["id"] == filed.json()["id"]
    assert notice["data"]["approval"]["status"] == "expired"
    assert notice["data"]["approval"]["amount_usd"] == 40


def test_a_closed_cycle_notifies_with_its_row(client, monkeypatch):
    from tests.selftest_invariants.test_modes import backdate_cycle, operate_project, run_mode_tick
    from yaaf.server import janitor

    recorded = Recorded()
    monkeypatch.setattr(janitor, "chairman_notify", recorded)
    project = operate_project(client, slug="closing")
    backdate_cycle(project["id"], days=9)
    run_mode_tick(project)

    notice = recorded.of("cycle_closed")[0]
    cycle = notice["data"]["cycle"]
    assert cycle["ordinal"] == 1 and cycle["status"] in {"skipped", "missed", "completed"}
    assert cycle["closed_at"] and cycle["id"]
    listing = client.get(f"/observe/projects/{project['id']}/cycles", headers=ADMIN).json()
    assert listing["history"][0]["id"] == cycle["id"]
    assert "goal" in listing["history"][0] and "due_at" in listing["current"]
